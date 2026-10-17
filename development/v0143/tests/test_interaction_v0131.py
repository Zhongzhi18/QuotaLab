import copy,json,tempfile,time,unittest
from pathlib import Path
from orchestration_service import OrchestrationService
from orchestration_models import iso,epoch
from product_migration_v0131 import migrate_to_v0131

class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.s=OrchestrationService(self.root/'data',mock=True);self.now=time.time();self.s.recommendation_lifecycle.clock=lambda:self.now
        self.snap={'provider':{'id':'openai-codex'},'app_server':'MOCK','last_snapshot':iso(self.now),'windows':[{'limit_id':'codex','slot':'primary','window_duration_minutes':300,'remaining_percent':100,'resets_at':self.now+18000,'window_state':'FIXED'},{'limit_id':'codex','slot':'secondary','window_duration_minutes':10080,'remaining_percent':80,'resets_at':self.now+500000}]}
        self.rec=self.s.handle('demand',{'expectedStart':iso(self.now+600),'expectedEnd':iso(self.now+1200),'intensity':'LIGHT'},self.snap)['recommendation']
    def tearDown(self):self.t.cleanup()
    def reminder(self):return self.s.handle('reminder',{'recommendationId':self.rec['id'],'beforeMinutes':5},self.snap)
    def advance(self,seconds):self.now+=seconds;self.snap['last_snapshot']=iso(self.now)
    def test_expired_demand_terminal_no_growth(self):
        self.advance(1300);self.s.public(self.snap);before=len(self.s.data['recommendations']);events=len(self.s.recommendation_lifecycle.data['events'])
        for _ in range(30):self.advance(31);self.s.public(self.snap)
        self.assertEqual(len(self.s.data['recommendations']),before);self.assertEqual(len(self.s.recommendation_lifecycle.data['events']),events)
    def test_distinct_transitions_have_distinct_ids(self):
        self.snap['windows'][0]['remaining_percent']=80;self.s.public(self.snap);events=self.s.recommendation_lifecycle.data['events'];self.assertEqual(len({e['eventId'] for e in events}),len(events));self.assertTrue(all(e['demandId'] and e['revision'] for e in events))
    def test_scheduled_is_not_fired_or_displayed(self):
        row=self.reminder();self.assertEqual(row['state'],'SCHEDULED');self.assertNotIn('displayedAt',row)
    def test_fake_clock_fire_once(self):
        row=self.reminder();self.advance(301)
        for _ in range(5):self.s.public(self.snap)
        self.assertEqual(row['state'],'FIRED');self.assertEqual(sum(e['type']=='REMINDER_FIRED' for e in self.s.data['history']),1)
    def test_fired_is_not_displayed(self):
        row=self.reminder();self.advance(301);self.s.public(self.snap);self.assertNotIn('displayedAt',row)
    def test_no_ui_ack_becomes_unconfirmed(self):
        row=self.reminder();self.advance(301);self.s.public(self.snap);self.advance(61);self.s.public(self.snap);self.assertEqual(row['state'],'DELIVERY_UNCONFIRMED')
    def test_display_ack_is_idempotent(self):
        row=self.reminder();self.advance(301);self.s.public(self.snap);payload={'id':row['id'],'recommendationId':row['recommendationId'],'revision':row['revision']}
        self.s.handle('reminder-displayed',payload,self.snap);self.s.handle('reminder-displayed',payload,self.snap);self.assertEqual(row['state'],'DISPLAYED');self.assertEqual(sum(e['type']=='REMINDER_DISPLAYED' for e in self.s.data['history']),1)
    def test_ack_before_fire_rejected(self):
        row=self.reminder();self.assertRaises(ValueError,self.s.handle,'reminder-displayed',{'id':row['id'],'recommendationId':row['recommendationId'],'revision':row['revision']},self.snap)
    def test_wrong_revision_ack_rejected(self):
        row=self.reminder();self.advance(301);self.s.public(self.snap);self.assertRaises(ValueError,self.s.handle,'reminder-displayed',{'id':row['id'],'recommendationId':row['recommendationId'],'revision':999},self.snap)
    def test_invalidated_reminder_preserved_diagnostic(self):
        row=self.reminder();self.snap['windows'][0]['remaining_percent']=70;view=self.s.public(self.snap);self.assertEqual(view['reminders'],[]);self.assertEqual(view['reminderRecords'][0]['state'],'INVALIDATED');self.assertEqual(row['stateReason'],'INPUT_CHANGED')
    def test_cancel_records_reason(self):
        row=self.reminder();self.s.handle('reminder-dismiss',{'id':row['id']},self.snap);self.assertEqual(row['state'],'CANCELLED');self.assertEqual(row['stateReason'],'USER_DISMISSED')
    def test_ended_reminder_records_expired_not_generic_invalidated(self):
        row=self.reminder();self.advance(1300);self.s.public(self.snap);self.assertEqual(row['state'],'EXPIRED');self.assertEqual(row['stateReason'],'WORK_PERIOD_ENDED');self.assertEqual(self.s.public(self.snap)['reminders'],[])
    def test_saved_reminder_records_utc_and_identity(self):
        row=self.reminder();event=self.s.data['history'][0]['details'];self.assertEqual(event['demandId'],self.rec['demandId']);self.assertEqual(event['remindAt'],row['remindAt']);self.assertEqual(epoch(row['serverNow']),epoch(row['createdAt']))
    def test_duplicate_reminder_does_not_add_event(self):
        a=self.reminder();b=self.reminder();self.assertEqual(a['id'],b['id']);self.assertEqual(sum(e['type']=='LOCAL_REMINDER_SAVED' for e in self.s.data['history']),1)
    def test_restart_does_not_reactivate(self):
        row=self.reminder();self.s=OrchestrationService(self.root/'data',mock=True);self.assertEqual(self.s.public(self.snap)['reminders'],[]);self.assertEqual(self.s.data['reminders'][0]['state'],'INVALIDATED')
    def test_copy_migration_retains_old_record_and_bytes(self):
        src=self.root/'old';(src/'data').mkdir(parents=True);p=src/'data/orchestration.json';p.write_text(json.dumps({'reminders':[{'id':'old','remindAt':iso(self.now),'dismissed':False}]}));old=p.read_bytes();result=migrate_to_v0131(src,self.root/'new');new=json.loads((self.root/'new/data/orchestration.json').read_text());self.assertEqual(new['reminders'][0]['state'],'INVALIDATED');self.assertEqual(p.read_bytes(),old);self.assertEqual((self.root/'new/backups/schema-13/data/orchestration.json').read_bytes(),old);self.assertEqual(result['dataSchemaVersion'],14)
    def test_unchanged_active_poll_no_events(self):
        count=len(self.s.recommendation_lifecycle.data['events'])
        for _ in range(30):self.advance(2);self.s.public(self.snap)
        self.assertEqual(count,len(self.s.recommendation_lifecycle.data['events']))

if __name__=='__main__':unittest.main()
