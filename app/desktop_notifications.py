"""Opt-in Windows notification-area reminders; no browser timers or subprocesses.

An accepted Shell request is SUBMITTED, never proof that the person saw it.
No registry, startup, firewall or system notification settings are modified.
"""
import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time

from file_persistence import replace_file
from orchestration_models import epoch, iso


class WindowsNotifier:
    def __init__(self, icon_path, on_click=lambda: None):
        self.icon_path, self.on_click = str(icon_path), on_click
        self.ready, self.commands = threading.Event(), queue.Queue()
        self.hwnd, self.available, self.popup = None, False, None
        self.popup_visible, self.last_delivery = False, {}
        self.thread = threading.Thread(target=self._run, name='desktop-notification-ui', daemon=True)
        self.thread.start()
        self.ready.wait(3)

    def notify(self, title, text, sound=False, desktop_window=True):
        if not self.available:
            return False
        done, result = threading.Event(), []
        self.commands.put((title, text, sound, desktop_window, done, result))
        if not self.user32.PostMessageW(self.hwnd, 0x8002, 0, 0):
            return False
        return done.wait(3) and bool(result and result[0])

    def close(self):
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        if os.name != 'nt':
            self.ready.set()
            return
        from ctypes import wintypes as w
        user32 = self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', w.UINT), ('proc', proc_type), ('clsExtra', ctypes.c_int), ('wndExtra', ctypes.c_int),
                        ('instance', w.HINSTANCE), ('icon', w.HICON), ('cursor', w.HANDLE), ('background', w.HBRUSH),
                        ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

        class NotifyIconData(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT), ('uFlags', w.UINT),
                        ('uCallbackMessage', w.UINT), ('hIcon', w.HICON), ('szTip', w.WCHAR * 128),
                        ('dwState', w.DWORD), ('dwStateMask', w.DWORD), ('szInfo', w.WCHAR * 256),
                        ('uTimeout', w.UINT), ('szInfoTitle', w.WCHAR * 64), ('dwInfoFlags', w.DWORD),
                        ('guidItem', ctypes.c_byte * 16), ('hBalloonIcon', w.HICON)]

        kernel32.GetModuleHandleW.argtypes, kernel32.GetModuleHandleW.restype = [w.LPCWSTR], w.HMODULE
        user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterClassW.argtypes, user32.RegisterClassW.restype = [ctypes.POINTER(WindowClass)], w.WORD
        user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        user32.CreateWindowExW.restype = w.HWND
        user32.PostMessageW.argtypes, user32.PostMessageW.restype = [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL
        user32.GetMessageW.argtypes, user32.GetMessageW.restype = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], ctypes.c_int
        user32.DispatchMessageW.argtypes, user32.DispatchMessageW.restype = [ctypes.POINTER(w.MSG)], ctypes.c_ssize_t
        user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user32.LoadImageW.argtypes, user32.LoadImageW.restype = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT], w.HANDLE
        user32.DestroyWindow.argtypes, user32.DestroyIcon.argtypes = [w.HWND], [w.HICON]
        user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        user32.ShowWindow.argtypes, user32.ShowWindow.restype = [w.HWND, ctypes.c_int], w.BOOL
        user32.IsWindowVisible.argtypes, user32.IsWindowVisible.restype = [w.HWND], w.BOOL
        user32.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
        user32.SetWindowPos.argtypes, user32.SetWindowPos.restype = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT], w.BOOL
        user32.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, w.LPVOID, w.UINT]
        user32.IsDialogMessageW.argtypes, user32.IsDialogMessageW.restype = [w.HWND, ctypes.POINTER(w.MSG)], w.BOOL
        user32.SendMessageW.argtypes, user32.SendMessageW.restype = [w.HWND, w.UINT, w.WPARAM, w.LPARAM], ctypes.c_ssize_t
        gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi32.GetStockObject.argtypes, gdi32.GetStockObject.restype = [ctypes.c_int], w.HANDLE
        shell32.Shell_NotifyIconW.argtypes, shell32.Shell_NotifyIconW.restype = [w.DWORD, ctypes.POINTER(NotifyIconData)], w.BOOL
        nid = NotifyIconData()
        nid.cbSize, nid.uID = ctypes.sizeof(nid), 1
        icon, atom, class_name = None, 0, 'QuotaLabReminder' + str(os.getpid()) + str(id(self))
        popup_body, popup_count = None, 0
        tray_available = False

        def show_desktop_window(title, text):
            nonlocal popup_body, popup_count
            if not self.popup:
                area = w.RECT()
                if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
                    return False
                width, height = min(480, area.right-area.left-24), min(250, area.bottom-area.top-24)
                # A separate app window, shown without taking keyboard focus.
                self.popup = user32.CreateWindowExW(0x00000088, class_name, title, 0x80C80000,
                    area.right-width-12, area.bottom-height-12, width, height, None, None, instance, None)
                if not self.popup:
                    return False
                controls = []
                popup_body = user32.CreateWindowExW(0, 'STATIC', '', 0x50000000,
                    20, 20, width-56, 90, self.popup, None, instance, None)
                controls.append(popup_body)
                controls.append(user32.CreateWindowExW(0, 'STATIC', '后台桌面提醒 · 不会自动执行 AI 任务', 0x50000000,
                    20, 112, width-56, 24, self.popup, None, instance, None))
                controls.append(user32.CreateWindowExW(0, 'BUTTON', '打开工作台', 0x50010001,
                    20, 150, 150, 34, self.popup, w.HMENU(101), instance, None))
                controls.append(user32.CreateWindowExW(0, 'BUTTON', '关闭窗口', 0x50010000,
                    184, 150, 120, 34, self.popup, w.HMENU(102), instance, None))
                if not all(controls):
                    user32.DestroyWindow(self.popup); self.popup = None
                    return False
                for control in controls:
                    user32.SendMessageW(control, 0x0030, gdi32.GetStockObject(17), 1)
                popup_count = 0
            popup_count += 1
            suffix = f'\n另有 {popup_count-1} 条提醒，请到工作台查看。' if popup_count > 1 else ''
            user32.SetWindowTextW(self.popup, title)
            user32.SetWindowTextW(popup_body, text[:180]+suffix)
            user32.ShowWindow(self.popup, 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(self.popup, w.HWND(-1), 0, 0, 0, 0, 0x0053)
            self.popup_visible = bool(user32.IsWindowVisible(self.popup))
            return self.popup_visible

        @proc_type
        def window_proc(hwnd, message, wp, lp):
            if hwnd == self.popup:
                if message == 0x0111 and (wp & 0xffff) in (101, 102):
                    if (wp & 0xffff) == 101:
                        threading.Thread(target=self.on_click, daemon=True).start()
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0010:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:
                    self.popup, self.popup_visible = None, False
                    return 0  # Closing a reminder must not terminate the dispatcher.
            if message == 0x8002:
                while not self.commands.empty():
                    title, text, sound, desktop_window, done, result = self.commands.get()
                    try:
                        nid.uFlags, nid.szInfoTitle, nid.szInfo = 0x10, title[:63], text[:255]
                        # Respect quiet time; do not request bypass of Windows policy.
                        nid.dwInfoFlags = 0x01 | 0x80 | (0 if sound else 0x10)
                        submitted = bool(tray_available and shell32.Shell_NotifyIconW(1, ctypes.byref(nid)))
                        visible = show_desktop_window(title, text) if desktop_window else False
                        self.last_delivery = {'systemSubmitted': submitted, 'desktopWindowVisible': visible}
                        result.append(submitted or visible)
                    except Exception:
                        result.append(False)
                    finally:
                        done.set()
                return 0
            if message == 0x8001 and lp in (0x0203, 0x0405):
                threading.Thread(target=self.on_click, daemon=True).start()
                return 0
            if message == 0x0010:
                if self.popup:
                    user32.DestroyWindow(self.popup)
                user32.DestroyWindow(hwnd)
                return 0
            if message == 0x0002:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wp, lp)

        try:
            instance = kernel32.GetModuleHandleW(None)
            wc = WindowClass()
            wc.proc, wc.instance, wc.name = window_proc, instance, class_name
            wc.background = w.HBRUSH(6)  # COLOR_WINDOW + 1, native contrast settings.
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                return
            self.hwnd = user32.CreateWindowExW(0, class_name, 'QuotaLab reminders', 0, 0, 0, 0, 0, w.HWND(-3), None, instance, None)
            if not self.hwnd:
                return
            icon = user32.LoadImageW(None, self.icon_path, 1, 32, 32, 0x10)
            nid.hWnd, nid.hIcon, nid.uCallbackMessage = self.hwnd, icon, 0x8001
            nid.uFlags, nid.szTip = 0x01 | 0x02 | 0x04, 'QuotaLab · 本地开工提醒'
            tray_available = bool(icon and shell32.Shell_NotifyIconW(0, ctypes.byref(nid)))
            self.available = True  # The desktop window does not depend on Explorer's tray.
            self.ready.set()
            if self.available:
                msg = w.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if self.popup and user32.IsDialogMessageW(self.popup, ctypes.byref(msg)):
                        continue
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            logging.exception('Desktop notification host unavailable')
        finally:
            self.available = False
            self.ready.set()
            if self.hwnd:
                if self.popup:
                    user32.DestroyWindow(self.popup)
                if tray_available:
                    shell32.Shell_NotifyIconW(2, ctypes.byref(nid))
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            if icon:
                user32.DestroyIcon(icon)
            if atom:
                user32.UnregisterClassW(class_name, instance)


