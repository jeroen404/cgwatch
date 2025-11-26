
# Tame the desktop

put all browsers in a pen

## set cgroup limits
```shell
cp -r examples/* ~/.config/systemd/user/
systemctl --user daemon-reload
```

# Daemon
## Notification Popup
![Notification Popup](doc/popup.png)

## Install daemon


if manual
```shell
copy to ~/.config/systemd/user/cgwatcherd.service
systemctl --user daemon-reload
```
```shell
systemctl --user enable cgwatcherd.service
systemctl --user start cgwatcherd.service
```

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
# Build
```shell
debuild -us -uc -b

# test

systemctl --user set-property --runtime app-slack@f22b6db44f2a4ade8b990458fac649e6.service MemoryMax=1100M
```
