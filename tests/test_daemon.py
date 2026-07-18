"""Unit tests for cgwatch.daemon (the notification daemon).

No real notify-send is ever spawned: daemon.subprocess.run is always
mocked/patched, and the daemon's own config loader (which would touch
~/.config/cgwatch) is never called for real either -- a fake
configparser.ConfigParser is injected via mock.patch.object instead.
"""

import configparser
import unittest
from unittest import mock

import humanize

import cgwatch.daemon as daemon


class FakeCGroup:
    """Minimal stand-in for cgwatch.cgroup.CGroup, exposing only what
    _maybe_notify()/daemon.main() actually touch."""

    def __init__(self, name, short_name=None, current="0", limit="max", percents=None):
        self.name = name
        self._short_name = short_name or name
        self._current = current
        self._limit = limit
        # Consumed one at a time by get_percent_memory_usage() -- only
        # used by the main()-loop integration tests below.
        self._percents = list(percents) if percents is not None else []

    def get_short_name(self):
        return self._short_name

    def get_current_memory_usage(self):
        return self._current

    def get_effective_memory_usage(self):
        # No page-cache concept modeled here -- these tests only exercise
        # _maybe_notify()/main()'s threshold/hysteresis logic, not
        # cache subtraction (that's cgwatch.cgroup's job, covered in
        # tests/test_cgroup.py). Effective == current for this fake.
        return int(self._current)

    def get_memory_limit(self):
        return self._limit

    def get_percent_memory_usage(self):
        return self._percents.pop(0)


class _FakeTree:
    def __init__(self, cgroups):
        self._cgroups = list(cgroups)

    def get_memory_limited_cgroups(self):
        return list(self._cgroups)

    def update_tree(self):
        pass


class _StopLoop(Exception):
    """Sentinel used to break out of daemon.main()'s infinite loop from
    within a patched time.sleep."""


def _make_raising_sleep(max_calls):
    state = {"n": 0}

    def _sleep(_seconds):
        state["n"] += 1
        if state["n"] >= max_calls:
            raise _StopLoop()

    return _sleep


def _make_config():
    cp = configparser.ConfigParser()
    cp.read_dict({
        "Thresholds": {"warning_percent": "80", "critical_percent": "90", "reset_hysteresis": "5"},
        "Timing": {
            "check_interval_sec": "2",
            # Large enough that the process-list refresh branch never
            # triggers during these short tests.
            "process_list_multiplier": "1000",
            "notification_timeout_ms": "15000",
        },
        "Look": {"myname": "CGWatcherd", "icon": "face-worried-symbolic.symbolic.png"},
    })
    return cp


class MaybeNotifyDirectTests(unittest.TestCase):
    """Direct unit tests of _maybe_notify, with send_notification mocked."""

    def setUp(self):
        self.cg = FakeCGroup("app-firefox@abc.service", short_name="firefox",
                              current=str(1024 * 1024 * 800), limit=str(1024 * 1024 * 1000))
        self._patch = mock.patch.object(daemon, "send_notification")
        self.mock_notify = self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_fires_when_over_hysteresis_window(self):
        last_notified = {self.cg.name: 0}
        daemon._maybe_notify(
            self.cg, 90.0, last_notified, 5,
            "Memory limit critical", "critical", "CGWatcherd", "icon.png", 15000,
        )
        self.mock_notify.assert_called_once()
        self.assertEqual(last_notified[self.cg.name], 90.0)

    def test_does_not_fire_within_hysteresis_window(self):
        last_notified = {self.cg.name: 90.0}
        # 90.0 is NOT > 90.0 + 5 -- must not re-fire.
        daemon._maybe_notify(
            self.cg, 90.0, last_notified, 5,
            "Memory limit critical", "critical", "CGWatcherd", "icon.png", 15000,
        )
        self.mock_notify.assert_not_called()
        self.assertEqual(last_notified[self.cg.name], 90.0)

    def test_fires_again_once_past_new_hysteresis_window(self):
        last_notified = {self.cg.name: 90.0}
        daemon._maybe_notify(
            self.cg, 96.0, last_notified, 5,
            "Memory limit critical", "critical", "CGWatcherd", "icon.png", 15000,
        )
        self.mock_notify.assert_called_once()
        self.assertEqual(last_notified[self.cg.name], 96.0)

    def test_message_text_and_urgency_for_critical(self):
        last_notified = {self.cg.name: 0}
        daemon._maybe_notify(
            self.cg, 92.5, last_notified, 5,
            "Memory limit critical", "critical", "CGWatcherd", "myicon.png", 15000,
        )
        args, kwargs = self.mock_notify.call_args
        title, body = args
        self.assertEqual(title, "Memory limit critical")
        expected_body = (
            f"firefox is using 92.5% of its memory limit "
            f"({humanize.naturalsize(self.cg.get_effective_memory_usage())} / "
            f"{humanize.naturalsize(self.cg.get_memory_limit())})"
        )
        self.assertEqual(body, expected_body)
        self.assertEqual(kwargs["urgency"], "critical")
        self.assertEqual(kwargs["app_name"], "CGWatcherd")
        self.assertEqual(kwargs["icon"], "myicon.png")
        self.assertEqual(kwargs["timeout"], 15000)

    def test_message_text_and_urgency_for_warning(self):
        last_notified = {self.cg.name: 0}
        daemon._maybe_notify(
            self.cg, 81.0, last_notified, 5,
            "Memory limit warning", "normal", "CGWatcherd", "myicon.png", 15000,
        )
        args, kwargs = self.mock_notify.call_args
        title, _body = args
        self.assertEqual(title, "Memory limit warning")
        self.assertEqual(kwargs["urgency"], "normal")


