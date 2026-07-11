"""Cross-language drift guard between ``plasmoid/contents/ui/logic.js``
(the Plasma widget's ES5 reimplementation) and the Python it must stay in
sync with (``humanize.naturalsize`` for byte formatting, and
``cgwatch.cgroup.CGroupCPUUsageHistory`` for the CPU-percent/throttled
delta math).

A ``humanize`` version bump, or an edit to either side's copy of the
math, could silently desync the widget's numbers from the TUI/CLI's.
This test evaluates the real ``logic.js`` under ``node`` (not a
reimplementation of its logic in Python) and compares its output
against the real ``cgwatch.cgroup``/``humanize`` code.

Both skip-guards below matter independently: the Debian build's pybuild
tree contains only the ``cgwatch`` package + ``tests/`` (no ``plasmoid/``,
no guaranteed ``node`` binary) -- see ``tests/test_jsonapi.py``'s
``CliShimExitCodeTests`` for the established precedent.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import humanize

import cgwatch.cgroup as cgroup

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGIC_JS = REPO_ROOT / "plasmoid" / "contents" / "ui" / "logic.js"

# --- humanBytes / humanize.naturalsize input set --------------------------

HUMAN_BYTES_INPUTS = [
    0, 1, 2, 999, 1000, 999999, 1000000,
    5 * 1000 ** 3,            # 5 GB
    2 * 1000 ** 4,            # 2 TB
    1234567890,               # ~1.2 GB, non-round
    2147483648,               # 2 GiB
    3298534883328,            # 3 TiB
]

# --- cpuPercentFrom / throttledDelta sample pairs --------------------------
#
# Values are strings, mirroring the real on-disk cpu.stat contents that
# CGroup.get_cpu_stat() returns (and that CGroupCPUUsageHistory.refresh()
# is fed in production).

CPU_CASES = {
    # A normal delta: 5 periods (=500000us) elapsed, 350000us of usage,
    # 4 more throttle events -- both sides must agree exactly (this is
    # the case that actually guards the shared PERIOD_USEC=100000
    # constant against drift).
    "normal": (
        {"usage_usec": "1000000", "nr_periods": "100", "nr_throttled": "10"},
        {"usage_usec": "1350000", "nr_periods": "105", "nr_throttled": "14"},
    ),
    # No periods elapsed since the last sample (e.g. an idle/no-quota
    # cgroup polled twice in the same period) -- both sides define this
    # as a real, verified zero (not a sentinel).
    "no_quota": (
        {"usage_usec": "100", "nr_periods": "50", "nr_throttled": "5"},
        {"usage_usec": "100", "nr_periods": "50", "nr_throttled": "5"},
    ),
    # A counter reset (nr_periods and nr_throttled both went backwards,
    # e.g. the service restarted between polls).
    "counter_reset": (
        {"usage_usec": "200", "nr_periods": "80", "nr_throttled": "20"},
        {"usage_usec": "100", "nr_periods": "70", "nr_throttled": "12"},
    ),
}


def _build_node_harness(tmp_dir: Path) -> Path:
    """Write a small node snippet that requires the real logic.js and
    dumps humanBytes/cpuPercentFrom/throttledDelta results as JSON.
    """
    script = """
