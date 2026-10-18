import hashlib,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from storage_manager import StorageManager,delete_owned_data,checked_root
from update_checker import UpdateChecker,safe_url,version,NoRedirect
from usage_intelligence import analyze,capacity,forecast,research_summary
from product_migration_v014 import migrate_to_v014
from product_evolution import ProductEvolution
from orchestration_models import iso
class EvolutionTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'owned-v014';self.storage=StorageManager(self.root);self.now=time.time()
    def tearDown(self):self.temp.cleanup()
    def checker(self,body=None,hash_ok=True):
        self.calls=[];binary=b'synthetic-installer-no-execution';asset={'url':'https://releases.example.org/setup.exe','size':len(binary),'sha256':hashlib.sha256(binary if hash_ok else b'bad').hexdigest()};doc=body or {'version':'0.14.1','windows':asset,'notes':{'features':['Synthetic feature'],'fixes':[],'performance':[]}}
        def fetch(url,limit):self.calls.append(url);return json.dumps(doc).encode() if url.endswith('.json') else binary
        checker=UpdateChecker(self.root,'0.14.0-product-evolution-preview',fetch=fetch);checker.configure('https://releases.example.org/manifest.json');return checker
    def test_unconfigured_does_not_network_or_invent_version(self):
        c=UpdateChecker(self.root,'0.14.0',fetch=lambda *_:self.fail('NETWORK'));self.assertEqual(c.check()['status'],'NOT_CONFIGURED');self.assertIsNone(c.public()['latestVersion'])
    def test_new_release_notes_and_available(self):
        c=self.checker();self.assertEqual(c.check()['status'],'AVAILABLE');self.assertEqual(c.public()['notes']['features'],['Synthetic feature'])
    def test_same_stable_version_newer_than_preview(self):self.assertGreater(version('0.14.0'),version('0.14.0-preview'))
    def test_older_version_not_downgrade(self):
        c=self.checker();c.current='0.15.0';self.assertEqual(c.check()['status'],'UP_TO_DATE');self.assertRaises(ValueError,c.offer)
    def test_download_needs_explicit_consent(self):
        c=self.checker();c.check();offer=c.offer();self.assertRaises(ValueError,c.download,offer['token'],False);self.assertEqual(len(self.calls),1)
    def test_verified_download_never_runs(self):
        c=self.checker();c.check();offer=c.offer();self.assertEqual(c.download(offer['token'],True)['status'],'DOWNLOADED');self.assertTrue((self.root/'downloads/QuotaLab-Setup-0.14.1-win64.exe').exists());self.assertFalse(c.public()['automaticInstall'])
    def test_hash_failure_not_installed(self):
        c=self.checker(hash_ok=False);c.check();offer=c.offer();self.assertEqual(c.download(offer['token'],True)['status'],'DOWNLOAD_FAILED');self.assertFalse((self.root/'downloads').exists())
    def test_check_failure_clears_old_offer(self):
        c=self.checker();c.check();o=c.offer();c.last=0;c.fetch=lambda *_:b'invalid json';self.assertEqual(c.check()['status'],'CHECK_FAILED');self.assertRaises(ValueError,c.download,o['token'],True)
    def test_feed_http_private_credentials_refused(self):
        for url in ('http://example.org/x','https://127.0.0.1/x','https://[::1]/x','https://localhost/x','https://user:pass@example.org/x','https://example.org/x?token=secret'):
            self.assertRaises(ValueError,safe_url,url)
    def test_asset_other_host_refused(self):
        c=self.checker();original=c.fetch
        def fetch(url,limit):
            d=json.loads(original(url,limit));d['windows']['url']='https://evil.example.org/x';return json.dumps(d).encode()
        c.fetch=fetch;self.assertEqual(c.check()['status'],'CHECK_FAILED')
    def test_invalid_manifest_size_refused(self):
        c=self.checker();original=c.fetch
        def fetch(url,limit):
            d=json.loads(original(url,limit));d['windows']['size']=1000000000;return json.dumps(d).encode()
        c.fetch=fetch;self.assertEqual(c.check()['status'],'CHECK_FAILED')
    def test_check_minimum_interval(self):
        c=self.checker();c.check();c.check();self.assertEqual(len(self.calls),1)
    def test_same_github_repository_asset_supported(self):
        c=self.checker();c.configure('https://raw.githubusercontent.com/myname/myrepo/main/update-manifest.json');original=c.fetch
        def fetch(url,limit):
            d=json.loads(original('https://releases.example.org/x.json',limit));d['windows']['url']='https://github.com/myname/myrepo/releases/download/v0.14.1/setup.exe';return json.dumps(d).encode()
        c.fetch=fetch;self.assertEqual(c.check()['status'],'AVAILABLE')
    def test_other_github_repository_asset_refused(self):
        c=self.checker();c.configure('https://raw.githubusercontent.com/myname/myrepo/main/update-manifest.json');original=c.fetch
        def fetch(url,limit):
            d=json.loads(original('https://releases.example.org/x.json',limit));d['windows']['url']='https://github.com/other/myrepo/releases/download/v0.14.1/x';return json.dumps(d).encode()
        c.fetch=fetch;self.assertEqual(c.check()['status'],'CHECK_FAILED')
    def test_untrusted_redirect_refused(self):self.assertRaises(ValueError,NoRedirect().redirect_request,None,None,302,'',{},'https://evil.example.org/x')
    def test_cleanup_preview_does_not_delete(self):
        (self.root/'cache').mkdir();p=self.root/'cache/a.tmp';p.write_text('temp');r=self.storage.preview(['cache']);self.assertEqual(r['files'],1);self.assertTrue(p.exists())
    def test_cleanup_moves_only_confirmed_files_recoverably(self):
        (self.root/'cache').mkdir();p=self.root/'cache/a.tmp';p.write_text('temp');r=self.storage.preview(['cache']);out=self.storage.clean(r['token'],True);self.assertFalse(p.exists());self.assertTrue((self.root/'trash'/out['recoveryId']/'cache/a.tmp').exists())
    def test_cleanup_business_categories_refused(self):
        for categories in (['data'],['backups'],['../../'],[]):self.assertRaises(ValueError,self.storage.preview,categories)
    def test_cleanup_active_log_protected(self):
        (self.root/'logs').mkdir();p=self.root/'logs/quotalab.log';p.write_text('active');r=self.storage.preview(['logs']);self.assertEqual(r['files'],0);self.assertTrue(p.exists())
    def test_cleanup_false_consent_refused(self):r=self.storage.preview(['cache']);self.assertRaises(ValueError,self.storage.clean,r['token'],False)
    def test_cleanup_changed_file_not_removed(self):
        (self.root/'cache').mkdir();p=self.root/'cache/a.tmp';p.write_text('one');r=self.storage.preview(['cache']);p.write_text('changed');self.assertEqual(self.storage.clean(r['token'],True)['movedFiles'],0)
    def test_delete_requires_owner_and_exact_confirmation(self):
        self.assertRaises(ValueError,delete_owned_data,self.root,'yes');other=Path(self.temp.name)/'unowned';other.mkdir();self.assertRaises(ValueError,delete_owned_data,other,'DELETE_QUOTALAB_V014_DATA')
    def test_delete_explicit_owned_test_root_only(self):
        sibling=Path(self.temp.name)/'retained-old';sibling.mkdir();(sibling/'history').write_text('old');delete_owned_data(self.root,'DELETE_QUOTALAB_V014_DATA');self.assertFalse(self.root.exists());self.assertTrue((sibling/'history').exists())
    def test_broad_root_refused(self):self.assertRaises(ValueError,checked_root,Path.home(),False)
    def test_current_install_candidate_scan_readonly(self):
        install=Path(self.temp.name)/'isolated-install';install.mkdir();(install/'_internal').mkdir();(install/'QuotaLab.exe').write_bytes(b'app');(install/'leftover.tmp').write_bytes(b'residual');s=StorageManager(self.root,install_dir=install);r=s.summary()['installation'];self.assertEqual(r['reviewCandidateFiles'],1);self.assertFalse(r['automaticRemoval']);self.assertTrue((install/'leftover.tmp').exists())
    def test_copy_migration_preserves_original_history(self):
        old=Path(self.temp.name)/'old';(old/'data').mkdir(parents=True);p=old/'data/orchestration.json';p.write_text(json.dumps({'history':[{'type':'ORIGINAL_A3'}],'reminders':[{'id':'r'}]}));before=p.read_bytes();new=Path(self.temp.name)/'migrated';r=migrate_to_v014(old,new);self.assertEqual(r['dataSchemaVersion'],15);self.assertEqual(p.read_bytes(),before);self.assertEqual((new/'backups/schema-14/data/orchestration.json').read_bytes(),before);self.assertTrue(json.loads((new/'data/orchestration.json').read_text())['reminders'][0]['invalidated'])
    def test_migration_idempotent(self):
        target=Path(self.temp.name)/'fresh';self.assertEqual(migrate_to_v014(None,target),migrate_to_v014(None,target))
    def test_research_mock_and_controls_excluded(self):
        rows=[{'mode':'mock','classification':'ANCHOR_CONFIRMED','eligibleForAnchorStatistics':True},{'mode':'live','classification':'IN_WINDOW_CONTROL_NO_REANCHOR','experimentType':'IN_WINDOW_CONTROL','eligibleForAnchorStatistics':True},{'mode':'live','classification':'ANCHOR_CONFIRMED','eligibleForConfirmedAnchorStatistics':True,'anchorErrorSeconds':-42}];before=json.dumps(rows);r=research_summary(rows);self.assertEqual(r['eligibleCompleted'],1);self.assertEqual(r['meanAbsoluteErrorSeconds'],42);self.assertIsNone(r['probability']);self.assertEqual(json.dumps(rows),before)
    def test_empty_research_not_zero_success_rate(self):self.assertIsNone(research_summary([])['observedAlignmentPercent'])
    def test_empty_insight_no_best_time_or_efficiency(self):
        u=analyze([],[],[],'Asia/Shanghai');self.assertIsNone(u['bestObservedHour']);self.assertIsNone(u['medianPlannedToActualRatio']);self.assertEqual(u['daily'],[])
    def test_mock_quota_not_live_usage(self):
        u=analyze([],[],[{'observed_at':iso(self.now),'mode':'mock','window_duration_minutes':300,'remaining_percent':30}],'Asia/Shanghai');self.assertEqual(u['daily'],[])
    def test_live_same_reset_drop_only(self):
        rows=[{'mode':'live','window_duration_minutes':300,'observed_at':iso(self.now+i*60),'remaining_percent':left,'resets_at':reset,'limit_id':'codex','slot':'primary'} for i,(left,reset) in enumerate(((90,1),(80,1),(100,2),(70,2)))];u=analyze([],[],rows,'Asia/Shanghai',self.now+200);self.assertEqual(u['daily'][0]['observedQuotaDrop'],40)
    def test_invalid_history_timestamp_does_not_break_quota_read(self):self.assertEqual(analyze([],[],[{'mode':'live','window_duration_minutes':300,'observed_at':'bad'}],'Asia/Shanghai')['daily'],[])
    def test_full_capacity_not_guaranteed_hours(self):
        c=capacity({'windows':[{'window_duration_minutes':300,'remaining_percent':100}],'orchestration':{'quotaFreshness':'FRESH'}},{});self.assertIsNone(c['guaranteedHours']);self.assertIsNone(c['guaranteedTasks']);self.assertIsNone(c['historicalEstimatedMinutes'])
    def test_stale_capacity_not_current(self):
        c=capacity({'windows':[{'window_duration_minutes':300,'remaining_percent':100}],'orchestration':{'quotaFreshness':'OFFLINE'}},{});self.assertIsNone(c['remainingPercent']);self.assertEqual(c['state'],'UNAVAILABLE')
    def test_forecast_three_horizons_unknown_quota(self):
        r=forecast([],'Asia/Shanghai',{},self.now);self.assertEqual(set(r['horizons']),{'3','7','30'});self.assertIsNone(r['probability']);self.assertEqual(len(r['horizons']['30']['days']),30);self.assertIsNone(r['horizons']['3']['days'][0]['quotaEstimatePercent'])
    def test_forecast_midnight_split_same_timezone(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        z=ZoneInfo('Asia/Shanghai');start=datetime(2026,10,13,23,30,tzinfo=z).timestamp();d={'id':'d','expectedStart':iso(start),'expectedEnd':iso(start+7200),'intensity':'HEAVY'};r=forecast([d],'Asia/Shanghai',{},start-86400);values=r['horizons']['3']['days'];self.assertEqual([x['plannedMinutes'] for x in values],[0,30,90])
    def test_background_update_does_not_lock_quota_public(self):
        import threading
        from orchestration_service import OrchestrationService
        product=ProductEvolution(self.root,'0.14.0');entered=threading.Event();finish=threading.Event();product.updates.configure('https://releases.example.org/x.json')
        def slow(*_):entered.set();finish.wait(3);return b'bad'
        product.updates.fetch=slow;thread=threading.Thread(target=lambda:product.handle('update-check',{},None));thread.start();self.assertTrue(entered.wait(1));began=time.monotonic()
        try:
            service=OrchestrationService(self.root/'data',mock=True);snapshot={'setup':{'completed':True},'orchestration':{'quotaFreshness':'OFFLINE'},'windows':[]};self.assertEqual(product.public(snapshot,service)['updates']['status'],'CHECKING');self.assertLess(time.monotonic()-began,.5)
        finally:finish.set();thread.join(4)
    def test_update_offer_expires(self):
        c=self.checker();c.check();o=c.offer();c.receipts[o['token']]['expires']=0;self.assertRaises(ValueError,c.download,o['token'],True)
    def test_cleanup_offer_expires(self):
        o=self.storage.preview(['cache']);self.storage.tokens[o['token']]['expires']=0;self.assertRaises(ValueError,self.storage.clean,o['token'],True)
    def test_outcome_must_started_and_idempotent(self):
        p=ProductEvolution(self.root,'0.14.0');d={'id':'started','expectedStart':iso(self.now-3600),'expectedEnd':iso(self.now),'intensity':'HEAVY'};state=SimpleNamespace(orchestrator=SimpleNamespace(data={'demands':[d]}));payload={'demandId':d['id'],'status':'COMPLETED','actualMinutes':60,'submissionId':'same'};a=p.handle('outcome',payload,state);b=p.handle('outcome',payload,state);self.assertEqual(a['id'],b['id']);self.assertEqual(len(p.data['outcomes']),1);self.assertRaises(ValueError,p.handle,'outcome',{**payload,'actualMinutes':45},state)
    def test_future_outcome_refused(self):
        p=ProductEvolution(self.root,'0.14.0');state=SimpleNamespace(orchestrator=SimpleNamespace(data={'demands':[{'id':'future','expectedStart':iso(self.now+3600)}]}));self.assertRaises(ValueError,p.handle,'outcome',{'demandId':'future','status':'COMPLETED','actualMinutes':60,'submissionId':'x'},state)
    def test_high_intensity_observed_fraction_needs_denominator(self):
        demands=[{'id':str(i),'expectedStart':iso(self.now-7200),'expectedEnd':iso(self.now-3600),'intensity':'HEAVY'} for i in range(5)];out=[{'demandId':str(i),'status':'COMPLETED' if i<3 else 'PARTIAL','actualMinutes':60} for i in range(5)];u=analyze(out,demands,[],'Asia/Shanghai');self.assertEqual(u['highIntensityCompletionPercent'],60);self.assertIsNone(u['bestObservedHour']);self.assertFalse(u['bestHourIsCausal'])
if __name__=='__main__':unittest.main()
