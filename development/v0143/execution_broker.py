"""Capability-driven selection. Handoff recording is NOT official task creation."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from orchestration_models import epoch, iso, now_iso

OFFICIAL_DOC = "https://learn.chatgpt.com/docs/automations?surface=app"


def capability(backend, availability="UNAVAILABLE", **kwargs):
    local = backend in {"WAKE_LOCAL", "LOCAL_ONLY"}
    return {"backend": backend, "availability": availability, "requiresLocalAgent": local,
            "requiresPcAwake": local, "requiresLocalNetwork": local,
            "supportsScheduling": False, "supportsCancellation": False, "supportsStatusRead": False,
            "programmaticCreation": False, "simulated": False, **kwargs}


class OfficialCloudExecutionAdapter:
    """No external API connector is implemented or inferred from in-app tools."""
    def capability(self):
        return capability("OFFICIAL_CLOUD", "UNKNOWN", capabilityState="OFFICIAL_UI_HANDOFF_ONLY",
                          creationMode="MANUAL_HANDOFF_ONLY", requiresPcAwake=None,
                          supportsScheduling=None, documentation=OFFICIAL_DOC,
                          accountAccess="NOT_VERIFIED", sameQuotaBucket="NOT_VERIFIED",
                          reason="Official UI handoff only. Confirm cloud scheduling and bucket in the official platform.")

    def create(self, task):
        raise ValueError("MANUAL_HANDOFF_ONLY")

    def read(self, reference):
        raise ValueError("OFFICIAL_STATUS_READ_UNAVAILABLE")

    def cancel(self, reference):
        raise ValueError("CANCEL_IN_OFFICIAL_PLATFORM")

    def reschedule(self, reference, at):
        raise ValueError("RESCHEDULE_IN_OFFICIAL_PLATFORM")

    def getStatus(self, reference):
        return {"status": "UNKNOWN", "reference": reference, "source": "NOT_CONNECTED"}


def local_available(at, device, wake=False):
    if device.get("poweredOff") is True or device.get("networkAvailable") is not True:
        return False
    for period in device.get("offlinePeriods", []):
        if epoch(period["start"]) <= at < epoch(period["end"]):
            return False
    if wake:
        return device.get("wakeVerified") is True and device.get("agentAvailable") is True
    if device.get("agentAvailable") is not True:
        return False
    zone = ZoneInfo(device.get("timezone", "Asia/Shanghai"))
    time = datetime.fromtimestamp(at, zone).strftime("%H:%M")
    start, end = device.get("onlineHours", ["00:00", "00:00"])
    if start == end:
        return device.get("alwaysOnline") is True
    return start <= time < end if start < end else time >= start or time < end


class ExecutionBroker:
    PRIORITY = ("OFFICIAL_CLOUD", "WAKE_LOCAL", "LOCAL_ONLY", "MANUAL_HANDOFF")

    def select(self, capabilities, candidate, device, preferred="AUTO"):
        indexed = {c["backend"]: c for c in capabilities}
        order = list(self.PRIORITY)
        if preferred in order:
            order.remove(preferred); order.insert(0, preferred)
        for backend in order:
            cap = indexed.get(backend)
            if cap is None or cap.get("availability") != "AVAILABLE":
                continue
            if backend == "OFFICIAL_CLOUD" and cap.get("supportsScheduling") is not True:
                continue
            if backend in {"WAKE_LOCAL", "LOCAL_ONLY"} and not local_available(candidate, device, backend == "WAKE_LOCAL"):
                continue
            return {"backend": backend, "capability": cap, "reliability": "SIMULATED" if cap.get("simulated") else "CONDITIONAL"}
        return {"backend": "MANUAL_HANDOFF", "capability": OfficialCloudExecutionAdapter().capability(), "reliability": "USER_CONFIRMATION_REQUIRED"}


def handoff_instruction(recommendation, demand):
    if recommendation["backend"] in {"LOCAL_ONLY", "WAKE_LOCAL"}:
        return (f"Local fallback proposal, NOT a scheduled task: {recommendation['recommendedExecutionAt']} UTC.\n"
                f"Demand: {demand['title']} · {demand['expectedStart']} — {demand['expectedEnd']}.\n"
                "To use local fallback, review the Lab experimental scheduling flow, explicitly enable real execution for this session "
                "and authorize one job. Keep the Agent awake and online; wake support alone is not a reliability guarantee.\n"
                "One fixed task only: Reply exactly OK. Do not use tools or modify files.\n"
                "Do not automatically retry, recur, or execute immediately as catch-up. QuotaLab has not scheduled or sent this proposal.")
    return (f"QuotaLab suggested ONE task at {recommendation['recommendedExecutionAt']} (UTC; convert to your official UI timezone).\n"
            f"Heavy demand: {demand['expectedStart']} — {demand['expectedEnd']}.\n"
            f"Target policy: {recommendation.get('targetBoundaryPolicy', recommendation['objective'])}; expected boundary (not guaranteed): {recommendation['expectedResetAt']}.\n"
            "If your official platform supports a cloud-based scheduled task for this account, schedule one run only. "
            "Do not choose a local-project automation if this computer will be off.\n"
            "Task prompt: Experimental minimal task. Reply exactly OK. Do not use tools, access private data, read files, modify files, or perform real work.\n"
            "Do not repeat, retry, or keep running. If scheduling is unavailable, do not run immediately instead.\n"
            "This is an experimental quota-window recommendation, not a reset guarantee. "
            "Cloud execution affecting the same observed quota bucket has NOT been verified. "
            "QuotaLab has NOT created a task. Retain the official run timestamp for later verification.")
