"""Unit tests for cgwatch.cgroup (the sysfs read layer).

Follows the established pattern from tests/test_jsonapi.py: build a fake
sysfs tree under a TemporaryDirectory (dirs with memory.max/current,
cpu.max/stat files) and monkeypatch cgwatch.cgroup.SYSFS_CGROUP_PATH. No
real /sys/fs/cgroup access, no subprocesses.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cgwatch.cgroup as cgroup

# cgwatch.cgroup does `import os`, so cgroup.os IS this same os module
# object -- patching cgroup.os.listdir patches the global os.listdir.
# Sorted-listdir tests below must call through this saved reference
# (captured before any patching) instead of the (by-then-patched) os
# module, or the lambda would recurse into its own mock.
_real_listdir = os.listdir


def _write_cgroup(root: Path, rel_path: str, *, memory_max=None, memory_current=None,
                   cpu_max=None, cpu_stat=None) -> Path:
    """Create a fake cgroup directory at root/rel_path.

    Only writes the files whose value is given (None means "leave that
    file absent", exercising the FileNotFoundError fallback paths).
    `rel_path` may contain '/' to create nested cgroups directly (for
    tree-traversal tests) without going through CGroup.build_subtree.
    """
    d = root / rel_path
    d.mkdir(parents=True, exist_ok=True)
    if memory_max is not None:
        (d / "memory.max").write_text(memory_max)
    if memory_current is not None:
        (d / "memory.current").write_text(memory_current)
    if cpu_max is not None:
        (d / "cpu.max").write_text(cpu_max)
    if cpu_stat is not None:
        lines = "\n".join(f"{k} {v}" for k, v in cpu_stat.items()) + "\n"
        (d / "cpu.stat").write_text(lines)
    return d


class MemoryGettersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_memory_limit_max(self):
        _write_cgroup(self.root, "user.slice/app-a.service", memory_max="max", memory_current="0")
        cg = cgroup.CGroup("app-a.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_memory_limit(), "max")
        self.assertFalse(cg.has_memory_limit())

    def test_memory_limit_numeric(self):
        _write_cgroup(self.root, "user.slice/app-b.service",
                      memory_max="2147483648", memory_current="1073741824")
        cg = cgroup.CGroup("app-b.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_memory_limit(), "2147483648")
        self.assertTrue(cg.has_memory_limit())
        self.assertEqual(cg.get_current_memory_usage(), "1073741824")
        self.assertAlmostEqual(cg.get_percent_memory_usage(), 50.0)

    def test_memory_limit_missing_file_defaults_to_max(self):
        _write_cgroup(self.root, "user.slice/app-c.service")  # no files at all
        cg = cgroup.CGroup("app-c.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_memory_limit(), "max")
        self.assertEqual(cg.get_current_memory_usage(), "0")

    def test_percent_usage_zero_when_limit_is_zero(self):
        _write_cgroup(self.root, "user.slice/app-d.service", memory_max="0", memory_current="0")
        cg = cgroup.CGroup("app-d.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_percent_memory_usage(), 0.0)

    def test_percent_usage_zero_when_limit_is_max(self):
        _write_cgroup(self.root, "user.slice/app-e.service", memory_max="max", memory_current="500")
        cg = cgroup.CGroup("app-e.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_percent_memory_usage(), 0.0)


class CpuQuotumTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_cpu_quota_max(self):
        _write_cgroup(self.root, "user.slice/app-a.service", cpu_max="max 100000")
        cg = cgroup.CGroup("app-a.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_cpu_quotum(), "max")
        self.assertFalse(cg.has_cpu_quota())

    def test_cpu_quota_numeric(self):
        _write_cgroup(self.root, "user.slice/app-b.service", cpu_max="200000 100000")
        cg = cgroup.CGroup("app-b.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_cpu_quotum(), 200.0)
        self.assertTrue(cg.has_cpu_quota())

    def test_cpu_quota_missing_file_defaults_to_max(self):
        _write_cgroup(self.root, "user.slice/app-c.service")
        cg = cgroup.CGroup("app-c.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_cpu_quotum(), "max")


class CpuStatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_cpu_stat_parses_all_fields(self):
        stat = {
            "usage_usec": "212910650", "user_usec": "141909552", "system_usec": "71001097",
            "nice_usec": "0", "nr_periods": "196783", "nr_throttled": "311",
            "throttled_usec": "89540730", "nr_bursts": "0", "burst_usec": "0",
        }
        _write_cgroup(self.root, "user.slice/app-a.service", cpu_stat=stat)
        cg = cgroup.CGroup("app-a.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_cpu_stat(), stat)

    def test_cpu_stat_missing_file_returns_empty_dict(self):
        _write_cgroup(self.root, "user.slice/app-b.service")
        cg = cgroup.CGroup("app-b.service", parent=cgroup.CGroup("user.slice"))
        self.assertEqual(cg.get_cpu_stat(), {})


class ShortNameTests(unittest.TestCase):
    """No sysfs needed -- get_short_name() only looks at .name."""

    def test_template_instance_strips_at_and_app_prefix(self):
        cg = cgroup.CGroup("app-firefox@abc123.service")
        self.assertEqual(cg.get_short_name(), "firefox")

    def test_escaped_dash_unescaped(self):
        cg = cgroup.CGroup("app-firefox\\x2desr@abc123.service")
        self.assertEqual(cg.get_short_name(), "firefox-esr")

    def test_app_prefix_stripped_without_instance(self):
        cg = cgroup.CGroup("app-standalone.service")
        self.assertEqual(cg.get_short_name(), "standalone.service")

    def test_no_at_no_app_prefix_unchanged(self):
        cg = cgroup.CGroup("syncthing.service")
        self.assertEqual(cg.get_short_name(), "syncthing.service")

    def test_at_without_app_prefix(self):
        cg = cgroup.CGroup("getty@tty1.service")
        self.assertEqual(cg.get_short_name(), "getty")


class CGroupTreeBuildAndTraversalTests(unittest.TestCase):
    """Builds a real multi-level fake sysfs tree and verifies build_tree()
    + _walk()/all_cgroups() perform a pre-order depth-first traversal in
    child-list (i.e. os.listdir) order.

    os.listdir() order isn't guaranteed by the filesystem, and
    get_sysfs_children() doesn't sort -- so to test the *traversal
    algorithm's* order-preserving property deterministically (rather
    than accidentally depending on filesystem listdir order, which is
    not part of the algorithm's contract), os.listdir is wrapped to
    return a fixed, known (sorted) order for the duration of this test.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # test-tree.slice/
        #   alpha.service/
        #     beta.service/      (nested two levels deep)
        #   gamma.service/
        _write_cgroup(self.root, "test-tree.slice/alpha.service/beta.service",
                      memory_max="max", cpu_max="max 100000")
        _write_cgroup(self.root, "test-tree.slice/gamma.service",
                      memory_max="max", cpu_max="max 100000")
        # alpha.service itself also needs the standard files.
        _write_cgroup(self.root, "test-tree.slice/alpha.service",
                      memory_max="max", cpu_max="max 100000")
        _write_cgroup(self.root, "test-tree.slice",
                      memory_max="max", cpu_max="max 100000")

        self._patches = [
            mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root)),
            mock.patch.object(cgroup.os, "listdir", side_effect=lambda path: sorted(_real_listdir(path))),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_build_tree_and_preorder_traversal_order(self):
        tree = cgroup.CGroupTree("test-tree.slice")
        names = [cg.name for cg in tree.all_cgroups()]
        self.assertEqual(names, ["test-tree.slice", "alpha.service", "beta.service", "gamma.service"])

    def test_map_cgroups_applies_func_in_same_order(self):
        tree = cgroup.CGroupTree("test-tree.slice")
        self.assertEqual(
            tree.map_cgroups(lambda cg: cg.name),
            ["test-tree.slice", "alpha.service", "beta.service", "gamma.service"],
        )

    def test_filter_cgroups_preserves_traversal_order(self):
        tree = cgroup.CGroupTree("test-tree.slice")
        matches = tree.filter_cgroups(lambda cg: cg.name != "test-tree.slice")
        self.assertEqual([cg.name for cg in matches], ["alpha.service", "beta.service", "gamma.service"])


class LimitedCGroupFilteringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        _write_cgroup(self.root, "user.slice", memory_max="max", cpu_max="max 100000")
        # memory-limited only
        _write_cgroup(self.root, "user.slice/app-mem.service",
                      memory_max="100000000", cpu_max="max 100000")
        # cpu-limited only
        _write_cgroup(self.root, "user.slice/app-cpu.service",
                      memory_max="max", cpu_max="50000 100000")
        # both limited
        _write_cgroup(self.root, "user.slice/app-both.service",
                      memory_max="200000000", cpu_max="75000 100000")
        # neither
        _write_cgroup(self.root, "user.slice/app-free.service",
                      memory_max="max", cpu_max="max 100000")

        self._patches = [
            mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root)),
            mock.patch.object(cgroup.os, "listdir", side_effect=lambda path: sorted(_real_listdir(path))),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_get_memory_limited_cgroups(self):
        tree = cgroup.CGroupTree("user.slice")
        names = {cg.name for cg in tree.get_memory_limited_cgroups()}
        self.assertEqual(names, {"app-mem.service", "app-both.service"})

    def test_get_cpu_limited_cgroups(self):
        tree = cgroup.CGroupTree("user.slice")
        names = {cg.name for cg in tree.get_cpu_limited_cgroups()}
        self.assertEqual(names, {"app-cpu.service", "app-both.service"})


