import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import quota_lab
from quota_lab import LiveMonitor, QuotaLabState, UsagePlanner


class UsagePlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            patch.object(quota_lab, "ROOT", root),
            patch.object(quota_lab, "SETTINGS_FILE", root / "settings.json"),
            patch.object(quota_lab, "DATA_DIR", root),
            patch.object(quota_lab, "EVIDENCE_DIR", root / "evidence"),
            patch.object(quota_lab, "PLAN_FILE", root / "usage_plan.json"),
            patch.object(quota_lab, "SCHEDULED_RUNS_FILE", root / "scheduled_runs.jsonl"),
            patch.object(quota_lab, "RESET_EVENTS_FILE", root / "reset_events.jsonl"),
            patch.object(quota_lab, "HISTORY_FILE", root / "quota_history.jsonl"),
            patch.object(quota_lab, "WINDOW_STATE_FILE", root / "window_state_observations.jsonl"),
            patch.object(quota_lab, "REPORT_HTML_FILE", root / "evidence" / "anchor_report.html"),
            patch.object(quota_lab, "REPORT_SVG_FILE", root / "evidence" / "anchor_results.svg"),
        ]
        for item in self.patchers:
            item.start()
        self.state = QuotaLabState(mock=True, debug=False, codex_version="test")
        self.state.state["windows"] = [{
            "limit_id": "codex", "slot": "primary", "window_duration_minutes": 300,
            "used_percent": 10, "remaining_percent": 90, "resets_at": 2_000_000_000,
        }]

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def test_plan_defaults_off_and_validates_times(self):
        self.assertFalse(self.state.public()["usage_plan"]["enabled"])
        plan = self.state.save_plan({"enabled": True, "auto_anchor": False, "times": ["18:00", "06:00"], "max_daily_runs": 2})
        self.assertEqual(plan["times"], ["06:00", "18:00"])
        self.assertFalse(plan["auto_anchor"])
        with self.assertRaises(ValueError):
            self.state.save_plan({"enabled": True, "times": ["25:00"], "max_daily_runs": 2})

    def test_high_intensity_session_plans_midpoint_minus_five_hours(self):
        start = (datetime.now() + timedelta(hours=6)).replace(second=0, microsecond=0)
        end = start + timedelta(hours=1)
        plan = self.state.plan_intensity_sessions({"sessions": [{
            "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes"),
        }]})
        expected_reset = start + timedelta(minutes=30)
        expected_anchor = expected_reset - timedelta(hours=5)
        session = plan["intensity_sessions"][0]
        self.assertEqual(session["target_reset"], expected_reset.isoformat(timespec="minutes"))
        self.assertEqual(session["anchor_time"], expected_anchor.isoformat(timespec="minutes"))
        self.assertEqual(plan["campaign_slots"], [expected_anchor.isoformat(timespec="minutes")])
        self.assertEqual(plan["planning_strategy"], "session_midpoint_minus_5_hours")
        self.assertTrue(plan["enabled"])
        self.assertTrue(plan["auto_anchor"])
        self.assertEqual(plan["target_anchor_runs"], 1)

    def test_high_intensity_session_rejects_invalid_or_past_anchor(self):
        now = datetime.now().replace(second=0, microsecond=0)
        with self.assertRaises(ValueError):
            self.state.plan_intensity_sessions({"sessions": [{
                "start": now.isoformat(timespec="minutes"),
                "end": (now + timedelta(minutes=10)).isoformat(timespec="minutes"),
            }]})
        with self.assertRaises(ValueError):
            self.state.plan_intensity_sessions({"sessions": [{
                "start": (now + timedelta(hours=2)).isoformat(timespec="minutes"),
                "end": (now + timedelta(hours=3)).isoformat(timespec="minutes"),
            }]})

    def test_reset_change_is_recorded(self):
        with patch.object(self.state, "_write_runtime_evidence"):
            self.state.update_limits({"rateLimits": {"limitId": "codex", "primary": {
                "usedPercent": 11, "windowDurationMins": 300, "resetsAt": 2_000_000_100,
            }}}, "notification")
        event = self.state.public()["reset_events"][0]
        self.assertEqual(event["old_resets_at"], 2_000_000_000)
        self.assertEqual(event["new_resets_at"], 2_000_000_100)

    def test_mock_automatic_slot_creates_anchor_without_external_call(self):
        planner = UsagePlanner(self.state)
        plan = {"auto_anchor": True, "max_daily_runs": 1}
        planner._execute_slot("2026-01-01|06:00", "06:00", datetime(2026, 1, 1, 6, 0), plan, self.state.public())
        public = self.state.public()
        self.assertEqual(public["active_experiment"]["status"], "TRIGGERED")
        self.assertEqual(public["scheduled_runs"][0]["status"], "MOCK_TRIGGERED")

    def test_campaign_stops_after_target_and_report_is_generated(self):
        self.state.save_plan({"enabled": True, "auto_anchor": True, "times": ["06:00"], "max_daily_runs": 3, "target_anchor_runs": 3})
        self.state.consume_anchor_run()
        self.state.consume_anchor_run()
        final = self.state.consume_anchor_run()
        self.assertFalse(final["enabled"])
        self.assertEqual(final["remaining_anchor_runs"], 0)
        self.state.state["experiments"] = [{
            "id": "20260101_060000", "mode": "live", "status": "REALIGNMENT OBSERVED",
            "trigger_at": "2026-01-01T06:00:00Z", "new_resets_at": 2_000_018_030,
            "difference_from_expected_seconds": 30,
        }]
        self.state._write_report_artifacts()
        self.assertIn("Anchor and control observations", quota_lab.REPORT_SVG_FILE.read_text(encoding="utf-8"))
        self.assertIn("证据不足", quota_lab.REPORT_HTML_FILE.read_text(encoding="utf-8"))

    def test_cloud_validation_refuses_live_anchor_even_with_authenticated_client(self):
        calls = []
        class FakeClient:
            closed = type("Closed", (), {"is_set": lambda self: False})()
            def request(self, method, params):
                calls.append((method, params))
                if method == "thread/start":
                    return {"thread": {"id": "thread-test"}}
                return {"turn": {"id": "turn-test"}}
        monitor = LiveMonitor(self.state)
        monitor.client = FakeClient()
        with self.assertRaisesRegex(quota_lab.AppServerError, "CLOUD_VALIDATION_READ_ONLY"):
            monitor.trigger_anchor()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
