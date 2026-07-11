#!/usr/bin/env python3
"""Stateless JSON helper CLI for cgwatch — entry point ``cgwatch-cli``.

This module backs the KDE Plasma widget (``plasmoid/``): every
invocation does one thing (dump the current state, apply new limits,
or clear limits) and prints exactly one JSON object to stdout, then
exits. It never talks to Textual/humanize — formatting for a UI is the
caller's job (``logic.js`` in the plasmoid). Only stdlib plus
``cgwatch.cgroup``/``cgwatch.service`` may be imported here.

Output contract
----------------
Every structured outcome — including validation/apply failures —
is a single JSON object on stdout with exit code 0. Every object has
``"schema": 1`` and a ``"kind"`` of ``"dump"``, ``"apply"`` or
``"unlimit"``. Exit code is non-zero only for argparse usage errors
(2, argparse's own doing) or a genuinely unexpected exception (1,
traceback on stderr, nothing on stdout).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback

from cgwatch.cgroup import CGroupTree
from cgwatch import service


SCHEMA_VERSION = 1


# --- dump ------------------------------------------------------------------


def _cgroup_entry(cg) -> dict:
    """Build one ``dump.cgroups[]`` entry for a memory-limited cgroup."""
    unit = service.cgroup_name_to_unit(cg.name)

    limit = cg.get_memory_limit()
    memory_max = None if limit == "max" else int(limit)

    quota = cg.get_cpu_quotum()
    cpu_quota_percent = None if quota == "max" else float(quota)

    raw_stat = cg.get_cpu_stat()
    cpu_stat = {
        "usage_usec": int(raw_stat.get("usage_usec", 0)),
        "nr_periods": int(raw_stat.get("nr_periods", 0)),
        "nr_throttled": int(raw_stat.get("nr_throttled", 0)),
        "throttled_usec": int(raw_stat.get("throttled_usec", 0)),
    }

    # Same "drop-in override, else derive from live sysfs" logic as the
    # TUI's EditLimitsModal.compose().
    existing = service.read_dropin(unit)
    mem_prefill = existing.get("MemoryMax") or service._fmt_memory_for_edit(cg)
    cpu_prefill = existing.get("CPUQuota") or service._fmt_cpu_for_edit(cg)

    return {
        "name": cg.name,
        "unit": unit,
        "short_name": cg.get_short_name(),
        "description": service.get_description(cg.name),
        "memory_current": int(cg.get_current_memory_usage()),
        "memory_max": memory_max,
        "memory_percent": cg.get_percent_memory_usage(),
        "cpu_quota_percent": cpu_quota_percent,
        "cpu_stat": cpu_stat,
        "edit_prefill": {"memory": mem_prefill, "cpu": cpu_prefill},
    }


def _dump_error(kind: str, message: str) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "kind": "dump",
        "ok": False,
        "error": {"kind": kind, "message": message},
    }


def _dump() -> dict:
    try:
        tree = CGroupTree("user.slice")
        cgroups = []
        for cg in tree.get_memory_limited_cgroups():
            try:
                cgroups.append(_cgroup_entry(cg))
            except Exception:
                # A single cgroup losing a sysfs race (e.g. the process
                # exited between listdir and read) shouldn't blank the
                # whole dump -- just skip that entry.
                continue
        candidates = service.candidate_services(tree)
    except Exception as e:  # defense in depth: sysfs surprises shouldn't crash
        return _dump_error("cgroups-unavailable", str(e))
    return {
        "schema": SCHEMA_VERSION,
        "kind": "dump",
        "ok": True,
        "ts_ms": int(time.time() * 1000),
        "cgroups": cgroups,
        "candidates": candidates,
    }


# --- apply / unlimit ---------------------------------------------------


def _result_dict(kind: str, unit: str, res) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "ok": res.ok,
        "unit": unit,
        "messages": list(res.messages),
        "wrote_dropin": res.wrote_dropin,
        "reloaded": res.reloaded,
        "set_runtime": res.set_runtime,
    }


def _internal_error_dict(kind: str, unit: str, exc: Exception) -> dict:
    """Structured failure for an unexpected exception inside apply/unlimit
    (e.g. ``FileNotFoundError`` when systemctl is missing from PATH) --
    same defense-in-depth idea as ``_dump_error``, but shaped like
    ``_result_dict`` so callers only ever branch on ``kind``.
    """
    return {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "ok": False,
        "unit": unit,
        "messages": [f"internal error: {exc}"],
        "wrote_dropin": False,
        "reloaded": False,
        "set_runtime": False,
    }


def _cmd_apply(args) -> dict:
    """Thin wrapper around ``service.resolve_and_apply`` -- the shared
    "resolve unit -> validate -> apply" decision logic also used by the
    TUI's ``AddServiceModal._save``. Only presentation (shaping the JSON
    result) happens here.

    ``--edit`` mirrors ``EditLimitsModal``'s save path instead, which
    applies unconditionally -- the unit-existence pre-check is skipped
    (though ``find_running_instance`` is still consulted to pick the
    runtime target) so the persistent drop-in is written even if the
    instance already exited.
    """
    try:
        outcome = service.resolve_and_apply(args.unit, args.mem, args.cpu, edit=args.edit)
        if outcome.apply_result is not None:
            return _result_dict("apply", outcome.unit, outcome.apply_result)
        res = service.ApplyResult().fail(outcome.error)
        return _result_dict("apply", outcome.unit, res)
    except Exception as e:
        return _internal_error_dict("apply", args.unit, e)


def _cmd_unlimit(args) -> dict:
    """Thin wrapper around ``ServiceManager().unlimit`` -- no extra
    validation, same as the TUI's direct calls from CGroupLine/EditLimitsModal.
    """
    try:
        unit = args.unit
        res = service.ServiceManager().unlimit(unit)
        return _result_dict("unlimit", unit, res)
    except Exception as e:
        return _internal_error_dict("unlimit", args.unit, e)


# --- CLI ---------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgwatch-cli",
        description="Stateless JSON helper for the cgwatch Plasma widget.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dump", help="Dump limited cgroups + add-service candidates as JSON.")

    apply_p = sub.add_parser("apply", help="Apply MemoryMax/CPUQuota limits to a unit.")
    apply_p.add_argument("unit", help="Unit or template name, e.g. app-foo@.service")
    apply_p.add_argument("--mem", default=None, help="MemoryMax value, e.g. 2G, 500M, max")
    apply_p.add_argument("--cpu", default=None, help="CPUQuota value, e.g. 200%%, max")
    apply_p.add_argument(
        "--edit", action="store_true", default=False,
        help="Edit an existing unit's limits (skip the unit-existence "
             "pre-check; mirrors the TUI's EditLimitsModal path).",
    )

    unlimit_p = sub.add_parser("unlimit", help="Remove the cgwatch drop-in for a unit.")
    unlimit_p.add_argument("unit", help="Unit or template name")

    return parser


def main(argv: list[str] | None = None) -> int | None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "dump":
            result = _dump()
        elif args.command == "apply":
            result = _cmd_apply(args)
        elif args.command == "unlimit":
            result = _cmd_unlimit(args)
        else:  # pragma: no cover - argparse enforces valid choices
            parser.error(f"unknown command {args.command!r}")
            return 2
        print(json.dumps(result))
        return 0
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
