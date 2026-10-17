import unittest
from pathlib import Path

from app_paths import default_data_root
from orchestration_service import OrchestrationService
from usage_intelligence import capacity, select_quota_windows
from ordinary_planner import OrdinaryPlanner


class PlanAwareQuotaTests(unittest.TestCase):
    def test_synthetic_window_shapes_are_not_inferred_from_plan_labels(self):
        # These are intentionally cross-combined synthetic snapshots, not a claim about
        # any plan's entitlements. The account label must never select/fabricate a window.
        plan_labels = ("free", "go", "plus", "pro", "pro-5x", "pro-20x",
                       "pro-100", "pro-200", "pro-500")
        shapes = (
            ([{"window_duration_minutes": 60, "remaining_percent": 70, "slot": "primary"}], 60),
            ([{"window_duration_minutes": 300, "remaining_percent": 70, "slot": "primary"}], 300),
            ([{"window_duration_minutes": 1440, "remaining_percent": 70, "slot": "primary"}], 1440),
            ([{"window_duration_minutes": 10080, "remaining_percent": 70, "slot": "secondary"}], 10080),
            ([], None),
        )
        for plan in plan_labels:
            for windows, expected_duration in shapes:
                with self.subTest(plan=plan, expected_duration=expected_duration):
                    result = capacity({
                        "account": {"plan_type": plan},
                        "windows": windows,
                        "orchestration": {"quotaFreshness": "FRESH"},
                    }, {})
                    self.assertEqual(result["windowDurationMinutes"], expected_duration)
                    self.assertEqual(result["remainingPercent"], 70 if expected_duration else None)

    def test_plus_style_five_hour_window_is_preferred_when_reported(self):
        windows = [
            {"window_duration_minutes": 10080, "remaining_percent": 72},
            {"window_duration_minutes": 300, "remaining_percent": 41},
        ]
        primary, weekly = select_quota_windows(windows)
        self.assertEqual(primary["window_duration_minutes"], 300)
        self.assertEqual(weekly["window_duration_minutes"], 10080)

    def test_five_hour_window_wins_over_a_provider_marked_long_window(self):
        windows = [
            {"window_duration_minutes": 43200, "remaining_percent": 87, "slot": "primary"},
            {"window_duration_minutes": 300, "remaining_percent": 42, "slot": "secondary"},
            {"window_duration_minutes": 10080, "remaining_percent": 75, "slot": "secondary"},
        ]
        primary, weekly = select_quota_windows(windows)
        self.assertEqual(primary["window_duration_minutes"], 300)
        self.assertEqual(primary["remaining_percent"], 42)
        self.assertEqual(weekly["window_duration_minutes"], 10080)

    def test_non_five_hour_plan_uses_shortest_reported_nonweekly_window(self):
        windows = [
            {"window_duration_minutes": 10080, "remaining_percent": 80, "slot": "secondary"},
            {"window_duration_minutes": 240, "remaining_percent": 55, "slot": "primary"},
            {"window_duration_minutes": 60, "remaining_percent": 90, "slot": "secondary"},
        ]
        primary, weekly = select_quota_windows(windows)
        self.assertEqual(primary["window_duration_minutes"], 240)
        self.assertEqual(weekly["remaining_percent"], 80)

    def test_weekly_only_and_no_window_are_not_fabricated(self):
        primary, weekly = select_quota_windows([{"window_duration_minutes": 10080, "remaining_percent": 33}])
        self.assertEqual(primary["window_duration_minutes"], 10080)
        self.assertEqual(weekly["remaining_percent"], 33)
        self.assertEqual(select_quota_windows([]), (None, None))

    def test_multiple_primary_buckets_resolve_deterministically(self):
        windows = [
            {"window_duration_minutes": 240, "remaining_percent": 54, "slot": "primary"},
            {"window_duration_minutes": 60, "remaining_percent": 82, "slot": "primary"},
        ]
        primary, _ = select_quota_windows(windows)
        self.assertEqual(primary["window_duration_minutes"], 60)

    def test_capacity_reports_account_window_and_duration(self):
        snapshot = {
            "windows": [{"window_duration_minutes": 60, "remaining_percent": 64,
                         "resets_at": "2026-10-14T13:00:00Z", "limit_id": "codex", "slot": "primary"}],
            "orchestration": {"quotaFreshness": "FRESH"},
        }
        result = capacity(snapshot, {})
        self.assertEqual(result["windowDurationMinutes"], 60)
        self.assertEqual(result["remainingPercent"], 64)
        self.assertEqual(result["quotaWindows"][0]["windowDurationMinutes"], 60)

    def test_stale_or_empty_snapshot_never_exposes_stale_percentages(self):
        snapshot = {"windows": [{"window_duration_minutes": 300, "remaining_percent": 96}],
                    "orchestration": {"quotaFreshness": "STALE"}}
        result = capacity(snapshot, {})
        self.assertEqual(result["state"], "UNAVAILABLE")
        self.assertIsNone(result["remainingPercent"])
        self.assertEqual(result["quotaWindows"], [])
        empty = capacity({"windows": [], "orchestration": {"quotaFreshness": "FRESH"}}, {})
        self.assertIsNone(empty["windowDurationMinutes"])
        self.assertIsNone(empty["remainingPercent"])

    def test_capacity_projects_the_same_primary_window_as_planner(self):
        result = capacity({"windows": [
            {"window_duration_minutes": 43200, "remaining_percent": 87, "slot": "primary"},
            {"window_duration_minutes": 300, "remaining_percent": 42, "slot": "secondary"},
        ], "orchestration": {"quotaFreshness": "FRESH"}}, {})
        self.assertEqual(result["windowDurationMinutes"], 300)
        self.assertEqual(result["remainingPercent"], 42)

    def test_orchestration_projection_uses_reported_duration(self):
        snapshot = {"provider": {"id": "CODEX"}, "windows": [
            {"window_duration_minutes": 120, "remaining_percent": 51, "resets_at": "2026-10-14T12:00:00Z",
             "limit_id": "codex", "slot": "primary", "window_state": "ROLLING"},
        ]}
        current, state, weekly = OrchestrationService.usage(None, snapshot, "CODEX")
        self.assertEqual(current["windowDuration"], 120)
        self.assertEqual(current["remainingPercent"], 51)
        self.assertEqual(state["state"], "ROLLING")
        self.assertIsNone(weekly["remainingPercent"])
        self.assertEqual(current["quotaWindows"][0]["windowDuration"], 120)


