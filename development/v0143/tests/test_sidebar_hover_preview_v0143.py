from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SidebarHoverPreviewTests(unittest.TestCase):
    def test_sidebar_destinations_are_buttons_not_browser_links(self):
        evolution = (ROOT / "ui_evolution.js").read_text(encoding="utf-8")
        product = (ROOT / "ui_productization.js").read_text(encoding="utf-8")
        orchestration = (ROOT / "ui_orchestration.js").read_text(encoding="utf-8")
        self.assertIn('`<button type="button" class="nav-link nav-route" data-nav="${key}">', evolution)
        self.assertIn('`<button type="button" class="nav-link nav-route" data-nav="${k}">', product)
        self.assertIn('`<button type="button" class="nav-link nav-route" data-nav="${key}">', orchestration)

    def test_sidebar_buttons_continue_to_update_hash_route(self):
        product = (ROOT / "ui_productization.js").read_text(encoding="utf-8")
        html = (ROOT / "ui_preview.html").read_text(encoding="utf-8")
        self.assertIn("document.addEventListener('click',event=>{const control=event.target.closest('#side-nav button[data-nav]')", product)
        self.assertIn("if(location.hash!==destination)location.hash=destination", product)
        self.assertIn("window.addEventListener('hashchange',navigate)", html)
        self.assertIn("node.setAttribute('aria-label',t(node.dataset.nav))", html)

    def test_navigation_button_reset_preserves_focus_treatment(self):
        css = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")
        self.assertIn(".nav-route {", css)
        self.assertIn("font: inherit", css)
        self.assertIn(":focus-visible", css)


if __name__ == "__main__":
    unittest.main()
