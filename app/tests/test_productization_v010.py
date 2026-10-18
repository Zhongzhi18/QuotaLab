"""Capability-gated product tests: no real scheduling, model calls or account data."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from autonomous_validation import autonomous_a4_result, run_autonomous_a4
from recommendation_engine import RecommendationEngine, task_instruction
from orchestration_models import DEFAULT_PREFERENCES, UsageDemand, epoch, iso
from orchestration_service import OrchestrationService
from product_migration import migrate_v091


class ProductPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now=epoch('2026-10-06T00:00:00+08:00')
        self.demand=UsageDemand.from_payload({'expectedStart':'2026-10-07T09:00:00+08:00','expectedEnd':'2026-10-07T12:00:00+08:00'}).public()
        self.args=dict(demand=self.demand,currentQuota={'windowDuration':300,'remainingPercent':90,'bucketId':'codex.primary.300'},
                       windowState={'state':'ROLLING'},weeklyQuota={'remainingPercent':80},resetForecast={},executionCapabilities=[],
                       deviceAvailability={},userPreferences={**DEFAULT_PREFERENCES,'targetBoundaryPolicy':'EARLY_REFRESH'},now=self.now)
    def rec(self,**kw):return RecommendationEngine().recommend(**{**self.args,**kw})
    def test_early_balanced_late_custom(self):
        for policy,hour in [('EARLY_REFRESH',4.5),('BALANCED',5.5),('LATE_REFRESH',6.5),('CUSTOM',4.75)]:
            r=self.rec(userPreferences={**DEFAULT_PREFERENCES,'targetBoundaryPolicy':policy,'customBoundaryMinutes':45})
            self.assertEqual(r['productAction'],'SCHEDULE_OFFICIAL_TASK')
            self.assertEqual(epoch(r['recommended_time']),epoch('2026-10-07T00:00:00+08:00')+hour*3600)
            self.assertIsNone(r['probability']);self.assertFalse(r['modelTaskSent'])
    def test_existing_alignment(self):
        r=self.rec(windowState={'state':'FIXED'},currentQuota={**self.args['currentQuota'],'resetAt':self.demand['expectedStart']})
        self.assertEqual(r['productAction'],'USE_CURRENT_WINDOW');self.assertIsNone(r['recommended_time'])
    def test_current_window_with_safety_buffer(self):
        quota={**self.args['currentQuota'],'resetAt':epoch(self.demand['expectedEnd'])+3600}
        r=self.rec(windowState={'state':'FIXED'},currentQuota=quota,burn_rate={'status':'ESTIMATED','bucketId':'codex.primary.300','percentPerMinute':.1})
        self.assertEqual(r['productAction'],'USE_CURRENT_WINDOW');self.assertEqual(r['estimatedUsagePercent'],19.5)
    def test_mismatched_burn_bucket_not_used(self):
        self.assertIsNone(self.rec(burn_rate={'status':'ESTIMATED','bucketId':'other','percentPerMinute':.1})['estimatedUsagePercent'])
    def test_weekly_empty_defer(self):self.assertEqual(self.rec(weeklyQuota={'remainingPercent':0})['productAction'],'DEFER_HEAVY_WORK')
    def test_fixed_window_does_not_override_weekly_exhaustion(self):
        r=self.rec(weeklyQuota={'remainingPercent':0},windowState={'state':'FIXED'},
                   currentQuota={**self.args['currentQuota'],'resetAt':epoch(self.demand['expectedEnd'])+3600},
                   burn_rate={'status':'ESTIMATED','bucketId':'codex.primary.300','percentPerMinute':.1})
        self.assertEqual(r['productAction'],'DEFER_HEAVY_WORK')
    def test_low_priority_preserves_weekly(self):
        d={**self.demand,'priority':'LOW'};self.assertEqual(self.rec(demand=d,weeklyQuota={'remainingPercent':5})['productAction'],'DEFER_HEAVY_WORK')
    def test_usage_warning(self):
        r=self.rec(burn_rate={'status':'ESTIMATED','bucketId':'codex.primary.300','percentPerMinute':1})
        self.assertIn('ESTIMATED_USAGE_EXCEEDS_CURRENT',r['risk']);self.assertEqual(r['riskLevel'],'HIGH')
    def test_unknown_monitor(self):self.assertEqual(self.rec(windowState={'state':'UNKNOWN'})['productAction'],'MONITOR')
    def test_light_no_action(self):self.assertEqual(self.rec(demand={**self.demand,'intensity':'LIGHT'})['productAction'],'NO_ACTION')
    def test_signal_not_authorization(self):
        r=self.rec(resetForecast={'assessment':'HIGH_SIGNAL','freshness':1,'forecastTarget':'EXCEPTIONAL_WEEKLY_GLOBAL'})
        self.assertIn('PUBLIC_RESET_SIGNAL_CONSIDERED',r['reason']);self.assertFalse(r['automaticExecution'])
    def test_device_does_not_select_local(self):
        r=self.rec(deviceAvailability={'poweredOff':True},executionCapabilities=[{'backend':'LOCAL_ONLY','availability':'AVAILABLE'}])
        self.assertEqual(r['backend'],'MANUAL_HANDOFF');self.assertIn('LOCAL_REMINDER_UNAVAILABLE_OFFLINE',r['risk'])
    def test_minimal_prompt_no_title_or_private_notes(self):
        r=self.rec(demand={**self.demand,'title':'PRIVATE-TITLE','notes':'PRIVATE-NOTES'})
        text=task_instruction(r);self.assertNotIn('PRIVATE-',text);self.assertIn('Reply exactly OK',text)
    def test_invalid_buffer(self):
        with self.assertRaises(ValueError):self.rec(userPreferences={**DEFAULT_PREFERENCES,'safetyBufferMinutes':float('nan')})
    def test_blocked_is_valid_and_never_dispatches(self):
        adapter=Mock();r=run_autonomous_a4(adapter=adapter)
        self.assertEqual(r['status'],'BLOCKED_BY_SUPPORTED_INTERFACE');adapter.assert_not_called()
        self.assertFalse(r['officialTasksCreated']);self.assertFalse(r['sameBucketConfirmed'])
        self.assertEqual(len(r['requirements']),7)
    def test_migration_source_hashes_original_conclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            source,target=Path(temp)/'v09',Path(temp)/'v010';(source/'data').mkdir(parents=True)
            files={'anchor_experiment_A1.json':'{"classification":"OLD_A1"}', 'anchor_experiment_A3.json':'{"classification":"ANCHOR_CONFIRMED"}',
                   'orchestration.json':'{"cloudExperiments":[{"classification":"OLD_A4"}]}','window_state_observations.jsonl':'{"raw":true}\n'}
            for name,text in files.items():(source/'data'/name).write_text(text)
            result=migrate_v091(source,target)
            for name in files:
                self.assertEqual((source/'data'/name).read_bytes(),(target/'data'/name).read_bytes())
                self.assertEqual((source/'data'/name).read_bytes(),(target/'imports'/'v0.9.1'/'data'/name).read_bytes())
            self.assertEqual(result,migrate_v091(source,target));self.assertFalse(result['executionResumed'])
    def test_preferences_default_and_local_reminder_only(self):
        with tempfile.TemporaryDirectory() as temp:
            s=OrchestrationService(Path(temp),mock=True);self.assertFalse(s.public({})['researchMode'])
            s.data['preferences']['researchMode']=True  # explicitly test historical experimental reminders
            future=datetime.now(timezone.utc).timestamp()+3600;r=self.rec();r['recommended_time']=iso(future)
            r['demandEnd']=iso(future+10800)  # legitimate future reminder; not an already-ended fixture
            s.data['recommendations']=[r]
            snap={'app_server':'MOCK'}
            s.recommendation_lifecycle.register(r,s.recommendation_lifecycle.inputs(snap),None)
            a=s.handle('reminder',{'recommendationId':r['id']},snap)
            self.assertEqual(a,s.handle('reminder',{'recommendationId':r['id']},snap));self.assertEqual(a['delivery'],'LOCAL_APP_ONLY')
            self.assertFalse(a['modelTaskSent'])


class ProductBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import quota_lab
        from playwright.sync_api import sync_playwright
        cls.q=quota_lab;cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name)
        paths={'ROOT':'.','DATA_DIR':'data','EVIDENCE_DIR':'evidence','HISTORY_FILE':'data/history.jsonl','SETTINGS_FILE':'settings.json',
               'PLAN_FILE':'data/plan.json','SCHEDULED_RUNS_FILE':'data/runs.jsonl','RESET_EVENTS_FILE':'data/resets.jsonl',
               'WINDOW_STATE_FILE':'data/windows.jsonl','REPORT_HTML_FILE':'evidence/report.html','REPORT_SVG_FILE':'evidence/report.svg'}
        cls.patchers=[patch.object(quota_lab,k,root/v) for k,v in paths.items()]
        for p in cls.patchers:p.start()
        cls.state=quota_lab.QuotaLabState(mock=True,debug=False,codex_version='Mock')
        cls.state.settings['setup_completed']=True;cls.state.set_connection('MOCK',status='MOCK DATA')
        now=int(time.time())
        for offset in (-180,-120,-60,0):
            with patch.object(quota_lab,'utc_now',return_value=iso(now+offset)):
                cls.state.update_limits({'rateLimitsByLimitId':{'codex':{'limitId':'codex','primary':{'usedPercent':10,'windowDurationMins':300,'resetsAt':now+offset+18000},'secondary':{'usedPercent':15,'windowDurationMins':10080,'resetsAt':now+500000}}}},'mock-product-fixture')
        quota_lab.QuotaLabHandler.state=cls.state;cls.server=quota_lab.ExclusiveThreadingHTTPServer(('127.0.0.1',0),quota_lab.QuotaLabHandler)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.worker.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}/';cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.server.shutdown();cls.server.server_close()
        for p in reversed(cls.patchers):p.stop()
        cls.temp.cleanup()
    def setUp(self):
        self.state.orchestrator.data['preferences']['researchMode']=False
        self.state.orchestrator.recommendation_lifecycle.clock=time.time
        self.state.orchestrator.data['demands']=[];self.state.orchestrator.data['recommendations']=[]
        self.context=self.browser.new_context(viewport={'width':1440,'height':900},timezone_id='Asia/Shanghai',locale='zh-CN',permissions=['clipboard-read','clipboard-write'])
        self.page=self.context.new_page();self.errors=[];self.posts=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('request',lambda r:self.posts.append(r.url) if r.method=='POST' else None)
        self.page.goto(self.url,wait_until='networkidle')
    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertFalse(any('/api/execution/' in r or '/api/experiments/' in r for r in self.posts))
        self.context.close()
    def normal_text(self):
        import re
        text=self.page.locator('body').inner_text()
        self.assertIsNone(re.search(r'\bA[1-5]\b|slope|horizon|PRE WINDOW|POST WINDOW|evidence level|codex\.primary|HANDOFF_GENERATED|USER_REPORTED_EXECUTED',text,re.I))
    def test_three_steps_recommendation_and_copy(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click() # Main interaction 1
        self.page.locator('#product-plan-form button[type="submit"]').click() # Main interaction 2
        expect(self.page.locator('[data-work-remind]')).to_have_count(1)
        expect(self.page.locator('[data-product-open],[data-product-copy]')).to_have_count(0)
        self.assertEqual(sum('/api/orchestration/demand' in x for x in self.posts),1)
        rec=self.state.orchestrator.data['recommendations'][0]
        response=self.page.request.post(self.url+'api/orchestration/task-instruction',data={'recommendationId':rec['id']})
        self.assertEqual(response.status,409);self.assertNotIn('instruction',response.json())
        self.normal_text()
    def test_normal_navigation_and_deep_links(self):
        for lang in ('zh','en'):
            self.page.locator('#lang-'+lang).click()
            self.assertEqual(self.page.locator('#side-nav .nav-link').count(),8)
            self.assertEqual(self.page.locator('#side-nav [data-nav="lab"]').count(),0)
            for route in ('overview','planner','usage','forecast','updates','signals','history','settings'):
                self.page.goto(self.url+'#'+route,wait_until='networkidle');self.normal_text()
            for route in ('lab','diagnostics','experiments','raw-data','tasks','activity'):
                self.page.goto(self.url+'#'+route,wait_until='networkidle');self.normal_text()
                self.assertEqual(self.page.locator('#side-nav [aria-current="page"]').get_attribute('data-nav'),'overview')
    def test_research_gate_persistence_and_history(self):
        from playwright.sync_api import expect
        original=copy.deepcopy(self.state.state['experiments'])
        self.state.state['experiments']=[{'id':'A3','classification':'ANCHOR_CONFIRMED','mode':'live'}]
        try:
            self.page.goto(self.url+'#settings',wait_until='networkidle');self.page.locator('#product-research-mode').check()
            expect(self.page.locator('#side-nav [data-nav="lab"]')).to_have_count(1)
            self.page.goto(self.url+'#experiments',wait_until='networkidle')
            expect(self.page.locator('#page-content')).to_contain_text('ANCHOR_CONFIRMED')
            expect(self.page.locator('#page-content')).to_contain_text('BLOCKED_BY_SUPPORTED_INTERFACE')
            self.page.reload(wait_until='networkidle');expect(self.page.locator('#side-nav [data-nav="lab"]')).to_have_count(1)
            self.page.goto(self.url+'#settings',wait_until='networkidle');self.page.locator('#product-research-mode').uncheck()
            expect(self.page.locator('#side-nav [data-nav="lab"]')).to_have_count(0)
            self.assertEqual(self.state.state['experiments'][0]['classification'],'ANCHOR_CONFIRMED')
        finally:self.state.state['experiments']=original
    def test_mobile_no_overflow_bilingual(self):
        for lang in ('zh','en'):
            self.page.locator('#lang-'+lang).click();self.page.set_viewport_size({'width':390,'height':844})
            for route in ('overview','planner','signals','history','settings'):
                self.page.goto(self.url+'#'+route,wait_until='networkidle')
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390);self.normal_text()
                self.assertEqual(self.page.locator('#sidebar').evaluate("e=>getComputedStyle(e).visibility"),'hidden')
                self.assertGreaterEqual(self.page.locator('#page-content').bounding_box()['x'],0)
    def test_no_plan_and_a4_cannot_block_product(self):
        from playwright.sync_api import expect
        self.state.orchestrator.data['cloudExperiments']=[{'experimentId':'mock-A4','classification':'CLOUD_INCONCLUSIVE','status':'RECOMMENDED'}]
        self.page.reload(wait_until='networkidle');self.normal_text()
        self.page.locator('a.button[href="#planner"]').click();expect(self.page.locator('#product-plan-form')).to_be_visible()

    def test_expired_dom_cannot_copy_or_open(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click()
        self.page.locator('#product-plan-form button[type="submit"]').click()
        expect(self.page.locator('[data-work-remind]')).to_be_visible()
        self.page.evaluate("navigator.clipboard.writeText('UNCHANGED')")
        rec=self.state.orchestrator.data['recommendations'][0]
        self.state.orchestrator.recommendation_lifecycle.clock=lambda:epoch(rec['demandEnd'])+1
        self.page.on('dialog',lambda d:d.dismiss())
        self.page.locator('[data-work-remind]').click()
        expect(self.page.locator('[data-product-open]')).to_have_count(0)
        expect(self.page.locator('[data-product-copy]')).to_have_count(0)
        self.assertEqual(self.page.evaluate('navigator.clipboard.readText()'),'UNCHANGED')
        for action in ('task-instruction','action-check','reminder','handoff'):
            response=self.page.request.post(self.url+'api/orchestration/'+action,data={'recommendationId':rec['id'],'acknowledgeRisk':True})
            self.assertIn(response.status,(400,409))
            self.assertNotIn('instruction',response.json())

    def test_offline_removes_actions_even_without_poll(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click();self.page.locator('#product-plan-form button[type="submit"]').click()
        expect(self.page.locator('[data-work-remind]')).to_be_visible()
        self.context.set_offline(True)
        expect(self.page.locator('[data-product-open]')).to_have_count(0)
        expect(self.page.locator('[data-work-remind]')).to_have_count(0)

    def test_browser_zone_does_not_control_submission(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click()
        form=self.page.locator('#product-plan-form');form.locator('[name="date"]').fill('2030-01-02')
        form.locator('[name="start"]').fill('23:00');form.locator('[name="end"]').fill('01:00')
        form.locator('button[type="submit"]').click();expect(self.page.locator('.product-recommendation')).to_be_visible()
        d=self.state.orchestrator.data['demands'][0]
        self.assertEqual(d['expectedStart'],'2030-01-02T15:00:00Z');self.assertEqual(d['expectedEnd'],'2030-01-02T17:00:00Z')

    def test_editing_form_does_not_retain_superseded_buttons(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click();self.page.locator('#product-plan-form button[type="submit"]').click()
        expect(self.page.locator('[data-work-remind]')).to_be_visible()
        rec=self.state.orchestrator.data['recommendations'][0]
        self.page.locator('#product-plan-form [name="start"]').focus()
        self.state.orchestrator.recommendation_lifecycle.clock=lambda:time.time()+31
        response=self.page.request.post(self.url+'api/orchestration/recommend',data={'demandId':rec['demandId']})
        self.assertEqual(response.status,200)
        self.page.evaluate('refresh()')
        expect(self.page.locator('[data-product-open]')).to_have_count(0)
        expect(self.page.locator('[data-product-copy]')).to_have_count(0)

    def test_v012_freshness_explanation_and_doctor(self):
        from playwright.sync_api import expect
        self.page.locator('a.button[href="#planner"]').click();self.page.locator('#product-plan-form button[type="submit"]').click()
        expect(self.page.locator('#page-content')).to_contain_text('刚刚更新')
        expect(self.page.locator('#page-content')).to_contain_text('为什么这样建议')
        expect(self.page.locator('[data-lead-for]')).to_be_visible()
        self.page.goto(self.url+'#settings',wait_until='networkidle');self.page.locator('#release-doctor').click()
        expect(self.page.locator('#release-doctor-result')).to_contain_text('Quota API')

    def test_v012_stale_quota_natural_language(self):
        from playwright.sync_api import expect
        def stale(route):
            payload=route.fetch().json();payload['orchestration']['quotaFreshness']='STALE';route.fulfill(json=payload)
        self.page.route('**/api/status',stale);self.page.reload(wait_until='networkidle')
        expect(self.page.locator('#page-content')).to_contain_text('历史读数')

    def test_v012_onboarding_missing_auth_and_auto_continue(self):
        from playwright.sync_api import expect
        before=self.state.settings['setup_completed']
        try:
            self.state.settings['setup_completed']=False
            self.state.evolution.data['onboarding']['step']=0
            self.page.reload(wait_until='networkidle');expect(self.page.locator('.evo-steps li')).to_have_count(4)
            expect(self.page.locator('#page-content')).to_contain_text('Codex 尚未登录')
            self.state.set_account({'account':{'type':'chatgpt','planType':'plus'}})
            self.page.evaluate('refresh()');expect(self.page.locator('.evo-welcome')).to_be_visible()
            self.assertFalse(any('/api/setup/complete' in url for url in self.posts))
            for step in (1,2,3):self.page.locator('[data-onboard-step="'+str(step)+'"]').click()
            self.page.locator('[data-onboard-complete]').click();expect(self.page.locator('.evo-capacity-hero')).to_be_visible()
            self.page.goto(self.url+'#planner',wait_until='networkidle');expect(self.page.locator('#product-plan-form')).to_be_visible()
        finally:self.state.settings['setup_completed']=before;self.state.state['account']=None

    def test_v012_local_web_threats_rejected(self):
        for route in ('task-instruction','preferences','reminder'):
            response=self.page.request.post(self.url+'api/orchestration/'+route,headers={'Origin':'https://evil.example'},data={})
            self.assertEqual(response.status,403)
        response=self.page.request.get(self.url+'api/status',headers={'Host':'evil.example:80'})
        self.assertEqual(response.status,403)
        response=self.page.request.post(self.url+'api/shutdown',headers={'Content-Type':'text/plain'},data='{}')
        self.assertEqual(response.status,403)

    def test_v012_basic_accessibility_research_mobile(self):
        from playwright.sync_api import expect
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        for name in ('date','start','end','intensity'):self.assertGreater(self.page.locator('#product-plan-form [name="'+name+'"]').evaluate('e=>e.labels.length'),0)
        self.page.keyboard.press('Tab');self.assertTrue(self.page.evaluate('document.activeElement!==document.body'))
        self.state.orchestrator.data['preferences']['researchMode']=True
        self.page.set_viewport_size({'width':390,'height':844});self.page.emulate_media(reduced_motion='reduce')
        for lang in ('zh','en'):
            self.page.locator('#lang-'+lang).click()
            for route in ('lab','raw-data','diagnostics','experiments'):
                self.page.goto(self.url+'#'+route,wait_until='networkidle');expect(self.page.locator('main')).to_have_count(1)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
    def test_v012_contrast_focus_and_button_names(self):
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        result=self.page.evaluate("""()=>{const parse=s=>(s.match(/[\\d.]+/g)||[]).slice(0,3).map(Number);const lum=a=>a.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0);const body=getComputedStyle(document.body);const a=lum(parse(body.color)),b=lum(parse(body.backgroundColor));return {contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),unnamed:[...document.querySelectorAll('button')].filter(e=>e.offsetParent&&!e.disabled&&!((e.getAttribute('aria-label')||e.title||e.textContent).trim())).length}}""")
        self.assertGreaterEqual(result['contrast'],4.5);self.assertEqual(result['unnamed'],0)
        self.page.keyboard.press('Tab');self.assertGreater(float(self.page.evaluate('parseFloat(getComputedStyle(document.activeElement).outlineWidth)')),0)
    def test_v012_missing_detection_is_plain_language(self):
        from playwright.sync_api import expect
        def missing(route):
            p=route.fetch().json();p['setup']={'completed':False,'ready':False,'steps':{'detect':False,'account':False,'quota':False}};route.fulfill(json=p)
        self.page.route('**/api/status',missing);self.page.reload(wait_until='networkidle')
        expect(self.page.locator('#page-content')).to_contain_text('未检测到 Codex Desktop')
        self.assertNotIn('FileNotFoundError',self.page.locator('#page-content').inner_text())

    def test_v0131_reading_and_draft_survive_31_seconds_and_language(self):
        from playwright.sync_api import expect
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        f=self.page.locator('#product-plan-form');f.locator('[name="date"]').fill('2030-01-02');f.locator('[name="start"]').fill('14:07');f.locator('[name="end"]').fill('15:23')
        f.locator('button[type="submit"]').click();expect(self.page.locator('[data-submit-feedback]')).to_contain_text('14:07')
        detail=self.page.locator('.product-recommendation details').first;detail.locator('summary').click();expect(detail).to_have_attribute('open','')
        self.page.wait_for_timeout(31000)
        expect(self.page.locator('.product-recommendation details').first).to_have_attribute('open','');self.assertEqual(f.locator('[name="start"]').input_value(),'14:07')
        f.locator('[name="start"]').fill('16:05');self.page.locator('#lang-en').click();self.assertEqual(f.locator('[name="start"]').input_value(),'16:05');expect(self.page.locator('.product-recommendation details').first).to_have_attribute('open','')
        self.page.locator('[data-next-plan]').click();f.locator('[name="end"]').fill('17:10');f.locator('button[type="submit"]').click();expect(self.page.locator('.product-recommendation')).to_have_count(2)
        self.assertEqual(f.locator('[name="end"]').input_value(),'17:10');self.assertEqual(len(self.state.orchestrator.data['demands']),2)

    def test_v0131_lost_ack_retry_language_and_double_submit_same_identity(self):
        from playwright.sync_api import expect
        self.page.goto(self.url+'#planner',wait_until='networkidle')
        f=self.page.locator('#product-plan-form');f.locator('[name="date"]').fill('2030-01-03');f.locator('[name="start"]').fill('14:07');f.locator('[name="end"]').fill('15:23')
        captured=[]
        def lost_ack(route):
            captured.append(route.request.post_data_json);route.fetch();route.abort('failed')
        self.page.route('**/api/orchestration/demand',lost_ack)
        f.locator('button[type="submit"]').click();expect(self.page.locator('[data-submit-feedback]')).to_contain_text('尚未确认')
        self.assertEqual(len(self.state.orchestrator.data['demands']),1)
        self.page.unroute('**/api/orchestration/demand',lost_ack);self.page.locator('#lang-en').click()
        def retry_capture(route):captured.append(route.request.post_data_json);route.continue_()
        self.page.route('**/api/orchestration/demand',retry_capture)
        self.page.evaluate("document.querySelector('#product-plan-form').requestSubmit();document.querySelector('#product-plan-form').requestSubmit()")
        expect(self.page.locator('[data-submit-feedback]')).to_contain_text('Saved:')
        expect(self.page.locator('#interaction-feedback')).to_have_count(0)
        self.assertEqual(len(self.state.orchestrator.data['demands']),1)
        self.assertEqual(len(captured),2);self.assertEqual(captured[0],captured[1])
        self.assertEqual(self.state.orchestrator.data['submissionReceipts'][captured[0]['submissionId']]['demandId'],self.state.orchestrator.data['demands'][0]['id'])
        self.assertEqual(f.locator('[name="start"]').input_value(),'14:07')

    def test_v0131_fake_clock_fired_has_visible_browser_ack(self):
        from playwright.sync_api import expect
        before=copy.deepcopy(self.state.state)
        now=time.time();self.state.orchestrator.recommendation_lifecycle.clock=lambda:now
        payload={'expectedStart':iso(now+600),'expectedEnd':iso(now+1200),'intensity':'LIGHT'}
        created=self.page.request.post(self.url+'api/orchestration/demand',data=payload).json()
        row=self.page.request.post(self.url+'api/orchestration/reminder',data={'recommendationId':created['recommendation']['id'],'beforeMinutes':5}).json()
        self.assertEqual(row['state'],'SCHEDULED');self.page.goto(self.url+'#settings',wait_until='networkidle')
        now+=301;self.state.state['last_snapshot']=iso(now)
        for w in self.state.state['windows']:
            if w.get('window_state')=='ROLLING':w['resets_at']+=301
        self.page.evaluate('refresh()');expect(self.page.locator('[data-delivery-reminder]')).to_be_visible()
        self.page.wait_for_timeout(300)
        view=self.page.request.get(self.url+'api/status').json()['orchestration'];saved=next(r for r in view['reminderRecords'] if r['id']==row['id']);self.assertEqual(saved['state'],'DISPLAYED');self.assertIn('displayedAt',saved)
        self.state.state=before

    def test_v014_new_routes_and_unknown_forecast(self):
        from playwright.sync_api import expect
        self.page.goto(self.url+'#updates',wait_until='networkidle');expect(self.page.locator('#page-content')).to_contain_text('更新源尚未配置')
        self.assertFalse(any('/api/evolution/update-download' in x for x in self.posts))
        self.page.goto(self.url+'#forecast',wait_until='networkidle')
        for days in (3,7,30):
            self.page.locator('[data-forecast-days="'+str(days)+'"]').click();expect(self.page.locator('.evo-forecast-list article')).to_have_count(days)
        self.page.goto(self.url+'#usage',wait_until='networkidle');expect(self.page.locator('#page-content')).to_contain_text('证据不足');self.normal_text()

    def test_v014_onboarding_revisit_survives_refresh(self):
        from playwright.sync_api import expect
        try:
            self.page.goto(self.url+'#settings',wait_until='networkidle');self.page.locator('[data-onboard-revisit]').click();expect(self.page.locator('.evo-welcome')).to_be_visible()
            self.page.evaluate('refresh()');expect(self.page.locator('.evo-steps [aria-current="step"]')).to_contain_text('01')
            self.page.locator('[data-onboard-step="1"]').click();self.page.locator('[data-onboard-step="2"]').click();self.page.locator('[data-onboard-step="3"]').click()
            self.page.locator('#evo-onboarding-preferences [name="purpose"]').select_option('Writing');self.page.locator('[data-onboard-complete]').click();expect(self.page.locator('.evo-capacity-hero')).to_be_visible()
            self.assertEqual(self.state.evolution.data['onboarding']['purpose'],'Writing')
        finally:self.state.settings['setup_completed']=True

    def test_v014_cleanup_dialog_cancel_preserves_file(self):
        from playwright.sync_api import expect
        cache=self.state.evolution.root/'cache';cache.mkdir(exist_ok=True);file=cache/'synthetic-cache.tmp';file.write_text('keep on cancel')
        self.page.goto(self.url+'#settings',wait_until='networkidle');self.page.once('dialog',lambda d:d.dismiss());self.page.locator('[data-storage-clean]').click();self.page.wait_for_timeout(200);self.assertTrue(file.exists())

    def test_v014_capacity_offline_frontend_guard(self):
        from playwright.sync_api import expect
        self.page.goto(self.url+'#overview',wait_until='networkidle');self.context.set_offline(True)
        try:expect(self.page.locator('[data-capacity-number]')).to_have_text('—');self.assertEqual(self.page.locator('[data-work-remind]').count(),0)
        finally:self.context.set_offline(False)

    def test_v014_research_dashboard_gate_and_no_mutation(self):
        from playwright.sync_api import expect
        before=copy.deepcopy(self.state.state['experiments']);self.page.goto(self.url+'#research',wait_until='networkidle');self.normal_text();self.assertEqual(self.page.locator('#side-nav [data-nav="research"]').count(),0)
        self.page.request.post(self.url+'api/orchestration/preferences',data={'researchMode':True});self.page.goto(self.url+'#research',wait_until='networkidle');self.page.evaluate('refresh()');expect(self.page.locator('#page-content')).to_contain_text('让结论来自证据');self.assertEqual(self.state.state['experiments'],before)
    def test_v014_research_view_accepts_ordinary_record_without_execution_fields(self):
        from playwright.sync_api import expect
        now=time.time();self.page.request.post(self.url+'api/orchestration/demand',data={'expectedStart':iso(now+86400),'expectedEnd':iso(now+90000),'intensity':'LIGHT'})
        self.page.request.post(self.url+'api/orchestration/preferences',data={'researchMode':True});self.page.goto(self.url+'#research',wait_until='networkidle');self.page.evaluate('refresh()');expect(self.page.locator('#page-content')).to_contain_text('让结论来自证据');self.assertEqual(self.page.locator('[data-handoff-create]').count(),0)
