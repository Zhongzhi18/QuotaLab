"""Provider-neutral contracts and validated local demand models (no execution)."""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol


def epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Timezone required")
            result = parsed.timestamp()
        except (TypeError, ValueError) as exc:
            raise ValueError("INVALID_TIMESTAMP: use an ISO time with timezone") from exc
    if not math.isfinite(result):
        raise ValueError("INVALID_TIMESTAMP")
    return result


def iso(value):
    return datetime.fromtimestamp(epoch(value), timezone.utc).isoformat().replace("+00:00", "Z")


def now_iso():
    return iso(datetime.now(timezone.utc).timestamp())


class QuotaProvider(Protocol):
    def snapshot(self) -> dict: ...


class DemandProvider(Protocol):
    def demands(self) -> list[dict]: ...


class SignalProvider(Protocol):
    def fetch(self) -> list[dict]: ...
    def normalize(self, record: dict) -> dict: ...


class ExecutionBackend(Protocol):
    def capability(self) -> dict: ...
    def create(self, task: dict) -> dict: ...
    def read(self, reference: str) -> dict: ...
    def cancel(self, reference: str) -> dict: ...
    def reschedule(self, reference: str, at: str) -> dict: ...
    def getStatus(self, reference: str) -> dict: ...


class VerificationProvider(Protocol):
    def verify(self, recommendation: dict, observations: list[dict], **kwargs) -> dict: ...


@dataclass
class UsageDemand:
    id: str
    title: str
    provider: str
    expectedStart: str
    expectedEnd: str
    priority: str
    intensity: str
    estimatedDuration: float
    estimatedUsageClass: str
    flexibilityBeforeMinutes: int
    flexibilityAfterMinutes: int
    mustBeReadyAt: str
    createdAt: str
    notes: str

    @classmethod
    def from_payload(cls, payload: dict):
        start, end = epoch(payload.get("expectedStart")), epoch(payload.get("expectedEnd"))
        if not 0 < end - start <= 7 * 86400:
            raise ValueError("INVALID_DEMAND_RANGE")
        priority = str(payload.get("priority", "HIGH")).upper()
        intensity = str(payload.get("intensity", "HEAVY")).upper()
        if priority not in {"LOW", "MEDIUM", "HIGH"} or intensity not in {"LIGHT", "MEDIUM", "HEAVY"}:
            raise ValueError("INVALID_DEMAND_CLASS")
        title = str(payload.get("title", "AI work")).strip()[:160]
        provider = str(payload.get("provider", "openai-codex")).strip()[:80]
        if not title or not provider:
            raise ValueError("DEMAND_TITLE_PROVIDER_REQUIRED")
        ready = epoch(payload.get("mustBeReadyAt") or start)
        if ready > end:
            raise ValueError("INVALID_READY_TIME")
        before = int(payload.get("flexibilityBeforeMinutes", 0))
        after = int(payload.get("flexibilityAfterMinutes", 0))
        if not 0 <= before <= 1440 or not 0 <= after <= 1440:
            raise ValueError("INVALID_FLEXIBILITY")
        duration = float(payload.get("estimatedDuration", (end - start) / 60))
        if not math.isfinite(duration) or duration <= 0 or duration > (end-start)/60:
            raise ValueError("INVALID_ESTIMATED_DURATION")
        return cls(uuid.uuid4().hex, title, provider, iso(start), iso(end), priority, intensity,
                   duration, str(payload.get("estimatedUsageClass", intensity))[:80], before, after,
                   iso(ready), now_iso(), str(payload.get("notes", ""))[:2000])

    def public(self):
        return asdict(self)


DEFAULT_PROFILES = [
    {"name": name, "start": start, "end": end, "intensity": intensity, "days": days}
    for name, start, end, intensity, days in [
        ("Work", "08:30", "12:00", "HEAVY", [1,2,3,4,5]),
        ("School", "14:00", "18:00", "MEDIUM", [1,2,3,4,5]),
        ("Research", "09:00", "12:00", "HEAVY", [1,2,3,4,5]),
        ("Night coding", "20:00", "23:00", "HEAVY", [1,2,3,4,5]),
        ("Weekend", "10:00", "12:00", "MEDIUM", [6,7]),
    ]
]
DEFAULT_PREFERENCES = {
    "riskPreference": "Balanced", "preferredExecutionMethod": "AUTO",
    "workHours": ["09:00", "18:00"], "sleepHours": ["23:00", "07:00"],
    "deviceOnlineHours": ["07:00", "23:00"], "timezone": "Asia/Shanghai",
    "resetPlacement": "MIDPOINT", "earlyBoundaryMinutes": 30,
    "lateBoundaryMinutes": 30, "customBoundaryMinutes": 30,
    "researchMode": False, "safetyBufferMinutes": 15,
    "publicSignalFetchEnabled": False,
}


class UsageBurnRateEstimator:
    def estimate(self, samples: list[dict], task_type: str):
        eligible = []
        for sample in samples:
            if sample.get("taskType") != task_type or sample.get("mode") != "live" or sample.get("resetOccurred"):
                continue
            try:
                delta = float(sample["beforeRemaining"]) - float(sample["afterRemaining"])
                duration = float(sample["durationMinutes"])
                if delta >= 0 and duration > 0 and math.isfinite(delta+duration) and sample.get("bucketId"):
                    eligible.append((sample["bucketId"], delta/duration))
            except (KeyError, TypeError, ValueError):
                continue
        buckets = {b for b, _ in eligible}
        if len(eligible) < 5 or len(buckets) != 1:
            return {"status": "Learning", "samples": len(eligible), "percentPerMinute": None}
        rates = sorted(rate for _, rate in eligible)
        n = len(rates)
        median = rates[n//2] if n % 2 else (rates[n//2-1]+rates[n//2])/2
        return {"status": "ESTIMATED", "samples": n, "bucketId": next(iter(buckets)),
                "percentPerMinute": median, "method": "median observational; not causal"}
