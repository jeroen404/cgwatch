"""Manage systemd --user service limit drop-ins.

This module is the write-side counterpart to ``cgwatch/cgroup.py`` (which
only reads sysfs). It writes a dedicated drop-in file
``zz-cgwatch.conf`` into each ``<unit>.d/`` directory so it does not
collide with user-authored files (notably ``override.conf``). Because
``zz-`` sorts last, systemd applies our values after any user drop-in,
so TUI edits take effect even when the user has another drop-in with
the same keys.
"""

import configparser
import io
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


DROPIN_FILENAME = "zz-cgwatch.conf"
USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()


def cgroup_name_to_unit(cgroup_dir_name: str) -> str:
    """Convert a cgroup directory name to its systemd unit name.

    Template instances have their instance id stripped so the drop-in
    affects every instance of the template:

        ``app-firefox\\x2desr@<uuid>.service`` -> ``app-firefox\\x2desr@.service``

    Non-template names are returned unchanged. The literal ``\\x2d``
    escape is preserved (it matches systemd's own escaping for ``-`` in
    filenames and in ``~/.config/systemd/user/``).
    """
    if not cgroup_dir_name.endswith(".service"):
        return cgroup_dir_name
    at = cgroup_dir_name.find("@")
    if at == -1:
        return cgroup_dir_name
    return cgroup_dir_name[: at + 1] + ".service"


def unit_to_dropin_dir(unit: str) -> Path:
    return USER_UNIT_DIR / f"{unit}.d"


def dropin_path(unit: str) -> Path:
    return unit_to_dropin_dir(unit) / DROPIN_FILENAME


def list_running_services() -> list[str]:
    """Return instance unit names of currently-running user services."""
    try:
        cp = subprocess.run(
            [
                "systemctl", "--user", "list-units",
                "--type=service", "--state=running",
                "--plain", "--no-legend", "--no-pager",
            ],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return []
    if cp.returncode != 0:
        return []
    names = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "<unit> loaded active running <description>"
        name = line.split()[0]
        if name.endswith(".service"):
            names.append(name)
    return names


def list_limited_services() -> list[str]:
    """Return unit names that currently have a cgwatch drop-in."""
    if not USER_UNIT_DIR.is_dir():
        return []
    units = []
    for entry in USER_UNIT_DIR.iterdir():
        if not entry.is_dir() or not entry.name.endswith(".service.d"):
            continue
        if (entry / DROPIN_FILENAME).is_file():
            units.append(entry.name[: -len(".d")])
    return units


def _new_parser() -> configparser.ConfigParser:
    # Preserve case of keys (systemd keys are CamelCase) and disable
    # %-interpolation so values like ``200%`` can be written verbatim.
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    return cp


def read_dropin(unit: str) -> dict[str, str]:
    path = dropin_path(unit)
    if not path.is_file():
        return {}
    cp = _new_parser()
    try:
        cp.read(path)
    except configparser.Error:
        return {}
    if not cp.has_section("Service"):
        return {}
    return {k: v for k, v in cp.items("Service")}


def write_dropin(
    unit: str,
    memory_max: str | None = None,
    cpu_quota: str | None = None,
) -> None:
    """Merge values into our drop-in file.

    ``None`` means "leave that key as-is". Use empty string to clear a
    key (writes ``Key=`` which resets the property in systemd).
    """
    path = dropin_path(unit)
    path.parent.mkdir(parents=True, exist_ok=True)

    cp = _new_parser()
    if path.is_file():
        cp.read(path)
    if not cp.has_section("Service"):
        cp.add_section("Service")

    if memory_max is not None:
        cp.set("Service", "MemoryMax", memory_max)
    if cpu_quota is not None:
        cp.set("Service", "CPUQuota", cpu_quota)

    buf = io.StringIO()
    cp.write(buf)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(buf.getvalue())
    os.replace(tmp, path)


def delete_dropin(unit: str) -> None:
    path = dropin_path(unit)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    # Remove parent .d/ if empty.
    parent = path.parent
    try:
        parent.rmdir()
    except OSError:
        pass


def daemon_reload() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True, check=False,
    )


