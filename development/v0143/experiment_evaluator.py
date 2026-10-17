"""Read-only, evidence-first evaluation of one real Anchor intervention.

This module does not execute model tasks or alter the window-state algorithm.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from window_state import FIXED, ROLLING, analyze_window_state, as_epoch, bucket_identity, normalize_observation


CONFIRMATION_TOLERANCE_SECONDS = 120
CONTINUOUS_GAP_SECONDS = 180
REQUEST_CORRELATION_SECONDS = 120


def iso_utc(value: Any) -> str | None:
    epoch = as_epoch(value)
    if epoch is None:
        return None
    result = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds")
    return result.replace("+00:00", "Z")


def _segment_before(rows: list[dict[str, Any]], boundary: float) -> list[dict[str, Any]]:
    prior = [row for row in rows if row["observedAt"] < boundary]
    if not prior:
        return []
    segment = [prior[-1]]
    for row in reversed(prior[:-1]):
        if segment[-1]["observedAt"] - row["observedAt"] > CONTINUOUS_GAP_SECONDS:
            break
        segment.append(row)
    return list(reversed(segment))


def _segment_after(rows: list[dict[str, Any]], boundary: float) -> list[dict[str, Any]]:
    later = [row for row in rows if row["observedAt"] >= boundary]
    if not later:
        return []
    segment = [later[0]]
    for row in later[1:]:
        if row["observedAt"] - segment[-1]["observedAt"] > CONTINUOUS_GAP_SECONDS:
            break
        segment.append(row)
    return segment


def _request_events(experiment_id: str, scheduled_runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in scheduled_runs if row.get("experiment_id") == experiment_id
            and row.get("mode") == "live" and row.get("action") == "model_call"]


def evaluate_anchor_experiment(
    experiment: dict[str, Any],
    observations: Iterable[dict[str, Any]],
    scheduled_runs: Iterable[dict[str, Any]],
    *,
    execution_events: Iterable[dict[str, Any]] = (),
    observation_gaps: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Classify from persisted observations and a single authorized real request.

    ``SUCCESS.completed_at`` is the persisted success timestamp; a more precise
    send-start time is retained separately but never substituted for success time.
    """
    experiment_id = str(experiment.get("id") or experiment.get("experimentId") or "")
    identity = (experiment.get("target_limit_id", "codex"), experiment.get("target_slot", "primary"),
                int(experiment.get("windowDurationMins", 300)))
    bucket = ".".join(map(str, identity))
    relevant = sorted(({**raw, **row} for raw in observations if (row := normalize_observation(raw))
                       and bucket_identity(row) == identity), key=lambda row: row["observedAt"])
    runs = list(scheduled_runs)
    own = _request_events(experiment_id, runs)
    started = [row for row in own if row.get("status") == "STARTED"]
    successful = [row for row in own if row.get("status") == "SUCCESS"]
    distinct_attempts = {str(row.get("turn_id") or row.get("request_started_epoch") or row.get("recorded_at"))
                         for row in started}
    request_start = min((as_epoch(row.get("request_started_epoch")) or as_epoch(row.get("recorded_at"))
                         for row in started), default=None)
    success_at = min((as_epoch(row.get("completed_at")) for row in successful
                      if as_epoch(row.get("completed_at")) is not None), default=None)
    request_status = "SUCCESS" if successful else ("FAILED" if any(row.get("status") == "FAILED" for row in own) else "UNKNOWN")
    pre_rows = _segment_before(relevant, request_start) if request_start is not None else []
    post_rows = _segment_after(relevant, success_at) if success_at is not None else []
    pre_state = analyze_window_state(pre_rows)
    first_fixed_index = next((index for index in range(2, len(post_rows))
                              if (state := analyze_window_state(post_rows[:index + 1]))["state"] == FIXED
                              and state["evidence"] == "Strong"), None)
    confirmed_post = post_rows[:first_fixed_index + 1] if first_fixed_index is not None else post_rows
    post_state = analyze_window_state(confirmed_post)
    observed_reset = post_state.get("latestResetAt") if first_fixed_index is not None else None
    expected_reset = success_at + identity[2] * 60 if success_at is not None else None
    error = observed_reset - expected_reset if observed_reset is not None and expected_reset is not None else None

    reasons: list[str] = []
    if pre_state["state"] != ROLLING or pre_state["evidence"] != "Strong":
        reasons.append("PRE_NOT_STRONG_ROLLING")
    if request_status != "SUCCESS":
        reasons.append("REQUEST_NOT_SUCCESS")
    if len(distinct_attempts) != 1 or len(started) != 1 or len(successful) != 1:
        reasons.append("REQUEST_NOT_UNIQUE")
    if post_state["state"] != FIXED or post_state["evidence"] != "Strong":
        reasons.append("POST_NOT_STRONG_FIXED")
    if error is None or abs(error) > CONFIRMATION_TOLERANCE_SECONDS:
        reasons.append("RESET_OUTSIDE_TOLERANCE")
    if not pre_rows or not post_rows or request_start is None or success_at is None:
        reasons.append("MISSING_BOUNDARY_OBSERVATION")
    else:
        if request_start - pre_rows[-1]["observedAt"] > CONTINUOUS_GAP_SECONDS or post_rows[0]["observedAt"] - success_at > CONTINUOUS_GAP_SECONDS:
            reasons.append("TRANSITION_DURING_GAP")
    gap_rows = list(observation_gaps)
    if request_start is not None and confirmed_post:
        if any(row.get("event_type") == "OBSERVATION_GAP"
               and row.get("limit_id") == identity[0] and row.get("slot") == identity[1]
               and as_epoch(row.get("detected_at")) is not None
               and request_start <= as_epoch(row.get("detected_at")) <= confirmed_post[-1]["observedAt"]
               for row in gap_rows):
            reasons.append("TRANSITION_DURING_GAP")
    other_requests = []
    if success_at is not None:
        for row in runs:
            if row.get("experiment_id") != experiment_id and row.get("mode") == "live" and row.get("action") == "model_call" and row.get("status") in {"SUCCESS", "STARTED", "TIMEOUT", "INCONCLUSIVE"}:
                at = as_epoch(row.get("request_started_epoch") or row.get("completed_at"))
                if at is not None and abs(at - success_at) <= REQUEST_CORRELATION_SECONDS:
                    other_requests.append(row)
        own_turns = {row.get("turn_id") for row in own if row.get("turn_id")}
        for row in execution_events:
            result = row.get("result") or {}
            if row.get("experimentId") == experiment_id or result.get("turnId") in own_turns:
                continue
            if row.get("status") in {"SUCCESS", "INCONCLUSIVE", "FAILED", "EXECUTING"} and result.get("modelTaskSent") is True:
                at = as_epoch(row.get("actualSentAt") or row.get("sendAttemptAt") or row.get("actualCompletedAt"))
                if at is not None and abs(at - success_at) <= REQUEST_CORRELATION_SECONDS:
                    other_requests.append(row)
    if other_requests:
        reasons.append("MULTIPLE_POSSIBLE_REQUESTS")
    reasons = list(dict.fromkeys(reasons))
    eligible_blockers = {"PRE_NOT_STRONG_ROLLING", "REQUEST_NOT_SUCCESS", "REQUEST_NOT_UNIQUE",
                         "MISSING_BOUNDARY_OBSERVATION", "TRANSITION_DURING_GAP", "MULTIPLE_POSSIBLE_REQUESTS"}
    if post_state["state"] not in {ROLLING, FIXED} or post_state["evidence"] != "Strong":
        eligible_blockers.add("POST_NOT_STRONG_FIXED")
    eligible = not any(reason in eligible_blockers for reason in reasons)
    confirmed = not reasons
    request = {
        "status": request_status,
        "actualSentAt": iso_utc(request_start),
        "actualCompletedAt": iso_utc(success_at),
        "attemptCount": len(distinct_attempts),
        "timestampPrecision": "success: seconds; send-start: persisted epoch precision",
        "source": "scheduled_runs.jsonl",
        "rawStartedEpoch": min((row.get("request_started_epoch") for row in started
                                if isinstance(row.get("request_started_epoch"), (int, float))), default=None),
        "rawSuccessTimestamp": next((row.get("completed_at") for row in successful
                                     if as_epoch(row.get("completed_at")) == success_at), None),
    }
    summary = {
        "experimentId": experiment_id,
        "label": experiment.get("trialLabel", experiment_id),
        "bucket": bucket,
        "windowDurationMinutes": identity[2],
        "experimentType": "ANCHOR_TRANSITION_CANDIDATE",
        "pre": {"state": pre_state["state"], "evidence": pre_state["evidence"].upper(),
                "slope": pre_state["resetSlope"], "sampleCount": pre_state["samples"],
                "observationSpanSeconds": pre_state["observationSpanSeconds"],
                "segmentSampleCount": len(pre_rows)},
        "request": request,
        "post": {"state": post_state["state"], "evidence": post_state["evidence"].upper(),
                 "slope": post_state["resetSlope"], "sampleCount": post_state["samples"],
                 "observationSpanSeconds": post_state["observationSpanSeconds"],
                 "segmentSampleCount": len(post_rows)},
        "expectedResetAt": iso_utc(expected_reset),
        "observedResetAt": iso_utc(observed_reset),
        "rawObservedResetAtEpoch": observed_reset,
        "anchorErrorSeconds": error,
        "toleranceSeconds": CONFIRMATION_TOLERANCE_SECONDS,
        "eligible": eligible,
        "confirmed": confirmed,
        "classification": "ANCHOR_CONFIRMED" if confirmed else "INCONCLUSIVE",
        "attribution": "AUTHORIZED_REQUEST" if confirmed else ("MULTIPLE_POSSIBLE_REQUESTS" if other_requests else "OBSERVATION_GAP" if "TRANSITION_DURING_GAP" in reasons else "UNKNOWN"),
        "evidenceCompleteness": "COMPLETE" if eligible else "INCOMPLETE",
        "exclusionReasons": reasons,
        "nearbyConfirmedRequestCount": sum(row.get("status") == "SUCCESS" for row in other_requests),
        "nearbyPossibleRequestEventCount": len(other_requests),
        "timezone": "UTC (display may use local Asia/Shanghai)",
        "sourceFiles": ["window_state_observations.jsonl", "scheduled_runs.jsonl", "reset_events.jsonl"],
    }
    summary["evidenceFingerprint"] = hashlib.sha256(json.dumps({
        "experimentId": experiment_id, "pre": pre_rows[-4:], "request": own, "post": confirmed_post,
    }, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {"summary": summary, "preObservations": pre_rows, "postObservations": post_rows,
            "confirmationObservations": confirmed_post, "requestEvents": own}
