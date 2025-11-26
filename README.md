
# Install daemon

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

# Run cli

```shell
cgwatcher
```
# Build
```shell
debuild -us -uc -b
```
