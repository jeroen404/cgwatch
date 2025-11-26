
*** Install daemon

systemctl --user daemon-reload
systemctl --user enable cgwatcherd.service
systemctl --user start cgwatcherd.service

** see status

systemctl --user status cgwatcherd
journalctl --user -u cgwatcherd -f

*** Run cli

cgwatcher

*** Build

debuild -us -uc -b