#!/usr/bin/env bash
#
# Smoke test: install into a throwaway $HOME, verify the layout, uninstall,
# then install again over user-modified files to prove idempotency, and
# uninstall once more. Nothing outside a temporary directory is touched.

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_HOME="$(mktemp -d)"
trap 'rm -rf -- "$TEST_HOME"' EXIT

SCRIPT="$TEST_HOME/.local/share/hyprland-session-manager/src/session_manager.py"
LUA="$TEST_HOME/.local/share/hyprland-session-manager/config/session-manager.lua"
CONFIG="$TEST_HOME/.config/hyprland-session-manager/config.json"
SERVICE_UNIT="$TEST_HOME/.config/systemd/user/hyprland-session-save.service"
TIMER_UNIT="$TEST_HOME/.config/systemd/user/hyprland-session-save.timer"
HOOK="$TEST_HOME/.config/caelestia/hypr-user.lua"
LOADER='dofile(os.getenv("HOME")'

step() { printf '\n=== %s\n' "$*"; }

step "fresh install"
HOME="$TEST_HOME" "$REPO_DIR/install.sh"
[ -x "$SCRIPT" ]
[ -f "$LUA" ]
[ -f "$CONFIG" ]
[ -f "$SERVICE_UNIT" ]
[ -f "$TIMER_UNIT" ]
[ -f "$HOOK" ]
grep -qF "$LOADER" "$HOOK"
echo "layout ok"

step "uninstall (--purge) after a clean install"
HOME="$TEST_HOME" "$REPO_DIR/uninstall.sh" --purge
[ ! -e "$TEST_HOME/.local/share/hyprland-session-manager" ]
[ ! -e "$TEST_HOME/.config/hyprland-session-manager" ]
[ ! -e "$SERVICE_UNIT" ]
[ ! -e "$TIMER_UNIT" ]
[ ! -e "$HOOK" ]   # created by the installer -> removed with it
echo "everything removed"

step "reinstall over user-modified files"
HOME="$TEST_HOME" "$REPO_DIR/install.sh"
printf '{\n  "restore_timeout": 99\n}\n' > "$CONFIG"
printf -- '-- user code\nprint("mine")\n' > "$HOOK"
HOME="$TEST_HOME" "$REPO_DIR/install.sh"
grep -q '"restore_timeout": 99' "$CONFIG"     # user config preserved
grep -qF 'print("mine")' "$HOOK"              # user lua preserved
[ "$(grep -cF "$LOADER" "$HOOK")" -eq 1 ]     # hook added exactly once
echo "user files intact, no duplicates"

step "final uninstall (--purge) with user lua kept"
HOME="$TEST_HOME" "$REPO_DIR/uninstall.sh" --purge
[ -f "$HOOK" ]                                # user code survives
grep -qF 'print("mine")' "$HOOK"
if grep -qF "$LOADER" "$HOOK"; then           # our lines must be gone
    echo "FAIL: loader line still present in $HOOK" >&2
    exit 1
fi
[ ! -e "$TEST_HOME/.local/share/hyprland-session-manager" ]
[ ! -e "$TEST_HOME/.config/hyprland-session-manager" ]
echo "clean removal, user lua preserved"

printf '\nsmoke test passed\n'
