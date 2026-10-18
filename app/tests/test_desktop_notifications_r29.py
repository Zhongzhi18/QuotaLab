from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from desktop_notifications import DesktopReminders
from orchestration_models import iso


class FakeNotifier:
    calls = []
    accepted = True
    closed = False
    def __init__(self, *args): pass
    def notify(self, title, body, sound=False, desktop_window=True):
        self.calls.append((title, body, sound))
        return self.accepted
    def close(self): self.closed = True


class DesktopReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'notifications.json'
        self.now = 2000000000
        self.state = SimpleNamespace(mock=False, runtime={'port':18861}, stop_event=threading.Event())
        FakeNotifier.calls, FakeNotifier.accepted = [], True
        self.service = DesktopReminders(self.state, self.path, 'unused.ico', FakeNotifier, lambda:self.now)
        self.service.supported = True
        self.snapshot = {'orchestration': {'demands':[{'id':'d','title':'PRIVATE PROJECT'}], 'reminderRecords':[
            {'id':'r','revision':1,'demandId':'d','state':'FIRED','remindAt':iso(self.now-1)}]}}
    def tearDown(self):
        self.state.stop_event.set()
        if self.service.is_alive(): self.service.join(timeout=3)
        self.tmp.cleanup()

    def test_disabled_by_default_and_no_side_effect_file(self):
        self.service.process(self.snapshot)
        self.assertEqual(FakeNotifier.calls, [])
        self.assertFalse(self.path.exists())

    def test_due_private_dispatch_is_submitted_not_claimed_seen(self):
        self.service.configure({'enabled':True})
        self.service.process(self.snapshot)
        self.assertEqual(len(FakeNotifier.calls),1)
        self.assertNotIn('PRIVATE',str(FakeNotifier.calls))
        self.assertFalse(FakeNotifier.calls[0][2])
        self.assertEqual(self.service.data['attempts']['r:1']['status'],'SUBMITTED')
        self.assertFalse(self.service.public()['deliveryGuaranteed'])

    def test_opt_in_title_and_sound(self):
        self.service.configure({'enabled':True,'includeTitle':True,'sound':True})
        self.service.process(self.snapshot)
        self.assertIn('PRIVATE PROJECT',FakeNotifier.calls[0][1])
        self.assertTrue(FakeNotifier.calls[0][2])

    def test_dispatch_dedup_survives_service_restart(self):
        self.service.configure({'enabled':True})
        self.service.process(self.snapshot)
        self.service.process(self.snapshot)
        replacement = DesktopReminders(self.state,self.path,'unused.ico',FakeNotifier,lambda:self.now)
        replacement.process(self.snapshot)
        self.assertEqual(len(FakeNotifier.calls),1)

    def test_invalid_cancelled_and_future_do_not_notify(self):
        self.service.configure({'enabled':True})
        for state in ('CANCELLED','INVALIDATED','EXPIRED','SCHEDULED'):
            self.snapshot['orchestration']['reminderRecords'][0]['state']=state
            self.service.process(self.snapshot)
        row=self.snapshot['orchestration']['reminderRecords'][0]
        row.update(state='FIRED',remindAt=iso(self.now+10))
        self.service.process(self.snapshot)
        self.assertEqual(FakeNotifier.calls,[])

    def test_stale_wake_alert_is_recorded_missed_not_replayed(self):
        self.service.configure({'enabled':True})
        self.snapshot['orchestration']['reminderRecords'][0]['remindAt']=iso(self.now-600)
        self.service.process(self.snapshot)
        self.assertEqual(FakeNotifier.calls,[])
        self.assertEqual(self.service.data['attempts']['r:1']['status'],'MISSED')

    def test_failure_not_misreported_as_displayed_or_repeated(self):
        self.service.configure({'enabled':True})
        FakeNotifier.accepted=False
        self.service.process(self.snapshot); self.service.process(self.snapshot)
        self.assertEqual(len(FakeNotifier.calls),1)
        self.assertEqual(self.service.data['attempts']['r:1']['status'],'FAILED')

    def test_mock_never_calls_native_backend(self):
        self.state.mock=True
        self.service.data['enabled']=True
        self.service.process(self.snapshot)
        self.assertEqual(FakeNotifier.calls,[])
        self.assertEqual(self.service.last_status,'SIMULATED')

    def test_test_button_requires_enabled_and_rate_limits(self):
        with self.assertRaisesRegex(ValueError,'ENABLE_DESKTOP_FIRST'): self.service.test_notification()
        self.service.configure({'enabled':True})
        result=self.service.test_notification()
        self.assertEqual(result['status'],'SUBMITTED')
        self.assertIsNone(result['shownToUser'])
        with self.assertRaisesRegex(ValueError,'COOLDOWN'): self.service.test_notification()

    def test_bad_settings_rejected_without_partial_updates(self):
        with self.assertRaisesRegex(ValueError,'BOOLEAN_REQUIRED'): self.service.configure({'enabled':True,'sound':'yes'})
        self.assertFalse(self.service.data['enabled'])

    def test_background_dispatch_has_no_browser_dependency(self):
        self.service.configure({'enabled':True})
        dispatched=threading.Event()
        def snapshot(): dispatched.set(); return self.snapshot
        self.state.public=snapshot
        self.service.start()
        self.assertTrue(dispatched.wait(2))
        self.state.stop_event.set();self.service.join(timeout=3)
        self.assertEqual(len(FakeNotifier.calls),1)


if __name__=='__main__':unittest.main()
