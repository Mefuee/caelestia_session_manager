#!/usr/bin/env python3
"""Hyprland Session Manager: сохранение и восстановление оконной сессии.

Использование:
    session_manager.py save      — записать открытые окна в session.json
    session_manager.py restore   — восстановить сессию (обычно вызывает lua-хук)
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# Пути соответствуют XDG Base Directory Specification.
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
STATE_HOME = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))

CONFIG_DIR = os.path.join(CONFIG_HOME, "hyprland-session-manager")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = os.path.join(STATE_HOME, "hyprland-session-manager")
CACHE_DIR = os.path.join(CACHE_HOME, "hyprland-session-manager")
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
LOG_FILE = os.path.join(CACHE_DIR, "session-manager.log")
LEGACY_SESSION_FILE = os.path.expanduser("~/.cache/hyprland-session.json")

# Лог не должен расти бесконечно: при превышении LOG_MAX_BYTES
# оставляем последние LOG_KEEP_LINES строк.
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_LINES = 500


def migrate_legacy_session():
    """Переносит сессию старой версии, не удаляя исходный файл."""
    if os.path.exists(SESSION_FILE) or not os.path.isfile(LEGACY_SESSION_FILE):
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    shutil.copy2(LEGACY_SESSION_FILE, SESSION_FILE)


migrate_legacy_session()

# Значения по умолчанию. Пользовательские значения из config.json
# переопределяют их; отсутствующий конфиг не мешает запуску программы.
DEFAULT_CONFIG = {
    "restore_timeout": 45,
    "settle_seconds": 20,
    "terminal_shell": os.path.basename(os.environ.get("SHELL") or "/bin/sh"),
    "launch_commands": {},
    "ignore_classes": ["", "xdg-desktop-portal-gtk", "xwaylandvideobridge"],
    "single_instance": ["steam", "discord", "spotify", "org.telegram.desktop", "thunderbird"],
    "terminals": {
        "foot": "foot --working-directory={cwd} {shell}",
        "kitty": "kitty -d {cwd} {shell}",
        "konsole": "konsole --workdir {cwd} -e {shell}",
        "Alacritty": "alacritty --working-directory {cwd} -e {shell}",
    },
    "refresh_special": True,
}


def as_int(value, name, fallback):
    """Числовые параметры конфига приводим к int, при ошибке — дефолт."""
    try:
        return int(value)
    except (TypeError, ValueError):
        print(f"Параметр {name}={value!r} не число — использую {fallback}.", file=sys.stderr)
        return fallback


def load_config():
    config = DEFAULT_CONFIG.copy()
    # Вложенные словари копируем отдельно, чтобы не менять DEFAULT_CONFIG.
    for key in ("launch_commands", "terminals"):
        config[key] = DEFAULT_CONFIG[key].copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        if not isinstance(user_config, dict):
            raise ValueError("корневой элемент конфига должен быть JSON-объектом")
        for key, value in user_config.items():
            if key in ("launch_commands", "terminals") and isinstance(value, dict):
                config[key].update(value)
            elif key in ("ignore_classes", "single_instance") and isinstance(value, list):
                config[key] = value
            elif key in DEFAULT_CONFIG and key not in ("launch_commands", "terminals", "ignore_classes", "single_instance"):
                config[key] = value
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Не удалось прочитать конфиг {CONFIG_FILE}: {e}. Использую настройки по умолчанию.", file=sys.stderr)
    # Числовые параметры могли прийти из JSON строками — приводим к норме.
    for key in ("restore_timeout", "settle_seconds"):
        config[key] = as_int(config[key], key, DEFAULT_CONFIG[key])
    return config


CONFIG = load_config()
RESTORE_TIMEOUT = CONFIG["restore_timeout"]
SETTLE_SECONDS = CONFIG["settle_seconds"]
TERMINAL_SHELL = CONFIG["terminal_shell"]
LAUNCH_COMMANDS = CONFIG["launch_commands"]
IGNORE_CLASSES = set(CONFIG["ignore_classes"])
SINGLE_INSTANCE = set(CONFIG["single_instance"])
TERMINALS = CONFIG["terminals"]
REFRESH_SPECIAL = CONFIG["refresh_special"]


def log(msg):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    print(msg)


def trim_log():
    """Обрезаем разросшийся лог, оставляя последние строки."""
    try:
        if not os.path.isfile(LOG_FILE) or os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-LOG_KEEP_LINES:])
        os.replace(tmp, LOG_FILE)
    except OSError:
        pass  # обслуживание лога не должно ломать основную работу


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


def desktop_file_dirs():
    """Каталоги с .desktop-файлами, от более приоритетных к менее.
    Учитываем XDG_DATA_HOME / XDG_DATA_DIRS и экспорты Flatpak."""
    data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    dirs = [
        os.path.join(data_home, "applications"),
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
    ]
    for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(os.path.join(d, "applications"))
    unique, seen = [], set()
    for d in dirs:
        d = os.path.normpath(d)
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def build_desktop_index():
    """class (в нижнем регистре) -> команда из .desktop-файла."""
    index = {}
    for d in desktop_file_dirs():  # более ранние каталоги имеют приоритет
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

    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
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
            # Одна команда → одно окно: ждём ровно одно окно этого класса.
            to_launch = [items[0]]
        else:
            to_launch = items[already:]
            if already:
                log(f"{cls}: уже открыто окон: {already}")

        # Запускаем по одному; неудавшиеся элементы выкидываем точечно,
        # не сбрасывая уже запущенные окна и не прерывая цикл.
        launched = []
        for item in to_launch:
            cmd = build_cmd(item)
            if not cmd:
                log(f"Нет команды запуска для {cls}")
                continue
            log(f"Запуск {cmd}...")
            # Вместо Popen — просим Hyprland запустить приложение.
            # Так оно получит ровно то же окружение, что и при нажатии хоткея.
            safe_cmd = json.dumps(cmd)
            dispatch(f'hl.dsp.exec_cmd({{ command = {safe_cmd} }})')
            launched.append(item)

        # В очередь ожидания идут только те, что реально запущены.
        if launched:
            pending[cls] = launched
            
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
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in ("-h", "--help"):
        print(__doc__.strip())
    elif arg == "save":
        trim_log()
        save_session()
    elif arg == "restore":
        trim_log()
        restore_session()
    else:
        print("Использование: session_manager.py [save|restore]")
        sys.exit(2)
