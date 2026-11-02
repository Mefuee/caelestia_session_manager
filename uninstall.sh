#!/usr/bin/env bash
#
# hyprland-session-manager — uninstaller
#
# Stops and removes the systemd units, deletes the program files and takes
# the hook lines back out of the Caelestia config. The user configuration
# and session data are only removed with your consent (or with --purge).
#
# Usage:
#   ./uninstall.sh [--purge] [--keep-data] [-h|--help]

set -euo pipefail

# ── Install layout (must match install.sh) ────────────────────────────────
DATA_DIR="$HOME/.local/share/hyprland-session-manager"
CONF_DIR="$HOME/.config/hyprland-session-manager"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/hyprland-session-manager"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/hyprland-session-manager"
UNITS_DIR="$HOME/.config/systemd/user"
CAELESTIA_HOOK="$HOME/.config/caelestia/hypr-user.lua"
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
Usage: ./uninstall.sh [options]

Options:
  --purge      remove everything: program, units, hook, config, session data
  --keep-data  remove only the program files, units and the hook line
  -h, --help   show this help

Always removed:
  * systemd units (the timer is stopped and disabled first)
  * ~/.local/share/hyprland-session-manager (program files)
  * the two hook lines in ~/.config/caelestia/hypr-user.lua
    (the file itself is deleted only if the installer created it and
    nothing else is left in it)

Kept unless you agree or pass --purge:
  * ~/.config/hyprland-session-manager      (user config)
  * ~/.local/state/hyprland-session-manager (saved session)
  * ~/.cache/hyprland-session-manager       (logs)
EOF
}

PURGE=0
KEEP_DATA=0
while [ $# -gt 0 ]; do
    case $1 in
        --purge) PURGE=1 ;;
        --keep-data) KEEP_DATA=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[ -n "${HOME:-}" ] || die "HOME is not set"

ask_yes() {
    # $1 — question; returns 0 when the answer is yes
    local reply
    printf '%s [y/N] ' "$1"
    read -r reply || return 1
    case $reply in
        [Yy]|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

# ── 1. systemd units ──────────────────────────────────────────────────────
info "Stopping and removing systemd units"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now hyprland-session-save.timer 2>/dev/null || true
fi
rm -f -- "$UNITS_DIR/hyprland-session-save.service" "$UNITS_DIR/hyprland-session-save.timer"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user reset-failed 2>/dev/null || true
fi
ok "units removed"

# ── 2. Program files ──────────────────────────────────────────────────────
rm -rf -- "$DATA_DIR"
ok "removed $DATA_DIR"

# ── 3. Caelestia hook ─────────────────────────────────────────────────────
if [ -f "$CAELESTIA_HOOK" ] && grep -qF -- "$LOADER_LINE" "$CAELESTIA_HOOK"; then
    tmp="$(mktemp)"
    # Remove exactly the two lines install.sh added, nothing else.
    grep -vF -e "$HOOK_MARKER" -e "$LOADER_LINE" -- "$CAELESTIA_HOOK" > "$tmp" || true
    if grep -q '[^[:space:]]' "$tmp"; then
        # cat > keeps the original permissions, unlike mv from mktemp
        cat -- "$tmp" > "$CAELESTIA_HOOK"
        ok "hook lines removed from $CAELESTIA_HOOK"
    else
        # The file consisted only of our lines — the installer created it.
        rm -f -- "$CAELESTIA_HOOK"
        ok "removed $CAELESTIA_HOOK (it was created by the installer)"
    fi
    rm -f -- "$tmp"
    rmdir -- "$(dirname -- "$CAELESTIA_HOOK")" 2>/dev/null || true
fi

# ── 4. User config and session data ──────────────────────────────────────
remove_config=0
remove_data=0
if [ "$PURGE" -eq 1 ]; then
    remove_config=1
    remove_data=1
elif [ "$KEEP_DATA" -eq 1 ]; then
    :
elif [ -t 0 ]; then
    if [ -e "$CONF_DIR" ] && ask_yes "Remove the user configuration ($CONF_DIR)?"; then
        remove_config=1
    fi
    if { [ -e "$STATE_DIR" ] || [ -e "$CACHE_DIR" ]; } && \
       ask_yes "Remove session data and logs ($STATE_DIR, $CACHE_DIR)?"; then
        remove_data=1
    fi
else
    warn "not run from a terminal — keeping config and data (use --purge to remove them)"
fi

if [ "$remove_config" -eq 1 ]; then
    rm -rf -- "$CONF_DIR"
    ok "removed $CONF_DIR"
elif [ -e "$CONF_DIR" ]; then
    printf 'kept: %s\n' "$CONF_DIR"
fi

if [ "$remove_data" -eq 1 ]; then
    rm -rf -- "$STATE_DIR" "$CACHE_DIR"
    ok "removed $STATE_DIR and $CACHE_DIR"
elif [ -e "$STATE_DIR" ] || [ -e "$CACHE_DIR" ]; then
    printf 'kept: %s (session), %s (logs)\n' "$STATE_DIR" "$CACHE_DIR"
fi

printf '\n'
info "Uninstall complete"
