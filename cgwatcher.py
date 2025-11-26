#!/usr/bin/env python3


import cgwatch
from cgwatch.cgroup import CGroupTree, CGroup
import humanize
import os

from textual.app import App, ComposeResult
from textual.containers import HorizontalGroup, VerticalScroll
from textual.widgets import Button, Label, Footer, Header
from textual.color import Color
from textual.reactive import reactive



class MyColors:

    @staticmethod
    def percent_to_rgb(percent: float) -> Color:
        percent = max(0.0, min(100.0, percent))        
        if percent < 50:
            # Green to Yellow (Red increases to 255, Green stays 255)
            red = int(255 * (percent / 50))
            green = 255
        else:
            # Yellow to Red (Red stays 255, Green decreases to 0)
            red = 255
            green = int(255 * ((100 - percent) / 50))            
        return Color(red, green, 0)
    @staticmethod
    def percent_of_percent_to_rgb(percent: float, of_percent: float) -> Color:
        relative_percent = (percent / of_percent) * 100
        return MyColors.percent_to_rgb(relative_percent)

class MemoryUsageHumanized(Label):
    humanized_value = reactive(0)
    def __init__(self, mem_display_type: str, cgroup: CGroup, **kwargs):
        super().__init__(0, **kwargs)
        self.mem_display_type = mem_display_type
        self.cgroup = cgroup
        self.refresh_data()
                
    def watch_humanized_value(self, old_value, new_value):
        self.update(humanize.naturalsize(new_value))
    def refresh_data(self):
        if self.mem_display_type == "usage":
            new_value = int(self.cgroup.get_current_memory_usage())
        elif self.mem_display_type == "limit":
            limit = self.cgroup.get_memory_limit()
            new_value = int(limit) if limit != "max" else 0
        else:
            new_value = 0
        self.humanized_value = new_value
class MemoryPercent(Label):
    percent = reactive(0.0)
    def __init__(self, cgroup: CGroup, **kwargs):
        percent = cgroup.get_percent_memory_usage()
        color = MyColors.percent_to_rgb(percent)
        super().__init__(f"{percent:.2f}%", **kwargs)
        self.styles.color = color
        self.cgroup = cgroup
        self.percent = percent
    def watch_percent(self, old_value, new_value):
        self.update(f"{new_value:.2f}%")
        self.styles.color = MyColors.percent_to_rgb(new_value)
    def refresh_data(self):
        new_percent = self.cgroup.get_percent_memory_usage()
        self.percent = new_percent
class CGroupCPUQuota(Label):
    quota = reactive(0.0)
    def __init__(self, cgroup: CGroup, **kwargs):
        quota = cgroup.get_cpu_quotum()
        display_quota = quota if quota != "max" else "max"
        self.cgroup = cgroup
        super().__init__(f"{display_quota}%", **kwargs)
        self.quota = quota
    def watch_quota(self, old_value, new_value):
        display_quota = new_value if new_value != "max" else "max"
        self.update(f"{display_quota}%")
    def refresh_data(self):
        new_quota = self.cgroup.get_cpu_quotum()
        self.quota = new_quota
class CGroupCPUPercentUsage(Label):
    cpu_percent = reactive(0.0)
    def __init__(self, cgroup: CGroup, **kwargs):
        percent = cgroup.get_cpu_last_usage_percent()
        self.cgroup = cgroup
        super().__init__(f"{percent:.2f}%", **kwargs)
        self.cpu_percent = percent
        self.styles.color = "green"

    def watch_cpu_percent(self, old_value, new_value):
        
        if self.cgroup.has_cpu_quota():
            quota = self.cgroup.get_cpu_quotum()
            # self.log(f"quota type: {type(quota)}, value: {quota}")
            new_color = MyColors.percent_of_percent_to_rgb(new_value, quota)
            # self.log(f"new color: {new_color}")
            # self.log(f"CPU Update: usage={new_value:.2f}%, quota={quota}%, relative={(new_value/quota)*100:.2f}%")
            self.styles.color = new_color
            new_color_hex = new_color.hex
            # self.log(f"new color hex: {new_color_hex}")
            #self.update(f"[{new_color}.hex]{new_value:.2f}%[/]")
            self.update(f"[{new_color_hex}]{new_value:.2f}%[/]")
        else:
            self.styles.color = "yellow"
        
        #self.update(f"{new_value:.2f}%")
        
    def refresh_data(self):
        new_percent = self.cgroup.get_cpu_last_usage_percent()
        self.cpu_percent = new_percent
