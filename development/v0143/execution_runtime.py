"""QuotaLab-facing adapter for the isolated v0.8 ExecutionJob engine."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from execution_reliability import (
    ExecutionEngine, ExecutionPreflight, ExecutionStore, FakeNetworkProbe,
    FakePowerAdapter, RealClock, metrics, stamp,
)
from wake_adapter import FakeWakeAdapter, WindowsWakeAdapter


class StateNetworkProbe:
    """Provider-specific read-only connection state; never pings a public IP."""

    def __init__(self, state):
        self.state_owner = state

    def state(self) -> str:
        with self.state_owner.lock:
            status = self.state_owner.state.get("status")
            server = self.state_owner.state.get("app_server")
            error = str(self.state_owner.state.get("error") or "").lower()
        if server == "CONNECTED" and status == "LIVE":
            return "PROVIDER_READY"
        if any(word in error for word in ("network", "offline", "dns", "connection refused")):
            return "NETWORK_OFFLINE"
        return "PROVIDER_UNREACHABLE"


class LocalPowerAdapter:
    def state(self) -> str:
        return "AWAKE"  # If this Agent is executing, the local system is awake.


class ExecutionRuntime:
    def __init__(self, state, data_dir: Path, agent_exe: Path, port: int, *, scheduler_owner: bool):
        self.state = state
        self.data_dir = Path(data_dir)
        self.agent_exe = Path(agent_exe)
        self.port = port
        self.scheduler_owner = scheduler_owner
        self.clock = RealClock()
        self.store = ExecutionStore(self.data_dir)
        self.settings_path = self.data_dir / "execution_settings.json"
        self.settings = self._load_settings()
        self.network = FakeNetworkProbe() if state.mock else StateNetworkProbe(state)
        self.power = FakePowerAdapter() if state.mock else LocalPowerAdapter()
        self.wake = FakeWakeAdapter() if state.mock else WindowsWakeAdapter()
        # The Windows capability probe starts PowerShell. It is optional and
        # must not delay or decorate an ordinary dashboard launch.
        self.wake_capability = self.wake.capability() if state.mock else {
            "taskScheduler": "UNKNOWN", "wakeTimer": "UNKNOWN",
            "sleepWake": "UNCERTAIN", "hibernateWake": "UNCERTAIN",
            "poweredOff": "UNSUPPORTED",
        }
        self.preflight = ExecutionPreflight(self._snapshot, self.network, self.power, self.clock)
        self.engine = ExecutionEngine(self.store, self.preflight, self._run, self.clock)
        self.engine.recover()
        self.thread: threading.Thread | None = None
        self.heartbeat_path = self.data_dir / "execution_heartbeat.json"

    def _load_settings(self) -> dict[str, Any]:
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        # A previous session's opt-in must not silently arm a new process.
        return {"executionMode": value.get("executionMode", "LOCAL_ONLY")
                if value.get("executionMode") in {"LOCAL_ONLY", "WAKE_ENABLED_LOCAL"} else "LOCAL_ONLY",
                "liveExecutionEnabled": False}

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = payload.get("executionMode")
        if mode not in {"LOCAL_ONLY", "WAKE_ENABLED_LOCAL"}:
            raise ValueError("INVALID_EXECUTION_MODE")
        if mode == "WAKE_ENABLED_LOCAL":
            self.wake_capability = self.wake.capability()
            if self.wake_capability.get("taskScheduler") not in {"AVAILABLE", "MOCK"}:
                raise ValueError("WINDOWS_TASK_SCHEDULER_UNAVAILABLE")
        live_enabled = payload.get("liveExecutionEnabled", self.settings.get("liveExecutionEnabled", False)) is True
        if live_enabled and not self.state.mock:
            raise ValueError("CLOUD_VALIDATION_READ_ONLY")
        if live_enabled and not self.settings.get("liveExecutionEnabled") and payload.get("acknowledgeRisk") is not True:
            raise ValueError("LIVE_EXECUTION_REQUIRES_EXPLICIT_ACKNOWLEDGEMENT")
        self.settings = {"executionMode": mode, "liveExecutionEnabled": live_enabled}
        self._atomic_json(self.settings_path, self.settings)
        return copy.deepcopy(self.settings)

    def _snapshot(self) -> dict[str, Any]:
        with self.state.lock:
            snapshot = copy.deepcopy(self.state.state)
            snapshot["scheduler_owner"] = bool(self.state.runtime.get("scheduler_owner"))
            snapshot["health"] = {"schedulerOwner": snapshot["scheduler_owner"]}
            snapshot["execution_dispatch_enabled"] = bool(self.state.mock or self.settings.get("liveExecutionEnabled"))
        return snapshot

    def _run(self, job: dict[str, Any]) -> dict[str, Any]:
        if self.state.mock:
            return {"status": "SUCCESS", "mode": "mock", "modelTaskSent": False}
        raise RuntimeError("CLOUD_VALIDATION_READ_ONLY")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = {**payload, "executionMode": self.settings["executionMode"]}
        if clean["executionMode"] == "WAKE_ENABLED_LOCAL":
            target = datetime.fromisoformat(str(clean["targetExecutionAt"]).replace("Z", "+00:00"))
            if target.astimezone(timezone.utc) <= self.clock.now() + timedelta(minutes=6):
                raise ValueError("WAKE_TARGET_TOO_SOON")
        return self.engine.create(clean)

    def authorize(self, job_id: str) -> dict[str, Any]:
        if not self.state.mock:
            raise ValueError("CLOUD_VALIDATION_READ_ONLY")
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("JOB_NOT_FOUND")
        wake_registered = False
        try:
            if job["executionMode"] == "WAKE_ENABLED_LOCAL":
                scheduled = self.wake.schedule(job_id, job["wakeAt"], self.agent_exe, self.port)
                wake_registered = True
                job.update(wakeTask=scheduled, wakeScheduledAt=stamp(self.clock.now()))
                self.store.save(job, "WAKE_SCHEDULED", self.clock.now())
            return self.engine.authorize(job_id)
        except Exception as exc:
            if job["executionMode"] == "WAKE_ENABLED_LOCAL":
                if not wake_registered:
                    job.update(hadWakeFailure=True, errorCode="WAKE_FAILED", errorMessage=type(exc).__name__)
                    self.store.save(job, "WAKE_FAILED", self.clock.now())
                try:
                    self.wake.cancel(job_id)
                except Exception:
                    pass
            raise

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.engine.cancel(job_id)
        if job["executionMode"] == "WAKE_ENABLED_LOCAL":
            try:
                self.wake.cancel(job_id)
            except Exception:
                pass
        return job

    def wake_test(self) -> dict[str, Any]:
        if not self.state.mock:
            return {"result": "MANUAL_TEST_REQUIRED", "modelTaskSent": False,
                    "reason": "A live wake test would register a system task; use the manual smoke procedure."}
        test_id = "f" * 32
        wake_at = stamp(self.clock.now() + timedelta(minutes=3))
        scheduled = self.wake.schedule(test_id, wake_at, self.agent_exe, self.port)
        self.wake.cancel(test_id)
        return {"result": "WAKE_TEST_SUCCESS", "scheduled": scheduled, "modelTaskSent": False,
                "mode": "mock"}

    def public(self) -> dict[str, Any]:
        jobs = self.store.all()
        active = [job for job in jobs if job["status"] not in {"SUCCESS", "FAILED", "MISSED", "SKIPPED", "CANCELLED", "INCONCLUSIVE"}]
        pending_real = [job for job in active if job.get("authorizedAt") and job.get("executionMode") in {"LOCAL_ONLY", "WAKE_ENABLED_LOCAL"}]
        capability = self.wake_capability
        public_jobs = [{key: value for key, value in job.items() if key != "idempotencyToken"} for job in jobs]
        return {"mode": self.settings["executionMode"], "engine": "READY" if self.scheduler_owner else "READ_ONLY",
                "jobs": sorted(public_jobs, key=lambda item: item["createdAt"], reverse=True)[:20],
                "events": self.store.events(30), "metrics": metrics(jobs),
                "wake": capability, "nextWake": min((j["wakeAt"] for j in active if j["executionMode"] == "WAKE_ENABLED_LOCAL"), default=None),
                "network": self.network.state(), "heartbeat": self._read_heartbeat(),
                "liveDispatchEnabled": bool(self.state.mock or self.settings.get("liveExecutionEnabled")),
                "liveExecutionEnabled": bool(self.settings.get("liveExecutionEnabled")),
                "pendingRealJobs": len(pending_real) if not self.state.mock else 0,
                "safetyAlert": "PENDING_REAL_JOB_FOUND" if pending_real and not self.state.mock else None,
                "allowRetryAfterUnknown": False}

    def _read_heartbeat(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".execution-meta-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_name, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(.05)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def start(self) -> None:
        if not self.scheduler_owner or self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="execution-engine", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        previous_heartbeat = self._read_heartbeat() or {}
        try:
            last_heartbeat = datetime.fromisoformat(str(previous_heartbeat.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            last_heartbeat = None
        last_resume = previous_heartbeat.get("lastResume")
        while not self.state.stop_event.is_set():
            try:
                jobs = self.store.all()
                now = self.clock.now()
                if last_heartbeat is not None and (now - last_heartbeat).total_seconds() > 90:
                    last_resume = stamp(now)
                if last_heartbeat is None or (now - last_heartbeat).total_seconds() >= 30:
                    self._atomic_json(self.heartbeat_path, {
                        "pid": os.getpid(), "instanceToken": self.state.runtime.get("instance_id"),
                        "timestamp": stamp(now), "schedulerOwner": self.scheduler_owner,
                        "lastResume": last_resume,
                        "executionState": next((j["status"] for j in jobs if j["status"] == "EXECUTING"), "IDLE"),
                    })
                    last_heartbeat = now
                changed = self.engine.tick()
                for job in changed:
                    if job["executionMode"] == "WAKE_ENABLED_LOCAL" and job["status"] in {"SUCCESS", "FAILED", "MISSED", "SKIPPED", "CANCELLED", "INCONCLUSIVE"}:
                        try:
                            self.wake.cancel(job["jobId"])
                        except Exception:
                            pass
            except Exception:
                import logging
                logging.exception("Execution engine tick failed")
            next_target = min((datetime.fromisoformat(j["targetExecutionAt"].replace("Z", "+00:00"))
                               for j in self.store.all() if j["status"] in {"WAITING", "WAKE_PENDING", "PREFLIGHT", "READY"}), default=None)
            delay = 1 if next_target and next_target - self.clock.now() <= timedelta(minutes=2) else 15
            self.state.stop_event.wait(delay)
