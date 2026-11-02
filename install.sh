#!/usr/bin/env bash
#
# hyprland-session-manager — installer
#
# Checks dependencies, installs the program, wires the Caelestia hook and
# enables the systemd user timer. User configuration is never overwritten,
# so the installer is idempotent — safe to run again (e.g. after a git pull).
#
# Usage:
#   ./install.sh [--force] [--no-caelestia] [--no-systemd] [-h|--help]

set -euo pipefail

# ── Install layout ────────────────────────────────────────────────────────
# Keep in sync with src/session_manager.py, caelestia/session-manager.lua
# and the systemd units.
DATA_DIR="$HOME/.local/share/hyprland-session-manager"    # script + lua loader
CONF_DIR="$HOME/.config/hyprland-session-manager"         # user config
UNITS_DIR="$HOME/.config/systemd/user"                    # systemd user units
CAELESTIA_HOOK="$HOME/.config/caelestia/hypr-user.lua"    # Caelestia user hook
LOADER_LINE='dofile(os.getenv("HOME") .. "/.local/share/hyprland-session-manager/config/session-manager.lua")'
HOOK_MARKER='-- hyprland-session-manager (added by install.sh)'

# ── Output helpers ────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_G=$'\e[32m' C_Y=$'\e[33m' C_R=$'\e[31m' C_B=$'\e[1m' C_0=$'\e[0m'
else
    C_G='' C_Y='' C_R='' C_B='' C_0=''
fi
info() { printf '%s\n' "${C_B}==>${C_0} $*"; }
ok()   { printf '%s\n' "${C_G}==>${C_0} $*"; }
warn() { printf '%s\n' "${C_Y}warning:${C_0} $*" >&2; }
die()  { printf '%s\n' "${C_R}error:${C_0} $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --force         continue even if the python3 version check fails (testing)
  --no-caelestia  do not touch ~/.config/caelestia/hypr-user.lua
  --no-systemd    do not install or enable the systemd user units
  -h, --help      show this help

What it does (in order):
  1. checks dependencies (python3 >= 3.7 required, others are warnings)
  2. installs the program into ~/.local/share/hyprland-session-manager
  3. installs the systemd user units (service + timer)
  4. creates ~/.config/hyprland-session-manager/config.json only if missing
  5. appends the loader line to ~/.config/caelestia/hypr-user.lua only if
     it is not there yet (the rest of the file is never modified)
  6. reloads systemd and enables the autosave timer

User files are never overwritten: running this script twice is safe.
EOF
}

FORCE=0
SKIP_CAELESTIA=0
SKIP_SYSTEMD=0
while [ $# -gt 0 ]; do
    case $1 in
        --force) FORCE=1 ;;
        --no-caelestia) SKIP_CAELESTIA=1 ;;
        --no-systemd) SKIP_SYSTEMD=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[ -n "${HOME:-}" ] || die "HOME is not set"

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

info "hyprland-session-manager installer"
printf '  repository: %s\n' "$REPO_DIR"
printf '  home:       %s\n\n' "$HOME"

# ── 1. Dependencies ───────────────────────────────────────────────────────
info "Checking dependencies"

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    if [ "$FORCE" -eq 0 ]; then
        die "python3 (>= 3.8) is required"
    fi
    warn "python3 not found, continuing (--force)"
elif "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    ok "python3: $("$PYTHON_BIN" --version 2>&1) at $PYTHON_BIN"
elif [ "$FORCE" -eq 0 ]; then
    die "python3 >= 3.8 is required, found: $("$PYTHON_BIN" --version 2>&1)"
else
    warn "python3 is older than 3.8, continuing (--force)"
fi

if command -v hyprctl >/dev/null 2>&1; then
    ok "hyprctl: $(command -v hyprctl)"
else
    warn "hyprctl not found — the manager only works inside a Hyprland session"
fi

if command -v pgrep >/dev/null 2>&1; then
    ok "pgrep: $(command -v pgrep)"
else
    warn "pgrep (procps) not found — terminal working directories will not be restored"
fi

if command -v readlink >/dev/null 2>&1; then
    ok "readlink: $(command -v readlink)"
else
    warn "readlink (coreutils) not found — terminal working directories will not be restored"
fi

if [ "$SKIP_SYSTEMD" -eq 0 ] && ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — the autosave timer cannot be enabled"
fi

# ── 2. Program files (owned by the tool, updated on re-install) ──────────
info "Installing program files to $DATA_DIR"
install -Dm 755 "$REPO_DIR/src/session_manager.py" "$DATA_DIR/src/session_manager.py"
install -Dm 644 "$REPO_DIR/caelestia/session-manager.lua" "$DATA_DIR/config/session-manager.lua"