class CGroupCPUUsageHistoryTests(unittest.TestCase):
    """Direct delta-math tests (Python side); see tests/test_parity.py for
    the cross-language guard against plasmoid/contents/ui/logic.js."""

    def _history(self):
        return cgroup.CGroupCPUUsageHistory(cgroup.CGroup("dummy"))

    def test_first_sample_returns_zero_not_error(self):
        h = self._history()
        h.refresh({"usage_usec": "100", "nr_periods": "1", "nr_throttled": "0"})
        self.assertEqual(h.get_last_cpu_usage_percent(), 0.0)
        self.assertEqual(h.throttled_since_last(), 0)

    def test_normal_delta(self):
        h = self._history()
        h.refresh({"usage_usec": "1000000", "nr_periods": "100", "nr_throttled": "10"})
        h.refresh({"usage_usec": "1350000", "nr_periods": "105", "nr_throttled": "14"})
        self.assertAlmostEqual(h.get_last_cpu_usage_percent(), 70.0)
        self.assertEqual(h.throttled_since_last(), 4)

    def test_no_periods_elapsed_is_zero(self):
        h = self._history()
        h.refresh({"usage_usec": "100", "nr_periods": "50", "nr_throttled": "5"})
        h.refresh({"usage_usec": "100", "nr_periods": "50", "nr_throttled": "5"})
        self.assertEqual(h.get_last_cpu_usage_percent(), 0.0)
        self.assertEqual(h.throttled_since_last(), 0)

    def test_periods_counter_reset_folds_to_zero_percent(self):
        h = self._history()
        h.refresh({"usage_usec": "200", "nr_periods": "80", "nr_throttled": "20"})
        h.refresh({"usage_usec": "100", "nr_periods": "70", "nr_throttled": "12"})
        self.assertEqual(h.get_last_cpu_usage_percent(), 0.0)
        # throttled_since_last() clamps a negative diff (nr_throttled counter
        # reset on service restart) to 0, matching logic.js's throttledDelta().
        self.assertEqual(h.throttled_since_last(), 0)

    def test_history_trims_to_max_length(self):
        h = cgroup.CGroupCPUUsageHistory(cgroup.CGroup("dummy"), max_length=3)
        for i in range(5):
            h.refresh({"usage_usec": str(i), "nr_periods": str(i), "nr_throttled": str(i)})
        self.assertEqual(len(h.usage_history), 3)
        self.assertEqual(h.usage_history[0]["nr_periods"], "2")
        self.assertEqual(h.usage_history[-1]["nr_periods"], "4")


if __name__ == "__main__":
    unittest.main()
