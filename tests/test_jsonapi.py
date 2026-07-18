"""Unit tests for cgwatch.jsonapi (the cgwatch-cli helper).

Uses a fake sysfs tree under a TemporaryDirectory plus a monkeypatched
``cgwatch.cgroup.SYSFS_CGROUP_PATH``, and stubs the ``cgwatch.service``
functions that would otherwise shell out to systemctl / read real unit
files. No subprocesses of the system python are spawned; stdout is
captured via ``contextlib.redirect_stdout``.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cgwatch.cgroup as cgroup
import cgwatch.service as service
import cgwatch.jsonapi as jsonapi


def _write_cgroup(root: Path, name: str, *, memory_max="max", memory_current="0",
                   cpu_max="max 100000", cpu_stat=None, memory_stat=None) -> None:
    """Create a fake cgroup directory (memory.max/current, cpu.max/stat,
    optionally memory.stat for page-cache accounting)."""
    d = root / "user.slice" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.max").write_text(memory_max)
    (d / "memory.current").write_text(memory_current)
    (d / "cpu.max").write_text(cpu_max)
    stat = {
        "usage_usec": "0", "user_usec": "0", "system_usec": "0", "nice_usec": "0",
        "nr_periods": "0", "nr_throttled": "0", "throttled_usec": "0",
        "nr_bursts": "0", "burst_usec": "0",
    }
    if cpu_stat:
        stat.update(cpu_stat)
    lines = "\n".join(f"{k} {v}" for k, v in stat.items()) + "\n"
    (d / "cpu.stat").write_text(lines)
    if memory_stat is not None:
        mem_lines = "\n".join(f"{k} {v}" for k, v in memory_stat.items()) + "\n"
        (d / "memory.stat").write_text(mem_lines)


def _run_cli(argv):
    """Invoke jsonapi.main(argv) with stdout captured.

    Asserts stdout is exactly one parseable JSON object (the output
    contract) and returns (exit_code, parsed_json).
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = jsonapi.main(argv)
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line on stdout, got: {out!r}"
    parsed = json.loads(lines[0])
    return code, parsed


class DumpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # Memory-limited: the only cgroup that should show up in
        # dump()["cgroups"]. Cache-heavy on purpose (800M of the 1G
        # current is reclaimable page cache) so memory_percent/
        # memory_effective exercise the cache-subtraction, not just the
        # raw current/max ratio.
        _write_cgroup(
            self.root, "app-firefox@abc.service",
            memory_max="2147483648", memory_current="1073741824",
            cpu_max="max 100000",
            cpu_stat={
                "usage_usec": "100000000", "nr_periods": "500",
                "nr_throttled": "10", "throttled_usec": "200000",
            },
            memory_stat={"anon": "273741824", "file": "800000000", "shmem": "0"},
        )
        # Unlimited, running -> a candidate.
        _write_cgroup(
            self.root, "app-code@xyz.service",
            memory_max="max", memory_current="500000000", cpu_max="max 100000",
        )
        # CPU-limited only -> excluded from both cgroups[] and candidates.
        _write_cgroup(
            self.root, "app-cpuonly@1.service",
            memory_max="max", memory_current="10000000",
            cpu_max="200000 100000",
        )

        self._patches = [
            mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root)),
            mock.patch.object(
                service, "list_running_services",
                lambda: [
                    "app-firefox@abc.service",
                    "app-code@xyz.service",
                    "app-cpuonly@1.service",
                    "app-ghost@3.service",  # running, no matching cgroup dir
                ],
            ),
            mock.patch.object(service, "get_description", lambda name: f"Desc({name})"),
            mock.patch.object(
                service, "read_dropin",
                lambda unit: {"MemoryMax": "3G"} if unit == "app-firefox@.service" else {},
            ),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_dump_schema_filtering_and_fields(self):
        code, result = _run_cli(["dump"])
        self.assertEqual(code, 0)
        self.assertEqual(result["schema"], 1)
        self.assertEqual(result["kind"], "dump")
        self.assertTrue(result.get("ok", True))
        self.assertIsInstance(result["ts_ms"], int)
        self.assertGreater(result["ts_ms"], 0)

        # Only the one cgroup with a real (non-"max") memory.max shows up.
        cgroups = result["cgroups"]
        self.assertEqual(len(cgroups), 1)
        entry = cgroups[0]
        self.assertEqual(entry["name"], "app-firefox@abc.service")
        self.assertEqual(entry["unit"], "app-firefox@.service")
        self.assertEqual(entry["short_name"], "firefox")
        self.assertEqual(entry["description"], "Desc(app-firefox@abc.service)")
        self.assertEqual(entry["memory_current"], 1073741824)
        self.assertEqual(entry["memory_cache"], 800000000)
        self.assertEqual(entry["memory_effective"], 1073741824 - 800000000)
        self.assertIsInstance(entry["memory_effective"], int)
        self.assertIsInstance(entry["memory_cache"], int)
        self.assertEqual(entry["memory_max"], 2147483648)
        # memory_percent is now effective (current - cache) / max * 100,
        # not the raw 50% current/max would give.
        self.assertAlmostEqual(
            entry["memory_percent"],
            ((1073741824 - 800000000) / 2147483648) * 100,
        )
        self.assertLess(entry["memory_percent"], 50.0)
        self.assertIsNone(entry["cpu_quota_percent"])
        self.assertEqual(entry["cpu_stat"], {
            "usage_usec": 100000000,
            "nr_periods": 500,
            "nr_throttled": 10,
            "throttled_usec": 200000,
        })
        # Drop-in override wins for memory; no CPUQuota override so it
        # falls back to the value derived from live sysfs ("max").
        self.assertEqual(entry["edit_prefill"], {"memory": "3G", "cpu": "max"})

    def test_candidates_filter_and_sort(self):
        code, result = _run_cli(["dump"])
        self.assertEqual(code, 0)
        candidates = result["candidates"]
        # app-firefox is memory-limited and app-cpuonly is cpu-limited --
        # both excluded. app-code (backed by a real cgroup) and
        # app-ghost (no matching cgroup dir -> memory_current 0) remain,
        # sorted by memory_current descending.
        self.assertEqual(
            [c["unit"] for c in candidates],
            ["app-code@xyz.service", "app-ghost@3.service"],
        )
        self.assertEqual(candidates[0]["template"], "app-code@.service")
        self.assertEqual(candidates[0]["memory_current"], 500000000)
        self.assertEqual(candidates[0]["description"], "Desc(app-code@xyz.service)")
        self.assertEqual(candidates[1]["template"], "app-ghost@.service")
        self.assertEqual(candidates[1]["memory_current"], 0)


