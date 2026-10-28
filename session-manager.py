#!/usr/bin/env python3
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time

SESSION_FILE = os.path.expanduser("~/.cache/hyprland-session.json")
LOG_FILE = os.path.expanduser("~/.cache/session-manager.log")

RESTORE_TIMEOUT = 45  # сколько секунд ждать появления окон
SETTLE_SECONDS = 20   # окно должно прожить столько секунд, чтобы считаться настоящим (а не заставкой)
TERMINAL_SHELL = "fish"

# Переопределения команд запуска. Нужны ТОЛЬКО там, где автоопределение
# (через .desktop-файлы и /proc/<pid>/cmdline) ошибается или не находит команду.
LAUNCH_COMMANDS = {
    "zen": "/opt/zen-browser-bin/zen-bin",
    "gog_com-RimWorld_1": "/home/mefue/Games/RimWorld/start.sh",
}

# Классы окон, которые не нужно сохранять
IGNORE_CLASSES = {"", "xdg-desktop-portal-gtk", "xwaylandvideobridge"}

# Приложения, у которых один процесс сам открывает все окна:
# запускаем один раз, даже если окон в сессии несколько.
SINGLE_INSTANCE = {"steam", "discord", "spotify", "org.telegram.desktop", "thunderbird"}

# Терминалы: шаблон запуска с рабочей директорией
TERMINALS = {
    "foot": "foot --working-directory={cwd} {shell}",
    "kitty": "kitty -d {cwd} {shell}",
    "konsole": "konsole --workdir {cwd} -e {shell}",
    "Alacritty": "alacritty --working-directory {cwd} -e {shell}",
}

# После расстановки «встряхнуть» special workspace (показать и скрыть),
# чтобы Hyprland пересчитал раскладку окон внутри него.
REFRESH_SPECIAL = True


def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    print(msg)


# ───────────────────────── Hyprland ─────────────────────────

def get_clients():
    try:
        output = subprocess.check_output(["hyprctl", "clients", "-j"])
        return json.loads(output)
    except Exception as e:
        log(f"Ошибка получения списка окон: {e}")
        return []


def get_monitors():
    try:
        return json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))
    except Exception as e:
        log(f"Ошибка получения списка мониторов: {e}")
        return []


