#!/usr/bin/env python3


import cgwatch
from cgwatch.cgroup import CGroupTree, CGroup
from cgwatch import service as svc
import humanize
import os
import argparse

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, VerticalScroll, Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
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
    can_focus = True
    BINDINGS = [
        Binding("enter", "edit", "Edit"),
        Binding("plus,+,equals_sign", "bump_mem_up", "Mem +10%"),
        Binding("minus,-", "bump_mem_down", "Mem -10%"),
        Binding("delete", "unlimit", "Unlimit"),
    ]

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

    def action_edit(self) -> None:
        self.app.push_screen(EditLimitsModal(self.cgroup), self.app._after_edit)

    def action_unlimit(self) -> None:
        self.app.push_screen(
            ConfirmModal(f"Remove cgwatch limit for {self.cgroup.get_short_name()}?"),
            lambda ok: self.app._do_unlimit(self.cgroup.name) if ok else None,
        )

    def action_bump_mem_up(self) -> None:
        self._bump_mem(0.10)

    def action_bump_mem_down(self) -> None:
        self._bump_mem(-0.10)

    MIN_MEM_BYTES = 64 * 1024 * 1024  # 64 MiB floor on ±% bumps.

    def _bump_mem(self, delta: float) -> None:
        current = self.cgroup.get_memory_limit()
        if current == "max":
            self.app.notify(
                "Can't bump — MemoryMax is unlimited. Use Enter to set a value.",
                severity="warning",
            )
            return
        try:
            cur_bytes = int(current)
        except (ValueError, TypeError):
            self.app.notify(f"Can't parse current limit: {current!r}", severity="error")
            return
        new_bytes = max(self.MIN_MEM_BYTES, int(cur_bytes * (1.0 + delta)))
        new_mib = max(1, new_bytes // (1024 * 1024))
        new_value = f"{new_mib}M"
        result = svc.ServiceManager().apply(self.cgroup.name, new_value, None)
        if not result.ok:
            self.app.notify(
                "; ".join(result.messages) or "apply failed", severity="error"
            )
            return
        if result.messages:
            self.app.notify("; ".join(result.messages), severity="warning")
        self.refresh_data()

def _fmt_memory_for_edit(cgroup: CGroup) -> str:
    """Show an existing memory limit in a form the user can re-edit."""
    raw = cgroup.get_memory_limit()
    if raw == "max":
        return "max"
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return ""
    for suffix, factor in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if n % factor == 0:
            return f"{n // factor}{suffix}"
    return str(n)


def _fmt_cpu_for_edit(cgroup: CGroup) -> str:
    q = cgroup.get_cpu_quotum()
    if q == "max":
        return "max"
    try:
        return f"{int(round(float(q)))}%"
    except (ValueError, TypeError):
        return ""


_NAV_BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
    Binding("up", "focus_up", show=False),
    Binding("down", "focus_down", show=False),
    Binding("left", "focus_up", show=False),
    Binding("right", "focus_down", show=False),
]


class ConfirmModal(ModalScreen[bool]):
    # Buttons don't consume left/right, so we can safely bind them here.
    BINDINGS = _NAV_BINDINGS

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button("OK", variant="warning", id="confirm-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-ok")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_up(self) -> None:
        self.focus_previous()

    def action_focus_down(self) -> None:
        self.focus_next()


