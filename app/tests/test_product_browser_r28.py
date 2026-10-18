"""Multi-plan selection regression from the r27 user acceptance screenshots."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r27 import ProductR27BrowserTests
from playwright.sync_api import expect


class ProductR28BrowserTests(ProductR27BrowserTests):
    def test_two_plans_save_select_remind_switch_reload_and_refresh(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        future = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(days=2)
        form = self.page.locator('#product-plan-form')
        form.locator('[name="title"]').fill('Old tomorrow plan')
        form.locator('[name="date"]').fill(future.strftime('%Y-%m-%d'))
        form.locator('[name="start"]').fill('09:00')
        form.locator('[name="end"]').fill('12:00')
        form.locator('[type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('Old tomorrow plan')
        old_id = self.page.locator('.r11-advice-wrap [data-for-demand]').get_attribute('data-for-demand')
        form.locator('[data-next-plan]').click()
        form.locator('[name="title"]').fill('New ten minute plan')
        form.locator('[name="date"]').fill((future-timedelta(days=1)).strftime('%Y-%m-%d'))
        form.locator('[name="start"]').fill('23:43')
        form.locator('[name="end"]').fill('23:53')
        form.locator('[name="intensity"]').select_option('LIGHT')
        form.locator('[type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('New ten minute plan')
        card = self.page.locator('.r11-advice-wrap [data-for-demand]')
        expect(card.locator('.phase-plan-title')).to_have_text('New ten minute plan')
        expect(card.locator('.phase-work-period')).to_contain_text('23:43')
        new_id = card.get_attribute('data-for-demand')
        self.assertNotEqual(new_id, old_id)
        with self.page.expect_response('**/api/orchestration/reminder') as response:
            card.locator('[data-work-remind]').click()
        self.assertEqual(response.value.status, 200)
        reminder = response.value.json()
        self.assertEqual(reminder['demandId'], new_id)
        self.assertTrue(reminder['remindAt'].endswith('15:38:00Z'))
        expect(self.page.locator('#interaction-feedback')).to_contain_text('New ten minute plan')
        self.page.reload(wait_until='networkidle')
        expect(card.locator('.phase-plan-title')).to_have_text('New ten minute plan')
        self.page.locator(f'[data-review-plan="{old_id}"]').click()
        expect(self.page.locator('.r11-advice-wrap .r11-section-heading')).to_contain_text('Old tomorrow plan')
        expect(self.page.locator(f'[data-review-plan="{old_id}"]')).to_have_attribute('aria-pressed', 'true')
        expect(self.page.locator('.r11-advice-wrap [data-for-demand="'+new_id+'"]')).to_have_count(0)
        self.page.locator(f'[data-review-plan="{new_id}"]').click()
        expect(card.locator('.phase-plan-title')).to_have_text('New ten minute plan')
        self.page.evaluate('document.activeElement.blur(); render()')
        expect(card.locator('.phase-plan-title')).to_have_text('New ten minute plan')
        expect(self.page.locator('.ql-selected-reminders')).to_contain_text('New ten minute plan')
        with self.page.expect_response('**/api/orchestration/reminder-dismiss') as cancelled:
            self.page.locator('.ql-selected-reminders [data-product-dismiss]').click()
        self.assertEqual(cancelled.value.status, 200)
        expect(self.page.locator('.ql-selected-reminders')).to_have_count(0)
        self.assertEqual(len(self.state.orchestrator.data['demands']), 2)

    def test_missing_selected_advice_never_substitutes_another_plan(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        self.page.evaluate('''()=>{
          const o=orch(), original=o.demands[0];
          o.demands.push({...original,id:'pending-plan',title:'Pending specific plan'});
          selectPlannerDemand('pending-plan');
        }''')
        expect(self.page.locator('.r11-advice-wrap')).to_contain_text('Pending specific plan')
        expect(self.page.locator('.r11-advice-wrap [data-work-remind]')).to_have_count(0)
        expect(self.page.locator('.r11-advice-wrap [data-for-demand]')).to_have_count(0)


if __name__ == '__main__':
    unittest.main()