class CGroupEntryNullHandlingTests(unittest.TestCase):
    """Exercise _cgroup_entry's "max" -> null handling directly.

    get_memory_limited_cgroups() never actually yields an unlimited
    cgroup, so this drives the per-entry builder straight against a
    CGroup pointed at an unlimited fake sysfs dir (defense in depth,
    e.g. a limit removed between the filter check and this read).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _write_cgroup(self.root, "app-unlimited@1.service", memory_max="max", cpu_max="max 100000")

        self._patches = [
            mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root)),
            mock.patch.object(service, "get_description", lambda name: ""),
            mock.patch.object(service, "read_dropin", lambda unit: {}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_memory_max_and_cpu_quota_percent_are_null(self):
        cg = cgroup.CGroup("app-unlimited@1.service", parent=cgroup.CGroup("user.slice"))
        entry = jsonapi._cgroup_entry(cg)
        self.assertIsNone(entry["memory_max"])
        self.assertIsNone(entry["cpu_quota_percent"])
        self.assertEqual(entry["edit_prefill"], {"memory": "max", "cpu": "max"})


class ApplyTests(unittest.TestCase):
    class _ExplodingServiceManager:
        def __init__(self, *a, **kw):
            raise AssertionError("ServiceManager must not be constructed")

    def test_validation_failure_short_circuits_before_service_manager(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", self._ExplodingServiceManager):
            code, result = _run_cli(["apply", "myapp.service", "--mem", "not-a-number"])

        self.assertEqual(code, 0)
        self.assertEqual(result["schema"], 1)
        self.assertEqual(result["kind"], "apply")
        self.assertFalse(result["ok"])
        self.assertEqual(result["unit"], "myapp.service")
        self.assertTrue(any("MemoryMax" in m for m in result["messages"]))
        self.assertFalse(result["wrote_dropin"])
        self.assertFalse(result["reloaded"])
        self.assertFalse(result["set_runtime"])

    def test_both_none_short_circuits_before_service_manager(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", self._ExplodingServiceManager):
            code, result = _run_cli(["apply", "myapp.service"])

        self.assertEqual(code, 0)
        self.assertFalse(result["ok"])
        # P4: exact TUI-parity message (AddServiceModal's empty_message).
        self.assertEqual(result["messages"], ["set at least one of MemoryMax / CPUQuota"])

    def test_unknown_unit(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: None), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", self._ExplodingServiceManager):
            code, result = _run_cli(["apply", "unknownsvc", "--mem", "1G"])

        self.assertEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["unit"], "unknownsvc.service")  # suffix appended
        self.assertTrue(any("doesn't know unit" in m for m in result["messages"]))

    def test_apply_success_with_mocked_service_manager(self):
        fake_result = service.ApplyResult(
            ok=True, wrote_dropin=True, reloaded=True, set_runtime=True,
            messages=["daemon-reload failed: nope"],
        )
        manager_instance = mock.MagicMock()
        manager_instance.apply.return_value = fake_result
        manager_class = mock.MagicMock(return_value=manager_instance)

        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", manager_class):
            code, result = _run_cli(["apply", "myapp.service", "--mem", "2G", "--cpu", "200%"])

        self.assertEqual(code, 0)
        self.assertEqual(result["kind"], "apply")
        self.assertTrue(result["ok"])
        self.assertEqual(result["unit"], "myapp.service")
        self.assertEqual(result["messages"], ["daemon-reload failed: nope"])
        self.assertTrue(result["wrote_dropin"])
        self.assertTrue(result["reloaded"])
        self.assertTrue(result["set_runtime"])
        manager_instance.apply.assert_called_once_with("myapp.service", "2G", "200%")


class EmptyUnitTests(unittest.TestCase):
    """P2: a blank/whitespace unit must fail before suffixing, with the
    exact TUI-parity message (``AddServiceModal._save``'s empty-unit
    check). Nothing downstream (find_running_instance/unit_exists/
    ServiceManager) should be touched.
    """

    def test_blank_unit_returns_structured_failure(self):
        code, result = _run_cli(["apply", "", "--mem", "2G"])

        self.assertEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["messages"], ["pick or type a unit name"])
        self.assertFalse(result["wrote_dropin"])

    def test_whitespace_only_unit_returns_structured_failure(self):
        code, result = _run_cli(["apply", "   ", "--mem", "2G"])

        self.assertEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["messages"], ["pick or type a unit name"])


class EditFlagTests(unittest.TestCase):
    """P3: ``--edit`` mirrors EditLimitsModal's save path -- it skips the
    unknown-unit rejection (but still calls find_running_instance to pick
    the runtime target). Without ``--edit`` the existing add-service
    rejection is unchanged.
    """

    def test_edit_skips_unknown_unit_rejection(self):
        fake_result = service.ApplyResult(
            ok=True, wrote_dropin=True, reloaded=True, set_runtime=True, messages=[],
        )
        manager_instance = mock.MagicMock()
        manager_instance.apply.return_value = fake_result
        manager_class = mock.MagicMock(return_value=manager_instance)

        with mock.patch.object(service, "find_running_instance", lambda unit: None), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", manager_class):
            code, result = _run_cli(["apply", "unknownsvc.service", "--mem", "1G", "--edit"])

        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        manager_instance.apply.assert_called_once_with("unknownsvc.service", "1G", None)

    def test_edit_still_prefers_runtime_target_when_found(self):
        fake_result = service.ApplyResult(
            ok=True, wrote_dropin=True, reloaded=True, set_runtime=True, messages=[],
        )
        manager_instance = mock.MagicMock()
        manager_instance.apply.return_value = fake_result
        manager_class = mock.MagicMock(return_value=manager_instance)

        with mock.patch.object(service, "find_running_instance",
                                lambda unit: "myapp@abc123.service"), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", manager_class):
            code, result = _run_cli(["apply", "myapp@.service", "--mem", "1G", "--edit"])

        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        # --edit still resolves the running instance for the runtime target.
        manager_instance.apply.assert_called_once_with("myapp@abc123.service", "1G", None)

    def test_without_edit_still_rejects_unknown_unit(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: None), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", ApplyTests._ExplodingServiceManager):
            code, result = _run_cli(["apply", "unknownsvc.service", "--mem", "1G"])

        self.assertEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertTrue(any("doesn't know unit" in m for m in result["messages"]))


class UnlimitTests(unittest.TestCase):
    def test_unlimit_success(self):
        fake_result = service.ApplyResult(
            ok=True, wrote_dropin=True, reloaded=True, set_runtime=True, messages=[],
        )
        manager_instance = mock.MagicMock()
        manager_instance.unlimit.return_value = fake_result
        manager_class = mock.MagicMock(return_value=manager_instance)

        with mock.patch.object(service, "ServiceManager", manager_class):
            code, result = _run_cli(["unlimit", "myapp.service"])

        self.assertEqual(code, 0)
        self.assertEqual(result["kind"], "unlimit")
        self.assertTrue(result["ok"])
        self.assertEqual(result["unit"], "myapp.service")
        manager_instance.unlimit.assert_called_once_with("myapp.service")


class ContractTests(unittest.TestCase):
    def test_missing_subcommand_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            jsonapi.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_unexpected_exception_exits_1_and_prints_nothing_on_stdout(self):
        with mock.patch.object(jsonapi, "_dump", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            errbuf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
                code = jsonapi.main(["dump"])
            self.assertEqual(code, 1)
            self.assertEqual(buf.getvalue(), "")
            self.assertIn("RuntimeError", errbuf.getvalue())


@unittest.skipUnless(
    (Path(__file__).resolve().parent.parent / "cgwatch_cli.py").is_file(),
    "root shim cgwatch_cli.py not co-located (e.g. isolated pybuild tree)",
)
class CliShimExitCodeTests(unittest.TestCase):
    """P1: cgwatch_cli.py must propagate jsonapi.main()'s exit code
    (it used to call bare ``main()``, discarding a nonzero return so
    crash paths always exited 0).

    ``apply``/``unlimit`` now handle a missing/broken ``systemctl``
    internally (see P6's ``_internal_error_dict``), so that no longer
    reaches ``main()``'s catch-all -- there is no environment knob left
    that makes the real CLI process crash deterministically. Instead we
    force the one remaining, always-reachable failure mode of
    ``print(json.dumps(result))`` itself: close the read end of the
    child's stdout pipe *before it starts*, so its first write gets
    ``EPIPE``/``BrokenPipeError`` -- caught by ``main()``'s catch-all,
    which returns 1 -- and assert the real subprocess exits 1 (not 0).
    """

    def test_crash_path_propagates_nonzero_exit(self):
        repo_root = Path(__file__).resolve().parent.parent
        cli_path = repo_root / "cgwatch_cli.py"
        self.assertTrue(cli_path.is_file())

        read_fd, write_fd = os.pipe()
        os.close(read_fd)  # break the pipe before the child ever writes
        try:
            proc = subprocess.Popen(
                [sys.executable, str(cli_path), "dump"],
                stdout=write_fd, stderr=subprocess.PIPE,
            )
        finally:
            os.close(write_fd)
        _, stderr = proc.communicate(timeout=10)

        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"BrokenPipeError", stderr)

    def test_usage_error_still_propagates_exit_2(self):
        """Sanity check: argparse's own SystemExit(2) path (unaffected by
        this fix, since SystemExit was never swallowed) still works
        through the real subprocess."""
        repo_root = Path(__file__).resolve().parent.parent
        cli_path = repo_root / "cgwatch_cli.py"

        cp = subprocess.run(
            [sys.executable, str(cli_path)],
            capture_output=True, timeout=10,
        )
        self.assertEqual(cp.returncode, 2)


if __name__ == "__main__":
    unittest.main()
