"""Visible task workbench and native-notification settings, using mock data only."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r28 import ProductR28BrowserTests
from playwright.sync_api import expect


class ProductR29BrowserTests(ProductR28BrowserTests):
    def test_task_workbench_templates_scope_output_and_keyboard_modes(self):
        self.page.goto(self.url+'#planner', wait_until='networkidle')
        self.page.locator('[data-planner-mode="work"]').focus()
        self.page.keyboard.press('ArrowRight')
        panel=self.page.locator('#ql-task-preparation')
        expect(panel).to_be_visible()
        expect(self.page.locator('#ql-work-panel')).to_be_hidden()
        panel.locator('.ql-task-advanced > summary').click()
        panel.locator('[data-task-template="research"]').click()
        expect(panel.locator('#ql-task-name')).to_have_value('资料整理')
        panel.locator('#ql-task-scope').fill('Only the supplied README; no credentials')
        panel.locator('#ql-task-output').fill('Five points with sources and gaps')
        panel.locator('#ql-task-date').fill('2099-10-18')
        panel.locator('[data-prepare-task]').click()
        output=panel.locator('#ql-task-instructions').input_value()
        for text in ('资料整理','Only the supplied README','Five points with sources','不修改文件'):
            self.assertIn(text,output)
        panel.locator('#ql-task-output').fill('Changed acceptance criteria')
        expect(panel.locator('#ql-task-result')).to_be_hidden()
        panel.locator('#ql-task-output').fill('')
        self.page.locator('[data-planner-mode="work"]').click()
        self.page.locator('[data-planner-mode="tasks"]').click()
        expect(panel.locator('#ql-task-output')).to_have_value('')
        self.assertEqual(self.posts,[])
        output_dir=Path(__file__).resolve().parents[1]/'build'/'r29-ui-review'
        output_dir.mkdir(parents=True,exist_ok=True)
        for language in ('zh','en'):
            self.page.locator('#lang-'+language).click()
            for width,theme in ((1440,'light'),(375,'dark')):
                self.page.set_viewport_size({'width':width,'height':1000})
                self.page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.page.screenshot(path=str(output_dir/f'workbench-{language}-{width}-{theme}.png'),full_page=True)

    def test_mock_notification_controls_and_cross_origin_are_safe(self):
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        expect(self.page.locator('[data-desktop-enable]')).to_be_disabled()
        expect(self.page.locator('#ql-desktop-settings')).to_contain_text('模拟验收不会发送真实通知')
        for action in ('settings','test'):
            response=self.page.request.post(self.url+'api/desktop-notifications/'+action,headers={'Origin':'https://evil.example'},data={})
            self.assertEqual(response.status,403)
        response=self.page.request.post(self.url+'api/desktop-notifications/test',data={})
        self.assertEqual(response.status,409)
        self.assertEqual(response.json()['error'],'ENABLE_DESKTOP_FIRST')
        self.assertIsNone(self.state.desktop_notifications.backend)


if __name__=='__main__':unittest.main()
