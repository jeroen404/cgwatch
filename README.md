
# Tame the desktop

Put all browsers in a pen!

Stop one application from using all memory and slowing down the entire computer/desktop and possibly killing random processes.
Uses Linux CGroups. ( https://en.wikipedia.org/wiki/Cgroups )

## Compatibility / Requirements

This project only works when applications are launched as user systemd
services/scopes (so they can be discovered and controlled with
`systemctl --user`).

| Environment | Support Level | Note |
|---|---|---|
| KDE Plasma | Native | Works out of the box. Apps are launched as individual units in modern Plasma systemd startup. |
| GNOME | Partial | Works for Flatpaks or apps launched via `systemd-run --user`. Many standard app launches are not tracked as discrete units. |
| Others (XFCE, i3, etc.) | Manual | Usually requires launching apps via `systemd-run --user` for cgwatch to see them as separate units/scopes. |

The TUI also needs a recent `textual` version. It works with Debian 13's
package, but not with the older Debian 12 package.

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

This file is auto-created with defaults on first run. Example:

```ini
[Thresholds]
warning_percent = 80
critical_percent = 90
reset_hysteresis = 5

[Timing]
check_interval_sec = 2
process_list_multiplier = 5
notification_timeout_ms = 15000

[Look]
myname = CGWatcherd
icon = face-worried-symbolic.symbolic.png
```

`warning_percent`: send a normal warning notification when a limited app reaches this percentage of its `MemoryMax`.

`critical_percent`: send a critical notification when a limited app reaches this percentage of its `MemoryMax`.

`reset_hysteresis`: suppress repeat notifications until usage rises by this many additional percentage points; notification state is also reset once usage drops below `warning_percent - reset_hysteresis`.

`check_interval_sec`: how often the daemon re-checks memory usage for already tracked limited apps.

`process_list_multiplier`: how many check intervals to wait before rescanning the cgroup tree for the current list of limited apps. The effective rescan period is `check_interval_sec * process_list_multiplier`.

`notification_timeout_ms`: how long the popup notification stays visible, in milliseconds.

`myname`: application name shown in the desktop notification.

`icon`: icon name or icon path passed to `notify-send` for the notification.



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

The TUI config file is `~/.config/cgwatch/cgwatch.ini` and is created on first
run if it does not already exist. Optional settings:

```ini
[cgwatcher]
show_descriptions = false
```

`show_descriptions = false` starts the list with short unit names. If you
toggle descriptions with `n`, cgwatch saves the current setting on exit and
uses it as the next startup default.

# Build
```shell
debuild -us -uc -b
```
