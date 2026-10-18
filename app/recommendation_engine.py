"""Product recommendation policy. No model/cloud dispatch; no calibrated probability."""
from __future__ import annotations
import math
from orchestration_models import epoch, iso
from planning_engine import PlanningEngine

PRODUCT_ACTIONS = {"NO_ACTION", "MONITOR", "SCHEDULE_OFFICIAL_TASK", "DEFER_HEAVY_WORK", "USE_CURRENT_WINDOW"}


def finite_percent(value):
    try:
        number = float(value)
        return min(100, max(0, number)) if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


class RecommendationEngine:
    def recommend(self, demand, currentQuota, windowState, weeklyQuota, resetForecast,
                  executionCapabilities, deviceAvailability, userPreferences, now, *, burn_rate=None):
        # Product planning never turns mock/local runtime availability into local dispatch advice.
        caps = [c for c in executionCapabilities if c.get("backend") in {"OFFICIAL_CLOUD", "MANUAL_HANDOFF"}]
        prefs = {**userPreferences, "preferredExecutionMethod": "MANUAL_HANDOFF"}
        base = PlanningEngine().recommend(demand, currentQuota, windowState, weeklyQuota, resetForecast,
                                         caps, deviceAvailability, prefs, now)
        action = {"RECOMMEND_HANDOFF": "SCHEDULE_OFFICIAL_TASK", "NO_EXECUTION_NEEDED": "USE_CURRENT_WINDOW",
                  "MONITOR_EXISTING_WINDOW": "MONITOR", "COLLECT_EVIDENCE": "MONITOR", "NORMAL_USAGE": "NO_ACTION",
                  "PRESERVE_WEEKLY_QUOTA": "DEFER_HEAVY_WORK", "WEEKLY_EXHAUSTED": "DEFER_HEAVY_WORK",
                  "REPLAN_REQUIRED": "MONITOR", "DEMAND_EXPIRED": "NO_ACTION"}[base["action"]]
        remaining = finite_percent(currentQuota.get("remainingPercent"))
        weekly = finite_percent(weeklyQuota.get("remainingPercent"))
        start, end = epoch(demand["expectedStart"]), epoch(demand["expectedEnd"])
        minutes = float(demand.get("estimatedDuration", (end-start)/60))
        buffer = float(userPreferences.get("safetyBufferMinutes", 15))
        if not math.isfinite(buffer) or not 0 <= buffer <= 120: raise ValueError("INVALID_SAFETY_BUFFER")
        reasons = ["DEMAND_AND_REFRESH_PREFERENCE"]
        risks = []
        state = windowState.get("state") if isinstance(windowState, dict) else windowState
        reset = currentQuota.get("resetAt")
        rate = (burn_rate or {}).get("percentPerMinute")
        expected_consumption = None
        if (burn_rate or {}).get("status") == "ESTIMATED" and (burn_rate or {}).get("bucketId") == currentQuota.get("bucketId"):
            try:
                rate = float(rate)
                if math.isfinite(rate) and rate >= 0: expected_consumption = rate*(minutes+buffer)
            except (ValueError,TypeError): pass
        if expected_consumption is None: risks.append("BURN_RATE_LEARNING")
        elif remaining is not None:
            reasons.append("OBSERVATIONAL_USAGE_ESTIMATE")
            if remaining < expected_consumption: risks.append("ESTIMATED_USAGE_EXCEEDS_CURRENT")
        if weekly is None: risks.append("WEEKLY_UNKNOWN")
        elif weekly <= 10: risks.append("WEEKLY_LOW")
        if remaining is None: risks.append("CURRENT_QUOTA_UNKNOWN")
        elif remaining <= 10: risks.append("CURRENT_QUOTA_LOW")
        if weekly is not None and weekly <= 0:
            action = "DEFER_HEAVY_WORK"; reasons.append("WEEKLY_EXHAUSTED")
        elif demand.get("intensity") == "HEAVY" and demand.get("priority") != "HIGH" and weekly is not None and weekly <= 10:
            action = "DEFER_HEAVY_WORK"; reasons.append("PRESERVE_WEEKLY_FOR_PRIORITY_WORK")
        preserve_weekly = weekly is not None and (weekly <= 0 or (weekly <= 10 and demand.get("intensity") == "HEAVY" and demand.get("priority") != "HIGH"))
        if not preserve_weekly and state == "FIXED" and reset and epoch(reset) >= end+buffer*60:
            if expected_consumption is not None and remaining is not None and remaining >= expected_consumption:
                action = "USE_CURRENT_WINDOW"; reasons.append("CURRENT_WINDOW_COVERS_DEMAND_AND_BUFFER")
            elif remaining is not None and remaining <= 0 and start <= now+buffer*60 and demand.get("intensity") == "HEAVY":
                action = "DEFER_HEAVY_WORK"; reasons.append("CURRENT_WINDOW_EXHAUSTED")
        if state == "FIXED" and reset and start <= epoch(reset) <= end:
            reasons.append("EXISTING_REFRESH_ALIGNED")
            if remaining is not None and remaining <= 10 and epoch(reset) > start:
                risks.append("LIMITED_QUOTA_BEFORE_REFRESH")
        if resetForecast.get("assessment") == "HIGH_SIGNAL" and resetForecast.get("freshness",0)>0:
            risks.append("EXCEPTIONAL_RESET_NOT_PERSONAL_DELIVERY")
            reasons.append("PUBLIC_RESET_SIGNAL_CONSIDERED")
        if action == "SCHEDULE_OFFICIAL_TASK":
            risks.extend(["CLOUD_EFFECT_UNVERIFIED", "OFFICIAL_SCHEDULING_USER_MANAGED"])
            if deviceAvailability.get("poweredOff") or not deviceAvailability.get("networkAvailable",True):
                risks.append("LOCAL_REMINDER_UNAVAILABLE_OFFLINE")
        risk = "HIGH" if any(x in risks for x in ("WEEKLY_LOW","CURRENT_QUOTA_LOW","ESTIMATED_USAGE_EXCEEDS_CURRENT","CURRENT_QUOTA_UNKNOWN")) or action == "DEFER_HEAVY_WORK" else "MEDIUM" if risks else "LOW"
        confidence = "LOW" if action == "MONITOR" or remaining is None or weekly is None else "MEDIUM" if action == "SCHEDULE_OFFICIAL_TASK" or expected_consumption is None else "HIGH"
        if end <= now: action = "NO_ACTION"
        base.update(productAction=action, recommended_time=base["recommendedExecutionAt"] if action == "SCHEDULE_OFFICIAL_TASK" else None,
                    expected_boundary=base["expectedResetAt"], productConfidence=confidence, reason=reasons, risk=risks,
                    riskLevel=risk, plannedHeavyUseMinutes=minutes, safetyBufferMinutes=buffer,
                    estimatedUsagePercent=expected_consumption, burnRateStatus=(burn_rate or {}).get("status","Learning"),
                    probability=None, officialSchedulingProgrammatic=False, sameBucketConfirmed=False)
        base["product"]={"action":action,"recommended_time":base["recommended_time"],"expected_boundary":base["expected_boundary"],
                         "confidence":confidence,"reason":reasons,"risk":risks,"riskLevel":risk}
        return base


def task_instruction(rec):
    if rec.get("productAction") != "SCHEDULE_OFFICIAL_TASK" or not rec.get("recommended_time"):
        raise ValueError("NO_OFFICIAL_TASK_RECOMMENDED")
    return (f"If cloud scheduling is available for my account, suggest ONE official cloud task at {rec['recommended_time']} UTC "
            f"(convert to my timezone). My planned intensive work is {rec['demandStart']} — {rec['demandEnd']}; "
            f"the suggested boundary is {rec['expected_boundary']}, not guaranteed. "
            "Do not run now as catch-up. Do not repeat or retry. Do not use a local-project task if this PC will be asleep.\n\n"
            "Experimental minimal task. Reply exactly OK. Do not use tools, access private data, read files, "
            "modify files, or perform real work. Do not repeat, retry, or keep running.\n\n"
            "QuotaLab has not created a task. Scheduling availability and effect on subscription usage are not verified; consumption is possible.")
