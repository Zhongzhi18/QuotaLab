"""Windows wake scheduling. Tasks start the local Agent; they never send a model task."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TASK_PATH = "\\QuotaLab-v08\\"


class FakeWakeAdapter:
    def __init__(self):
        self.tasks: dict[str, dict[str, Any]] = {}

    def capability(self) -> dict[str, Any]:
        return {"taskScheduler": "MOCK", "wakeTimer": "MOCK", "sleepWake": "MOCK_SUPPORTED",
                "hibernateWake": "UNCERTAIN", "poweredOff": "UNSUPPORTED"}

    def schedule(self, job_id: str, wake_at: str, agent_exe: Path, port: int) -> dict[str, Any]:
        task = {"taskName": f"QuotaLab-v08-{job_id}", "wakeAt": wake_at,
                "action": "START_LOCAL_AGENT", "wakeToRun": True, "modelTaskSent": False}
        self.tasks[job_id] = task
        return task

    def query(self, job_id: str) -> dict[str, Any] | None:
        return self.tasks.get(job_id)

    def cancel(self, job_id: str) -> bool:
        return self.tasks.pop(job_id, None) is not None


class WindowsWakeAdapter:
    def __init__(self, runner=None):
        self.runner = runner or subprocess.run

    @staticmethod
    def _task_name(job_id: str) -> str:
        if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            raise ValueError("INVALID_JOB_ID")
        return f"QuotaLab-v08-{job_id}"

    def _powershell(self, script: str, payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("WINDOWS_ONLY")
        raw = json.dumps(payload, ensure_ascii=False).replace("'", "''")
        command = "$ErrorActionPreference='Stop'; $p=ConvertFrom-Json '" + raw + "'; " + script
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        completed = self.runner(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, text=True, timeout=20, check=False,
                                creationflags=creationflags)
        if completed.returncode:
            raise RuntimeError("WAKE_TASK_ERROR:" + (completed.stderr or completed.stdout).strip()[:240])
        output = completed.stdout.strip()
        return json.loads(output) if output else {}

    def capability(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"taskScheduler": "UNAVAILABLE", "wakeTimer": "UNKNOWN",
                    "sleepWake": "UNSUPPORTED", "hibernateWake": "UNCERTAIN", "poweredOff": "UNSUPPORTED"}
        try:
            result = self._powershell(
                "$service=Get-Service -Name Schedule; "
                "$cmd=Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue; "
                "@{taskScheduler=if($service.Status -eq 'Running' -and $cmd){'AVAILABLE'}else{'UNAVAILABLE'}; "
                "wakeTimer='UNKNOWN'; sleepWake='UNCERTAIN'; hibernateWake='UNCERTAIN'; "
                "poweredOff='UNSUPPORTED'} | ConvertTo-Json -Compress", {})
            return result
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
            return {"taskScheduler": "UNAVAILABLE", "wakeTimer": "UNKNOWN",
                    "sleepWake": "UNCERTAIN", "hibernateWake": "UNCERTAIN", "poweredOff": "UNSUPPORTED"}

    def schedule(self, job_id: str, wake_at: str, agent_exe: Path, port: int) -> dict[str, Any]:
        name = self._task_name(job_id)
        executable = Path(agent_exe).resolve()
        if not executable.is_file() or executable.suffix.lower() != ".exe":
            raise ValueError("AGENT_EXE_NOT_FOUND")
        if not (1024 <= int(port) <= 65535):
            raise ValueError("INVALID_PORT")
        moment = datetime.fromisoformat(wake_at.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("WAKE_TIME_REQUIRES_OFFSET")
        if moment.astimezone(timezone.utc) <= datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError("WAKE_TIME_TOO_SOON")
        local = moment.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        payload = {"name": name, "at": local, "exe": str(executable),
                   "arguments": f"start --no-browser --port {int(port)}"}
        return self._powershell(
            "$action=New-ScheduledTaskAction -Execute $p.exe -Argument $p.arguments; "
            "$trigger=New-ScheduledTaskTrigger -Once -At ([datetime]::Parse($p.at)); "
            "$settings=New-ScheduledTaskSettingsSet -WakeToRun; "
            "Register-ScheduledTask -TaskPath '\\QuotaLab-v08\\' -TaskName $p.name "
            "-Action $action -Trigger $trigger -Settings $settings -Force | Out-Null; "
            "@{taskName=$p.name; wakeAt=$p.at; action='START_LOCAL_AGENT'; "
            "wakeToRun=$true; modelTaskSent=$false} | ConvertTo-Json -Compress", payload)

    def query(self, job_id: str) -> dict[str, Any] | None:
        name = self._task_name(job_id)
        try:
            return self._powershell(
                "$task=Get-ScheduledTask -TaskPath '\\QuotaLab-v08\\' -TaskName $p.name "
                "-ErrorAction Stop; @{taskName=$task.TaskName; state=[string]$task.State; "
                "wakeToRun=[bool]$task.Settings.WakeToRun} | ConvertTo-Json -Compress", {"name": name})
        except RuntimeError:
            return None

    def cancel(self, job_id: str) -> bool:
        name = self._task_name(job_id)
        self._powershell(
            "Unregister-ScheduledTask -TaskPath '\\QuotaLab-v08\\' -TaskName $p.name -Confirm:$false; "
            "@{cancelled=$true} | ConvertTo-Json -Compress", {"name": name})
        return True
