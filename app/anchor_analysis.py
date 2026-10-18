"""Deterministic Anchor experiment classification and legacy reclassification."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any


RESET_JITTER_TOLERANCE_SECONDS = 5
ANCHOR_CONFIRM_TOLERANCE_SECONDS = 120
POST_EXPIRY_GRACE_SECONDS = 30

IN_WINDOW_CONTROL = "IN_WINDOW_CONTROL"
POST_EXPIRY_ANCHOR = "POST_EXPIRY_ANCHOR"
INCONCLUSIVE_TYPE = "INCONCLUSIVE"

FINAL_CLASSIFICATIONS = {
    "ANCHOR_CONFIRMED",
    "NO_REANCHOR",
    "RESET_CHANGED_NOT_ANCHORED",
    "IN_WINDOW_CONTROL_NO_REANCHOR",
    "IN_WINDOW_CONTROL_RESET_CHANGED",
    "INCONCLUSIVE",
    "REQUEST_FAILED",
    "TIMEOUT",
    "CANCELLED",
}


def epoch(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def classify_anchor_observation(
    *,
    previous_reset_at: Any,
    trigger_actual_at: Any,
    window_duration_minutes: Any,
    observed_reset_at: Any,
    request_status: str | None = None,
) -> dict[str, Any]:
    previous = epoch(previous_reset_at)
    trigger = epoch(trigger_actual_at)
    observed = epoch(observed_reset_at)
    try:
        duration = int(window_duration_minutes)
    except (TypeError, ValueError):
        duration = 0

    base = {
        "experimentType": INCONCLUSIVE_TYPE,
        "previousResetAt": previous,
        "triggerActualAt": trigger,
        "windowDurationMins": duration or None,
        "expectedResetAt": None,
        "observedResetAt": observed,
        "resetShiftSeconds": None,
        "anchorErrorSeconds": None,
        "resetJitterToleranceSeconds": RESET_JITTER_TOLERANCE_SECONDS,
        "anchorToleranceSeconds": ANCHOR_CONFIRM_TOLERANCE_SECONDS,
        "classification": "INCONCLUSIVE",
        "eligibleForAnchorStatistics": False,
        "notes": "Insufficient reliable timing or follow-up snapshot data.",
    }
    if request_status == "FAILED":
        base.update(classification="REQUEST_FAILED", notes="The request failed; no Anchor inference is permitted.")
        return base
    if previous is None or trigger is None or observed is None or duration <= 0:
        return base

    expected = trigger + duration * 60
    shift = observed - previous
    error = observed - expected
    base.update(
        expectedResetAt=expected,
        resetShiftSeconds=round(shift, 3),
        anchorErrorSeconds=round(error, 3),
    )
    unchanged = abs(shift) <= RESET_JITTER_TOLERANCE_SECONDS
    if trigger < previous:
        base["experimentType"] = IN_WINDOW_CONTROL
        base["classification"] = (
            "IN_WINDOW_CONTROL_NO_REANCHOR" if unchanged else "IN_WINDOW_CONTROL_RESET_CHANGED"
        )
        base["notes"] = (
            "In-window control: no re-anchor observed; reset movement is within jitter tolerance."
            if unchanged
            else "In-window control: reset changed beyond jitter tolerance, but this is not an eligible Anchor trial."
        )
        return base
    if trigger < previous + POST_EXPIRY_GRACE_SECONDS:
        base["notes"] = "Trigger occurred inside the post-reset grace interval."
        return base

    base["experimentType"] = POST_EXPIRY_ANCHOR
    base["eligibleForAnchorStatistics"] = True
    if abs(error) <= ANCHOR_CONFIRM_TOLERANCE_SECONDS:
        base.update(classification="ANCHOR_CONFIRMED", notes="Observed reset is within Anchor confirmation tolerance.")
    elif unchanged:
        base.update(classification="NO_REANCHOR", notes="No reset movement beyond jitter tolerance was observed.")
    else:
        base.update(
            classification="RESET_CHANGED_NOT_ANCHORED",
            notes="Reset changed, but the observed reset is outside Anchor confirmation tolerance.",
        )
    return base


def _matched_reset(record: dict[str, Any], side: str) -> Any:
    matched = record.get("matched_window")
    if isinstance(matched, dict) and isinstance(matched.get(side), dict):
        return matched[side].get("resets_at")
    windows = record.get("before" if side == "before" else "after", {}).get("windows", [])
    target = (
        record.get("target_limit_id"), record.get("target_slot"),
        record.get("target_window_duration_minutes"),
    )
    for window in windows if isinstance(windows, list) else []:
        identity = (window.get("limit_id"), window.get("slot"), window.get("window_duration_minutes"))
        if identity == target or (not any(target) and window.get("window_duration_minutes") == 300):
            return window.get("resets_at")
    return None


def reclassify_experiment(record: dict[str, Any]) -> dict[str, Any]:
    """Return a v0.7.1 record while preserving every legacy observation field."""
    result = copy.deepcopy(record)
    current = result.get("classification")
    if current in FINAL_CLASSIFICATIONS and result.get("classificationSchemaVersion") == 1:
        return result
    legacy = result.get("status") or current
    request_status = result.get("requestStatus")
    if legacy in {"TIMED OUT", "TIMEOUT"}:
        classification = {"experimentType": INCONCLUSIVE_TYPE, "classification": "TIMEOUT", "eligibleForAnchorStatistics": False,
                          "resetJitterToleranceSeconds": RESET_JITTER_TOLERANCE_SECONDS, "anchorToleranceSeconds": ANCHOR_CONFIRM_TOLERANCE_SECONDS,
                          "notes": "No conclusive follow-up snapshot arrived before timeout."}
    elif legacy == "CANCELLED":
        classification = {"experimentType": INCONCLUSIVE_TYPE, "classification": "CANCELLED", "eligibleForAnchorStatistics": False,
                          "resetJitterToleranceSeconds": RESET_JITTER_TOLERANCE_SECONDS, "anchorToleranceSeconds": ANCHOR_CONFIRM_TOLERANCE_SECONDS,
                          "notes": "Experiment was cancelled."}
    else:
        classification = classify_anchor_observation(
            previous_reset_at=result.get("previousResetAt", result.get("old_resets_at", _matched_reset(result, "before"))),
            trigger_actual_at=result.get("triggerActualAt", result.get("trigger_epoch", result.get("trigger_at"))),
            window_duration_minutes=result.get("windowDurationMins", result.get("window_duration_minutes", result.get("target_window_duration_minutes"))),
            observed_reset_at=result.get("observedResetAt", result.get("new_resets_at", _matched_reset(result, "after"))),
            request_status=request_status,
        )
    if legacy and legacy != classification["classification"] and "legacyClassification" not in result:
        result["legacyClassification"] = legacy
    result.update(classification)
    result["status"] = classification["classification"]
    result["classificationSchemaVersion"] = 1
    result.setdefault("triggerScheduledAt", result.get("scheduled_at"))
    result.setdefault("requestStatus", request_status or "UNKNOWN")
    # Backward-compatible aliases for existing local consumers.
    result["old_resets_at"] = result.get("previousResetAt")
    result["new_resets_at"] = result.get("observedResetAt")
    result["window_duration_minutes"] = result.get("windowDurationMins")
    result["difference_from_expected_seconds"] = result.get("anchorErrorSeconds")
    return result