class SendNotificationNeverShellsOutForRealTests(unittest.TestCase):
    """Exercises the REAL send_notification() (not _maybe_notify-mocked)
    but with subprocess.run patched -- confirms the notify-send argv
    shape without ever spawning a real process."""

    def test_send_notification_invokes_notify_send_argv_via_mocked_subprocess(self):
        with mock.patch.object(daemon.subprocess, "run") as mock_run:
            daemon.send_notification(
                "Memory limit critical", "firefox is using 92% ...",
                timeout=15000, urgency="critical", app_name="CGWatcherd", icon="icon.png",
            )
        mock_run.assert_called_once()
        (argv,), kwargs = mock_run.call_args
        self.assertEqual(argv[0], "notify-send")
        self.assertIn("Memory limit critical", argv)
        self.assertIn("-u", argv)
        self.assertIn("critical", argv)
        self.assertIn("-a", argv)
        self.assertIn("CGWatcherd", argv)
        self.assertIn("--icon=icon.png", argv)
        self.assertEqual(kwargs.get("check"), False)

    def test_send_notification_swallows_exceptions_from_subprocess(self):
        with mock.patch.object(daemon.subprocess, "run", side_effect=OSError("no notify-send")):
            # Must not raise -- send_notification catches and logs.
            daemon.send_notification(
                "t", "b", timeout=1000, urgency="normal", app_name="a", icon="i",
            )


class MainLoopIntegrationTests(unittest.TestCase):
    """Drives the real daemon.main() loop (config/tree/sleep/notify all
    mocked) to lock in the reset branch's real effect: once a cgroup's
    percent drops below (warning - hysteresis), last_notified resets to
    0 so the next threshold crossing notifies again instead of being
    suppressed by stale hysteresis state.
    """

    def test_reset_branch_allows_renotification_after_dropping_and_reclimbing(self):
        cg = FakeCGroup(
            "app-firefox@abc.service", short_name="firefox",
            current="800", limit="1000",
            # iter1: critical (fires) -> iter2: still critical, same
            # percent (suppressed by hysteresis) -> iter3: drops well
            # below warning-hysteresis (resets last_notified to 0) ->
            # iter4: critical again at the SAME percent as iter1 (must
            # fire again now that the reset happened).
            percents=[95.0, 95.0, 40.0, 95.0],
        )
        fake_tree = _FakeTree([cg])
        cfg = _make_config()

        with mock.patch.object(daemon, "load_config", return_value=cfg), \
             mock.patch.object(daemon, "CGroupTree", return_value=fake_tree) as mock_tree_cls, \
             mock.patch.object(daemon, "send_notification") as mock_notify, \
             mock.patch.object(daemon.subprocess, "run") as mock_run, \
             mock.patch.object(daemon.time, "sleep", side_effect=_make_raising_sleep(4)):
            with self.assertRaises(_StopLoop):
                daemon.main()

        mock_tree_cls.assert_called_once_with("user.slice")
        # Exactly two notifications: the initial critical crossing (iter1)
        # and the re-crossing after the reset (iter4). The repeat at the
        # same percent in iter2 must be suppressed.
        self.assertEqual(mock_notify.call_count, 2)
        for call in mock_notify.call_args_list:
            args, kwargs = call
            title, _body = args
            self.assertEqual(title, "Memory limit critical")
            self.assertEqual(kwargs["urgency"], "critical")
        # No real notify-send process is ever spawned -- send_notification
        # itself was mocked out, so subprocess.run must never be reached.
        mock_run.assert_not_called()

    def test_warning_bucket_notification_uses_warning_title_and_normal_urgency(self):
        cg = FakeCGroup(
            "app-code@xyz.service", short_name="code",
            current="800", limit="1000",
            percents=[82.0],
        )
        fake_tree = _FakeTree([cg])
        cfg = _make_config()

        with mock.patch.object(daemon, "load_config", return_value=cfg), \
             mock.patch.object(daemon, "CGroupTree", return_value=fake_tree), \
             mock.patch.object(daemon, "send_notification") as mock_notify, \
             mock.patch.object(daemon.subprocess, "run") as mock_run, \
             mock.patch.object(daemon.time, "sleep", side_effect=_make_raising_sleep(1)):
            with self.assertRaises(_StopLoop):
                daemon.main()

        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        title, _body = args
        self.assertEqual(title, "Memory limit warning")
        self.assertEqual(kwargs["urgency"], "normal")
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
