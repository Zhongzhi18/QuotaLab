from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))


class TrustFirstUiR14Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.visual = (ROOT / "ui_visual.js").read_text(encoding="utf-8")
        cls.product = (ROOT / "ui_productization.js").read_text(encoding="utf-8")
        cls.art = (ROOT / "ui_art_direction.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")
        cls.interaction = (ROOT / "ui_interaction.js").read_text(encoding="utf-8")
        cls.overview = cls.visual.rsplit("productOverview=()=>{", 1)[-1].split("window.addEventListener('click'", 1)[0]

    def test_plus_five_hour_reading_cannot_fall_back_to_another_window(self):
        self.assertIn("featured=fresh?(plusPlan?windows.fiveHour:windows.primary):null", self.overview)
        self.assertIn("missingPlusFiveHour=fresh&&plusPlan&&!windows.fiveHour", self.overview)
        self.assertIn("const resetAt=featured?.resets_at", self.overview)
        self.assertNotIn("featured?.resets_at||c.resetAt", self.overview)
        self.assertIn("does not replace the Plus 5-hour quota", self.overview)
        self.assertIn("This snapshot did not return the Plus 5-hour quota", self.product)

    def test_quota_status_has_its_own_timestamp_and_connection_is_not_called_live_data(self):
        self.assertIn("Account snapshot · Read at", self.overview)
        self.assertIn('data-fresh="${fresh?', self.overview)
        self.assertIn("function applyTrustChrome()", self.art)
        self.assertIn("Connection only; use the quota card for availability and last-updated time.", self.art)
        self.assertIn("current !== 'overview'", self.art)

    def test_settings_distinguish_local_planning_from_execution_and_translate_controls(self):
        self.assertIn("日常计划和提醒仍保存在本机，不会自动启动 Codex。", self.product)
        self.assertIn("真实任务执行保持关闭", self.product)
        self.assertIn("L('谨慎','Conservative')", self.product)
        self.assertIn("L('均衡','Balanced')", self.product)
        self.assertIn("L('积极','Aggressive')", self.product)
        self.assertIn("<h2>${L('研究模式','Research mode')}</h2>", self.product)

    def test_reminder_overlay_is_bounded_and_announced_politely(self):
        self.assertIn("max-height: min(42vh, 360px)", self.css)
        self.assertIn("width: calc(100vw - 24px)", self.css)
        self.assertIn('role="status" aria-live="polite" data-delivery-reminder', self.interaction)

    def test_primary_button_text_meets_wcag_aa_in_both_themes(self):
        pairs = (("#17352b", "#a8d9bf"), ("#ffffff", "#286650"))
        for foreground, background in pairs:
            values = sorted((luminance(foreground), luminance(background)), reverse=True)
            self.assertGreaterEqual((values[0] + 0.05) / (values[1] + 0.05), 4.5)

    def test_r14_and_r16_releases_are_preserved_while_current_art_targets_r31(self):
        build = (ROOT / "scripts" / "build_windows_v0143.ps1").read_text(encoding="utf-8")
        version = (ROOT / "packaging" / "version_info-v0143-r14.txt").read_text(encoding="utf-8")
        self.assertIn("plan-aware-v0143-r14", build)
        self.assertIn("0.14.3.14", version)
        self.assertIn("v1.0.0", self.art)
        # Source CI must not depend on ignored, machine-local historical binaries.
        self.assertIn('plan-aware-v0143-r16', build)
        self.assertTrue((ROOT / 'packaging' / 'version_info-v0143-r16.txt').is_file())


if __name__ == "__main__":
    unittest.main()
