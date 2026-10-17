from types import SimpleNamespace
import tempfile
import threading
from pathlib import Path
import unittest
from desktop_notifications import DesktopReminders
from orchestration_models import iso


class WindowBackend:
    instances=[]
    def __init__(self,*args):
        self.popup_visible=False
        self.last_delivery={}
        self.calls=[]
        self.closed=False
        self.instances.append(self)
    def notify(self,title,body,sound=False,desktop_window=True):
        self.calls.append((title,body,sound,desktop_window))
        self.popup_visible=desktop_window
        self.last_delivery={'systemSubmitted':True,'desktopWindowVisible':desktop_window}
        return True
    def close(self):self.closed=True;self.popup_visible=False


class DesktopWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=2000000000
        self.state=SimpleNamespace(mock=False,runtime={'port':18861},stop_event=threading.Event())
        self.service=DesktopReminders(self.state,Path(self.tmp.name)/'notifications.json','unused.ico',WindowBackend,lambda:self.now)
        self.service.supported=True
    def tearDown(self):
        if self.service.backend:self.service.backend.close()
        self.tmp.cleanup()
    def test_window_is_explicit_option_and_disabled_with_service(self):
        self.assertFalse(self.service.public()['enabled'])
        self.assertTrue(self.service.public()['desktopWindow'])
        self.service.configure({'enabled':True})
        result=self.service.test_notification()
        self.assertTrue(result['desktopWindowVisible'])
        self.assertIsNone(result['shownToUser'])
        backend=self.service.backend
        self.service.configure({'enabled':False})
        self.assertTrue(backend.closed)
        self.assertFalse(self.service.public()['desktopWindowVisible'])
    def test_actual_reminder_records_window_delivery_without_browser(self):
        self.service.configure({'enabled':True})
        snapshot={'orchestration':{'reminderRecords':[{'id':'r','revision':1,'state':'FIRED','remindAt':iso(self.now-1)}]}}
        self.service.process(snapshot)
        self.assertTrue(self.service.public()['lastDelivery']['desktopWindowVisible'])
        self.assertEqual(self.service.public()['lastDelivery']['kind'],'reminder')
        self.service.process(snapshot)
        self.assertEqual(len(self.service.backend.calls),1)
        self.assertNotIn('PRIVATE',str(self.service.backend.calls))
    def test_disable_window_leaves_system_channel_and_persists_preference(self):
        self.service.configure({'enabled':True,'desktopWindow':False})
        result=self.service.test_notification()
        self.assertFalse(result['desktopWindowVisible'])
        self.assertTrue(result['systemSubmitted'])
        restored=DesktopReminders(self.state,self.service.path,'unused.ico',WindowBackend,lambda:self.now)
        self.assertFalse(restored.public()['desktopWindow'])
    def test_window_setting_rejects_non_boolean_atomically(self):
        with self.assertRaisesRegex(ValueError,'BOOLEAN_REQUIRED'):
            self.service.configure({'enabled':True,'desktopWindow':'yes'})
        self.assertFalse(self.service.public()['enabled'])


if __name__=='__main__':unittest.main()
