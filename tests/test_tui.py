"""A small, time-boxed set of tests for cgwatch.tui using Textual's
App.run_test() harness.

Most of the TUI's actual decision logic (parse/validate/apply,
candidate filtering, ...) has moved into cgwatch.service and is
covered directly (and much more cheaply) by tests/test_service.py; this
file only locks in a couple of high-value, TUI-specific behaviors:
a metric label's rendered text/color, and that the edit/bump actions
correctly delegate to the (mocked) service layer rather than shelling
out themselves.

Uses the same fake-sysfs-tree + monkeypatched
cgwatch.cgroup.SYSFS_CGROUP_PATH pattern as the rest of the suite, plus
mocked cgwatch.service entry points, so no real cgroup/systemd state is
ever touched.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cgwatch.cgroup as cgroup
import cgwatch.tui as tui


def _write_cgroup(root: Path, name: str, *, memory_max="max", memory_current="0",
                   cpu_max="max 100000") -> None:
    d = root / "user.slice" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.max").write_text(memory_max)
    (d / "memory.current").write_text(memory_current)
    (d / "cpu.max").write_text(cpu_max)
    (d / "cpu.stat").write_text("usage_usec 0\nnr_periods 0\nnr_throttled 0\n")


class MemoryPercentLabelTests(unittest.IsolatedAsyncioTestCase):
    async def test_renders_expected_text_and_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 10% usage -> unambiguously in the "green-to-yellow" branch
            # of MyColors.percent_to_rgb (percent < 50).
            _write_cgroup(root, "app-firefox@abc.service",
                          memory_max="1000", memory_current="100")
            with mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(root)):
                app = tui.CGroupWatcherApp(config={})
                async with app.run_test() as pilot:
                    await pilot.pause()
                    label = next(iter(app.query(tui.MemoryPercent)))
                    self.assertEqual(str(label.renderable), "10.00%")
                    expected_color = tui.MyColors.percent_to_rgb(10.0)
                    self.assertEqual(label.styles.color.hex, expected_color.hex)


class CGroupLineBumpMemTests(unittest.IsolatedAsyncioTestCase):
    async def test_bump_up_computes_new_value_and_delegates_to_service_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cgroup(root, "app-firefox@abc.service",
                          memory_max=str(1000 * 1024 * 1024), memory_current="0")
            with mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(root)):
                fake_result = tui.svc.ApplyResult(
                    ok=True, wrote_dropin=True, reloaded=True, set_runtime=True, messages=[],
                )
                manager_instance = mock.MagicMock()
                manager_instance.apply.return_value = fake_result
                manager_class = mock.MagicMock(return_value=manager_instance)

                with mock.patch.object(tui.svc, "ServiceManager", manager_class):
                    app = tui.CGroupWatcherApp(config={})
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        line = next(iter(app.query(tui.CGroupLine)))
                        line.focus()
                        await pilot.pause()
                        await pilot.press("plus")
                        await pilot.pause()

                # 1000 MiB limit + 10% = 1100 MiB -- no subprocess/systemctl
                # call happens directly from the TUI; it all goes through
                # the (mocked) ServiceManager.
                manager_instance.apply.assert_called_once_with(
                    "app-firefox@abc.service", "1100M", None,
                )

class EditLimitsModalSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_delegates_to_service_apply_limits_and_dismisses_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cgroup(root, "app-firefox@abc.service",
                          memory_max=str(1000 * 1024 * 1024), memory_current="0")
            with mock.patch.object(cgroup, "SYSFS_CGROUP_PATH", str(root)):
                with mock.patch.object(tui.svc, "read_dropin", lambda unit: {}), \
                     mock.patch.object(tui.svc, "apply_limits") as mock_apply_limits:
                    mock_apply_limits.return_value = tui.svc.ResolveOutcome(
                        ok=True, unit="app-firefox@.service",
                        apply_result=tui.svc.ApplyResult(ok=True, messages=[]),
                    )
                    app = tui.CGroupWatcherApp(config={})
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        line = next(iter(app.query(tui.CGroupLine)))
                        line.focus()
                        await pilot.pause()
                        await pilot.press("enter")  # opens EditLimitsModal
                        await pilot.pause()
                        self.assertTrue(
                            any(isinstance(s, tui.EditLimitsModal) for s in app.screen_stack)
                        )
                        mem_input = app.query_one("#edit-mem", tui.Input)
                        mem_input.value = "2G"
                        await pilot.pause()
                        await pilot.click("#edit-save")
                        await pilot.pause()

                        # Modal dismissed itself (popped back to the
                        # default screen) after a successful apply.
                        self.assertFalse(
                            any(isinstance(s, tui.EditLimitsModal) for s in app.screen_stack)
                        )

                # Edited MemoryMax field ("2G") plus the untouched CPUQuota
                # field's prefill ("max", the unlimited cgroup's derived
                # value) both reach service.apply_limits -- no direct
                # write_dropin/systemctl call from the modal itself.
                mock_apply_limits.assert_called_once_with(
                    "app-firefox@abc.service", "2G", "max",
                    empty_message="nothing to change (both fields empty)",
                )


if __name__ == "__main__":
    unittest.main()
