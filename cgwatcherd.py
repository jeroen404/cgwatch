#!/usr/bin/env python3

import time
import os
from collections import defaultdict

from cgwatch.cgroup import CGroupTree, CGroup
import humanize

MYNAME = "CGWatcherd"
ICON="face-worried-symbolic.symbolic.png"
PERCENT_WARNING_THRESHOLD = 80  # percent
PERCENT_CRITICAL_THRESHOLD = 90  # percent
PERCENT_RESET_HYSTERESIS = 5  # percent

INTERVAL_PROCESS_VALUES_SEC = 2
INTERVAL_PROCESS_LIST_MULTIPLIER = 5

DEFAULT_TIMEOUT_MS = 15000

def send_notification(title, body,timeout=DEFAULT_TIMEOUT_MS,urgency="critical",app_name=MYNAME,icon=ICON):
    try:
        # Using notify-send command to send notification
        #  notify-send 'Memory limit' "Firefox is using 80% of memory limit" -e -u critical -a CGWatcherd --icon=face-worried-symbolic.symbolic.png        
        command = f'notify-send "{title}" "{body}" -u {urgency} -a {app_name} --icon={icon} -t {timeout}'
        os.system(command)
    except Exception as e:
        print(f"Error sending notification: {e}")

if __name__ == "__main__":
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
                            urgency="critical"
                        )                        
                elif mem_percent >= PERCENT_WARNING_THRESHOLD:
                    if mem_percent > last_notified[cg.name] + PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = mem_percent
                        send_notification(
                            "Memory limit warning",
                            f"{cg.get_short_name()} is using {mem_percent:.1f}% of its memory limit ({humanize.naturalsize(cg.get_current_memory_usage())} / {humanize.naturalsize(cg.get_memory_limit())})",
                            urgency="normal"
                        )
                else:
                    if mem_percent < PERCENT_WARNING_THRESHOLD - PERCENT_RESET_HYSTERESIS:
                        last_notified[cg.name] = 0  # reset notification state

            time.sleep(INTERVAL_PROCESS_VALUES_SEC)
            process_list_counter -= 1