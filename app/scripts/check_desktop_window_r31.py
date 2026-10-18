"""Explicit native acceptance: a generic desktop TEST window, closed after two seconds.

Run manually with --native-window. No user data, model or official task is used.
"""
import argparse
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_notifications import DesktopReminders
from orchestration_models import iso


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--native-window',action='store_true',required=True)
    parser.parse_args()
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.GetForegroundWindow.restype=w.HWND
    user32.IsWindowVisible.argtypes=[w.HWND]
    user32.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    before=user32.GetForegroundWindow()
    with tempfile.TemporaryDirectory(prefix='QuotaLab-r31-native-') as temporary:
        now=time.time()
        snapshot={'orchestration':{'demands':[{'id':'native-test','title':'隔离测试窗口，无实际任务；两秒后自动关闭'}], 'reminderRecords':[{'id':'isolated-native-test','demandId':'native-test','revision':1,'state':'FIRED','remindAt':iso(now-1)}]}}
        state=SimpleNamespace(mock=False,runtime={},stop_event=threading.Event(),public=lambda:snapshot)
        service=DesktopReminders(state,Path(temporary)/'notifications.json',Path(__file__).resolve().parents[1]/'assets/phase.ico')
        service.configure({'enabled':True,'includeTitle':True})
        backend=None
        try:
            service.start()
            deadline=time.monotonic()+8
            while time.monotonic()<deadline and not service.public()['lastDelivery']:
                state.stop_event.wait(.05)
            backend=service.backend
            assert backend and backend.popup and user32.IsWindowVisible(backend.popup),'NATIVE_WINDOW_NOT_VISIBLE'
            rect=w.RECT()
            assert user32.GetWindowRect(backend.popup,ctypes.byref(rect)) and rect.right>rect.left and rect.bottom>rect.top
            assert service.public()['lastDelivery']['kind']=='reminder'
            print('BACKGROUND_DUE_REMINDER_WITHOUT_BROWSER=PASS')
            print('NATIVE_DESKTOP_WINDOW_VISIBLE=PASS')
            print('WINDOW_RECT='+str((rect.left,rect.top,rect.right,rect.bottom)))
            print('FOREGROUND_UNCHANGED='+str(user32.GetForegroundWindow()==before))
            time.sleep(2)
        finally:
            state.stop_event.set()
            service.join(timeout=5)
            assert not service.is_alive(),'NATIVE_HOST_DID_NOT_STOP'
            assert not backend or not backend.popup_visible,'NATIVE_WINDOW_NOT_CLOSED'
            print('NATIVE_WINDOW_AND_HOST_CLEANUP=PASS')


if __name__=='__main__':main()