class CGroupThrottled(Label):
    throttled = reactive(0)
    def __init__(self, cgroup: CGroup, **kwargs):
        throttled = cgroup.throttled_since_last()
        self.cgroup = cgroup
        super().__init__(f"{throttled}", **kwargs)
        self.throttled = throttled
    def watch_throttled(self, old_value, new_value):
        warning = "⚠️" if new_value > 0 else "✅"
        self.update(f"{warning}")
    def refresh_data(self):
        new_throttled = self.cgroup.throttled_since_last()
        self.throttled = new_throttled

class CGroupName(Label):
    def __init__(self, cgroup: CGroup, **kwargs):
        name = cgroup.get_short_name()
        super().__init__(name, **kwargs)
        name = name.replace("\\x2d", "-")
        super().__init__(name, **kwargs)

class CGroupLine(HorizontalGroup):
    def __init__(self, cgroup: CGroup):
        super().__init__()
        self.cgroup = cgroup
        self.styles.border = ("round", MyColors.percent_to_rgb(cgroup.get_percent_memory_usage()))
    
    def compose(self) -> ComposeResult:
        yield CGroupName(self.cgroup, id="cgroup-name")
        yield MemoryUsageHumanized("usage", cgroup=self.cgroup, id="mem-usage")
        yield MemoryUsageHumanized("limit", cgroup=self.cgroup, id="mem-limit")
        yield MemoryPercent(self.cgroup, id="mem-percent")
        yield CGroupCPUPercentUsage(self.cgroup, id="cpu-percent")
        yield CGroupCPUQuota(self.cgroup, id="cpu-quota")
        yield CGroupThrottled(self.cgroup, id="cpu-throttled")
        yield Label("", id="line-spacer")
    
    def refresh_data(self) -> None:
        """Refresh all data displayed in this line."""
        for widget in self.children:
            if isinstance(widget, (MemoryUsageHumanized, MemoryPercent, CGroupCPUPercentUsage, CGroupCPUQuota, CGroupThrottled)):
                widget.refresh_data()
        # Update border color based on new memory percent
        new_percent = self.cgroup.get_percent_memory_usage()
        self.styles.border = ("round", MyColors.percent_to_rgb(new_percent))

class CGHeaderbar(HorizontalGroup):
    def compose(self) -> ComposeResult:
        yield Label("CGroup Name", id="header-cgroup-name")
        yield Label("Mem Usage", id="header-mem-usage")
        yield Label("Mem Limit", id="header-mem-limit")
        yield Label("Mem %", id="header-mem-percent")
        yield Label("CPU %", id="header-cpu-percent")
        yield Label("CPU Quota", id="header-cpu-quota")
        yield Label("Throttled", id="header-cpu-throttled")

class CGroupWatcherApp(App):
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode"),
                ("q", "quit", "Quit the app")]
    CSS_PATH = os.path.join(os.path.dirname(cgwatch.__file__), "style.tcss")
    limited_cgroups = reactive([],init=False)  # Don't call watcher on init
    def __init__(self):
        super().__init__()
        self.user_tree = CGroupTree("user.slice")
        
    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.theme = ("textual-dark" if self.theme == "textual-light" else "textual-light")
    def compose(self) -> ComposeResult:
        # yield Header(show_clock=True)
        yield CGHeaderbar()
        yield VerticalScroll(id="cgroup-lines-container")
        yield Footer()

    def on_mount(self) -> None:
        """Set up periodic updates."""
        self.set_interval(1.0, self.refresh_cgroups)  # Update every second
        self.set_interval(2.0, self.refresh_cgroup_list)  # Update cgroup list
        self.limited_cgroups = self.user_tree.get_memory_limited_cgroups()
    def refresh_cgroups(self) -> None:
        """Refresh all cgroup data."""
        for line in self.query(CGroupLine):
            line.refresh_data()
    def watch_limited_cgroups(self, old_value, new_value):
        """Called when the list of limited cgroups changes."""
        if self.is_mounted:
            self.update_lines()
    def refresh_cgroup_list(self):
        """Refresh the list of limited cgroups from the cgroup tree."""
        self.user_tree.update_tree()
        self.limited_cgroups = self.user_tree.get_memory_limited_cgroups()
    def update_lines(self):
        """Rebuild the displayed lines based on the current limited cgroups."""
        container = self.query_one(VerticalScroll)
        if container is None:
            return
        existing_lines = list(self.query(CGroupLine))
        existing_cgroups = [line.cgroup for line in existing_lines]
        # Remove lines for cgroups no longer limited
        for line in existing_lines:
            if line.cgroup not in self.limited_cgroups:
                line.remove()
        # Add lines for new limited cgroups
        for cgroup in self.limited_cgroups:
            if cgroup not in existing_cgroups:
                container.mount(CGroupLine(cgroup))



if __name__ == "__main__":
    app = CGroupWatcherApp()
    app.run()