"""Wake adapter tests are Mock-only and never register a Windows task."""

import unittest
import base64
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path
import subprocess
from types import SimpleNamespace

from execution_runtime import ExecutionRuntime
from wake_adapter import FakeWakeAdapter, WindowsWakeAdapter


class WakeAdapterTests(unittest.TestCase):
    def test_mock_lifecycle_never_sends_model(self):
        wake = FakeWakeAdapter()
        job_id = "a" * 32
        result = wake.schedule(job_id, "2026-09-27T04:25:00Z", Path("QuotaLab.exe"), 18767)
        self.assertTrue(result["wakeToRun"])
        self.assertFalse(result["modelTaskSent"])
        self.assertEqual(wake.query(job_id), result)
        self.assertTrue(wake.cancel(job_id))
        self.assertIsNone(wake.query(job_id))

    def test_windows_adapter_rejects_non_job_id_before_any_process(self):
        def fail_if_run(*args, **kwargs):
            raise AssertionError("No external process should run")
        wake = WindowsWakeAdapter(runner=fail_if_run)
        with self.assertRaisesRegex(ValueError, "INVALID_JOB_ID"):
            wake.query("../../../other-task")
    def test_optional_capability_missing_powershell_is_not_fatal(self):
        def missing(*args,**kwargs):raise FileNotFoundError('optional shell missing')
        result=WindowsWakeAdapter(runner=missing).capability()
        self.assertEqual(result['taskScheduler'],'UNAVAILABLE')

    def test_windows_capability_probe_hides_powershell_window(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"taskScheduler":"AVAILABLE","wakeTimer":"UNKNOWN"}', stderr='')
        runner = Mock(return_value=completed)
        with patch('wake_adapter.sys.platform', 'win32'), \
                patch('wake_adapter.os.name', 'nt'), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True):
            result = WindowsWakeAdapter(runner=runner).capability()

        self.assertEqual(result['taskScheduler'], 'AVAILABLE')
        self.assertEqual(runner.call_args.kwargs['creationflags'], 0x08000000)
        encoded = runner.call_args.args[0][-1]
        script = base64.b64decode(encoded).decode('utf-16le')
        self.assertIn('Get-Service -Name Schedule', script)
        self.assertNotIn('Register-ScheduledTask -TaskPath', script)

    def test_live_runtime_defers_optional_windows_probe_until_explicit_wake_selection(self):
        with tempfile.TemporaryDirectory() as data_dir, \
                patch('execution_runtime.WindowsWakeAdapter') as adapter_type:
            runtime = ExecutionRuntime(SimpleNamespace(mock=False), Path(data_dir),
                                       Path('QuotaLab.exe'), 18861, scheduler_owner=False)
            adapter_type.return_value.capability.assert_not_called()

            runtime.save_settings({'executionMode': 'LOCAL_ONLY'})
            adapter_type.return_value.capability.assert_not_called()
            self.assertEqual(runtime.wake_capability['taskScheduler'], 'UNKNOWN')


if __name__ == "__main__":
    unittest.main()