def dispatch(expr):
    """expr — Lua-выражение диспетчера. hyprctl сам оборачивает его в
    `return hl.dispatch(<expr>)`, поэтому внешний hl.dispatch(...) писать не нужно."""
    r = subprocess.run(["hyprctl", "dispatch", expr], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or "error" in out.lower():
        log(f"  dispatch не сработал: {expr}\n    ответ: {out}")
        return False
    return True


def move_to_workspace(addr, ws):
    # ws: число или "special:имя"
    return dispatch(
        f'hl.dsp.window.move({{ workspace = "{ws}", window = "address:{addr}", follow = false }})'
    )


def set_floating(addr, state):
    action = "enable" if state else "disable"
    return dispatch(f'hl.dsp.window.float({{ action = "{action}", window = "address:{addr}" }})')


def set_geometry(addr, at, size):
    x, y = at
    w, h = size
    dispatch(f'hl.dsp.window.resize({{ x = {w}, y = {h}, relative = false, window = "address:{addr}" }})')
    dispatch(f'hl.dsp.window.move({{ x = {x}, y = {y}, relative = false, window = "address:{addr}" }})')


def toggle_special(short_name):
    # В вашем конфиге: hl.dsp.workspace.toggle_special() без аргумента = "special"
    arg = "" if short_name == "special" else f'"{short_name}"'
    return dispatch(f"hl.dsp.workspace.toggle_special({arg})")


def focus_window(addr):
    return dispatch(f'hl.dsp.focus({{ window = "address:{addr}" }})')


def workspace_target(c):
    """Обычный стол -> номер, special workspace (caelestia) -> 'special:имя'."""
    ws = c.get("workspace", {})
    name = ws.get("name", "")
    if name.startswith("special"):
        return name
    return ws.get("id", 1)


# ───────────────── Автоопределение команды запуска ─────────────────

def clean_exec(line):
    line = re.sub(r"%[fFuUdDnNickvm]|@@\w*", "", line).replace("%%", "%").strip()
    return re.sub(r"\s+--$", "", line)  # хвостовой "--" остаётся от "%u"


def build_desktop_index():
    """class (в нижнем регистре) -> команда из .desktop-файла."""
    dirs = [
        os.path.expanduser("~/.local/share/applications"),
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        "/usr/local/share/applications",
        "/usr/share/applications",
    ]
    index = {}
    for d in dirs:  # более ранние каталоги имеют приоритет
        for path in glob.glob(os.path.join(d, "*.desktop")):
            try:
                exec_line = wmclass = None
                in_entry = False
                with open(path, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("["):
                            in_entry = line == "[Desktop Entry]"
                        elif in_entry and line.startswith("Exec=") and exec_line is None:
                            exec_line = line[5:]
                        elif in_entry and line.startswith("StartupWMClass="):
                            wmclass = line.split("=", 1)[1]
                if not exec_line:
                    continue
                exec_line = clean_exec(exec_line)
                name = os.path.basename(path)[:-len(".desktop")].lower()
                if wmclass:
                    index.setdefault(wmclass.lower(), exec_line)
                index.setdefault(name, exec_line)
            except Exception:
                continue
    return index


def cmdline_of(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a.decode(errors="ignore") for a in f.read().split(b"\0") if a]
        return shlex.join(args) if args else None
    except Exception:
        return None


def resolve_cmd(c, index):
    cls = c.get("class", "")
    if cls in LAUNCH_COMMANDS:
        return LAUNCH_COMMANDS[cls]
    for key in (cls, c.get("initialClass", "")):
        if key and key.lower() in index:
            return index[key.lower()]
    pid = c.get("pid")
    return cmdline_of(pid) if pid else None


def get_cwd(pid):
    """Пытаемся получить рабочую директорию шелла внутри терминала."""
    try:
        child = subprocess.check_output(
            ["pgrep", "-P", str(pid)], text=True
        ).strip().split("\n")[0]
        return subprocess.check_output(
            ["readlink", "-f", f"/proc/{child}/cwd"], text=True
        ).strip()
    except Exception:
        return None


# ───────────────────────── Сохранение ─────────────────────────

def save_session():
    index = build_desktop_index()
    session = []
    for c in get_clients():
        cls = c.get("class", "")
        if cls in IGNORE_CLASSES or not c.get("mapped", True):
            continue
        cmd = resolve_cmd(c, index)
        if not cmd and cls not in TERMINALS:
            log(f"Не удалось определить команду запуска для {cls}, пропускаю")
            continue
        entry = {
            "class": cls,
            "cmd": cmd,
            "workspace": workspace_target(c),
            "floating": bool(c.get("floating")),
            "at": c.get("at"),
            "size": c.get("size"),
        }
        if cls in TERMINALS and c.get("pid"):
            entry["cwd"] = get_cwd(c["pid"])
        session.append(entry)

    if not session:
        log("Окон для сохранения нет — старую сессию не перезаписываю.")
        return

    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(session, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SESSION_FILE)
    log(f"Сессия сохранена. Окон: {len(session)}: {[s['class'] for s in session]}")


# ──────────────────────── Восстановление ────────────────────────

def build_cmd(item):
    cls = item["class"]
    if cls in TERMINALS:
        cwd = item.get("cwd") or os.path.expanduser("~")
        return TERMINALS[cls].format(cwd=shlex.quote(cwd), shell=TERMINAL_SHELL)
    cmd = LAUNCH_COMMANDS.get(cls) or item.get("cmd")
    return re.sub(r"\s+--$", "", cmd) if cmd else None


def place_window(c, item):
    addr = c["address"]
    cls = item["class"]
    target = item.get("workspace", 1)

    if str(workspace_target(c)) != str(target):
        move_to_workspace(addr, target)

    at, size = item.get("at"), item.get("size")
    if item.get("floating") and at and size:
        if not c.get("floating"):
            set_floating(addr, True)
        set_geometry(addr, at, size)
    elif c.get("floating") and "floating" in item:
        set_floating(addr, False)

    log(f"  {cls} → {target}" + (f" @ {at} {size}" if item.get("floating") else ""))


def verify_placement(placed):
    """Перечитываем окна и повторяем перенос, если окно оказалось не там."""
    time.sleep(1)
    clients = {c["address"]: c for c in get_clients()}
    bad = 0
    for addr, item in placed:
        c = clients.get(addr)
        if not c:
            log(f"  {item['class']}: окно пропало")
            continue
        target, actual = item.get("workspace", 1), workspace_target(c)
        if str(actual) != str(target):
            bad += 1
            log(f"  {item['class']}: сейчас на {actual}, ожидалось {target} — повторяю перенос")
            move_to_workspace(addr, target)
    if not bad:
        log("Проверка: все окна на своих местах.")


def refresh_special_workspaces(placed):
    """Показываем и сразу скрываем special workspace, в который перенесли окна,
    чтобы Hyprland пересчитал в нём раскладку."""
    by_ws = {}
    for addr, item in placed:
        ws = str(item.get("workspace", ""))
        if ws.startswith("special"):
            by_ws.setdefault(ws, []).append(addr)
    if not by_ws:
        return
    shown = {m.get("specialWorkspace", {}).get("name") for m in get_monitors()}
    for name, addrs in by_ws.items():
        if name in shown:
            continue  # уже показан — не трогаем
        short = name.split(":", 1)[1] if ":" in name else name
        log(f"  обновляю {name}")
        if not toggle_special(short):
            continue
        time.sleep(0.4)
        for a in addrs:  # то же, что делает клик по окну в панели
            focus_window(a)
            time.sleep(0.2)
        toggle_special(short)

#!/usr/bin/env python3

def restore_session():
    if not os.path.exists(SESSION_FILE):
        log("Файл сессии не найден.")
        return
    with open(SESSION_FILE, "r") as f:
        session = json.load(f)
    log(f"Восстановление сессии. Окон: {len(session)}")

    time.sleep(3)

    # Окна, которые уже открыты, не трогаем
    clients = get_clients()
    known = {c["address"] for c in clients}
    opened = {}
    for c in clients:
        opened[c.get("class")] = opened.get(c.get("class"), 0) + 1

    # Тайловые окна запускаем в порядке стол -> позиция (x, y), чтобы раскладка
    # получалась как можно ближе к исходной
    def sort_key(i):
        at = i.get("at") or [0, 0]
        return (str(i.get("workspace", 1)), bool(i.get("floating")), at[0], at[1])

    groups = {}
    for item in sorted(session, key=sort_key):
        groups.setdefault(item["class"], []).append(item)

    # Шаг 1: запускаем всё сразу
    pending = {}
    for cls, items in groups.items():
        already = opened.get(cls, 0)
        if cls in SINGLE_INSTANCE:
            if already:
                log(f"{cls} уже открыт")
                continue
            pending[cls] = list(items)
            to_launch = [items[0]]
        else:
            pending[cls] = items[already:]
            to_launch = pending[cls]
            if already:
                log(f"{cls}: уже открыто окон: {already}")

        for item in to_launch:
            cmd = build_cmd(item)
            if not cmd:
                log(f"Нет команды запуска для {cls}")
                pending[cls] = []
                break
            log(f"Запуск {cmd}...")
            subprocess.Popen(cmd, shell=True, start_new_session=True)

    # Шаг 2: ждём появления НОВЫХ окон и расставляем их.
    # Некоторые приложения (например pgAdmin) сначала показывают заставку того же
    # класса, а настоящее окно открывают позже. Если назначенное окно исчезло,
    # его место возвращается в очередь, и мы ждём следующее окно этого класса.
    assigned = {}  # address -> (item, время назначения)
    deadline = time.time() + RESTORE_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        clients = get_clients()
        if not clients:
            continue
        alive = {c.get("address") for c in clients}
        now = time.time()

        for addr in list(assigned):
            if addr not in alive:
                item, _ = assigned.pop(addr)
                pending.setdefault(item["class"], []).insert(0, item)
                log(f"  окно {item['class']} закрылось (заставка?) — жду следующее")

        for c in clients:
            addr, cls = c.get("address"), c.get("class")
            if addr in known or not c.get("mapped", True) or not pending.get(cls):
                continue
            known.add(addr)
            item = pending[cls].pop(0)
            place_window(c, item)
            assigned[addr] = (item, now)

        if not any(pending.values()) and all(now - t >= SETTLE_SECONDS for _, t in assigned.values()):
            break

    placed = [(addr, item) for addr, (item, _) in assigned.items()]
    left = {k: len(v) for k, v in pending.items() if v}
    if left:
        log(f"Не дождался окон: {left}")

    # Шаг 3: проверка и «встряхивание» special workspace
    verify_placement(placed)
    if REFRESH_SPECIAL:
        refresh_special_workspaces(placed)

    log("Восстановление завершено.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "save":
        save_session()
    elif len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore_session()
    else:
        print("Использование: session-manager.py [save|restore]")
