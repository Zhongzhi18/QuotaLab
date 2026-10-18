"""Stable disclosure across actual polling/reconstruction and clear desktop delivery."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_product_browser_r30 import ProductR30BrowserTests
from playwright.sync_api import expect


class ProductR31BrowserTests(ProductR30BrowserTests):
    def test_task_advanced_stays_open_through_polling_language_and_navigation(self):
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        self.page.locator('[data-planner-mode="tasks"]').click()
        details=self.page.locator('.ql-task-advanced')
        details.locator('summary').click()
        details.locator('#ql-task-scope').fill('Keep this scope across refreshes')
        self.page.locator('h1').click()  # No focused input to suppress rebuilding.
        for _ in range(4):
            self.page.evaluate('refresh()')
            expect(details).to_have_attribute('open','')
            expect(details.locator('#ql-task-scope')).to_have_value('Keep this scope across refreshes')
        self.page.wait_for_timeout(6500)  # Cross real status polling, not only manual renders.
        expect(details).to_have_attribute('open','')
        self.page.locator('#lang-en').click()
        expect(details).to_have_attribute('open','')
        self.page.locator('[data-planner-mode="work"]').click()
        self.page.locator('[data-planner-mode="tasks"]').click()
        expect(details).to_have_attribute('open','')
        self.page.evaluate('location.hash="history"')
        expect(details).to_have_count(0)
        self.page.evaluate('location.hash="planner"')
        expect(details).to_have_attribute('open','')
        expect(details.locator('#ql-task-scope')).to_have_value('Keep this scope across refreshes')
        details.locator('summary').click()
        self.page.evaluate('refresh()')
        expect(details).not_to_have_attribute('open','')
        self.page.wait_for_timeout(100)
        self.page.evaluate('refresh()')
        expect(details).not_to_have_attribute('open','')

    def test_desktop_window_option_and_last_delivery_have_explicit_status(self):
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        expect(self.page.locator('[data-desktop-option="desktopWindow"]')).to_be_checked()
        expect(self.page.locator('[data-desktop-option="desktopWindow"]')).to_be_disabled()
        self.assertIn('桌面通道尚未启用',self.page.locator('[data-desktop-delivery-status]').inner_text())
        messages=self.page.evaluate('''()=>[
          desktopDeliveryText({enabled:true,desktopWindowVisible:true,lastDelivery:{at:new Date().toISOString(),status:'SUBMITTED'}}),
          desktopDeliveryText({enabled:true,lastDelivery:{at:new Date().toISOString(),status:'FAILED'}}),
          desktopDeliveryText({enabled:true,lastDelivery:{at:new Date().toISOString(),status:'SUBMITTED',desktopWindowVisible:false}})
        ]''')
        self.assertIn('独立桌面窗口已创建',messages[0])
        self.assertIn('后台投递失败',messages[1])
        self.assertIn('实际显示未确认',messages[2])


if __name__=='__main__':unittest.main()
