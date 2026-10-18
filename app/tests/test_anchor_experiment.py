import csv
import io
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quota_lab
from quota_lab import EXPERIMENT_TIMEOUT_SECONDS, QuotaLabState, utc_now


def window(resets_at):
    return {
        "limit_id": "codex",
        "limit_name": "Codex",
        "slot": "primary",
        "window_duration_minutes": 300,
        "resets_at": resets_at,
        "used_percent": 1,
    }


class AnchorExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.patchers = [
            patch.object(quota_lab, "DATA_DIR", root / "data"),
            patch.object(quota_lab, "EVIDENCE_DIR", root / "evidence"),
            patch.object(quota_lab, "PLAN_FILE", root / "data/plan.json"),
            patch.object(quota_lab, "SCHEDULED_RUNS_FILE", root / "data/runs.jsonl"),
            patch.object(quota_lab, "RESET_EVENTS_FILE", root / "data/resets.jsonl"),
            patch.object(quota_lab, "DISPATCH_TESTS_FILE", root / "data/dispatch.jsonl"),
            patch.object(quota_lab, "WINDOW_STATE_FILE", root / "data/windows.jsonl"),
            patch.object(quota_lab, "REPORT_HTML_FILE", root / "evidence/report.html"),
            patch.object(quota_lab, "REPORT_SVG_FILE", root / "evidence/report.svg"),
        ]
        for item in self.patchers: item.start()

    def tearDown(self):
        for item in reversed(self.patchers): item.stop()
        self.temp.cleanup()

    def make_state(self):
        state = QuotaLabState(mock=True, debug=False, codex_version="test")
        state.state["experiments"] = []
        state.state["last_snapshot"] = utc_now()
        state.state["windows"] = [window(2_000_000_000)]
        return state

    def test_requires_manual_trigger_before_detection(self):
        state = self.make_state()
        state.start_experiment()
        state.state["windows"] = [window(2_000_018_000)]
        self.assertIsNone(state._detect_experiment_locked(utc_now()))
        self.assertEqual(state.state["active_experiment"]["status"], "PREPARED")

    def test_single_snapshot_near_five_hours_remains_unconfirmed(self):
        state = self.make_state()
        with patch("quota_lab.time.time", return_value=2_000_000_031):
            state.start_experiment()
            triggered = state.mark_experiment_triggered()
        state.state["windows"] = [window(triggered["trigger_epoch"] + 300 * 60 + 30)]
        result = state._detect_experiment_locked(utc_now())
        self.assertIsNone(result)
        self.assertIsNotNone(state.state["active_experiment"])
        self.assertNotEqual(state.state["active_experiment"].get("status"), "ANCHOR_CONFIRMED_BY_STATE_TRANSITION")

    def test_ignores_reset_changes_from_a_non_target_window(self):
        state = self.make_state()
        state.state["windows"].append({**window(2_100_000_000), "slot": "secondary", "window_duration_minutes": 10080})
        state.start_experiment()
        state.mark_experiment_triggered()
        state.state["windows"][1]["resets_at"] += 60
        result = state._detect_experiment_locked(utc_now())
        self.assertIsNone(result)
        self.assertEqual(state.state["active_experiment"]["observedResetAt"], 2_000_000_000)

    def test_triggered_experiment_times_out_and_is_exportable(self):
        state = self.make_state()
        state.start_experiment()
        triggered = state.mark_experiment_triggered()
        with patch("quota_lab.time.time", return_value=triggered["trigger_epoch"] + EXPERIMENT_TIMEOUT_SECONDS + 1), \
             patch.object(state, "_save_experiment"):
            public = state.public()
        self.assertIsNone(public["active_experiment"])
        self.assertEqual(public["experiments"][0]["status"], "TIMED OUT")
        rows = list(csv.DictReader(io.StringIO(state.experiments_csv())))
        self.assertEqual(rows[0]["status"], "TIMED OUT")
        self.assertEqual(rows[0]["id"], triggered["id"])


if __name__ == "__main__":
    unittest.main()
