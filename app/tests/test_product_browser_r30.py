"""r30: concise preparation, explicit rejected saves, and reliable receipt retries."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r29 import ProductR29BrowserTests
from playwright.sync_api import expect


class ProductR30BrowserTests(ProductR29BrowserTests):
    def test_expired_draft_is_not_saved_then_corrected_plan_is_selected(self):
        self.page.goto(self.url+'#planner', wait_until='networkidle')
        form=self.page.locator('#product-plan-form')
        form.locator('[name="title"]').fill('New visible plan')
        form.locator('[name="date"]').fill('2020-10-17')
        form.locator('[type="submit"]').click()
        expect(form.locator('#ql-plan-time-error')).to_contain_text('尚未保存')
        self.page.evaluate('document.activeElement.blur(); lastPage=""; render()')
        expect(form.locator('#ql-plan-time-error')).to_contain_text('尚未保存')
        self.assertEqual(len(self.state.orchestrator.data['demands']),0)
        self.assertEqual(self.posts,[])
        expect(self.page.locator('[data-submit-feedback]')).to_have_count(0)
        form.locator('[name="date"]').fill('2099-10-18')
        expect(form.locator('#ql-plan-time-error')).to_have_count(0)
        form.locator('[type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存「New visible plan」')
        expect(self.page.locator('.r11-advice-wrap')).to_contain_text('New visible plan')
        expect(self.page.locator('.r11-plan-row')).to_contain_text('New visible plan')
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('.r11-advice-wrap')).to_contain_text('New visible plan')
        self.assertEqual(len(self.state.orchestrator.data['demands']),1)

    def test_server_rejection_is_not_reported_as_uncertain_network_failure(self):
        self.page.goto(self.url+'#planner', wait_until='networkidle')
        self.page.route('**/api/orchestration/demand',lambda route:route.fulfill(status=409,json={'error':'FUTURE_DEMAND_REQUIRED'}))
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('#ql-plan-time-error')).to_contain_text('开始时间已过去')
        expect(self.page.locator('[data-submit-feedback]')).to_have_count(0)
        self.assertEqual(len(self.state.orchestrator.data['demands']),0)

    def test_lost_save_response_reuses_receipt_without_duplicate_plan(self):
        self.page.goto(self.url+'#planner', wait_until='networkidle')
        attempts=[]
        def intercept(route):
            attempts.append(route.request.post_data_json['submissionId'])
            if len(attempts)==1:
                route.fetch()
                route.abort()
            else:route.continue_()
        self.page.route('**/api/orchestration/demand',intercept)
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('尚未确认保存结果')
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        self.assertEqual(attempts[0],attempts[1])
        self.assertEqual(len(self.state.orchestrator.data['demands']),1)

    def test_basic_task_preparation_hides_optional_fields_and_never_claims_sync(self):
        self.page.goto(self.url+'#planner', wait_until='networkidle')
        self.page.locator('[data-planner-mode="tasks"]').click()
        panel=self.page.locator('#ql-task-preparation')
        expect(panel.locator('#ql-task-name')).to_be_hidden()
        expect(panel.locator('#ql-task-scope')).to_be_hidden()
        expect(panel.locator('.ql-task-review')).to_have_count(0)
        expect(panel.locator('.ql-task-boundary')).to_contain_text('不创建或同步官方任务')
        panel.locator('#ql-task-prompt').fill('整理公开说明为五条要点，不修改文件')
        panel.locator('#ql-task-date').fill('2099-10-18')
        panel.locator('[data-prepare-task]').click()
        expect(panel.locator('#ql-task-result')).to_be_visible()
        expect(panel.locator('#ql-task-feedback')).to_contain_text('尚未创建')
        self.assertEqual(self.posts,[])
        panel.locator('[data-copy-prepared-task]').click()
        out=Path(__file__).resolve().parents[1]/'build'/'r30-ui-review'
        out.mkdir(parents=True,exist_ok=True)
        for language in ('zh','en'):
            self.page.locator('#lang-'+language).click()
            for width,theme in ((1440,'light'),(375,'dark')):
                self.page.set_viewport_size({'width':width,'height':960})
                self.page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.page.screenshot(path=str(out/f'tasks-{language}-{width}-{theme}.png'),full_page=True)

    def test_save_acknowledged_but_refresh_failed_is_still_saved(self):
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        self.page.evaluate('()=>{refresh=async()=>{throw new Error("simulated refresh failure")}}')
        self.page.locator('#product-plan-form [type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        expect(self.page.locator('#interaction-feedback')).to_contain_text('列表刷新失败')
        self.assertEqual(len(self.state.orchestrator.data['demands']),1)
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('.r11-plan-row')).to_have_count(1)


if __name__=='__main__':unittest.main()
