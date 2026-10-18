"""Current product acceptance using isolated mock data and the real HTTP/UI path."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_productization_v010 as base
from playwright.sync_api import expect


class ProductR24BrowserTests(unittest.TestCase):
    setUpClass = classmethod(base.ProductBrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.ProductBrowserTests.tearDownClass.__func__)
    setUp = base.ProductBrowserTests.setUp
    tearDown = base.ProductBrowserTests.tearDown

    def test_sidebar_and_back_keep_routes_and_no_anchor_preview(self):
        self.page.locator('#side-nav [data-nav="planner"]').click()
        expect(self.page).to_have_url(self.url + '#planner')
        expect(self.page.locator('#product-plan-form')).to_be_visible()
        self.page.locator('#side-nav [data-nav="settings"]').click()
        expect(self.page).to_have_url(self.url + '#settings')
        self.page.go_back()
        expect(self.page).to_have_url(self.url + '#planner')
        expect(self.page.locator('#side-nav a[data-nav]')).to_have_count(0)
        expect(self.page.locator('#side-nav [aria-current="page"]')).to_have_attribute('data-nav', 'planner')

    def test_disabled_public_checks_are_not_reported_as_checked(self):
        self.state.orchestrator.data['preferences']['publicSignalFetchEnabled'] = False
        self.page.goto(self.url + '#signals', wait_until='networkidle')
        expect(self.page.locator('#page-content')).to_contain_text('公开信息读取未启用')
        expect(self.page.locator('[data-orch-action="signals-fetch"]')).to_be_disabled()
        self.assertFalse(any('/signals-fetch' in url for url in self.posts))

    def test_overnight_plan_saved_once_survives_reload(self):
        self.page.goto(self.url + '#planner', wait_until='networkidle')
        form = self.page.locator('#product-plan-form')
        form.locator('[name="title"]').fill('Mock overnight review')
        form.locator('[name="start"]').fill('23:00')
        form.locator('[name="end"]').fill('01:00')
        expect(form.locator('[data-phase-midnight-hint]')).to_be_visible()
        form.locator('[type="submit"]').click()
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('已保存')
        self.assertEqual(sum('/api/orchestration/demand' in url for url in self.posts), 1)
        self.page.reload(wait_until='networkidle')
        expect(self.page.locator('.r11-plan-list')).to_contain_text('Mock overnight review')
        self.assertEqual(len(self.state.orchestrator.data['demands']), 1)

    def test_common_widths_and_themes_have_no_horizontal_overflow(self):
        self.page.emulate_media(reduced_motion='reduce')
        for width in (375, 1024, 1280, 1600):
            self.page.set_viewport_size({'width': width, 'height': 900})
            for route in ('overview', 'planner', 'usage', 'forecast', 'signals', 'history', 'settings'):
                self.page.goto(self.url + '#' + route, wait_until='networkidle')
                for theme in ('light', 'dark'):
                    self.page.evaluate('(theme)=>{document.documentElement.dataset.theme=theme}', theme)
                    overflow = self.page.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().right>innerWidth+1).slice(0,10).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right}))")
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width + 1, (width, route, theme, overflow))
        output = Path(__file__).resolve().parents[1] / 'build' / 'r24-ui-review'
        output.mkdir(parents=True, exist_ok=True)
        self.page.set_viewport_size({'width': 1440, 'height': 960})
        for route in ('overview', 'usage', 'signals'):
            self.page.goto(self.url + '#' + route, wait_until='networkidle')
            if route == 'overview':
                reading = self.page.locator('.phase-overview-readout').bounding_box()
                advice = self.page.locator('.phase-overview-next > .phase-action-card').bounding_box()
                self.assertLessEqual(abs(reading['height'] - advice['height']), 1)
            self.page.screenshot(path=str(output / (route + '.png')), full_page=True)


if __name__ == '__main__':
    unittest.main()
