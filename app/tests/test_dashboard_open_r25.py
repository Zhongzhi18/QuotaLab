import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import quota_lab


class DashboardOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = Mock()
        self.guard.acquire.return_value = True
        for target, value in [('RUNTIME_DIR', self.root), ('WindowsUserMutex', Mock(return_value=self.guard))]:
            p = patch.object(quota_lab, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.url = 'http://127.0.0.1:18861'

    def test_rapid_repeated_launch_opens_one_browser_page(self):
        with patch.object(quota_lab.webbrowser, 'open', return_value=True) as browser:
            with patch.object(quota_lab.time, 'time', side_effect=[1000, 1001, 1002]):
                self.assertTrue(quota_lab.open_dashboard_once(self.url))
                self.assertFalse(quota_lab.open_dashboard_once(self.url))
                self.assertFalse(quota_lab.open_dashboard_once(self.url))
        browser.assert_called_once_with(self.url)

    def test_later_launch_can_reopen_a_closed_dashboard(self):
        with patch.object(quota_lab.webbrowser, 'open', return_value=True) as browser:
            with patch.object(quota_lab.time, 'time', side_effect=[1000, 1013]):
                self.assertTrue(quota_lab.open_dashboard_once(self.url))
                self.assertTrue(quota_lab.open_dashboard_once(self.url))
        self.assertEqual(browser.call_count, 2)

    def test_failed_browser_open_does_not_block_retry(self):
        with patch.object(quota_lab.webbrowser, 'open', side_effect=[False, True]):
            self.assertFalse(quota_lab.open_dashboard_once(self.url))
            self.assertTrue(quota_lab.open_dashboard_once(self.url))

    def test_busy_cross_process_guard_does_not_open_page(self):
        self.guard.acquire.return_value = False
        with patch.object(quota_lab.webbrowser, 'open') as browser:
            self.assertFalse(quota_lab.open_dashboard_once(self.url))
        browser.assert_not_called()

    def test_invalid_or_old_marker_is_recovered(self):
        marker = self.root / 'dashboard-open.json'
        for previous in ('broken', json.dumps({'openedAt': 1, 'url': self.url})):
            marker.write_text(previous, encoding='utf-8')
            with patch.object(quota_lab.webbrowser, 'open', return_value=True) as browser:
                self.assertTrue(quota_lab.open_dashboard_once(self.url))
                browser.assert_called_once()


class CrossProcessDashboardOpenTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows cross-process mutex acceptance')
    def test_two_independent_launchers_open_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            code = (
                "import sys; from pathlib import Path; import quota_lab as q; "
                "q.RUNTIME_DIR=Path(sys.argv[1]); "
                "q.webbrowser.open=lambda url: (q.RUNTIME_DIR/'opened').open('a').write('opened\\n')>0; "
                "q.open_dashboard_once('http://127.0.0.1:18861')"
            )
            env = dict(os.environ, QUOTALAB_TEST_SCOPE='launch-test-' + uuid.uuid4().hex)
            children = [subprocess.Popen([sys.executable, '-c', code, folder], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
            try:
                for child in children:
                    output, error = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, error.decode(errors='replace'))
                self.assertEqual((Path(folder) / 'opened').read_text().splitlines(), ['opened'])
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
