# hyprland-session-manager

Save and restore your Hyprland window session — workspaces, floating
geometry, terminal working directories — with first-class
[Caelestia](https://github.com/caelestia-dots/caelestia) integration.

The manager periodically snapshots your open windows (class, launch command,
workspace, floating state and geometry, terminal `cwd`) into a JSON file and
replays that snapshot on the next Hyprland start: apps are relaunched, moved
to their workspaces, floating windows get their size and position back, and
special workspaces (Caelestia dashboards) are refreshed so their layout
settles correctly.

## Features

- **Automatic restore on login** via a hook in Caelestia's `hypr-user.lua`
- **Automatic saves** — first snapshot 90 s after login, then every 60 s,
  driven by a systemd user timer
- **Launch command auto-detection**: config overrides → `.desktop` files
  (including Flatpak exports and `$XDG_DATA_DIRS`) → live `/proc/<pid>/cmdline`
- **Terminals reopen where they were** — `foot`, `kitty`, `konsole` and
  `Alacritty` out of the box, extensible via config
- **Single-instance aware** — `steam`, `discord`, … are launched once even if
  several of their windows were open
- Handles splash screens (re-assigns the real window when the splash
  disappears) and re-checks placement after everything settles
- XDG Base Directory layout, atomic session writes, automatic log rotation
- Idempotent, non-destructive installer with a matching uninstaller

## How it works

```
 Hyprland starts
 └─ Caelestia loads ~/.config/caelestia/hypr-user.lua
     └─ dofile(...) loads the manager's lua hook
         ├─ systemctl --user import-environment WAYLAND_DISPLAY
         │              HYPRLAND_INSTANCE_SIGNATURE XDG_RUNTIME_DIR …
         └─ python3 session_manager.py restore
             ├─ relaunch every saved app (terminals with their cwd)
             ├─ move each new window to its saved workspace
             ├─ restore floating size / position
             ├─ verify placement, retry stragglers
             └─ "shake" special workspaces so Hyprland recalculates layout

 every 60 s (systemd user timer)
 └─ python3 session_manager.py save
     └─ ~/.local/state/hyprland-session-manager/session.json
```

The `import-environment` step is what lets the *systemd user manager* (and
therefore the timer) talk to Hyprland: the save service is guarded by
`ConditionEnvironment=HYPRLAND_INSTANCE_SIGNATURE` and silently does nothing
outside a Hyprland session.

## Requirements

| Dependency | Needed for | Notes |
| --- | --- | --- |
| Hyprland 0.55+ (Lua config era) | everything | the manager drives Hyprland with expression-style `hyprctl dispatch` calls (`hl.dsp.*`) |
| python3 ≥ 3.8 | everything | standard library only |
| systemd | autosave timer | user units; the installer warns if the user manager is unreachable |
| procps (`pgrep`) | terminal cwd | optional, recommended |
| Caelestia | auto-restore hook | optional — see [Without Caelestia](#without-caelestia) |

## Quick start

```sh
git clone https://github.com/YOUR_USERNAME/hyprland-session-manager.git
cd hyprland-session-manager
./install.sh
```

Then log into Hyprland — that's it. The first autosave happens 90 s after
the timer starts, afterwards every 60 s.

## What the installer does

`install.sh` is **idempotent**: run it as many times as you like (e.g. after
a `git pull`) — user files are never overwritten.

Step by step:

1. **Checks dependencies.** `python3` ≥ 3.7 is a hard requirement; missing
   `hyprctl` / `pgrep` / `readlink` / `systemctl` produce warnings only, so
   the installer also works inside dotfiles bootstrap scripts. `--force`
   skips the hard check.

2. **Installs the program** (tool-owned files, updated on re-install):

   | Repository file | Installed to | Mode |
   | --- | --- | --- |
   | `src/session_manager.py` | `~/.local/share/hyprland-session-manager/src/` | 755 |
   | `caelestia/session-manager.lua` | `~/.local/share/hyprland-session-manager/config/` | 644 |

   If `python3` does not live at `/usr/bin/python3`, the installed lua hook
   is patched to point at the real interpreter.

3. **Installs the systemd user units** into `~/.config/systemd/user/`:
   - `hyprland-session-save.service` — `Type=oneshot`, runs `… save`,
     guarded by `ConditionEnvironment=HYPRLAND_INSTANCE_SIGNATURE`
   - `hyprland-session-save.timer` — first run 90 s after the timer
     activates, then every 60 s (`AccuracySec=5s`)

4. **Creates the user config — only if it does not exist** —
   `~/.config/hyprland-session-manager/config.json` from
   `config.example.json`. An existing config is checked for JSON validity
   and otherwise left untouched.

5. **Adds the Caelestia hook — only if it is not there yet.** Two lines are
   appended to `~/.config/caelestia/hypr-user.lua` (the file is created if
   you don't have one; Caelestia loads it at the end of its startup
   sequence):

   ```lua
   -- hyprland-session-manager (added by install.sh)
   dofile(os.getenv("HOME") .. "/.local/share/hyprland-session-manager/config/session-manager.lua")
   ```

   The rest of `hypr-user.lua` is never modified, and `uninstall.sh` removes
   exactly these two lines.

6. **Enables the timer** (`systemctl --user daemon-reload && systemctl
   --user enable --now hyprland-session-save.timer`). If the user manager
   is unreachable (installing from a TTY), the installer prints the command
   to run after your next login.

Options: `--force`, `--no-caelestia`, `--no-systemd`, `--help`.

### Runtime file locations (XDG)

| Path | Purpose |
| --- | --- |
| `~/.config/hyprland-session-manager/config.json` | user configuration |
| `~/.local/state/hyprland-session-manager/session.json` | the saved session |
| `~/.cache/hyprland-session-manager/session-manager.log` | log, rotated at 256 KiB |
| `~/.cache/hyprland-session.json` | legacy pre-XDG session — migrated automatically on first run |

## Without Caelestia

The Python core only talks to `hyprctl`, so it works on any Hyprland 0.55+
setup:

```sh
./install.sh --no-caelestia
```

Then wire the restore into your own autostart — run, once Hyprland is up,
the same command the Caelestia hook runs:

```sh
systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_RUNTIME_DIR XDG_CURRENT_DESKTOP XDG_SESSION_TYPE
python3 ~/.local/share/hyprland-session-manager/src/session_manager.py restore
```

The `import-environment` part matters: it is what later lets the systemd
timer reach Hyprland.

## Configuration

`~/.config/hyprland-session-manager/config.json` — missing keys fall back
to defaults, and a missing file is fine too.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `restore_timeout` | int | `45` | seconds to wait for restored windows to appear |
| `settle_seconds` | int | `20` | quiet period after the last window before the final placement check |
| `terminal_shell` | string | basename of `$SHELL` | shell launched inside restored terminals |
| `launch_commands` | object | `{}` | per-class launch commands, highest priority |
| `ignore_classes` | list | `["", "xdg-desktop-portal-gtk", "xwaylandvideobridge"]` | window classes never saved — **replaces** the default list |
| `single_instance` | list | `["steam", "discord", "spotify", "org.telegram.desktop", "thunderbird"]` | classes launched once even if several windows were saved — **replaces** the default list |
| `terminals` | object | templates for `foot`, `kitty`, `konsole`, `Alacritty` | command templates; `{cwd}` and `{shell}` are substituted |
| `refresh_special` | bool | `true` | toggle special workspaces after restore to fix their layout |

Merge semantics:

- `launch_commands` and `terminals` are **merged** over the defaults
  (add or override individual keys)
- `ignore_classes` and `single_instance` **replace** the default list
  entirely — copy the defaults in if you only want to add entries

Example — pin launch commands and teach a new terminal:

```json
{
  "launch_commands": {
    "code-oss": "code-oss",
    "org.remmina.Remmina": "remmina"
  },
  "terminals": {
    "ghostty": "ghostty --working-directory={cwd} {shell}"
  },
  "ignore_classes": [
    "",
    "xdg-desktop-portal-gtk",
    "xwaylandvideobridge",
    "my-bar"
  ]
}
```

At save time the launch command for a window is resolved in this order:

1. `launch_commands[class]`
2. the `.desktop` index — `StartupWMClass` or the file name, lowercased
   (`$XDG_DATA_HOME/applications`, Flatpak exports, `$XDG_DATA_DIRS`)
3. the live process command line from `/proc/<pid>/cmdline`

### Session file format

`~/.local/state/hyprland-session-manager/session.json` is a plain JSON
array — handy for inspecting or hand-tweaking a session:

```json
[
  {
    "class": "kitty",
    "cmd": null,
    "workspace": 2,
    "floating": false,
    "at": [0, 0],
    "size": [800, 500],
    "cwd": "/home/user/projects"
  }
]
```

`workspace` is a number for regular workspaces or `"special:name"` for
special ones. For terminals `cmd` is unused — the `terminals` template
rebuilds the command.

## Manual usage

```sh
~/.local/share/hyprland-session-manager/src/session_manager.py save      # snapshot now
~/.local/share/hyprland-session-manager/src/session_manager.py restore   # replay
~/.local/share/hyprland-session-manager/src/session_manager.py --help

systemctl --user list-timers hyprland-session-save.timer                 # autosave status
journalctl --user -u hyprland-session-save.service                       # service output
```

## Troubleshooting

Everything the manager does is logged to
`~/.cache/hyprland-session-manager/session-manager.log`.

| Symptom | What to check |
| --- | --- |
| An app is not restored | the log for `Не удалось определить команду запуска` → add its class to `launch_commands` |
| Windows land on wrong workspaces | raise `settle_seconds`; slow-loading apps need a bigger `restore_timeout` |
| Special workspace layout looks wrong | keep `refresh_special` enabled |
| Nothing is saved | `systemctl --user list-timers hyprland-session-save.timer`; is `HYPRLAND_INSTANCE_SIGNATURE` imported (did you log in through the Caelestia hook)? |
| Terminal reopens in `$HOME` | the shell child of the terminal could not be probed — check that `pgrep` (procps) is installed |

## Uninstallation

```sh
./uninstall.sh              # asks before touching config / session data
./uninstall.sh --purge      # remove everything, no questions
./uninstall.sh --keep-data  # remove only program files, units and the hook
```

Always removed: the timer and service units (the timer is stopped and
disabled first), `~/.local/share/hyprland-session-manager`, and the two hook
lines in `hypr-user.lua` (the file itself is deleted only if the installer
created it and nothing else is left in it).

Kept unless you agree (interactive) or pass `--purge`:
`~/.config/hyprland-session-manager` (config),
`~/.local/state/hyprland-session-manager` (session) and
`~/.cache/hyprland-session-manager` (logs).

## Limitations

- Tiled window layout is approximated by launch order and position — exact
  tile geometry is not replayed
- Wayland apps map their windows asynchronously; splash screens are handled,
  but exotic launchers may need `launch_commands` entries
- The session is snapshotted every 60 s, so the last minute before logout
  may be lost
- Flatpak apps whose window class differs from their `.desktop` id may need
  manual `launch_commands` entries

## Roadmap

- [ ] save-on-exit hook for a perfectly fresh snapshot
- [ ] monitor/geometry awareness for multi-head restores
- [ ] config schema versioning

## Repository layout

```
.
├── install.sh / uninstall.sh      deployment scripts (bash)
├── config.example.json            user config template
├── src/session_manager.py         the manager (Python, stdlib only)
├── caelestia/session-manager.lua  Caelestia hook, loaded via hypr-user.lua
├── systemd/                       user service + timer
└── tests/smoke.sh                 install/uninstall smoke test (used by CI)
```

## Development

```sh
tests/smoke.sh    # install → uninstall → reinstall over user files → uninstall,
                  # all inside a throwaway $HOME
```

CI (GitHub Actions) runs shellcheck, a Python syntax check, JSON validation
and the smoke test on every push and pull request. The Python core is
deliberately dependency-free — please keep it that way.

## License

[MIT](LICENSE)
