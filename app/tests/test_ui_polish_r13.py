from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UiPolishR13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visual = (ROOT / "ui_visual.js").read_text(encoding="utf-8")
        cls.art = (ROOT / "ui_art_direction.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")
        cls.build = (ROOT / "scripts" / "build_windows_v0143.ps1").read_text(encoding="utf-8")
        cls.overview = cls.visual.rsplit("productOverview=()=>{", 1)[-1].split("\n};", 1)[0]

    def test_overview_distinguishes_offline_expired_and_empty_snapshots(self):
        self.assertIn("temporarily unavailable", self.overview)
        self.assertIn("out of date", self.overview)
        self.assertIn("No quota window was returned in this snapshot", self.overview)
        self.assertIn("Check your connection to Codex", self.overview)

    def test_old_windows_are_not_rendered_when_snapshot_is_not_fresh(self):
        self.assertIn("featured=fresh?(plusPlan?windows.fiveHour:windows.primary):null", self.overview)
        self.assertIn("const weekly=fresh?windows.weekly:null", self.overview)
        self.assertIn("(fresh?windows.all:[])", self.overview)
        self.assertIn("Older readings are hidden", self.overview)

    def test_primary_plus_and_weekly_remain_visible_and_extras_are_progressive(self):
        self.assertIn("productWindowHeading(duration)", self.overview)
        self.assertIn("const weekly=fresh?windows.weekly:null", self.overview)
        self.assertIn('class="phase-extra-windows"', self.overview)
        self.assertIn("Other quota windows", self.overview)
        self.assertIn("does not replace the Plus 5-hour quota", self.overview)
        for tier in ("Free", "Go", "Pro"):
            self.assertNotIn(tier, self.overview)

    def test_refresh_action_has_pending_feedback_and_keeps_normal_motion_accessible(self):
        self.assertIn("Refreshing…", self.visual)
        self.assertIn("aria-busy", self.visual)
        self.assertIn("min-height: 44px", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)

    def test_candidate_has_a_new_non_overwriting_build_target(self):
        self.assertIn("plan-aware-v0143-r15", self.build)
        self.assertIn("v1.0.0", self.art)


if __name__ == "__main__":
    unittest.main()
