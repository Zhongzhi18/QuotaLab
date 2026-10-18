"""Content accuracy and a complete isolated user journey before prerelease."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r26 import ProductR26BrowserTests
from playwright.sync_api import expect


class ProductR27BrowserTests(ProductR26BrowserTests):
    def test_saved_advice_has_units_and_reminder_boundary(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        form = self.page.locator('#product-plan-form')
        form.locator('[name="title"]').fill('Release walkthrough')
        form.locator('[type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        expect(self.page.locator('.phase-card-meta')).to_contain_text('基于当前读数')
        select = self.page.locator('select[data-lead-for]')
        expect(select.locator('option').first).to_have_text('提前 5 分钟')
        self.assertEqual(select.input_value(), '5')
        expect(self.page.locator('.ql-reminder-boundary')).to_contain_text('不会自动执行任务')
        self.page.locator('[data-work-remind]').click()
        expect(self.page.locator('#interaction-feedback')).to_contain_text('提醒已设置')
        self.assertEqual(len(self.state.orchestrator.data['demands']), 1)
        self.page.goto(self.url + '#history', wait_until='networkidle')
        expect(self.page.locator('.phase-history-plan')).to_contain_text('Release walkthrough')
        expect(self.page.locator('.ql-raw-history')).to_be_visible()
        self.page.locator('.ql-raw-history summary').click()
        expect(self.page.locator('.ql-raw-history')).to_contain_text('不代表创建了多个计划')

    def test_history_groups_refresh_noise_without_deleting_original_events(self):
        self.page.goto(self.url + '#history', wait_until='networkidle')
        result = self.page.evaluate('''()=>{
          const o = orch();
          o.demands = [{id:'audit-plan',title:'One real plan',expectedStart:'2099-10-18T01:00:00Z',expectedEnd:'2099-10-18T02:00:00Z'}];
          o.recommendations = [{id:'audit-rec',demandId:'audit-plan'}];
          o.history = [];
          o.recommendationRevisionHistory = ['STALE','RECOMPUTING','SUPERSEDED','UPDATED','NO_LONGER_NEEDED'].map((state,i)=>({recommendationId:'audit-rec',state,at:`2099-10-17T01:00:0${i}Z`}));
          const original = JSON.stringify(o.recommendationRevisionHistory);
          const template = document.createElement('template'); template.innerHTML = productHistory();
          return {count:template.content.querySelectorAll('[data-history-event]').length,
            raw:template.content.querySelector('.ql-raw-history').textContent,
            text:template.content.querySelector('.phase-event').textContent,
            unchanged:original===JSON.stringify(o.recommendationRevisionHistory)};
        }''')
        self.assertEqual(result['count'], 1)
        self.assertIn('正在重新计算', result['raw'])
        self.assertIn('One real plan', result['text'])
        self.assertTrue(result['unchanged'])

    def test_started_plan_outcome_is_reported_and_survives_reload(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        # Advance only the isolated fixture's plan start; no clock or user data is changed.
        demand = self.state.orchestrator.data['demands'][0]
        demand['expectedStart'] = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.page.goto(self.url + '#usage', wait_until='networkidle')
        self.page.locator('.phase-usage-detail').last.locator('summary').click()
        form = self.page.locator('#evo-outcome-form')
        expect(form).to_be_visible()
        form.locator('[name="actualMinutes"]').fill('10')
        form.locator('button').click()
        expect(self.page.locator('#evo-feedback')).to_contain_text('已保存用户自报结果')
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('.phase-insight-stats article').first.locator('strong')).to_have_text('1')
        expect(self.page.locator('.phase-insight-stats')).to_contain_text('至少 5 条')
        self.page.locator('.phase-reading-definition summary').click()
        expect(self.page.locator('.phase-data-definition')).to_contain_text('不是当天剩余额度')
        output = Path(__file__).resolve().parents[1] / 'build' / 'r27-ui-review'
        output.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(output / 'usage-content.png'), full_page=True)


if __name__ == '__main__':
    unittest.main()
