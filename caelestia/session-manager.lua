-- Hyprland Session Manager: Caelestia integration
-- Installed at:
--   ~/.local/share/hyprland-session-manager/config/session-manager.lua
--
-- install.sh appends this line to ~/.config/caelestia/hypr-user.lua
-- (created if missing; removed again by uninstall.sh):
--   dofile(os.getenv("HOME") .. "/.local/share/hyprland-session-manager/config/session-manager.lua")

local home = os.getenv("HOME")
local script = home .. "/.local/share/hyprland-session-manager/src/session_manager.py"

-- Восстанавливаем сессию на старте Hyprland. Перед этим импортируем
-- переменные окружения в systemd --user: без HYPRLAND_INSTANCE_SIGNATURE
-- таймер автосохранения не сможет обращаться к hyprctl из своих сервисов.
-- Если python3 установлен по нестандартному пути, install.sh поправит его ниже.
hl.on("hyprland.start", function()
    local command = 'systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_RUNTIME_DIR XDG_CURRENT_DESKTOP XDG_SESSION_TYPE && /usr/bin/python3 "' .. script .. '" restore'
    hl.exec_cmd(command)
end)
