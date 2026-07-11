"""Shared helpers for cgwatch's ``~/.config/cgwatch/*.ini`` config files.

Both entry points -- the TUI (``cgwatch.tui``) and the notification
daemon (``cgwatch.daemon``) -- keep a small ini file under
``~/.config/cgwatch/``: they seed a set of section/option defaults,
write the file out the first time it's needed, and otherwise read
back whatever is on disk (which may override some or all of the
defaults). This module factors that common "create-if-missing, then
read" flow into one place. Each program still owns its own filename,
section layout, defaults, and how it reports create/read problems --
those are exactly the bits that differ between the two, and the
call sites below pass them in explicitly.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CONFIG_DIR = Path.home() / ".config" / "cgwatch"


def build_default_parser(defaults: dict[str, dict[str, str]]) -> configparser.ConfigParser:
    """Build a ConfigParser pre-populated with the given section options."""
    cp = configparser.ConfigParser()
    for section, options in defaults.items():
        cp[section] = options
    return cp


def write_ini_file(path: Path, config: configparser.ConfigParser) -> None:
    """Write `config` to `path`, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        config.write(f)


@dataclass
class ConfigLoadResult:
    config: configparser.ConfigParser
    created: bool = False
    create_error: OSError | None = None


def load_ini_config(
    filename: str,
    defaults: dict[str, dict[str, str]],
    on_read_error: Callable[[Exception], None] | None = None,
) -> ConfigLoadResult:
    """Load ``~/.config/cgwatch/<filename>``.

    `defaults` maps section name to a dict of its options; it seeds a
    fresh :class:`configparser.ConfigParser`. If the file doesn't
    exist yet, it's written out verbatim with those defaults and
    `.created` is set on the result. If it already exists, it's read
    in place, overriding whichever defaults it sets.

    Read errors are only swallowed if `on_read_error` is given (it's
    called with the exception instead of it propagating); otherwise
    they behave like a bare ``ConfigParser.read()`` call. Create-time
    ``OSError``\\ s never propagate -- they're reported via
    ``.create_error`` on the result so callers can log/ignore them as
    they see fit.
    """
    config = build_default_parser(defaults)

    path = CONFIG_DIR / filename
    if not path.exists():
        try:
            write_ini_file(path, config)
        except OSError as e:
            return ConfigLoadResult(config, create_error=e)
        return ConfigLoadResult(config, created=True)

    if on_read_error is None:
        config.read(path)
    else:
        try:
            config.read(path)
        except Exception as e:  # noqa: BLE001 - mirrors prior broad except
            on_read_error(e)
    return ConfigLoadResult(config)
