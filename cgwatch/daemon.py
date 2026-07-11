#!/usr/bin/env python3

import time
import subprocess
from collections import defaultdict

from cgwatch.cgroup import CGroupTree, CGroup
from cgwatch.config import CONFIG_DIR, load_ini_config
import humanize

# Define location: ~/.config/cgwatch/cgwatcherd.ini
CONFIG_FILE = CONFIG_DIR / "cgwatcherd.ini"

DEFAULT_MYNAME = "CGWatcherd"
DEFAULT_ICON="face-worried-symbolic.symbolic.png"


def load_config():
    # Load config from file, or create file with defaults if it doesn't exist.

    def _on_read_error(e: Exception) -> None:
        print(f"Error reading config file, using defaults: {e}")

    result = load_ini_config(
        "cgwatcherd.ini",
        {
            'Thresholds': {
                'warning_percent': '80',
                'critical_percent': '90',
                'reset_hysteresis': '5'
            },
            'Timing': {
                'check_interval_sec': '2',
                'process_list_multiplier': '5',
                'notification_timeout_ms': '15000'
            },
            'Look': {
                'myname': DEFAULT_MYNAME,
                'icon': DEFAULT_ICON
            },
        },
        on_read_error=_on_read_error,
    )

    if result.created:
        print(f"Created default configuration at {CONFIG_FILE}")
    elif result.create_error is not None:
        print(f"Warning: Could not create config file: {result.create_error}")

    return result.config

def send_notification(title, body,timeout,urgency,app_name,icon):
    try:
        # Using notify-send command to send notification
        #  notify-send 'Memory limit' "Firefox is using 80% of memory limit" -e -u critical -a CGWatcherd --icon=face-worried-symbolic.symbolic.png
        subprocess.run(
            ["notify-send", title, body, "-u", urgency, "-a", app_name, f"--icon={icon}", "-t", str(timeout)],
            check=False,
        )
    except Exception as e:
        print(f"Error sending notification: {e}")


def _maybe_notify(cg, mem_percent, last_notified, reset_hysteresis, title, urgency, app_name, icon, timeout):
    """Send a threshold-crossing notification for `cg`, respecting the
    hysteresis window against the last percent notified for it.

    Shared body of the critical/warning notification blocks in `main`'s
    loop -- they differ only in `title`/`urgency`.
    """
    if mem_percent > last_notified[cg.name] + reset_hysteresis:
        last_notified[cg.name] = mem_percent
        send_notification(
            title,
            f"{cg.get_short_name()} is using {mem_percent:.1f}% of its memory limit ({humanize.naturalsize(cg.get_current_memory_usage())} / {humanize.naturalsize(cg.get_memory_limit())})",
            urgency=urgency,
            app_name=app_name,
            icon=icon,
            timeout=timeout,
        )


def main():
    config = load_config()
    PERCENT_WARNING_THRESHOLD = config.getint('Thresholds', 'warning_percent')
    PERCENT_CRITICAL_THRESHOLD = config.getint('Thresholds', 'critical_percent')
    PERCENT_RESET_HYSTERESIS = config.getint('Thresholds', 'reset_hysteresis')

    INTERVAL_PROCESS_VALUES_SEC = max(1, config.getint('Timing', 'check_interval_sec'))
    INTERVAL_PROCESS_LIST_MULTIPLIER = config.getint('Timing', 'process_list_multiplier')
    TIMEOUT_MS = config.getint('Timing', 'notification_timeout_ms')

    MYNAME = config.get('Look', 'myname')
    ICON = config.get('Look', 'icon')

    user_tree = CGroupTree("user.slice")
    while True:
        process_list_counter = INTERVAL_PROCESS_LIST_MULTIPLIER
        memory_limited_cgroups = user_tree.get_memory_limited_cgroups()
        last_notified = defaultdict(lambda: 0)  # cgroup name -> last notified percent
        while True:
            if process_list_counter == 0:
                user_tree.update_tree()
                memory_limited_cgroups: list[CGroup] = user_tree.get_memory_limited_cgroups()
                process_list_counter = INTERVAL_PROCESS_LIST_MULTIPLIER
            for cg in memory_limited_cgroups:
                mem_percent = cg.get_percent_memory_usage()
                if mem_percent >= PERCENT_CRITICAL_THRESHOLD:
                    _maybe_notify(
                        cg, mem_percent, last_notified, PERCENT_RESET_HYSTERESIS,
                        "Memory limit critical", "critical", MYNAME, ICON, TIMEOUT_MS,
                    )
                elif mem_percent >= PERCENT_WARNING_THRESHOLD:
                    _maybe_notify(
                        cg, mem_percent, last_notified, PERCENT_RESET_HYSTERESIS,
                        "Memory limit warning", "normal", MYNAME, ICON, TIMEOUT_MS,
                    )
                else:
                    if mem_percent < PERCENT_WARNING_THRESHOLD - PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = 0  # reset notification state

            time.sleep(INTERVAL_PROCESS_VALUES_SEC)
            process_list_counter -= 1


if __name__ == "__main__":
    main()
