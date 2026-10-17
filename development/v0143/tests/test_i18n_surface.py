import unittest

from quota_lab import HTML


class I18nSurfaceTests(unittest.TestCase):
    def test_language_controls_and_catalogs_exist(self):
        self.assertIn('id="lang-zh"', HTML)
        self.assertIn('id="lang-en"', HTML)
        self.assertIn("const catalog={", HTML)
        self.assertIn("zh:{overview:'概览'", HTML)
        self.assertIn("en:{overview:'Overview'", HTML)

    def test_locale_is_persisted_and_document_language_updates(self):
        self.assertIn("localStorage.setItem('quotalab_locale',locale)", HTML)
        self.assertIn("document.documentElement.lang=locale==='zh'?'zh-CN':'en'", HTML)

    def test_dynamic_window_labels_are_translated(self):
        self.assertIn("function durationLabel(window)", HTML)
        self.assertIn("t('fiveHour')", HTML)
        self.assertIn("t('weekly')", HTML)

    def test_anchor_state_controls_are_translated(self):
        self.assertIn("t('prepareExperiment')", HTML)
        self.assertIn('data-experiment-action="trigger"', HTML)
        self.assertIn('data-experiment-action="cancel"', HTML)
        self.assertIn('href="/api/experiments/export.csv"', HTML)

    def test_usage_plan_and_reset_event_controls_are_bilingual(self):
        self.assertIn('id="plan-form"', HTML)
        self.assertIn('id="intensity-form"', HTML)
        self.assertIn('id="reset-events"', HTML)
        self.assertIn("planUsage:'规划使用时间'", HTML)
        self.assertIn("planUsage:'Plan usage'", HTML)
        self.assertIn("advancedSchedule:'高级调度'", HTML)
        self.assertIn("agent:'QuotaLab Agent'", HTML)
        self.assertIn("post('/api/plan'", HTML)
        self.assertIn("post('/api/execution/jobs'", HTML)


if __name__ == "__main__":
    unittest.main()
