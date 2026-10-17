"""Current visual and help flows, inheriting the isolated current-product fixture."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r24 import ProductR24BrowserTests
from playwright.sync_api import expect


class ProductR25BrowserTests(ProductR24BrowserTests):
    def test_plan_preview_updates_from_inputs_without_sending_requests(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        form = self.page.locator('#product-plan-form')
        form.locator('[name="start"]').fill('23:00')
        form.locator('[name="end"]').fill('01:00')
        expect(self.page.locator('[data-preview-duration]')).to_have_text('2 小时')
        expect(self.page.locator('[data-preview-period]')).to_contain_text('次日结束')
        self.assertFalse(any('/api/orchestration/demand' in url for url in self.posts))

    def test_guide_can_open_close_and_start_a_real_plan_flow(self):
        self.page.locator('.top-actions [data-usage-guide]').click()
        guide = self.page.locator('#ql-usage-guide')
        expect(guide).to_be_visible()
        expect(guide.locator('.ql-guide-steps li')).to_have_count(4)
        self.page.keyboard.press('Escape')
        expect(guide).not_to_be_visible()
        expect(self.page.locator('.ql-home-description [data-usage-guide]')).to_have_count(0)
        self.page.locator('.top-actions [data-usage-guide]').click()
        guide.locator('[data-guide-route="planner"]').click()
        expect(guide).not_to_be_visible()
        expect(self.page.locator('#product-plan-form')).to_be_visible()
        self.page.locator('#lang-en').click()
        self.page.locator('.top-actions [data-usage-guide]').click()
        expect(guide).to_contain_text('Choose when you will work')


if __name__ == '__main__':
    unittest.main()