class PlatformPathTests(unittest.TestCase):
    def test_macos_uses_application_support(self):
        path = default_data_root(environment={}, home=Path("/Users/alex"), platform_name="darwin")
        self.assertEqual(path, Path("/Users/alex/Library/Application Support/QuotaLab-dev-v0143"))

    def test_windows_keeps_local_app_data_convention(self):
        path = default_data_root(environment={"LOCALAPPDATA": "C:/Users/alex/AppData/Local"},
                                 home=Path("C:/Users/alex"), platform_name="win32")
        self.assertEqual(path, Path("C:/Users/alex/AppData/Local/QuotaLab-dev-v0143"))

    def test_linux_uses_xdg_data_home(self):
        path = default_data_root(environment={"XDG_DATA_HOME": "/tmp/data"},
                                 home=Path("/home/alex"), platform_name="linux")
        self.assertEqual(path, Path("/tmp/data/QuotaLab-dev-v0143"))

    def test_data_root_isolated_from_v0142(self):
        path = default_data_root(environment={"LOCALAPPDATA": "C:/Users/alex/AppData/Local"},
                                 home=Path("C:/Users/alex"), platform_name="win32")
        self.assertNotEqual(path.name, "QuotaLab-dev-v014")

    def test_preview_uses_its_own_port_and_instance_family(self):
        import quota_lab
        self.assertEqual(quota_lab.DEFAULT_PORT, 18861)
        self.assertIn('Instance-v0143', (Path(__file__).resolve().parents[1] / 'quota_lab.py').read_text(encoding='utf-8'))


