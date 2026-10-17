from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UiPolishR19Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visual = (ROOT / "ui_visual.js").read_text(encoding="utf-8")
        cls.art = (ROOT / "ui_art_direction.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")

    def test_overview_keeps_one_explanation_for_missing_primary_window(self):
        overview = self.visual.split("productOverview=()=>{", 1)[1].split("usagePage=()=>{", 1)[0]
        self.assertNotIn("phase-quota-basis", overview)
        self.assertIn("phase-extra-windows", overview)

    def test_planner_does_not_render_a_placeholder_for_empty_saved_plans(self):
        planner = self.art.split("function artPlanPage()", 1)[1].split("productPlan = artPlanPage", 1)[0]
        self.assertIn("const plans = upcoming.length ?", planner)
        self.assertIn(" : '';", planner)
        self.assertNotIn(">01</span>", planner)

    def test_signals_merge_the_two_empty_conditions_and_keep_refresh_action(self):
        signals = self.art.split("productSignals =", 1)[1].split("productForecast =", 1)[0]
        self.assertIn("r11-signal-tags", signals)
        self.assertIn("r11-signal-empty", signals)
        self.assertIn("r11-signal-actions", signals)

    def test_forecast_empty_state_replaces_the_empty_metrics(self):
        forecast = self.art.split("const artForecastBase = forecastPage;", 1)[1].split("function bindArtPlanForm()", 1)[0]
        self.assertIn("phase-forecast-empty-panel", forecast)
        self.assertIn("r11-forecast-empty", forecast)
        self.assertIn("#planner", forecast)
        self.assertIn("Number(d.plannedMinutes) > 0 || Number(d.heavyPlans) > 0", forecast)

    def test_usage_summary_names_window_and_observation_scope(self):
        usage = self.visual.split("usagePage=()=>{", 1)[1].split("productHistory=()=>{", 1)[0]
        self.assertIn("productWindowLabel(Number(u.quotaWindowDurationMinutes))", usage)
        self.assertIn("latestObservedDay", usage)
        self.assertIn("Percentage points express the difference between percentages", usage)

    def test_compact_layout_preserves_keyboard_focus_and_reduced_motion(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn(".phase-forecast-empty-panel", self.css)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", self.css)

    def test_r22_visual_motion_is_subtle_reduced_motion_safe_and_not_quota_data(self):
        self.assertIn("r22-dial-sweep", self.css)
        self.assertIn("r22-page-arrive", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        self.assertIn("r22-forecast-mark\" aria-hidden=\"true\"", self.art)
        self.assertIn("phaseIcon('clock')", self.art)
        self.assertNotIn("remainingPercent", self.art.split("r22-forecast-empty-copy", 1)[1].split("</div>", 1)[0])


if __name__ == "__main__":
    unittest.main()
