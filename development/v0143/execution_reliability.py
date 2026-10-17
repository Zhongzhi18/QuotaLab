"""Crash-safe, local ExecutionJob state machine. No model request is made here.

The caller supplies a runner. The durable EXECUTING transition is written before
the runner is called, so an ambiguous result or crash can never trigger a retry.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


ACTIVE = {"PLANNED", "AUTHORIZED", "WAITING", "WAKE_PENDING", "PREFLIGHT", "READY", "EXECUTING"}
TERMINAL = {"SUCCESS", "FAILED", "MISSED", "SKIPPED", "CANCELLED", "INCONCLUSIVE"}
MODES = {"LOCAL_ONLY", "WAKE_ENABLED_LOCAL"}
POLICIES = {"SKIP", "RECALCULATE", "ASK_USER"}


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required")
    return parsed.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, at: datetime):
        self.at = at.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self.at

    def advance(self, seconds: int) -> None:
        self.at += timedelta(seconds=seconds)


class ExecutionStore:
    """Atomic full-state replacement plus a best-effort append-only event trail."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_path = self.root / "execution_jobs.json"
        self.events_path = self.root / "execution_events.jsonl"
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[dict[str, Any]]:
        with self.lock:
            if not self.jobs_path.exists():
                return []
            value = json.loads(self.jobs_path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError("Invalid execution job store")
            return copy.deepcopy(value)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return next((row for row in self.all() if row.get("jobId") == job_id), None)

    def save(self, job: dict[str, Any], event: str, now: datetime) -> dict[str, Any]:
        with self.lock:
            jobs = self.all()
            for index, row in enumerate(jobs):
                if row.get("jobId") == job["jobId"]:
                    jobs[index] = copy.deepcopy(job)
                    break
            else:
                jobs.append(copy.deepcopy(job))
            payload = json.dumps(jobs, ensure_ascii=False, indent=2).encode("utf-8")
            fd, temp_name = tempfile.mkstemp(prefix=".execution-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                for attempt in range(5):
                    try:
                        os.replace(temp_name, self.jobs_path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(.05)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            entry = {"at": stamp(now), "jobId": job["jobId"], "event": event,
                     "status": job["status"], "errorCode": job.get("errorCode")}
            with self.events_path.open("ab") as stream:
                stream.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return copy.deepcopy(job)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]


class NetworkProbe(Protocol):
    def state(self) -> str: ...


class FakeNetworkProbe:
    def __init__(self, state: str = "PROVIDER_READY"):
        self.value = state

    def state(self) -> str:
        return self.value


class PowerAdapter(Protocol):
    def state(self) -> str: ...


class FakePowerAdapter:
    def __init__(self, state: str = "AWAKE"):
        self.value = state

    def state(self) -> str:
        return self.value


class ExecutionPreflight:
    def __init__(self, snapshot: Callable[[], dict[str, Any]], network: NetworkProbe,
                 power: PowerAdapter, clock: Clock, *, max_snapshot_age: int = 180):
        self.snapshot, self.network, self.power, self.clock = snapshot, network, power, clock
        self.max_snapshot_age = max_snapshot_age

    def check(self, job: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
        now = self.clock.now()
        state = self.snapshot()
        network = self.network.state()
        power = self.power.state()
        snapshot_time = state.get("last_snapshot")
        try:
            fresh = snapshot_time is not None and 0 <= (now - parse_utc(snapshot_time)).total_seconds() <= self.max_snapshot_age
        except (ValueError, TypeError):
            fresh = False
        windows = state.get("windows") or []
        checks = {
            "agent": True,  # This check runs inside the live Agent.
            "systemTime": now.tzinfo is not None and now.year >= 2024,
            "device": power in {"AWAKE", "RESUMED"},
            "network": network == "PROVIDER_READY",
            "providerBinary": bool((state.get("codex_cli") or {}).get("found")) or state.get("app", {}).get("mode") == "mock",
            "appServer": state.get("app_server") in {"CONNECTED", "MOCK"},
            "authentication": bool(state.get("account")),
            "quota": bool(windows),
            "windowFresh": fresh,
            "schedulerOwner": bool((state.get("health") or {}).get("schedulerOwner") or state.get("scheduler_owner")),
            "authorized": job.get("status") in {"WAITING", "PREFLIGHT", "READY", "WAKE_PENDING"} and bool(job.get("authorizedAt")),
            "attemptAvailable": int(job.get("actualAttempts", 0)) < int(job.get("authorizedMaxAttempts", 1)),
            "uniqueToken": not any(other.get("jobId") != job.get("jobId") and other.get("idempotencyToken") == job.get("idempotencyToken") for other in jobs),
            "noConflict": not any(other.get("jobId") != job.get("jobId") and other.get("provider") == job.get("provider") and other.get("status") == "EXECUTING" for other in jobs),
            "dispatchEnabled": state.get("execution_dispatch_enabled", True),
        }
        return {"overall": "READY" if all(checks.values()) else "NOT_READY", "checks": checks,
                "networkState": network, "powerState": power, "checkedAt": stamp(now),
                "reasons": [name for name, okay in checks.items() if not okay]}


class ExecutionEngine:
    def __init__(self, store: ExecutionStore, preflight: ExecutionPreflight,
                 runner: Callable[[dict[str, Any]], dict[str, Any]], clock: Clock):
        self.store, self.preflight, self.runner, self.clock = store, preflight, runner, clock
        self.lock = threading.RLock()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            target = parse_utc(str(payload["targetExecutionAt"]))
            now = self.clock.now()
            if target <= now + timedelta(seconds=30):
                raise ValueError("TARGET_TOO_SOON")
            fallback = parse_utc(str(payload.get("fallbackEndAt") or payload["targetExecutionAt"]))
            if fallback < target or fallback > target + timedelta(hours=1):
                raise ValueError("INVALID_FALLBACK_WINDOW")
            session_start = payload.get("sessionStartAt")
            session_end = payload.get("sessionEndAt")
            if fallback > target and (not session_start or not session_end):
                raise ValueError("FALLBACK_PLAN_CONTEXT_REQUIRED")
            if session_start and session_end:
                session_start = parse_utc(str(session_start))
                session_end = parse_utc(str(session_end))
                if session_end <= session_start or not session_start <= target + timedelta(minutes=int(payload.get("windowDuration", 300))) <= session_end:
                    raise ValueError("INVALID_SESSION_CONTEXT")
            mode = payload.get("executionMode", "LOCAL_ONLY")
            policy = payload.get("missedPolicy", "RECALCULATE")
            if mode not in MODES or policy not in POLICIES:
                raise ValueError("INVALID_EXECUTION_POLICY")
            provider = str(payload.get("provider", "openai-codex"))
            if provider != "openai-codex":
                raise ValueError("PROVIDER_NOT_SUPPORTED")
            if any(row.get("provider") == provider and row.get("status") in ACTIVE for row in self.store.all()):
                raise ValueError("PENDING_JOB_CONFLICT")
            local = str(payload.get("targetLocal") or target.astimezone().isoformat(timespec="minutes"))
            try:
                local_utc = parse_utc(local)
            except ValueError as exc:
                raise ValueError("INVALID_LOCAL_TIME") from exc
            if abs((local_utc - target).total_seconds()) > 60:
                raise ValueError("LOCAL_UTC_MISMATCH")
            job = {
                "jobId": uuid.uuid4().hex, "createdAt": stamp(now), "authorizedAt": None,
                "targetExecutionAt": stamp(target), "targetLocal": local,
                "fallbackEndAt": stamp(fallback), "wakeAt": stamp(target - timedelta(minutes=5)),
                "sessionStartAt": stamp(session_start) if session_start else None,
                "sessionEndAt": stamp(session_end) if session_end else None,
                "provider": provider, "taskType": "MINIMAL_FIXED_TASK",
                "windowDuration": int(payload.get("windowDuration", 300)),
                "expectedResetAt": stamp(target + timedelta(minutes=int(payload.get("windowDuration", 300)))),
                "authorizedMaxAttempts": 1, "actualAttempts": 0,
                "executionMode": mode, "missedPolicy": policy, "fallbackPolicy": "RECHECK_PLAN",
                "status": "PLANNED", "lastStateChangeAt": stamp(now),
                "preflightState": None, "eligibilityState": None, "networkState": None,
                "providerState": None, "authState": None, "actualStartedAt": None,
                "actualSentAt": None, "actualCompletedAt": None, "result": None,
                "errorCode": None, "errorMessage": None, "idempotencyToken": uuid.uuid4().hex,
                "hadFailedPreflight": False, "hadNetworkFailure": False, "hadWakeFailure": False,
                "sourcePlanId": payload.get("sourcePlanId"), "experimentId": payload.get("experimentId"),
            }
            return self.store.save(job, "JOB_PLANNED", now)

    def authorize(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._require(job_id)
            if job["status"] != "PLANNED":
                raise ValueError("JOB_NOT_PLANNED")
            now = self.clock.now()
            if now >= parse_utc(job["targetExecutionAt"]):
                raise ValueError("AUTHORIZATION_TOO_LATE")
            job.update(status="AUTHORIZED", authorizedAt=stamp(now), lastStateChangeAt=stamp(now))
            self.store.save(job, "JOB_AUTHORIZED", now)
            job["status"] = "WAITING"
            return self.store.save(job, "JOB_WAITING", now)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._require(job_id)
            if job["status"] not in ACTIVE - {"EXECUTING"}:
                raise ValueError("JOB_CANNOT_CANCEL")
            now = self.clock.now()
            job.update(status="CANCELLED", lastStateChangeAt=stamp(now))
            return self.store.save(job, "JOB_CANCELLED", now)

    def recover(self) -> list[dict[str, Any]]:
        changed = []
        with self.lock:
            now = self.clock.now()
            for job in self.store.all():
                if job["status"] == "EXECUTING":
                    job.update(status="INCONCLUSIVE", errorCode="EXECUTION_INTERRUPTED",
                               errorMessage="Agent stopped after the durable send boundary; no retry is allowed",
                               lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "EXECUTION_INTERRUPTED", now))
                elif job["status"] == "PLANNED" and now >= parse_utc(job["targetExecutionAt"]):
                    job.update(status="SKIPPED", errorCode="NOT_AUTHORIZED", lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "EXECUTION_SKIPPED", now))
                elif job["status"] in ACTIVE and now >= parse_utc(job["targetExecutionAt"]):
                    job.update(status="MISSED", errorCode="AGENT_ABSENT_AT_TARGET",
                               lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "EXECUTION_MISSED", now))
        return changed

    def tick(self) -> list[dict[str, Any]]:
        changed = []
        with self.lock:
            now = self.clock.now()
            for job in self.store.all():
                if job["status"] == "PLANNED" and now >= parse_utc(job["targetExecutionAt"]):
                    job.update(status="SKIPPED", errorCode="NOT_AUTHORIZED", lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "EXECUTION_SKIPPED", now))
                    continue
                if job["status"] not in {"AUTHORIZED", "WAITING", "WAKE_PENDING", "PREFLIGHT", "READY"}:
                    continue
                target = parse_utc(job["targetExecutionAt"])
                end = parse_utc(job["fallbackEndAt"])
                if now > end:
                    skipped = job.get("missedPolicy") == "SKIP"
                    job.update(status="SKIPPED" if skipped else "MISSED", errorCode="AUTHORIZED_WINDOW_CLOSED", lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "EXECUTION_SKIPPED" if skipped else "EXECUTION_MISSED", now))
                    continue
                if now < target - timedelta(minutes=5):
                    if job["status"] == "AUTHORIZED":
                        job.update(status="WAITING", lastStateChangeAt=stamp(now))
                        changed.append(self.store.save(job, "JOB_WAITING", now))
                    continue
                if job["executionMode"] == "WAKE_ENABLED_LOCAL" and job["status"] == "WAITING":
                    job.update(status="WAKE_PENDING", lastStateChangeAt=stamp(now))
                    self.store.save(job, "WAKE_WINDOW_OPEN", now)
                job.update(status="PREFLIGHT", lastStateChangeAt=stamp(now))
                self.store.save(job, "PREFLIGHT_STARTED", now)
                report = self.preflight.check(job, self.store.all())
                job.update(preflightState=report, networkState=report["networkState"],
                           eligibilityState="READY" if report["checks"]["windowFresh"] else "STALE",
                           providerState="READY" if report["checks"]["appServer"] else "UNAVAILABLE",
                           authState="READY" if report["checks"]["authentication"] else "UNAVAILABLE")
                if report["overall"] != "READY":
                    job["hadFailedPreflight"] = True
                    if not report["checks"]["network"]:
                        job["hadNetworkFailure"] = True
                    job.update(status="WAITING", errorCode="FAILED_PRECHECK", errorMessage=",".join(report["reasons"]),
                               lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "NETWORK_WAIT" if not report["checks"]["network"] else "PREFLIGHT_WAIT", now))
                    continue
                if now < target:
                    job.update(status="READY", errorCode=None, errorMessage=None, lastStateChangeAt=stamp(now))
                    changed.append(self.store.save(job, "PREFLIGHT_READY", now))
                    continue
                if now > target and job.get("sessionEndAt"):
                    shifted_reset = now + timedelta(minutes=int(job["windowDuration"]))
                    if shifted_reset > parse_utc(job["sessionEndAt"]) or shifted_reset < parse_utc(job["sessionStartAt"]):
                        job.update(status="MISSED", errorCode="PLAN_NO_LONGER_VALID", lastStateChangeAt=stamp(now))
                        changed.append(self.store.save(job, "EXECUTION_MISSED", now))
                        continue
                # This fsynced transition is the one-way boundary. Even if the
                # runner fails before returning, restart recovery never retries.
                job.update(status="EXECUTING", actualAttempts=1, actualStartedAt=stamp(now),
                           sendAttemptAt=stamp(now), lastStateChangeAt=stamp(now), errorCode=None)
                self.store.save(job, "EXECUTION_STARTED", now)
                try:
                    result = self.runner(copy.deepcopy(job))
                    if result.get("modelTaskSent") is True:
                        job["actualSentAt"] = job["sendAttemptAt"]
                    elif result.get("mode") == "mock":
                        job["mockDispatchedAt"] = job["sendAttemptAt"]
                    if result.get("status") == "SUCCESS":
                        job.update(status="SUCCESS", result=result, errorCode=None)
                        event = "EXECUTION_SUCCESS"
                    elif result.get("status") == "FAILED":
                        job.update(status="FAILED", result=result, errorCode="MODEL_REQUEST_FAILED")
                        event = "EXECUTION_FAILED"
                    else:
                        job.update(status="INCONCLUSIVE", result=result, errorCode="UNKNOWN_OUTCOME")
                        event = "EXECUTION_INCONCLUSIVE"
                except Exception as exc:
                    job.update(status="INCONCLUSIVE", errorCode="UNKNOWN_OUTCOME", errorMessage=type(exc).__name__)
                    event = "EXECUTION_INCONCLUSIVE"
                job.update(actualCompletedAt=stamp(self.clock.now()), lastStateChangeAt=stamp(self.clock.now()))
                if isinstance(job.get("result"), dict):
                    job["responseAt"] = job["result"].get("responseAt")
                    job["postExecutionWindowState"] = job["result"].get("postExecutionWindowState")
                changed.append(self.store.save(job, event, self.clock.now()))
        return changed

    def _require(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("JOB_NOT_FOUND")
        return job


def metrics(jobs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "scheduledJobs": len(jobs),
        "successfulJobs": sum(j["status"] == "SUCCESS" for j in jobs),
        "missedJobs": sum(j["status"] == "MISSED" for j in jobs),
        "failedPreflight": sum(bool(j.get("hadFailedPreflight")) for j in jobs),
        "networkFailures": sum(bool(j.get("hadNetworkFailure")) for j in jobs),
        "wakeFailures": sum(bool(j.get("hadWakeFailure")) for j in jobs),
        "inconclusiveExecutions": sum(j["status"] == "INCONCLUSIVE" for j in jobs),
    }