'use strict';
var L = require(%(logic_path)s);
var inputs = %(inputs)s;
var cases = %(cases)s;
var out = { humanBytes: [], cpu: {} };
for (var i = 0; i < inputs.length; i++) {
    out.humanBytes.push(L.humanBytes(inputs[i]));
}
for (var name in cases) {
    var prev = cases[name][0];
    var cur = cases[name][1];
    out.cpu[name] = {
        two_sample_percent: L.cpuPercentFrom(prev, cur),
        two_sample_throttled: L.throttledDelta(prev, cur),
        first_sample_percent: L.cpuPercentFrom(null, cur),
        first_sample_throttled: L.throttledDelta(null, cur)
    };
}
console.log(JSON.stringify(out));
""" % {
        "logic_path": json.dumps(str(LOGIC_JS)),
        "inputs": json.dumps(HUMAN_BYTES_INPUTS),
        "cases": json.dumps(CPU_CASES),
    }
    path = tmp_dir / "parity_harness.js"
    path.write_text(script)
    return path


def _run_node_harness() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        harness = _build_node_harness(Path(tmp))
        cp = subprocess.run(
            ["node", str(harness)],
            capture_output=True, text=True, timeout=30, check=True,
        )
    return json.loads(cp.stdout)


def _python_cpu_history(prev, cur):
    """Build a CGroupCPUUsageHistory and feed it `prev` then `cur` (either
    may be None to mean "no such sample"), mirroring how
    CGroup.refresh_cpu_usage_history() feeds it real cpu.stat dicts.
    """
    history = cgroup.CGroupCPUUsageHistory(cgroup.CGroup("dummy"))
    if prev is not None:
        history.refresh(dict(prev))
    if cur is not None:
        history.refresh(dict(cur))
    return history


@unittest.skipUnless(
    LOGIC_JS.is_file(),
    "plasmoid/contents/ui/logic.js not co-located (e.g. isolated pybuild tree)",
)
@unittest.skipUnless(shutil.which("node"), "node binary not available in this environment")
class LogicJsParityTests(unittest.TestCase):
    """Compares the real logic.js (run under node) against the real
    Python it must stay in sync with."""

    @classmethod
    def setUpClass(cls):
        cls.node_result = _run_node_harness()

    def test_human_bytes_matches_humanize_naturalsize(self):
        js_values = self.node_result["humanBytes"]
        self.assertEqual(len(js_values), len(HUMAN_BYTES_INPUTS))
        for n, js_value in zip(HUMAN_BYTES_INPUTS, js_values):
            with self.subTest(n=n):
                self.assertEqual(js_value, humanize.naturalsize(n))

    def test_cpu_percent_and_throttled_normal_delta_match_exactly(self):
        """The one case that actually guards PERIOD_USEC drift: a normal
        two-sample delta must produce numerically identical output on
        both sides."""
        prev, cur = CPU_CASES["normal"]
        py_percent = _python_cpu_history(prev, cur).get_last_cpu_usage_percent()
        py_throttled = _python_cpu_history(prev, cur).throttled_since_last()
        js = self.node_result["cpu"]["normal"]
        self.assertEqual(js["two_sample_percent"], py_percent)
        self.assertEqual(js["two_sample_throttled"], py_throttled)

    def test_cpu_percent_and_throttled_no_quota_case_match_exactly(self):
        """No periods elapsed since the last sample -- both sides define
        this as a real, verified zero (not a sentinel)."""
        prev, cur = CPU_CASES["no_quota"]
        py_percent = _python_cpu_history(prev, cur).get_last_cpu_usage_percent()
        py_throttled = _python_cpu_history(prev, cur).throttled_since_last()
        js = self.node_result["cpu"]["no_quota"]
        self.assertEqual(js["two_sample_percent"], py_percent)
        self.assertEqual(py_percent, 0.0)
        self.assertEqual(js["two_sample_throttled"], py_throttled)
        self.assertEqual(py_throttled, 0)

    def test_cpu_percent_first_sample_sentinel_is_an_intentional_divergence(self):
        """First poll (no previous sample): Python's history has <2
        samples and folds this into a bland 0.0/0; logic.js deliberately
        returns null instead, so the UI can show "--" rather than a
        bogus percentage (see logic.js's own comment on cpuPercentFrom).
        This is NOT a drift bug -- lock in both sides' documented,
        differing behavior so a future change to either is deliberate.
        """
        _, cur = CPU_CASES["normal"]
        py_percent = _python_cpu_history(None, cur).get_last_cpu_usage_percent()
        py_throttled = _python_cpu_history(None, cur).throttled_since_last()
        js = self.node_result["cpu"]["normal"]
        self.assertEqual(py_percent, 0.0)
        self.assertIsNone(js["first_sample_percent"])
        self.assertEqual(py_throttled, 0)
        self.assertEqual(js["first_sample_throttled"], 0)

    def test_cpu_percent_counter_reset_sentinel_is_an_intentional_divergence(self):
        """A counter reset (nr_periods going backwards) is another case
        logic.js reports as null (no usable data) while Python's
        get_last_cpu_usage_percent folds it into 0.0 (usec_passed <= 0).
        Documented divergence, not drift -- see the same comment as
        above.
        """
        prev, cur = CPU_CASES["counter_reset"]
        py_percent = _python_cpu_history(prev, cur).get_last_cpu_usage_percent()
        js = self.node_result["cpu"]["counter_reset"]
        self.assertEqual(py_percent, 0.0)
        self.assertIsNone(js["two_sample_percent"])

    def test_throttled_delta_counter_reset_clamps_to_zero_on_both_sides(self):
        """When nr_throttled goes backwards (counter reset on service
        restart), both Python's throttled_since_last() and logic.js's
        throttledDelta() clamp the negative diff to 0 -- a negative
        "throttled since last" count would be meaningless. This guards
        that the two stay in sync.
        """
        prev, cur = CPU_CASES["counter_reset"]
        py_throttled = _python_cpu_history(prev, cur).throttled_since_last()
        js_throttled = self.node_result["cpu"]["counter_reset"]["two_sample_throttled"]
        self.assertEqual(py_throttled, 0)
        self.assertEqual(js_throttled, 0)


if __name__ == "__main__":
    unittest.main()
