from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground, background):
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class UiRefinementR16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visual = (ROOT / "ui_visual.js").read_text(encoding="utf-8")
        cls.art = (ROOT / "ui_art_direction.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")
        cls.build = (ROOT / "scripts" / "build_windows_v0143.ps1").read_text(encoding="utf-8")

    def test_primary_buttons_meet_wcag_aa_in_both_themes(self):
        self.assertIn("--art-button-bg: #a8d9bf", self.css)
        self.assertIn("--art-button-ink: #17352b", self.css)
        self.assertIn("--art-button-bg: #286650", self.css)
        self.assertIn("--art-button-ink: #ffffff", self.css)
        self.assertGreaterEqual(contrast("#17352b", "#a8d9bf"), 4.5)
        self.assertGreaterEqual(contrast("#ffffff", "#286650"), 4.5)
        self.assertIn("background: var(--art-button-bg)", self.css)

    def test_quota_observation_defines_percentage_points_and_hides_weak_trends(self):
        self.assertIn("days.length<4", self.visual)
        self.assertIn("Fewer than 4 observed days", self.visual)
        self.assertIn("80% to 70% is a 10-point drop", self.visual)
        self.assertIn("额度变化 · 百分点", self.visual)
        self.assertIn("percentage points", self.visual)

    def test_forecast_does_not_label_missing_plans_as_zero_minutes(self):
        forecast = self.visual.split("forecastPage=()=>{", 1)[1].split("render=()=>{phaseRenderBase()", 1)[0]
        self.assertIn("hasPlannedWork?L(`${minutes} 分钟`,`${minutes} min`):L('尚无已保存安排'", forecast)

    def test_history_has_one_real_empty_state_and_a_truthful_activity_count(self):
        history = self.visual.split("productHistory=()=>{", 1)[1].split("function phaseRefineDOM", 1)[0]
        self.assertIn("const empty=`<div class=\"phase-history-empty\"", history)
        self.assertIn("href=\"#planner\"", history)
        self.assertNotIn("phaseEmpty(", history)
        self.assertIn("${events.length}", history)
        self.assertIn(".slice(0,40)", history)

    def test_planner_empty_advice_is_compact_and_does_not_assume_quota_entitlement(self):
        self.assertIn("保存安排后，在这里查看建议", self.art)
        self.assertIn("没有额度读数时，仍可保留时间安排", self.art)
        self.assertNotIn("r11-empty-orbit", self.art.split("productPlan = artPlanPage", 1)[0].split("productSignals", 1)[0])
        self.assertIn("you can keep a plan even without a reading.", self.art)

    def test_signal_actions_are_grouped_and_explain_missing_evidence_naturally(self):
        self.assertIn("r11-signal-actions", self.art)
        self.assertIn("目前没有足够可靠的样本，暂不提供恢复概率", self.art)
        self.assertIn(".r11-signal-actions { display: flex", self.css)

    def test_timezone_is_localized_and_not_repeated_in_the_planner_form(self):
        self.assertIn("function artTimezoneLabel(zone)", self.art)
        self.assertIn("timeZoneName: 'longGeneric'", self.art)
        self.assertIn("本机时间", self.art)
        self.assertIn("!form.querySelector('.r11-timezone')", self.visual)
        self.assertIn("artLocalDate(d.expectedStart)", self.visual)

    def test_planner_submission_retains_advanced_preferences_and_validates_time(self):
        interaction = (ROOT / "ui_interaction.js").read_text(encoding="utf-8")
        self.assertIn("'targetBoundaryPolicy','boundaryMinutes'", interaction)
        self.assertIn("if(!f.reportValidity())return", interaction)
        self.assertIn("if(p.start===p.end)", interaction)
        self.assertIn("submissionId:submission.id", interaction)

    def test_r19_build_and_version_preserve_earlier_previews(self):
        version = (ROOT / "packaging" / "version_info-v0143-r19.txt").read_text(encoding="utf-8")
        self.assertIn("plan-aware-v0143-r16", self.build)
        self.assertIn("plan-aware-v0143-r18", self.build)
        self.assertIn("plan-aware-v0143-r19", self.build)
        self.assertIn("plan-aware-v0143-r20", self.build)
        self.assertIn("plan-aware-v0143-r25", self.build)
        self.assertIn("QuotaLab/_internal/ui_interaction.js", self.build)
        self.assertIn("0.14.3.19", version)
        self.assertIn("v1.0.0", self.art)
        self.assertTrue((ROOT / 'packaging' / 'version_info-v0143-r15.txt').is_file())
        self.assertTrue((ROOT / 'packaging' / 'version_info-v0143-r16.txt').is_file())


if __name__ == "__main__":
    unittest.main()