def set_property_runtime(instance_unit: str, **props: str) -> subprocess.CompletedProcess:
    """Apply properties immediately to a running instance via systemctl.

    Empty string values are allowed; systemd treats ``Key=`` as
    "reset to default/unlimited".
    """
    args = ["systemctl", "--user", "set-property", "--runtime", instance_unit]
    for k, v in props.items():
        args.append(f"{k}={v}")
    return subprocess.run(args, capture_output=True, text=True, check=False)


def find_running_instance(template_or_instance: str) -> str | None:
    """Return a running instance unit matching the given template or
    instance name, or ``None`` if nothing matches.

    If the argument is already a specific instance (``foo@xxx.service``),
    it is returned when currently running. Otherwise the template form
    is computed and any running instance matching it is returned.
    """
    tpl = cgroup_name_to_unit(template_or_instance)
    for running in list_running_services():
        if running == template_or_instance:
            return running
        if cgroup_name_to_unit(running) == tpl:
            return running
    return None


def _unit_file_search_paths() -> list[Path]:
    """Directories systemd --user searches for unit files, in priority order.

    Transient units (most desktop ``app-*@<uuid>.service`` entries) live
    in ``/run/user/<uid>/systemd/transient/`` and are keyed by the full
    instance name — that's where ``Description=`` actually is.
    """
    uid = os.getuid()
    return [
        Path(f"/run/user/{uid}/systemd/transient"),
        Path.home() / ".config/systemd/user",
        Path("/etc/systemd/user"),
        Path("/run/systemd/user"),
        Path("/usr/lib/systemd/user"),
        Path("/lib/systemd/user"),
    ]


def _find_unit_file(unit: str) -> Path | None:
    template = cgroup_name_to_unit(unit)
    names = [unit] if template == unit else [unit, template]
    for base in _unit_file_search_paths():
        for name in names:
            p = base / name
            if p.is_file():
                return p
    return None


def get_description(unit: str) -> str:
    """Return the systemd ``Description=`` value for a unit, or ''.

    Reads the unit file directly (transient first, then the user/system
    unit dirs). No subprocess.
    """
    path = _find_unit_file(unit)
    if path is None:
        return ""
    try:
        with path.open() as f:
            in_unit_section = False
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_unit_section = stripped == "[Unit]"
                    continue
                if in_unit_section and stripped.startswith("Description="):
                    return stripped.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def unit_exists(unit: str) -> bool:
    """True if systemd knows this unit (template or instance)."""
    cp = subprocess.run(
        ["systemctl", "--user", "show", "-p", "LoadState", "--value", unit],
        capture_output=True, text=True, check=False,
    )
    if cp.returncode != 0:
        return False
    state = cp.stdout.strip()
    return state in ("loaded", "stub")  # stub = exists on disk, not yet loaded


# --- validation -----------------------------------------------------------

_MEM_RE = re.compile(
    r"^\s*(?:(max|infinity)|(\d+(?:\.\d+)?)\s*([KMGTP])?(i?B)?)\s*$",
    re.IGNORECASE,
)

_SUFFIX_FACTORS = {
    "": 1,
    "K": 1024,
    "M": 1024 ** 2,
    "G": 1024 ** 3,
    "T": 1024 ** 4,
    "P": 1024 ** 5,
}


def parse_memory(s: str) -> tuple[str | None, str | None]:
    """Parse/normalize a MemoryMax input.

    Returns ``(normalized, None)`` on success, ``(None, error)`` on
    error. An empty input yields ``(None, None)`` meaning
    "leave key untouched".
    """
    if s is None or s.strip() == "":
        return None, None
    m = _MEM_RE.match(s)
    if not m:
        return None, "invalid memory value"
    if m.group(1):
        return "infinity", None
    num = float(m.group(2))
    suffix = (m.group(3) or "").upper()
    bytes_val = int(num * _SUFFIX_FACTORS[suffix])
    if bytes_val < 1024 * 1024:
        return None, "value below 1 MiB — likely wrong"
    # Re-emit in systemd's compact form, preferring the input suffix when
    # it was integer, otherwise falling back to bytes.
    if suffix and num == int(num):
        return f"{int(num)}{suffix}", None
    return f"{bytes_val}", None