class EditLimitsModal(ModalScreen[bool]):
    # No left/right — Input uses them for cursor movement.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "focus_up", show=False),
        Binding("down", "focus_down", show=False),
    ]

    def __init__(self, cgroup: CGroup):
        super().__init__()
        self.cgroup = cgroup
        self.instance_unit = cgroup.name
        self.template_unit = svc.cgroup_name_to_unit(self.instance_unit)

    def compose(self) -> ComposeResult:
        existing = svc.read_dropin(self.template_unit)
        mem_prefill = existing.get("MemoryMax") or _fmt_memory_for_edit(self.cgroup)
        cpu_prefill = existing.get("CPUQuota") or _fmt_cpu_for_edit(self.cgroup)
        title = f"Edit limits — {self.cgroup.get_short_name()}"
        with Vertical(id="edit-box"):
            yield Static(title, id="edit-title")
            yield Static(f"Unit: {self.template_unit}", classes="edit-sub")
            with Horizontal(classes="edit-row"):
                yield Label("MemoryMax:", classes="edit-label")
                yield Input(value=mem_prefill, placeholder="e.g. 2G, 500M, max",
                            id="edit-mem")
            with Horizontal(classes="edit-row"):
                yield Label("CPUQuota: ", classes="edit-label")
                yield Input(value=cpu_prefill, placeholder="e.g. 200%, max",
                            id="edit-cpu")
            yield Static("", id="edit-error", classes="error")
            with Horizontal(id="edit-buttons"):
                yield Button("Cancel", id="edit-cancel")
                yield Button("Unlimit", variant="warning", id="edit-unlimit")
                yield Button("Save", variant="primary", id="edit-save")

    def on_mount(self) -> None:
        self.query_one("#edit-mem", Input).focus()

    def _show_error(self, msg: str) -> None:
        self.query_one("#edit-error", Static).update(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-cancel":
            self.dismiss(False)
        elif event.button.id == "edit-save":
            self._save()
        elif event.button.id == "edit-unlimit":
            self._unlimit()

    def _save(self) -> None:
        mem_raw = self.query_one("#edit-mem", Input).value
        cpu_raw = self.query_one("#edit-cpu", Input).value
        mem, err = svc.parse_memory(mem_raw)
        if err:
            self._show_error(f"MemoryMax: {err}")
            return
        cpu, err = svc.parse_cpu_quota(cpu_raw)
        if err:
            self._show_error(f"CPUQuota: {err}")
            return
        if mem is None and cpu is None:
            self._show_error("nothing to change (both fields empty)")
            return
        mgr = svc.ServiceManager()
        result = mgr.apply(self.instance_unit, mem, cpu)
        if not result.ok:
            self._show_error("; ".join(result.messages) or "apply failed")
            return
        if result.messages:
            # partial success — still dismiss, but surface warnings via notify.
            self.app.notify("; ".join(result.messages), severity="warning")
        self.dismiss(True)

    def _unlimit(self) -> None:
        mgr = svc.ServiceManager()
        result = mgr.unlimit(self.instance_unit)
        if not result.ok:
            self._show_error("; ".join(result.messages) or "unlimit failed")
            return
        if result.messages:
            self.app.notify("; ".join(result.messages), severity="warning")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_up(self) -> None:
        self.focus_previous()

    def action_focus_down(self) -> None:
        self.focus_next()


class AddServiceModal(ModalScreen[bool]):
    # No left/right — Input uses them for cursor movement.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "focus_up", show=False),
        Binding("down", "focus_down", show=False),
    ]

    def __init__(self, already_limited_templates: set[str]):
        super().__init__()
        # Templates for services that are already memory- or CPU-limited
        # at the cgroup level, regardless of which drop-in (or transient
        # property) set the limit. Used to filter the picker.
        self._already = already_limited_templates

    def compose(self) -> ComposeResult:
        running = svc.list_running_services()
        candidates = [
            r for r in running
            if svc.cgroup_name_to_unit(r) not in self._already
        ]
        options = [Option(name, id=name) for name in candidates]
        with Vertical(id="add-box"):
            yield Static("Add service", id="add-title")
            yield Static("Pick a running service (or type one below):",
                         classes="edit-sub")
            yield OptionList(*options, id="add-list")
            with Horizontal(classes="edit-row"):
                yield Label("Unit:      ", classes="edit-label")
                yield Input(placeholder="e.g. app-foo@.service",
                            id="add-unit")
            with Horizontal(classes="edit-row"):
                yield Label("MemoryMax:", classes="edit-label")
                yield Input(placeholder="e.g. 2G, 500M, max", id="add-mem")
            with Horizontal(classes="edit-row"):
                yield Label("CPUQuota: ", classes="edit-label")
                yield Input(placeholder="e.g. 200%, max", id="add-cpu")
            yield Static("", id="add-error", classes="error")
            with Horizontal(id="add-buttons"):
                yield Button("Cancel", id="add-cancel")
                yield Button("Save", variant="primary", id="add-save")

    def on_mount(self) -> None:
        lst = self.query_one("#add-list", OptionList)
        if lst.option_count > 0:
            lst.focus()
        else:
            self.query_one("#add-unit", Input).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Populate the manual-entry field with the TEMPLATE form
        # (stripped of any instance UUID) so what the user sees matches
        # the drop-in file path that actually gets written.
        instance = event.option.id or ""
        self.query_one("#add-unit", Input).value = svc.cgroup_name_to_unit(instance)
        self.query_one("#add-mem", Input).focus()

    def _show_error(self, msg: str) -> None:
        self.query_one("#add-error", Static).update(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-cancel":
            self.dismiss(False)
        elif event.button.id == "add-save":
            self._save()

    def _save(self) -> None:
        unit = self.query_one("#add-unit", Input).value.strip()
        if not unit:
            self._show_error("pick or type a unit name")
            return
        if not unit.endswith(".service"):
            unit = unit + ".service"
        # Resolve a running instance for the runtime set-property call.
        # `systemctl show` refuses bare template names (e.g. for transient
        # app-*@.service units started by a desktop launcher), so don't
        # hard-fail validation on those — trust the name and let the
        # drop-in lie dormant if the service never appears.
        runtime_target = svc.find_running_instance(unit)
        is_template = unit.endswith("@.service")
        if runtime_target is None and not is_template and not svc.unit_exists(unit):
            self._show_error(f"systemd doesn't know unit '{unit}'")
            return
        mem_raw = self.query_one("#add-mem", Input).value
        cpu_raw = self.query_one("#add-cpu", Input).value
        mem, err = svc.parse_memory(mem_raw)
        if err:
            self._show_error(f"MemoryMax: {err}")
            return
        cpu, err = svc.parse_cpu_quota(cpu_raw)
        if err:
            self._show_error(f"CPUQuota: {err}")
            return
        if mem is None and cpu is None:
            self._show_error("set at least one of MemoryMax / CPUQuota")
            return
        result = svc.ServiceManager().apply(runtime_target or unit, mem, cpu)
        if not result.ok:
            self._show_error("; ".join(result.messages) or "apply failed")
            return
        if result.messages:
            self.app.notify("; ".join(result.messages), severity="warning")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_up(self) -> None:
        self.focus_previous()

    def action_focus_down(self) -> None:
        self.focus_next()


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
    BINDINGS = [
        Binding("d", "toggle_dark", "Toggle dark mode"),
        Binding("q", "quit", "Quit"),
        Binding("a", "add_service", "Add service"),
        Binding("up", "focus_prev_line", show=False),
        Binding("down", "focus_next_line", show=False),
    ]
    CSS_PATH = os.path.join(os.path.dirname(cgwatch.__file__), "cgwatcher.tcss")
    limited_cgroups = reactive([],init=False)  # Don't call watcher on init
    def __init__(self, config: dict):
        super().__init__()
        self.user_tree = CGroupTree("user.slice")
        self.refresh_interval = config.get('refresh_interval', 1.0)
        self.app_scan_interval = config.get('app_scan_interval', 2.0)

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.theme = ("textual-dark" if self.theme == "textual-light" else "textual-light")

    def action_add_service(self) -> None:
        self.push_screen(AddServiceModal(self._limited_templates()), self._after_edit)

    def _limited_templates(self) -> set[str]:
        """Templates of services currently memory- or CPU-limited."""
        templates: set[str] = set()
        for cg in self.user_tree.get_memory_limited_cgroups():
            templates.add(svc.cgroup_name_to_unit(cg.name))
        for cg in self.user_tree.get_cpu_limited_cgroups():
            templates.add(svc.cgroup_name_to_unit(cg.name))
        return templates

    def _focus_line_at(self, offset: int) -> None:
        lines = list(self.query(CGroupLine))
        if not lines:
            return
        focused = self.focused
        try:
            idx = lines.index(focused) if focused in lines else -1
        except ValueError:
            idx = -1
        if idx < 0:
            lines[0].focus()
            return
        new_idx = max(0, min(len(lines) - 1, idx + offset))
        lines[new_idx].focus()

    def action_focus_next_line(self) -> None:
        self._focus_line_at(1)

    def action_focus_prev_line(self) -> None:
        self._focus_line_at(-1)

    def compose(self) -> ComposeResult:
        # yield Header(show_clock=True)
        yield CGHeaderbar()
        yield VerticalScroll(id="cgroup-lines-container")
        yield Footer()

    def on_mount(self) -> None:
        """Set up periodic updates."""
        self.set_interval(self.refresh_interval, self.refresh_cgroups)  # Update every second
        self.set_interval(self.app_scan_interval, self.refresh_cgroup_list)  # Update cgroup list
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

    def _modal_open(self) -> bool:
        return isinstance(self.screen, ModalScreen)

    def update_lines(self):
        """Rebuild the displayed lines based on the current limited cgroups."""
        if self._modal_open():
            # Don't mount/remove lines while the user is editing; the modal
            # holds a reference to a specific CGroup and we don't want the
            # underlying line to disappear from under it.
            return
        container = self.query_one(VerticalScroll)
        if container is None:
            return
        existing_lines = list(self.query(CGroupLine))
        existing_cgroups = [line.cgroup for line in existing_lines]
        had_focus = any(line.has_focus for line in existing_lines)
        # Remove lines for cgroups no longer limited
        for line in existing_lines:
            if line.cgroup not in self.limited_cgroups:
                line.remove()
        # Add lines for new limited cgroups
        for cgroup in self.limited_cgroups:
            if cgroup not in existing_cgroups:
                container.mount(CGroupLine(cgroup))
        # Ensure something focusable gets focus on first paint.
        if not had_focus:
            self.call_after_refresh(self._focus_first_line)

    def _focus_first_line(self) -> None:
        lines = list(self.query(CGroupLine))
        if lines and not any(line.has_focus for line in lines):
            lines[0].focus()

    def _after_edit(self, saved) -> None:
        """Callback from modals; refresh list if anything was saved."""
        if saved:
            self.refresh_cgroup_list()

    def _do_unlimit(self, cgroup_name: str) -> None:
        result = svc.ServiceManager().unlimit(cgroup_name)
        if not result.ok:
            self.notify(
                "; ".join(result.messages) or "unlimit failed",
                severity="error",
            )
            return
        if result.messages:
            self.notify("; ".join(result.messages), severity="warning")
        self.refresh_cgroup_list()



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CGroup Watcher Application")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds. Minimum is 0.1 seconds.")
    parser.add_argument("--app-scan-interval", type=float, default=2.0, help="Interval to rescan cgroup list in seconds.")
    args = parser.parse_args()
    refresh_interval = max(0.1, args.interval)
    app_scan_interval = max(0.1, args.app_scan_interval)
    config = {}
    config['refresh_interval'] = refresh_interval
    config['app_scan_interval'] = app_scan_interval
    app = CGroupWatcherApp(config=config)
    app.run()