class DesktopReminders(threading.Thread):
    """Poll the server state, not a tab. Claim each reminder before dispatch."""
    def __init__(self, state, path, icon_path, notifier_factory=WindowsNotifier, clock=time.time):
        super().__init__(name='desktop-reminder-dispatch', daemon=True)
        self.state, self.path, self.icon_path = state, Path(path), icon_path
        self.factory, self.clock, self.lock, self.backend = notifier_factory, clock, threading.RLock(), None
        self.supported = os.name == 'nt' and not state.mock
        self.last_test, self.last_status = 0, 'NOT_TESTED'
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            loaded = {}
        self.data = loaded if isinstance(loaded, dict) else {}
        self.data['enabled'] = self.data.get('enabled') is True
        self.data['sound'] = self.data.get('sound') is True
        self.data['includeTitle'] = self.data.get('includeTitle') is True
        self.data['desktopWindow'] = self.data.get('desktopWindow') is not False
        if not isinstance(self.data.get('attempts'), dict):
            self.data['attempts'] = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        replace_file(tmp, self.path)

    def public(self):
        with self.lock:
            return {key: self.data[key] for key in ('enabled', 'sound', 'includeTitle', 'desktopWindow')} | {
                'supported': self.supported,
                'desktopWindowVisible': bool(getattr(self.backend, 'popup_visible', False)),
                'lastDelivery': self.data.get('lastDelivery'),
                'lastStatus': self.last_status, 'independentOfBrowser': True,
                'requiresBackgroundProcess': True, 'deliveryGuaranteed': False}

    def configure(self, payload):
        with self.lock:
            for key in ('enabled', 'sound', 'includeTitle', 'desktopWindow'):
                if key in payload:
                    if type(payload[key]) is not bool:
                        raise ValueError('BOOLEAN_REQUIRED')
                    if key == 'enabled' and payload[key] and not self.public()['supported']:
                        raise ValueError('DESKTOP_NOTIFICATIONS_UNAVAILABLE')
            for key in ('enabled', 'sound', 'includeTitle', 'desktopWindow'):
                if key in payload:
                    self.data[key] = payload[key]
            self._save()
            if (not self.data['enabled'] or payload.get('desktopWindow') is False) and self.backend:
                self.backend.close()
                self.backend = None
            return self.public()

    def _send(self, title, body):
        if self.state.mock:
            return 'SIMULATED'
        try:
            if self.backend is None:
                def open_workbench():
                    import webbrowser
                    port = self.state.runtime.get('port')
                    if isinstance(port, int) and 1024 <= port <= 65535:
                        webbrowser.open(f'http://127.0.0.1:{port}/#planner')
                self.backend = self.factory(self.icon_path, open_workbench)
            return 'SUBMITTED' if self.backend.notify(title, body, self.data['sound'], self.data['desktopWindow']) else 'FAILED'
        except Exception:
            logging.exception('Desktop notification submission failed')
            return 'FAILED'

    def test_notification(self):
        with self.lock:
            if not self.data['enabled']:
                raise ValueError('ENABLE_DESKTOP_FIRST')
            if self.clock() - self.last_test < 10:
                raise ValueError('DESKTOP_TEST_COOLDOWN')
            self.last_test = self.clock()
            self.last_status = self._send('QuotaLab · 桌面提醒测试', '这是一条测试通知，不是实际任务。最小化网页后，后台开工提醒也会使用此通道。')
            self._record_delivery('test', self.last_status)
            return {'status': self.last_status, 'shownToUser': None, **self.data['lastDelivery']}

    def _record_delivery(self, kind, status):
        self.data['lastDelivery'] = {'kind': kind, 'status': status, 'at': iso(self.clock()),
            'systemSubmitted': bool(getattr(self.backend, 'last_delivery', {}).get('systemSubmitted', False)),
            'desktopWindowVisible': bool(getattr(self.backend, 'popup_visible', False))}
        self._save()

    def process(self, snapshot):
        with self.lock:
            if not self.data['enabled']:
                return
            for row in snapshot.get('orchestration', {}).get('reminderRecords', []):
                if row.get('invalidated') or row.get('dismissed') or row.get('state') not in {'FIRED', 'DISPLAYED', 'DELIVERY_UNCONFIRMED'}:
                    continue
                key = str(row.get('id')) + ':' + str(row.get('revision'))
                if key in self.data['attempts']:
                    continue
                due = epoch(row.get('remindAt'))
                if due > self.clock():
                    continue
                # Do not replay stale alerts on wake/start. Leave evidence in the page.
                status = 'MISSED' if self.clock() - due > 300 else 'CLAIMED'
                self.data['attempts'][key] = {'at': iso(self.clock()), 'status': status}
                self._save()
                if status == 'MISSED':
                    continue
                demands = snapshot.get('orchestration', {}).get('demands', [])
                demand = next((d for d in demands if d.get('id') == row.get('demandId')), {})
                title = 'QuotaLab · 开工提醒'
                body = '一段工作即将开始。请打开 QuotaLab 复核计划时间与最新额度。'
                if self.data['includeTitle']:
                    body = str(demand.get('title', '工作计划'))[:120] + '。请打开 QuotaLab 复核时间与额度。'
                status = self._send(title, body)
                self.last_status = status
                self.data['attempts'][key]['status'] = status
                self._record_delivery('reminder', status)

    def run(self):
        try:
            while not self.state.stop_event.is_set():
                try:
                    if self.data['enabled']:
                        self.process(self.state.public())
                except Exception:
                    logging.exception('Desktop reminder loop failed')
                self.state.stop_event.wait(2)
        finally:
            if self.backend:
                self.backend.close()