class ProductCopyTests(unittest.TestCase):
    def test_primary_pages_do_not_assume_plus_or_a_five_hour_window(self):
        root = Path(__file__).resolve().parents[1]
        visual = (root / "ui_visual.js").read_text(encoding="utf-8")
        evolution = (root / "ui_evolution.js").read_text(encoding="utf-8")
        product = (root / "ui_productization.js").read_text(encoding="utf-8")
        overview = visual.rsplit("productOverview=()=>{", 1)[-1].split("document.addEventListener('click'", 1)[0]
        self.assertNotIn("五小时窗口", overview)
        self.assertIn("productWindowHeading(duration)", overview)
        self.assertIn("No quota window was returned in this snapshot", overview)
        self.assertIn("quotaWindows", evolution)
        self.assertNotIn("plan_type==='PLUS'", product)
        self.assertIn("slot||'').toLowerCase()==='primary'", product)
        self.assertIn("Plus · 五小时额度", product)
        self.assertIn("Save a work period now; quota advice appears when a fresh reading is available.", product)
        self.assertIn("${days} 天额度", product)
        self.assertIn("latest?.status!=='UNAVAILABLE'", overview)
        self.assertIn("v0.14.3 · r31", (root / "ui_art_direction.js").read_text(encoding="utf-8"))

    def test_theme_defaults_to_os_preference_but_respects_saved_choice(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "ui_preview.html").read_text(encoding="utf-8")
        self.assertIn("window.matchMedia?.('(prefers-color-scheme: light)')", html)
        self.assertIn("savedTheme==='light'||savedTheme==='dark'?savedTheme", html)


class AdditionalWindowPlanningTests(unittest.TestCase):
    def setUp(self):
        self.now = 1792022400
        self.demand = {"id": "d", "provider": "CODEX", "expectedStart": "2026-10-15T10:00:00+08:00",
                       "expectedEnd": "2026-10-15T12:00:00+08:00", "intensity": "HEAVY", "priority": "MEDIUM"}
        self.quota = {"bucketId": "codex.primary.300", "windowDuration": 300, "remainingPercent": 74,
                      "resetAt": "2026-10-15T17:00:00+08:00", "quotaWindows": [
                          {"bucketId": "codex.primary.300", "windowDuration": 300, "remainingPercent": 74,
                           "resetAt": "2026-10-15T17:00:00+08:00"},
                          {"bucketId": "codex.secondary.43200", "windowDuration": 43200, "remainingPercent": 0,
                           "resetAt": "2026-11-01T00:00:00+08:00"},
                      ]}

    def plan(self, *, fresh=True, other_percent=0, other_reset="2026-11-01T00:00:00+08:00"):
        self.quota["quotaWindows"][1]["remainingPercent"] = other_percent
        self.quota["quotaWindows"][1]["resetAt"] = other_reset
        return OrdinaryPlanner().recommend(self.demand, self.quota, {"state": "ROLLING"},
            {"remainingPercent": 78}, {"status": "LEARNING"}, {"safetyBufferMinutes": 15}, self.now, fresh)

    def test_exhausted_reported_long_window_blocks_heavy_work(self):
        result = self.plan()
        self.assertEqual(result["productAction"], "DEFER_HEAVY_WORK")
        self.assertIn("ADDITIONAL_WINDOW_EXHAUSTED", result["risk"])
        self.assertEqual(result["quotaChecks"][0]["windowDurationMinutes"], 43200)

    def test_other_window_that_resets_before_work_does_not_block(self):
        result = self.plan(other_percent=0, other_reset="2026-10-15T09:00:00+08:00")
        self.assertNotEqual(result["productAction"], "DEFER_HEAVY_WORK")
        self.assertEqual(result["quotaChecks"][0]["status"], "AVAILABLE")

    def test_low_extra_window_is_a_conservative_heavy_work_constraint(self):
        result = self.plan(other_percent=8)
        self.assertEqual(result["productAction"], "DEFER_HEAVY_WORK")
        self.assertIn("ADDITIONAL_WINDOW_LOW", result["risk"])

    def test_stale_snapshot_does_not_drive_additional_window_decisions(self):
        result = self.plan(fresh=False)
        self.assertEqual(result["quotaChecks"], [])
        self.assertNotIn("ADDITIONAL_WINDOW_EXHAUSTED", result["risk"])


if __name__ == "__main__":
    unittest.main()
