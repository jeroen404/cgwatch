#!/usr/bin/env python3


import cgwatch
from cgwatch.cgroup import CGroupTree, CGroup
from cgwatch import service as svc
from cgwatch.service import _fmt_cpu_for_edit, _fmt_memory_for_edit
from cgwatch.config import (
    CONFIG_DIR,
    build_default_parser,
    load_ini_config,
    write_ini_file,
)
import humanize
import os
import argparse

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, VerticalScroll, Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Footer, Input, OptionList, Static
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


class ReactiveMetricLabel(Label):
    """Shared scaffold for the small per-cgroup metric labels below.

    Each concrete label reads one value off a `CGroup`, renders it as
    text and (some of them) recolors itself. Subclasses only need to
    supply `_fetch` (read the raw value from the cgroup) and `_format`
    (turn a value into display text); `_render_update`/`_after_init`
    are optional hooks for the ones that also touch `.styles.color`.
    (`_format`, not `_render`: Textual's Widget defines an internal
    no-arg `_render()` that must not be shadowed.)
    """

    value = reactive(0)

    def __init__(self, cgroup: CGroup, **kwargs):
        self.cgroup = cgroup
        initial = self._fetch(cgroup)
        super().__init__(self._format(initial), **kwargs)
        self.value = initial
        self._after_init()

    def watch_value(self, old_value, new_value):
        self._render_update(new_value)

    def refresh_data(self):
        self.value = self._fetch(self.cgroup)

    def _fetch(self, cgroup: CGroup):
        raise NotImplementedError

    def _format(self, value) -> str:
        raise NotImplementedError

    def _render_update(self, value) -> None:
        """Default reaction to a new value: just re-render the text."""
        self.update(self._format(value))

    def _after_init(self) -> None:
        """Hook for a fixed post-init step. Default: no-op."""


class MemoryUsageHumanized(ReactiveMetricLabel):
    def __init__(self, mem_display_type: str, cgroup: CGroup, **kwargs):
        self.mem_display_type = mem_display_type
        super().__init__(cgroup, **kwargs)

    def _fetch(self, cgroup: CGroup):
        if self.mem_display_type == "usage":
            return int(cgroup.get_current_memory_usage())
        elif self.mem_display_type == "limit":
            limit = cgroup.get_memory_limit()
            return int(limit) if limit != "max" else 0
        else:
            return 0

    def _format(self, value) -> str:
        return humanize.naturalsize(value)


class MemoryPercent(ReactiveMetricLabel):
    def _fetch(self, cgroup: CGroup):
        return cgroup.get_percent_memory_usage()

    def _format(self, value) -> str:
        return f"{value:.2f}%"

    def _render_update(self, value) -> None:
        self.update(self._format(value))
        self.styles.color = MyColors.percent_to_rgb(value)


class CGroupCPUQuota(ReactiveMetricLabel):
    def _fetch(self, cgroup: CGroup):
        return cgroup.get_cpu_quotum()

    def _format(self, value) -> str:
        display_quota = value if value != "max" else "max"
        return f"{display_quota}%"


class CGroupCPUPercentUsage(ReactiveMetricLabel):
    def _fetch(self, cgroup: CGroup):
        return cgroup.get_cpu_last_usage_percent()

    def _format(self, value) -> str:
        return f"{value:.2f}%"

    def _render_update(self, value) -> None:
        if self.cgroup.has_cpu_quota():
            quota = self.cgroup.get_cpu_quotum()
            new_color = MyColors.percent_of_percent_to_rgb(value, quota)
            self.styles.color = new_color
            new_color_hex = new_color.hex
            self.update(f"[{new_color_hex}]{value:.2f}%[/]")
        else:
            self.styles.color = "yellow"

    def _after_init(self) -> None:
        self.styles.color = "green"


class CGroupThrottled(ReactiveMetricLabel):
    def _fetch(self, cgroup: CGroup):
        return cgroup.throttled_since_last()

    def _format(self, value) -> str:
        return "⚠️" if value > 0 else "✅"

