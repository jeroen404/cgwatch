
# Tame the desktop

Put all browsers in a pen!

Stop one application from using all memory and slowing down the entire computer/desktop and possibly killing random processes.
Uses Linux CGroups. ( https://en.wikipedia.org/wiki/Cgroups )

# Daemon
## Notification Popup

Gives a warning when apps are using too much memory.

![Notification Popup](doc/popup.png)

## Install daemon

If manually installing

Edit cgwatcherd.service for right path and copy to ~/.config/systemd/user/cgwatcherd.service
```shell
systemctl --user daemon-reload
```

Enable it
```shell
systemctl --user enable cgwatcherd.service
systemctl --user start cgwatcherd.service
```

## Config

~/.config/cgwatch/cgwatcherd.ini

## see status
```shell
systemctl --user status cgwatcherd
journalctl --user -u cgwatcherd -f
```

# CLI
## CLI Interface
![CLI Interface](doc/cli.png)

## Run cli

```shell
cgwatcher
```

## Setting limits

Use the interactive TUI to add and manage memory/CPU limits for desktop services.

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate between services (highlight fades after 3 s) |
| `enter` | Edit limits for the focused service |
| `+` / `-` | Increase or decrease MemoryMax by 10% |
| `a` | Add a limit to a new service |
| `delete` | Remove the cgwatch limit from the focused service |
| `n` | Toggle between service description and short name |
| `q` | Quit |

Limits are stored as drop-in files at
`~/.config/systemd/user/<service>.d/zz-cgwatch.conf` and take effect
immediately on the running instance via `systemctl --user set-property`.
The `zz-` prefix ensures they override any other drop-ins in the same
directory without touching those files.

# Build
```shell
debuild -us -uc -b
```
