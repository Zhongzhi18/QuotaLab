"""Explainable heuristic, deliberately not an optimizer or executor."""
from __future__ import annotations

import uuid
import math
from orchestration_models import epoch, iso
from execution_broker import ExecutionBroker, local_available


class TargetBoundaryPolicy:
    VALUES = {"EARLY_REFRESH", "BALANCED", "LATE_REFRESH", "CUSTOM"}

    @classmethod
    def resolve(cls, start, end, prefs):
        policy = prefs.get("targetBoundaryPolicy") or ("EARLY_REFRESH" if prefs.get("resetPlacement") == "EARLY_BOUNDARY" else "BALANCED")
        if policy not in cls.VALUES:
            raise ValueError("INVALID_TARGET_POLICY")
        offset = float(prefs.get("earlyBoundaryMinutes", 30))
        buffer = float(prefs.get("lateBoundaryMinutes", 30))
        custom = float(prefs.get("customBoundaryMinutes", 30))
        if not all(math.isfinite(x) and x >= 0 for x in (offset, buffer, custom)):
            raise ValueError("INVALID_BOUNDARY_MINUTES")
        target = {"EARLY_REFRESH": start+offset*60, "BALANCED": (start+end)/2,
                  "LATE_REFRESH": end-buffer*60, "CUSTOM": start+custom*60}[policy]
        if not start <= target <= end:
            raise ValueError("BOUNDARY_OUTSIDE_DEMAND")
        return policy, target


class PlanningEngine:
    def recommend(self, demand, currentQuota, windowState, weeklyQuota, resetForecast,
                  executionCapabilities, deviceAvailability, userPreferences, now):
        start, end = epoch(demand["expectedStart"]), epoch(demand["expectedEnd"])
        duration = float(currentQuota.get("windowDuration", 300)) * 60
        if duration <= 0:
            raise ValueError("INVALID_WINDOW_DURATION")
        policy, target = TargetBoundaryPolicy.resolve(start, end, userPreferences)
        objective = "EARLY_BOUNDARY" if policy == "EARLY_REFRESH" else "MIDPOINT" if policy == "BALANCED" else policy
        candidate = target-duration
        reasons = [{"code": policy, "detail": f"Intensive demand {iso(start)} — {iso(end)}; target {iso(target)} via {policy}. Recommended, not optimal; refresh is not guaranteed."}]
        warnings = []
        action = "RECOMMEND_HANDOFF"
        confidence = "MEDIUM"
        state = windowState.get("state", "INSUFFICIENT_DATA") if isinstance(windowState, dict) else windowState
        reset = currentQuota.get("resetAt")
        if state == "FIXED":
            if reset and start <= epoch(reset) <= end:
                action = "NO_EXECUTION_NEEDED"
                candidate = None; target = epoch(reset)
                reasons.append({"code": "ALREADY_ALIGNED", "detail": "Existing fixed boundary lies within demand; avoid extra consumption."})
            else:
                action = "MONITOR_EXISTING_WINDOW"; candidate = None
                reasons.append({"code": "FIXED_NO_REANCHOR", "detail": "No evidence that an in-window request would re-anchor. Observe natural reset first."})
                warnings.append("Existing fixed window is not aligned; a new request is not a proven way to shift it.")
        elif state != "ROLLING":
            action = "COLLECT_EVIDENCE"; candidate = None; confidence = "LOW"
            warnings.append("Insufficient window-state evidence. Remaining percentage is not a window-start detector.")
        if demand.get("intensity") == "LIGHT" and action == "RECOMMEND_HANDOFF":
            action = "NORMAL_USAGE"; candidate = None
            reasons.append({"code": "LIGHT_DEMAND", "detail": "Avoid an additional preparation task for light demand."})
        if weeklyQuota.get("remainingPercent") is None:
            warnings.append("Weekly quota unavailable; availability cannot be promised.")
        elif float(weeklyQuota["remainingPercent"]) <= 10:
            warnings.append("Weekly quota is nearly exhausted; a five-hour boundary does not replenish weekly quota.")
            if userPreferences.get("riskPreference") == "Conservative" and action == "RECOMMEND_HANDOFF":
                action = "PRESERVE_WEEKLY_QUOTA"; candidate = None
            if float(weeklyQuota["remainingPercent"]) <= 0 and action == "RECOMMEND_HANDOFF":
                action = "WEEKLY_EXHAUSTED"; candidate = None
        exceptional = (resetForecast.get("forecastTarget") == "EXCEPTIONAL_WEEKLY_GLOBAL" and
                       resetForecast.get("assessment") == "HIGH_SIGNAL" and resetForecast.get("freshness", 0)>0)
        if exceptional:
            reasons.append({"code": "EXCEPTIONAL_SIGNAL", "detail": "Consider preserving weekly quota; public announcement is not personal delivery."})
            warnings.append("Exceptional reset signal changes strategy only; it never authorizes execution or cancels work.")
        selected = ExecutionBroker().select(executionCapabilities, candidate or start, deviceAvailability,
                                            userPreferences.get("preferredExecutionMethod", "AUTO"))
        # A local-only alternative may be executable at a later time; disclose displaced boundary.
        if candidate is not None and selected["backend"] == "MANUAL_HANDOFF":
            cap = next((c for c in executionCapabilities if c["backend"] == "LOCAL_ONLY" and c["availability"] == "AVAILABLE"), None)
            if cap and userPreferences.get("preferredExecutionMethod") != "MANUAL_HANDOFF":
                for at in range(int(max(candidate, now+60)), int(start)+1, 60):
                    if local_available(at, deviceAvailability):
                        candidate = at; target = at+duration
                        selected = ExecutionBroker().select([cap], at, deviceAvailability)
                        warnings.append("Later local fallback displaces the target boundary; suboptimal but expected executable, not guaranteed.")
                        break
        if candidate is not None and candidate <= now:
            action = "REPLAN_REQUIRED"; candidate = None
            warnings.append("Suggested preparation time has passed. No immediate catch-up task is created.")
        if end <= now:
            action = "DEMAND_EXPIRED"; candidate = None
        if candidate is not None and selected["backend"] in {"OFFICIAL_CLOUD", "MANUAL_HANDOFF"}:
            warnings.append("Official scheduling access and effect on this personal quota bucket require verification.")
        reasons.append({"code": "BACKEND", "detail": selected["backend"]+": "+selected["reliability"]})
        return {"id": uuid.uuid4().hex, "demandId": demand["id"], "provider": demand["provider"],
                "generatedAt": iso(now), "demandStart": iso(start), "demandEnd": iso(end),
                "label": "Recommended", "method": "EXPLAINABLE_HEURISTIC_V1", "objective": objective,
                "targetBoundaryPolicy": policy, "policyTargetResetAt": iso(TargetBoundaryPolicy.resolve(start, end, userPreferences)[1]),
                "action": action, "recommendedExecutionAt": iso(candidate) if candidate is not None else None,
                "expectedResetAt": iso(target), "windowDuration": duration/60,
                "bucketId": currentQuota.get("bucketId"), "backend": selected["backend"],
                "executionCapability": selected["capability"], "confidence": confidence,
                "reasons": reasons, "warnings": warnings, "weeklyStrategy": "CONSIDER_PRESERVING" if exceptional else "NORMAL",
                "expectedAvailability": "NOT_GUARANTEED", "requiresUserConfirmation": True,
                "automaticExecution": False, "modelTaskSent": False}