_CPU_RE = re.compile(r"^\s*(max|(\d+)\s*%?)\s*$", re.IGNORECASE)


def parse_cpu_quota(s: str) -> tuple[str | None, str | None]:
    """Parse/normalize a CPUQuota input.

    Returns empty-string for ``max`` so callers can write ``CPUQuota=``
    to reset the property. Empty input yields ``(None, None)``.
    """
    if s is None or s.strip() == "":
        return None, None
    m = _CPU_RE.match(s)
    if not m:
        return None, "invalid CPU quota (use e.g. 200% or max)"
    if m.group(1).lower() == "max":
        return "", None
    n = int(m.group(2))
    if n < 1 or n > 10000:
        return None, "CPUQuota out of range (1..10000%)"
    return f"{n}%", None


# --- orchestration --------------------------------------------------------


@dataclass
class ApplyResult:
    ok: bool = True
    wrote_dropin: bool = False
    reloaded: bool = False
    set_runtime: bool = False
    messages: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> "ApplyResult":
        self.ok = False
        self.messages.append(msg)
        return self

    def warn(self, msg: str) -> "ApplyResult":
        self.messages.append(msg)
        return self


class ServiceManager:
    """High-level operations on a service's cgwatch drop-in."""

    def apply(
        self,
        instance_unit: str,
        memory_max: str | None,
        cpu_quota: str | None,
    ) -> ApplyResult:
        """Write drop-in + reload + set-property for immediate effect.

        ``memory_max`` / ``cpu_quota`` are already-normalized values
        from :func:`parse_memory` / :func:`parse_cpu_quota`. ``None``
        means "don't touch that key"; empty string means "clear".
        """
        res = ApplyResult()
        if memory_max is None and cpu_quota is None:
            return res.fail("nothing to apply")

        template = cgroup_name_to_unit(instance_unit)

        try:
            write_dropin(template, memory_max=memory_max, cpu_quota=cpu_quota)
            res.wrote_dropin = True
        except OSError as e:
            return res.fail(f"write failed: {e}")

        reload_cp = daemon_reload()
        res.reloaded = reload_cp.returncode == 0
        if not res.reloaded:
            res.warn(f"daemon-reload failed: {reload_cp.stderr.strip()}")

        props: dict[str, str] = {}
        if memory_max is not None:
            props["MemoryMax"] = memory_max
        if cpu_quota is not None:
            props["CPUQuota"] = cpu_quota
        set_cp = set_property_runtime(instance_unit, **props)
        res.set_runtime = set_cp.returncode == 0
        if not res.set_runtime:
            res.warn(
                "runtime apply failed (persisted, applies on next start): "
                + set_cp.stderr.strip()
            )
        return res

    def unlimit(self, instance_unit: str) -> ApplyResult:
        res = ApplyResult()
        template = cgroup_name_to_unit(instance_unit)

        try:
            delete_dropin(template)
            res.wrote_dropin = True
        except OSError as e:
            res.warn(f"delete failed: {e}")

        reload_cp = daemon_reload()
        res.reloaded = reload_cp.returncode == 0
        if not res.reloaded:
            res.warn(f"daemon-reload failed: {reload_cp.stderr.strip()}")

        set_cp = set_property_runtime(
            instance_unit, MemoryMax="infinity", CPUQuota=""
        )
        res.set_runtime = set_cp.returncode == 0
        if not res.set_runtime:
            res.warn(
                "runtime unlimit failed: " + set_cp.stderr.strip()
            )
        return res
