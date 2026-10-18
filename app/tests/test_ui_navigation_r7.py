from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVOLUTION = (ROOT / "ui_evolution.js").read_text(encoding="utf-8")
PRODUCT = (ROOT / "ui_productization.js").read_text(encoding="utf-8")
CSS = (ROOT / "styles" / "art-direction.css").read_text(encoding="utf-8")


class NavigationSimplificationTests(unittest.TestCase):
    def test_everyday_navigation_is_short_and_user_facing(self):
        self.assertIn("const evoMainNav=['overview','planner','history','settings']", EVOLUTION)
        self.assertIn("const evoInsightNav=['usage','forecast','signals']", EVOLUTION)
        self.assertIn("if(current==='updates')$('#side-nav [data-nav=\"settings\"]')?.setAttribute('aria-current','page')", EVOLUTION)

    def test_insights_are_grouped_under_native_disclosure(self):
        self.assertIn('class="nav-disclosure nav-insights"', EVOLUTION)
        self.assertIn('id="nav-insights-links"', EVOLUTION)
        self.assertIn("${evoInsightNav.map(evoNavLink).join('')}", EVOLUTION)
        self.assertIn("aria-controls=\"nav-insights-links\"", EVOLUTION)

    def test_research_tools_remain_opt_in_and_grouped(self):
        self.assertIn("const productResearchNav=['lab','raw-data','diagnostics','experiments','tasks']", PRODUCT)
        self.assertIn("productResearchNav.push('research')", EVOLUTION)
        self.assertIn("productResearchOn()?`<details class=\"nav-disclosure nav-research\"", EVOLUTION)
        for route in ("lab", "raw-data", "diagnostics", "experiments", "tasks", "research"):
            self.assertIn(route, EVOLUTION)

    def test_group_auto_opens_for_current_route(self):
        self.assertIn("insightWasOpen||evoInsightNav.includes(current)", EVOLUTION)
        self.assertIn("researchWasOpen||productResearchNav.includes(current)", EVOLUTION)

    def test_group_open_state_survives_navigation_rerender(self):
        self.assertIn("side.querySelector('#nav-insights')?.open===true", EVOLUTION)
        self.assertIn("side.querySelector('#nav-research')?.open===true", EVOLUTION)

    def test_internal_routes_remain_registered(self):
        self.assertIn("orchRoutes.push('usage','forecast','updates','research')", EVOLUTION)
        self.assertIn("productResearchNav", PRODUCT)

    def test_group_labels_are_bilingual_and_accessible(self):
        self.assertIn("L('洞察','Insights')", EVOLUTION)
        self.assertIn("L('研究工具','Research tools')", EVOLUTION)
        self.assertIn('aria-label=\"${insightLabel}\"', EVOLUTION)
        self.assertIn('aria-label=\"${researchLabel}\"', EVOLUTION)

    def test_disclosure_uses_native_summary_without_custom_click_logic(self):
        self.assertIn("<summary class=\"nav-link\"", EVOLUTION)
        self.assertNotIn("data-nav-disclosure-click", EVOLUTION)
        self.assertIn("aria-controls=\"nav-research-links\"", EVOLUTION)

    def test_navigation_builder_does_not_mutate_research_records(self):
        navigation = EVOLUTION.split("productNavigation=()=>{", 1)[1].split("const evoFields=", 1)[0]
        self.assertIn("productResearchNav.map(evoNavLink)", navigation)
        self.assertNotIn("post(", navigation)
        self.assertNotIn("classification=", navigation)

    def test_sidebar_menu_can_scroll_when_secondary_items_expand(self):
        self.assertIn("#side-nav {", CSS)
        self.assertIn("max-height: calc(100vh - 185px)", CSS)
        self.assertIn("overflow-y: auto", CSS)

    def test_keyboard_focus_is_visible_on_interactive_controls(self):
        self.assertIn(":focus-visible", CSS)
        self.assertIn("outline: 2px solid var(--art-accent)", CSS)
        self.assertIn("outline-offset: 3px", CSS)

    def test_reduced_motion_disables_new_navigation_and_press_transitions(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        # Multiple component-specific reduced-motion blocks are intentional.
        reduced = '\n'.join(part.split('\n}', 1)[0] for part in CSS.split("@media (prefers-reduced-motion: reduce)")[1:])
        self.assertIn(".nav-disclosure > summary", reduced)
        self.assertIn("transition: none !important", reduced)
        self.assertIn("transform: none", reduced)

    def test_disabled_controls_are_visibly_unavailable(self):
        self.assertIn(".button:disabled", CSS)
        self.assertIn("cursor: not-allowed", CSS)
        self.assertIn("opacity: .55", CSS)

    def test_revision_marker_is_current_candidate(self):
        art = (ROOT / "ui_art_direction.js").read_text(encoding="utf-8")
        self.assertIn("v1.0.0", art)


if __name__ == "__main__":
    unittest.main()
