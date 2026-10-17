"""r26 manual task preparation, advice semantics and restrained motion."""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r25 import ProductR25BrowserTests
from playwright.sync_api import expect


class ProductR26BrowserTests(ProductR25BrowserTests):
    def test_manual_task_preparation_validates_and_never_sends_a_task(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        panel = self.page.locator('#ql-task-preparation')
        self.page.locator('[data-planner-mode="tasks"]').click()
        panel.locator('[data-prepare-task]').click()
        expect(panel.locator('#ql-task-feedback')).to_contain_text('请先填写')
        prompt = '<script>alert("not HTML")</script> Review tests without changing files'
        panel.locator('#ql-task-prompt').fill(prompt)
        panel.locator('#ql-task-date').fill('2020-01-01')
        panel.locator('[data-prepare-task]').click()
        expect(panel.locator('#ql-task-feedback')).to_contain_text('已过去')
        panel.locator('#ql-task-date').fill('2099-10-18')
        panel.locator('#ql-task-time').fill('09:30')
        panel.locator('#ql-task-repeat').select_option('weekdays')
        panel.locator('[data-prepare-task]').click()
        expect(panel.locator('#ql-task-result')).to_be_visible()
        output = panel.locator('#ql-task-instructions').input_value()
        for expected in (prompt, '2099-10-18', '09:30', '周一至周五', '不保证'):
            self.assertIn(expected, output)
        expect(panel.locator('#ql-task-feedback')).to_contain_text('尚未创建')
        self.page.evaluate('render()')
        expect(panel.locator('#ql-task-result')).to_be_visible()
        self.assertEqual(panel.locator('#ql-task-instructions').input_value(), output)
        self.page.locator('[data-planner-mode="work"]').click()
        self.page.locator('#product-plan-form [name="start"]').fill('11:00')
        self.page.evaluate('document.activeElement.blur(); render()')
        self.page.locator('[data-planner-mode="tasks"]').click()
        expect(panel.locator('#ql-task-time')).to_have_value('09:30')
        self.assertEqual(panel.locator('#ql-task-instructions').input_value(), output)
        panel.locator('[data-copy-prepared-task]').click()
        expect(panel.locator('#ql-task-feedback')).to_contain_text(re.compile('已复制|Ctrl\\+C'))
        panel.locator('#ql-task-time').fill('10:00')
        expect(panel.locator('#ql-task-result')).to_be_hidden()
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.state.orchestrator.data['demands']), 0)
        self.page.locator('#lang-en').click()
        expect(self.page.locator('#ql-task-preparation')).to_contain_text('Describe the task and choose a time')
        output_dir = Path(__file__).resolve().parents[1] / 'build' / 'r26-ui-review'
        output_dir.mkdir(parents=True, exist_ok=True)
        for width, theme in ((1440, 'light'), (375, 'dark')):
            self.page.set_viewport_size({'width': width, 'height': 960})
            self.page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width + 1)
            self.page.screenshot(path=str(output_dir / f'task-{width}-{theme}.png'), full_page=True)

    def test_current_ordinary_advice_is_not_confused_with_waiting(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        result = self.page.evaluate('''()=>{
          const r = structuredClone(productRecommendation());
          r.recommendationLifecycle.state = 'NO_LONGER_NEEDED';
          r.recommendationLifecycle.validUntil = new Date(Date.now()+60000).toISOString();
          r.productAction = 'USE_CURRENT_WINDOW';
          const current = productRecCard(r);
          r.recommendationLifecycle.state = 'EXPIRED';
          return {current, expired: productRecCard(r)};
        }''')
        self.assertIn('phase-work-period', result['current'])
        self.assertNotIn('这段安排已不再需要等待', result['current'])
        self.assertIn('建议需要更新', result['expired'])
        self.assertNotIn('phase-work-period', result['expired'])

    def test_illustration_is_decorative_and_respects_reduced_motion(self):
        self.page.emulate_media(reduced_motion='reduce')
        expect(self.page.locator('.ql-time-orbit')).to_have_attribute('aria-hidden', 'true')
        self.assertEqual(self.page.locator('.ql-orbit-trace').evaluate('(e)=>getComputedStyle(e).animationName'), 'none')
        expect(self.page.locator('[data-usage-guide]')).to_have_count(1)
        self.page.emulate_media(reduced_motion='no-preference')
        self.assertEqual(self.page.locator('.ql-orbit-trace').evaluate('(e)=>getComputedStyle(e).animationName'), 'ql-orbit-draw')


if __name__ == '__main__':
    unittest.main()