class CGroupName(Label):
    # Leaves room for the column's horizontal padding (2 each side).
    DESC_MAX = 56

    def __init__(self, cgroup: CGroup, **kwargs):
        super().__init__("", **kwargs)
        self.cgroup = cgroup
        self._description: str | None = None

    def on_mount(self) -> None:
        self.update_name(getattr(self.app, "show_descriptions", False))

    def _get_description(self) -> str:
        if self._description is None:
            self._description = svc.get_description(self.cgroup.name)
        return self._description

    def update_name(self, descriptions: bool) -> None:
        if descriptions:
            desc = self._get_description()
            if not desc:
                # Fall back to the short name if systemctl had no answer.
                self.update(self.cgroup.get_short_name())
                return
            if len(desc) > self.DESC_MAX:
                desc = desc[: self.DESC_MAX - 1] + "…"
            self.update(desc)
        else:
            self.update(self.cgroup.get_short_name())

class CGroupLine(HorizontalGroup):
    can_focus = True
    BINDINGS = [
        Binding("enter", "edit", "Edit", key_display="enter"),
        Binding("a", "add_service", "Add service"),
        Binding("plus", "bump_mem_up", "Mem +10%"),
        Binding("equals_sign", "bump_mem_up", show=False),
        Binding("minus", "bump_mem_down", "Mem -10%"),
        Binding("delete", "unlimit", "Unlimit"),
        Binding("n", "toggle_names", "Description"),
        Binding("up", "focus_prev_line", "Navigate", key_display="↑↓"),
        Binding("down", "focus_next_line", show=False),
        Binding("q", "quit", "Quit"),
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
        else:
            self.app.notify(f"MemoryMax → {new_value}")
        self.refresh_data()

_NAV_BINDINGS = [
    Binding("escape", "cancel", "Cancel"),
    Binding("up", "focus_up", show=False),
    Binding("down", "focus_down", show=False),
    Binding("left", "focus_up", show=False),
    Binding("right", "focus_down", show=False),
]


class CGModalScreen(ModalScreen[bool]):
    """Base for cgwatch's modal screens.

    Provides the shared escape-to-cancel / up-down-focus navigation
    actions, plus (for the modals that edit limits) the common
    "parse MemoryMax/CPUQuota, apply, report errors" save sequence.
    """

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_up(self) -> None:
        self.focus_previous()

    def action_focus_down(self) -> None:
        self.focus_next()

    def _show_error(self, message: str) -> None:
        """Show a validation/apply error to the user; subclasses must implement."""
        raise NotImplementedError

    def _present_outcome(self, outcome: "svc.ResolveOutcome") -> None:
        """Show/notify a ``svc.ResolveOutcome`` and dismiss on success.

        Shared tail of both ``_apply_limits`` (below) and
        ``AddServiceModal._save``: shows the error (via `self._show_error`)
        and leaves the modal open on any validation or apply failure. On
        success, surfaces any warnings via `self.app.notify` and dismisses
        with `True`.
        """
        if outcome.error is not None:
            self._show_error(outcome.error)
            return
        result = outcome.apply_result
        if not result.ok:
            self._show_error("; ".join(result.messages) or "apply failed")
            return
        if result.messages:
            self.app.notify("; ".join(result.messages), severity="warning")
        self.dismiss(True)

    def _apply_limits(
        self,
        target: str,
        mem_raw: str,
        cpu_raw: str,
        empty_message: str,
    ) -> None:
        """Parse MemoryMax/CPUQuota input and apply them to `target`.

        Presentation-only wrapper around the shared decision logic in
        ``svc.apply_limits``.
        """
        outcome = svc.apply_limits(target, mem_raw, cpu_raw, empty_message=empty_message)
        self._present_outcome(outcome)


class ConfirmModal(CGModalScreen):
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


class EditLimitsModal(CGModalScreen):
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
        self._apply_limits(
            self.instance_unit, mem_raw, cpu_raw,
            "nothing to change (both fields empty)",
        )

    def _unlimit(self) -> None:
        mgr = svc.ServiceManager()
        result = mgr.unlimit(self.instance_unit)
        if not result.ok:
            self._show_error("; ".join(result.messages) or "unlimit failed")
            return
        if result.messages:
            self.app.notify("; ".join(result.messages), severity="warning")
        self.dismiss(True)


class AddServiceModal(CGModalScreen):
    # No left/right — Input uses them for cursor movement.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "focus_up", show=False),
        Binding("down", "focus_down", show=False),
    ]

    def __init__(self, already_limited_templates: set[str], tree: CGroupTree):
        super().__init__()
        # Templates for services that are already memory- or CPU-limited
        # at the cgroup level, regardless of which drop-in (or transient
        # property) set the limit. Used to filter the picker.
        self._already = already_limited_templates
        self._tree = tree

    def compose(self) -> ComposeResult:
        candidates = svc.candidate_service_names(self._tree, self._already)
        cg_by_name = {cg.name: cg for cg in self._tree.all_cgroups()}
        MEM_COL = 10  # width for right-justified humanized memory
        options = []
        for name in candidates:
            cg = cg_by_name.get(name)
            if cg:
                mem = humanize.naturalsize(int(cg.get_current_memory_usage()))
            else:
                mem = ""
            display = name
            at = name.find("@")
            if at != -1 and name.endswith(".service"):
                display = name[:at + 1] + "….service"
            label = f"{mem:>{MEM_COL}}  {display}"
            options.append(Option(label, id=name))
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
        # Unit resolution (blank check, ".service" suffixing, resolving a
        # running instance for the runtime set-property call -- `systemctl
        # show` refuses bare template names, e.g. for transient
        # app-*@.service units started by a desktop launcher, so validation
        # doesn't hard-fail on those and just trusts the name) plus
        # MemoryMax/CPUQuota parsing and the actual apply all live in
        # ``svc.resolve_and_apply`` now, shared with the CLI's ``apply``
        # command.
        unit_raw = self.query_one("#add-unit", Input).value.strip()
        mem_raw = self.query_one("#add-mem", Input).value
        cpu_raw = self.query_one("#add-cpu", Input).value
        outcome = svc.resolve_and_apply(unit_raw, mem_raw, cpu_raw)
        self._present_outcome(outcome)


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
        Binding("a", "add_service", "Add service"),  # fallback when no line focused
        Binding("n", "toggle_names", "Description"),
        Binding("up", "focus_prev_line", "Navigate", key_display="↑↓"),
        Binding("down", "focus_next_line", show=False),
        Binding("q", "quit", "Quit"),
    ]
    CSS_PATH = os.path.join(os.path.dirname(cgwatch.__file__), "cgwatcher.tcss")
    limited_cgroups = reactive([],init=False)  # Don't call watcher on init
    show_descriptions = reactive(False)
    def __init__(self, config: dict):
        super().__init__()
        self.cgwatch_config = dict(config)
        self.user_tree = CGroupTree("user.slice")
        self.refresh_interval = config.get('refresh_interval', 1.0)
        self.app_scan_interval = config.get('app_scan_interval', 2.0)
        self.highlight_timeout = config.get('highlight_timeout', 3.0)
        self.show_descriptions = bool(config.get('show_descriptions', False))
        self._highlight_timer = None
        self._last_focused_index = -1
        self._initial_focus_done = False

    def action_add_service(self) -> None:
        self.push_screen(AddServiceModal(self._limited_templates(), self.user_tree), self._after_edit)

    def action_toggle_names(self) -> None:
        self.show_descriptions = not self.show_descriptions

    def action_quit(self) -> None:
        self.cgwatch_config['show_descriptions'] = self.show_descriptions
        try:
            _save_config_file(self.cgwatch_config)
        except OSError:
            pass
        self.exit()

    def watch_show_descriptions(self, old_value: bool, new_value: bool) -> None:
        if not self.is_mounted:
            return
        self.set_class(new_value, "descriptions")
        for w in self.query(CGroupName):
            w.update_name(new_value)

    def _limited_templates(self) -> set[str]:
        """Templates of services currently memory- or CPU-limited."""
        return svc.limited_templates(self.user_tree)

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
            # Use last-known index so navigation resumes after highlight timeout
            idx = max(0, min(len(lines) - 1, self._last_focused_index))
        new_idx = max(0, min(len(lines) - 1, idx + offset))
        self._last_focused_index = new_idx
        lines[new_idx].focus()
        # Reset the highlight auto-dismiss timer
        if self._highlight_timer is not None:
            self._highlight_timer.stop()
        self._highlight_timer = self.set_timer(
            self.highlight_timeout, self._dismiss_highlight
        )

    def _dismiss_highlight(self) -> None:
        """Remove focus highlight from the current CGroupLine."""
        if isinstance(self.focused, CGroupLine):
            self.screen.set_focus(None)

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
        self.set_class(self.show_descriptions, "descriptions")
        self.set_interval(self.refresh_interval, self.refresh_cgroups)  # Update every second
        self.set_interval(self.app_scan_interval, self.refresh_cgroup_list)  # Update cgroup list
        self.refresh_cgroup_list()
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
        cgroups = self.user_tree.get_memory_limited_cgroups()
        cgroups.sort(key=lambda cg: cg.get_percent_memory_usage(), reverse=True)
        self.limited_cgroups = cgroups

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
        # Remove all existing lines
        for line in existing_lines:
            line.remove()
        # Mount fresh lines in sorted order
        for cgroup in self.limited_cgroups:
            container.mount(CGroupLine(cgroup))
        # Ensure something focusable gets focus on first paint.
        if not self._initial_focus_done:
            self._initial_focus_done = True
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



