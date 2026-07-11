"""Unit tests for cgwatch.service: the resolve/validate/apply decision
logic (resolve_and_apply/apply_limits) and the candidate-listing helpers
(limited_templates/candidate_service_names/candidate_services).

Follows the established pattern from tests/test_jsonapi.py: a fake
sysfs tree under a TemporaryDirectory plus a monkeypatched
cgwatch.cgroup.SYSFS_CGROUP_PATH, and service.* functions that would
otherwise shell out (list_running_services/get_description/
find_running_instance/unit_exists/ServiceManager) are stubbed so
nothing real is ever invoked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cgwatch.cgroup as cgroup
import cgwatch.service as service


def _write_cgroup(root: Path, name: str, *, memory_max="max", memory_current="0",
                   cpu_max="max 100000") -> None:
    d = root / "user.slice" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.max").write_text(memory_max)
    (d / "memory.current").write_text(memory_current)
    (d / "cpu.max").write_text(cpu_max)


class _ExplodingServiceManager:
    """Stand-in for service.ServiceManager that fails the test if
    constructed -- used to assert a short-circuit path never reaches
    the actual apply/unlimit machinery."""

    def __init__(self, *a, **kw):
        raise AssertionError("ServiceManager must not be constructed on this path")


def _ok_manager(expected_method="apply"):
    """A mock ServiceManager class whose .apply()/.unlimit() returns a
    successful ApplyResult, for exercising the success path without
    shelling out."""
    fake_result = service.ApplyResult(
        ok=True, wrote_dropin=True, reloaded=True, set_runtime=True, messages=[],
    )
    instance = mock.MagicMock()
    getattr(instance, expected_method).return_value = fake_result
    manager_class = mock.MagicMock(return_value=instance)
    return manager_class, instance


class ResolveAndApplyTests(unittest.TestCase):
    def test_service_suffix_appended_when_missing(self):
        manager_class, instance = _ok_manager()
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", manager_class):
            outcome = service.resolve_and_apply("myapp", "2G", None)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.unit, "myapp.service")
        instance.apply.assert_called_once_with("myapp.service", "2G", None)

    def test_already_suffixed_unit_unchanged(self):
        manager_class, instance = _ok_manager()
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", manager_class):
            outcome = service.resolve_and_apply("myapp.service", "2G", None)
        self.assertEqual(outcome.unit, "myapp.service")

    def test_edit_true_skips_unknown_unit_rejection(self):
        manager_class, instance = _ok_manager()
        with mock.patch.object(service, "find_running_instance", lambda unit: None), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", manager_class):
            outcome = service.resolve_and_apply("unknownsvc.service", "1G", None, edit=True)
        self.assertTrue(outcome.ok)
        instance.apply.assert_called_once_with("unknownsvc.service", "1G", None)

    def test_edit_false_rejects_unknown_unit(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: None), \
             mock.patch.object(service, "unit_exists", lambda unit: False), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("unknownsvc.service", "1G", None, edit=False)
        self.assertFalse(outcome.ok)
        self.assertIn("doesn't know unit", outcome.error)

    def test_blank_unit_rejected_before_anything_else(self):
        with mock.patch.object(service, "find_running_instance",
                                side_effect=AssertionError("must not be called")), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("", "2G", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "pick or type a unit name")

    def test_whitespace_only_unit_rejected(self):
        with mock.patch.object(service, "find_running_instance",
                                side_effect=AssertionError("must not be called")), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("   ", "2G", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "pick or type a unit name")

    def test_both_mem_and_cpu_none_short_circuits(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("myapp.service", None, None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "set at least one of MemoryMax / CPUQuota")

    def test_custom_empty_message_used(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply(
                "myapp.service", None, None, empty_message="nothing to change (both fields empty)",
            )
        self.assertEqual(outcome.error, "nothing to change (both fields empty)")

    def test_invalid_memory_value_error_prefix(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("myapp.service", "not-a-number", None)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error.startswith("MemoryMax: "))
        self.assertIn("invalid memory value", outcome.error)

    def test_invalid_cpu_quota_error_prefix(self):
        with mock.patch.object(service, "find_running_instance", lambda unit: "myapp.service"), \
             mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.resolve_and_apply("myapp.service", None, "not-valid")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error.startswith("CPUQuota: "))


class ApplyLimitsDirectTests(unittest.TestCase):
    """apply_limits() is resolve_and_apply's shared tail (parse -> both-
    None check -> ServiceManager().apply()) -- test it directly too,
    since it's also called straight from the TUI's CGModalScreen."""

    def test_both_none_uses_default_empty_message(self):
        with mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.apply_limits("myapp.service", None, None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "set at least one of MemoryMax / CPUQuota")

    def test_both_blank_strings_treated_as_none(self):
        with mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.apply_limits("myapp.service", "", "")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "set at least one of MemoryMax / CPUQuota")

    def test_memory_error_short_circuits_before_service_manager(self):
        with mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.apply_limits("myapp.service", "bogus", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "MemoryMax: invalid memory value")

    def test_cpu_error_short_circuits_before_service_manager(self):
        with mock.patch.object(service, "ServiceManager", _ExplodingServiceManager):
            outcome = service.apply_limits("myapp.service", None, "bogus")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, "CPUQuota: invalid CPU quota (use e.g. 200% or max)")

    def test_success_calls_service_manager_apply_with_normalized_values(self):
        manager_class, instance = _ok_manager()
        with mock.patch.object(service, "ServiceManager", manager_class):
            outcome = service.apply_limits("myapp.service", "2G", "200%")
        self.assertTrue(outcome.ok)
        instance.apply.assert_called_once_with("myapp.service", "2G", "200%")


class CandidateListingTests(unittest.TestCase):
    """limited_templates/candidate_service_names/candidate_services over
    a fake cgroup tree -- filtering (memory OR cpu limited excluded) and
    sort order (memory_current descending)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # Memory-limited only -> excluded from candidates.
        _write_cgroup(self.root, "app-mem@1.service", memory_max="100000000", memory_current="1000")
        # CPU-limited only -> excluded from candidates.
        _write_cgroup(self.root, "app-cpu@2.service", cpu_max="50000 100000", memory_current="2000")
        # Both limited -> excluded from candidates.
        _write_cgroup(self.root, "app-both@3.service",
                      memory_max="200000000", cpu_max="75000 100000", memory_current="3000")
        # Unlimited, running -> candidates, with distinct memory_current
        # values to check descending sort.
        _write_cgroup(self.root, "app-low@4.service", memory_current="500")
        _write_cgroup(self.root, "app-high@5.service", memory_current="90000")

        self._patches = [
            mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(self.root)),
            mock.patch.object(
                service, "list_running_services",
                lambda: [
                    "app-mem@1.service", "app-cpu@2.service", "app-both@3.service",
                    "app-low@4.service", "app-high@5.service",
                    "app-ghost@6.service",  # running, no matching cgroup dir
                ],
            ),
            mock.patch.object(service, "get_description", lambda name: f"Desc({name})"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _tree(self):
        return cgroup.CGroupTree("user.slice")

    def test_limited_templates_includes_memory_and_cpu_and_both(self):
        templates = service.limited_templates(self._tree())
        self.assertEqual(
            templates,
            {"app-mem@.service", "app-cpu@.service", "app-both@.service"},
        )

    def test_candidate_service_names_excludes_limited_sorted_by_memory_desc(self):
        names = service.candidate_service_names(self._tree())
        # app-ghost has no backing cgroup dir -> memory_current 0, sorts last.
        self.assertEqual(
            names,
            ["app-high@5.service", "app-low@4.service", "app-ghost@6.service"],
        )

    def test_candidate_service_names_accepts_precomputed_already_set(self):
        tree = self._tree()
        already = service.limited_templates(tree)
        names_precomputed = service.candidate_service_names(tree, already)
        names_default = service.candidate_service_names(tree)
        self.assertEqual(names_precomputed, names_default)

    def test_candidate_services_shape_and_sort(self):
        candidates = service.candidate_services(self._tree())
        self.assertEqual(
            [c["unit"] for c in candidates],
            ["app-high@5.service", "app-low@4.service", "app-ghost@6.service"],
        )
        self.assertEqual(candidates[0]["template"], "app-high@.service")
        self.assertEqual(candidates[0]["memory_current"], 90000)
        self.assertEqual(candidates[0]["description"], "Desc(app-high@5.service)")
        # Ghost has no matching cgroup dir -> memory_current falls back to 0.
        self.assertEqual(candidates[2]["memory_current"], 0)


if __name__ == "__main__":
    unittest.main()