# The lua hook invokes /usr/bin/python3; point it at the real interpreter
# if python3 lives elsewhere (e.g. /usr/local/bin, Homebrew, Nix).
if [ -n "$PYTHON_BIN" ] && [ "$PYTHON_BIN" != "/usr/bin/python3" ]; then
    sed -i "s|/usr/bin/python3|$PYTHON_BIN|g" "$DATA_DIR/config/session-manager.lua"
    ok "lua hook now uses $PYTHON_BIN"
fi

# The script must at least compile before we call it done.
if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" - "$DATA_DIR/src/session_manager.py" <<'PYEOF'
import sys
compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")
PYEOF
    ok "session_manager.py compiles"
fi

# ── 3. systemd user units (owned by the tool, updated on re-install) ─────
if [ "$SKIP_SYSTEMD" -eq 1 ]; then
    info "--no-systemd given, skipping unit installation"
else
    info "Installing systemd user units to $UNITS_DIR"
    install -Dm 644 "$REPO_DIR/systemd/hyprland-session-save.service" \
        "$UNITS_DIR/hyprland-session-save.service"
    install -Dm 644 "$REPO_DIR/systemd/hyprland-session-save.timer" \
        "$UNITS_DIR/hyprland-session-save.timer"
fi

# ── 4. User configuration (never overwritten) ────────────────────────────
info "Setting up user configuration"
if [ -e "$CONF_DIR/config.json" ]; then
    ok "$CONF_DIR/config.json already exists — leaving it untouched"
    if [ -n "$PYTHON_BIN" ] && ! "$PYTHON_BIN" -m json.tool "$CONF_DIR/config.json" >/dev/null 2>&1; then
        warn "config.json is not valid JSON — the manager will fall back to defaults"
    fi
else
    install -Dm 644 "$REPO_DIR/config.example.json" "$CONF_DIR/config.json"
    ok "created $CONF_DIR/config.json (copy of config.example.json)"
fi

# ── 5. Caelestia hook (added, never overwritten) ─────────────────────────
if [ "$SKIP_CAELESTIA" -eq 1 ]; then
    info "--no-caelestia given, skipping the Caelestia hook"
else
    if [ ! -d "$(dirname -- "$CAELESTIA_HOOK")" ]; then
        warn "Caelestia is not configured yet ($(dirname -- "$CAELESTIA_HOOK") does not exist)"
        warn "the hook will be created now and picked up once Caelestia is installed"
    fi
    if [ -f "$CAELESTIA_HOOK" ] && grep -qF -- "$LOADER_LINE" "$CAELESTIA_HOOK"; then
        ok "hook already present in $CAELESTIA_HOOK"
    else
        mkdir -p -- "$(dirname -- "$CAELESTIA_HOOK")"
        # Make sure the file ends with a newline before appending to it.
        if [ -s "$CAELESTIA_HOOK" ] && [ -n "$(tail -c 1 "$CAELESTIA_HOOK")" ]; then
            printf '\n' >> "$CAELESTIA_HOOK"
        fi
        printf '%s\n%s\n' "$HOOK_MARKER" "$LOADER_LINE" >> "$CAELESTIA_HOOK"
        ok "hook added to $CAELESTIA_HOOK"
    fi
fi

# ── 6. Enable the autosave timer ─────────────────────────────────────────
if [ "$SKIP_SYSTEMD" -eq 1 ]; then
    :
elif ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — when it is available, run:"
    printf '    systemctl --user daemon-reload && systemctl --user enable --now hyprland-session-save.timer\n'
elif ! systemctl --user daemon-reload 2>/dev/null; then
    warn "systemd user manager not reachable (no user session?) — after logging in run:"
    printf '    systemctl --user daemon-reload && systemctl --user enable --now hyprland-session-save.timer\n'
elif systemctl --user enable --now hyprland-session-save.timer 2>/dev/null; then
    ok "autosave timer installed and enabled (first save in 90 s, then every 60 s)"
else
    warn "could not enable hyprland-session-save.timer — check: systemctl --user status hyprland-session-save.timer"
fi

# ── Summary ───────────────────────────────────────────────────────────────
printf '\n'
info "Install complete"
cat <<EOF
Layout:
  program         $DATA_DIR
  user config     $CONF_DIR/config.json
  systemd units   $UNITS_DIR/hyprland-session-save.service + .timer
  caelestia hook  $CAELESTIA_HOOK
  session state   ~/.local/state/hyprland-session-manager/session.json
  log             ~/.cache/hyprland-session-manager/session-manager.log

Next steps:
  * Log into Hyprland — Caelestia loads the hook and restores the session.
  * Check the timer:  systemctl --user list-timers hyprland-session-save.timer
  * Snapshot manually: $DATA_DIR/src/session_manager.py save
  * Tune $CONF_DIR/config.json — see README.md for the full reference.
EOF