CONFIG_FILE = CONFIG_DIR / "cgwatch.ini"

DEFAULTS = {
    'refresh_interval': 1.0,
    'app_scan_interval': 2.0,
    'highlight_timeout': 3.0,
    'show_descriptions': False,
}


def _config_as_ini_section(config: dict) -> dict[str, str]:
    """Stringify a config dict into the on-disk [cgwatcher] section form."""
    return {
        'refresh_interval': str(config.get('refresh_interval', DEFAULTS['refresh_interval'])),
        'app_scan_interval': str(config.get('app_scan_interval', DEFAULTS['app_scan_interval'])),
        'highlight_timeout': str(config.get('highlight_timeout', DEFAULTS['highlight_timeout'])),
        'show_descriptions': 'true' if config.get('show_descriptions', DEFAULTS['show_descriptions']) else 'false',
    }


def _save_config_file(config: dict) -> None:
    cp = build_default_parser({'cgwatcher': _config_as_ini_section(config)})
    write_ini_file(CONFIG_FILE, cp)


def load_config() -> dict:
    """Load TUI config from ~/.config/cgwatch/cgwatch.ini."""
    config = dict(DEFAULTS)
    result = load_ini_config(
        "cgwatch.ini", {'cgwatcher': _config_as_ini_section(DEFAULTS)}
    )
    if result.create_error is not None:
        return config
    section = result.config['cgwatcher']
    for key in ('refresh_interval', 'app_scan_interval', 'highlight_timeout'):
        config[key] = max(0.1, float(section[key]))
    config['show_descriptions'] = str(section['show_descriptions']).strip().lower() in (
        '1', 'true', 'yes', 'on'
    )
    return config


def main():
    parser = argparse.ArgumentParser(description="CGroup Watcher Application")
    parser.add_argument("--interval", type=float, default=None, help="Refresh interval in seconds. Minimum is 0.1 seconds.")
    parser.add_argument("--app-scan-interval", type=float, default=None, help="Interval to rescan cgroup list in seconds.")
    args = parser.parse_args()
    config = load_config()
    if args.interval is not None:
        config['refresh_interval'] = max(0.1, args.interval)
    if args.app_scan_interval is not None:
        config['app_scan_interval'] = max(0.1, args.app_scan_interval)
    app = CGroupWatcherApp(config=config)
    app.run()


if __name__ == "__main__":
    main()
