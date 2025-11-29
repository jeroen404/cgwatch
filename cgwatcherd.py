#!/usr/bin/env python3

import time
import os
from collections import defaultdict

from cgwatch.cgroup import CGroupTree, CGroup
import humanize
import configparser
from pathlib import Path

# Define location: ~/.config/cgwatch/cgwatcherd.ini
CONFIG_DIR = Path.home() / ".config" / "cgwatch"
CONFIG_FILE = CONFIG_DIR / "cgwatcherd.ini"

DEFAULT_MYNAME = "CGWatcherd"
DEFAULT_ICON="face-worried-symbolic.symbolic.png"


def load_config():
    # Load config from file, or create file with defaults if it doesn't exist.
    config = configparser.ConfigParser()
    
    # 1. Define Defaults
    config['Thresholds'] = {
        'warning_percent': '80',
        'critical_percent': '90',
        'reset_hysteresis': '5'
    }
    config['Timing'] = {
        'check_interval_sec': '2',
        'process_list_multiplier': '5',
        'notification_timeout_ms': '15000'
    }
    config['Look'] = {
        'myname': DEFAULT_MYNAME,
        'icon': DEFAULT_ICON
    }

    # 2. Check if file exists
    if not CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, 'w') as f:
                config.write(f)
            print(f"Created default configuration at {CONFIG_FILE}")
        except OSError as e:
            print(f"Warning: Could not create config file: {e}")
    else:
        # 3. Read existing file (overrides defaults)
        try:
            config.read(CONFIG_FILE)
        except Exception as e:
            print(f"Error reading config file, using defaults: {e}")

    return config

def send_notification(title, body,timeout,urgency,app_name,icon):
    try:
        # Using notify-send command to send notification
        #  notify-send 'Memory limit' "Firefox is using 80% of memory limit" -e -u critical -a CGWatcherd --icon=face-worried-symbolic.symbolic.png        
        command = f'notify-send "{title}" "{body}" -u {urgency} -a {app_name} --icon={icon} -t {timeout}'
        os.system(command)
    except Exception as e:
        print(f"Error sending notification: {e}")

if __name__ == "__main__":

    config = load_config()
    PERCENT_WARNING_THRESHOLD = config.getint('Thresholds', 'warning_percent')
    PERCENT_CRITICAL_THRESHOLD = config.getint('Thresholds', 'critical_percent')
    PERCENT_RESET_HYSTERESIS = config.getint('Thresholds', 'reset_hysteresis')

    INTERVAL_PROCESS_VALUES_SEC = config.getint('Timing', 'check_interval_sec')
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
                    if mem_percent > last_notified[cg.name] + PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = mem_percent
                        send_notification(
                            "Memory limit critical",
                            f"{cg.get_short_name()} is using {mem_percent:.1f}% of its memory limit ({humanize.naturalsize(cg.get_current_memory_usage())} / {humanize.naturalsize(cg.get_memory_limit())})",
                            urgency="critical",
                            app_name=MYNAME,
                            icon=ICON,
                            timeout=TIMEOUT_MS,
                        )                        
                elif mem_percent >= PERCENT_WARNING_THRESHOLD:
                    if mem_percent > last_notified[cg.name] + PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = mem_percent
                        send_notification(
                            "Memory limit warning",
                            f"{cg.get_short_name()} is using {mem_percent:.1f}% of its memory limit ({humanize.naturalsize(cg.get_current_memory_usage())} / {humanize.naturalsize(cg.get_memory_limit())})",
                            urgency="normal",
                            app_name=MYNAME,
                            icon=ICON,
                            timeout=TIMEOUT_MS,
                        )
                else:
                    if mem_percent < PERCENT_WARNING_THRESHOLD - PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = 0  # reset notification state

            time.sleep(INTERVAL_PROCESS_VALUES_SEC)
            process_list_counter -= 1