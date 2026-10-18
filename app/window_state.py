"""Provider-neutral rolling/fixed quota-window analysis and Anchor attribution."""

from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any, Iterable


UNKNOWN = "UNKNOWN"
ROLLING = "ROLLING"
FIXED = "FIXED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

WINDOW_STATE_MIN_SAMPLES = 3
WINDOW_STATE_MIN_SPAN_SECONDS = 120
WINDOW_STATE_WINDOW_SAMPLES = 4
RESET_JITTER_TOLERANCE_SECONDS = 5
ANCHOR_TOLERANCE_SECONDS = 120


def as_epoch(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def bucket_identity(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (
        item.get("limitId", item.get("limit_id")),
        item.get("slot"),
        item.get("windowDurationMins", item.get("window_duration_minutes")),
    )


def bucket_id(item: dict[str, Any]) -> str:
    identity = bucket_identity(item)
    return ".".join(str(value if value is not None else "unknown") for value in identity)


def normalize_observation(item: dict[str, Any]) -> dict[str, Any] | None:
    observed = as_epoch(item.get("observedAt", item.get("observed_at")))
    reset = as_epoch(item.get("resetAt", item.get("resets_at")))
    duration = item.get("windowDurationMins", item.get("window_duration_minutes"))
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return None
    if observed is None or reset is None or duration <= 0:
        return None
    return {
        "observedAt": observed,
        "limitId": item.get("limitId", item.get("limit_id")),
        "limitName": item.get("limitName", item.get("limit_name")),
        "slot": item.get("slot"),
        "windowDurationMins": duration,
        "usedPercent": item.get("usedPercent", item.get("used_percent")),
        "remainingPercent": item.get("remainingPercent", item.get("remaining_percent")),
        "resetAt": reset,
        "horizonSeconds": round(reset - observed, 3),
        "source": item.get("source"),
    }


def _slope(points: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if len(points) < 2:
        return None, None
    origin = points[0]["observedAt"]
    xs = [point["observedAt"] - origin for point in points]
    ys = [point["resetAt"] for point in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None, None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    residual = max(abs(y - (intercept + slope * x)) for x, y in zip(xs, ys))
    return slope, residual


def analyze_window_state(observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = [item for item in (normalize_observation(raw) for raw in observations) if item]
    normalized.sort(key=lambda item: item["observedAt"])
    deduped: list[dict[str, Any]] = []
    for item in normalized:
        if deduped and item["observedAt"] == deduped[-1]["observedAt"]:
            deduped[-1] = item
        else:
            deduped.append(item)
    points = deduped[-WINDOW_STATE_WINDOW_SAMPLES:]
    samples = len(points)
    span = 0.0 if samples < 2 else points[-1]["observedAt"] - points[0]["observedAt"]
    horizons = [point["horizonSeconds"] for point in points]
    slope, residual = _slope(points)
    result = {
        "state": INSUFFICIENT_DATA if samples < WINDOW_STATE_MIN_SAMPLES or span < WINDOW_STATE_MIN_SPAN_SECONDS else UNKNOWN,
        "samples": samples,
        "observationSpanSeconds": round(span, 3),
        "resetSlope": None if slope is None else round(slope, 6),
        "maxResidualSeconds": None if residual is None else round(residual, 3),
        "horizonMeanSeconds": None if not horizons else round(sum(horizons) / len(horizons), 3),
        "latestHorizonSeconds": None if not horizons else horizons[-1],
        "latestResetAt": None if not points else points[-1]["resetAt"],
        "evidence": "Insufficient",
        "rollingPairs": 0,
        "fixedPairs": 0,
        "bucketId": bucket_id(points[-1]) if points else None,
    }
    if result["state"] == INSUFFICIENT_DATA:
        return result

    rolling_pairs = fixed_pairs = 0
    for previous, current in zip(points, points[1:]):
        dt = current["observedAt"] - previous["observedAt"]
        dr = current["resetAt"] - previous["resetAt"]
        if dt <= 0:
            continue
        tolerance = max(RESET_JITTER_TOLERANCE_SECONDS, dt * 0.15)
        duration_seconds = current["windowDurationMins"] * 60
        horizon_tolerance = max(120.0, duration_seconds * 0.001)
        if abs(dr - dt) <= tolerance and abs(current["horizonSeconds"] - duration_seconds) <= horizon_tolerance:
            rolling_pairs += 1
        if abs(dr) <= RESET_JITTER_TOLERANCE_SECONDS and abs((current["horizonSeconds"] - previous["horizonSeconds"]) + dt) <= tolerance:
            fixed_pairs += 1
    result["rollingPairs"] = rolling_pairs
    result["fixedPairs"] = fixed_pairs
    required = 2
    residual_limit = max(10.0, span * 0.15)
    rolling = rolling_pairs >= required and slope is not None and 0.8 <= slope <= 1.2 and residual is not None and residual <= residual_limit
    fixed = fixed_pairs >= required and slope is not None and -0.05 <= slope <= 0.05 and residual is not None and residual <= max(RESET_JITTER_TOLERANCE_SECONDS, span * 0.05)
    if rolling and not fixed:
        result.update(state=ROLLING, evidence="Strong" if rolling_pairs == len(points) - 1 else "Moderate")
    elif fixed and not rolling:
        result.update(state=FIXED, evidence="Strong" if fixed_pairs == len(points) - 1 else "Moderate")
    return result


def pair_looks_rolling(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    first, second = normalize_observation(previous), normalize_observation(current)
    if not first or not second or bucket_identity(first) != bucket_identity(second):
        return False
    dt = second["observedAt"] - first["observedAt"]
    dr = second["resetAt"] - first["resetAt"]
    if dt <= 0:
        return False
    tolerance = max(RESET_JITTER_TOLERANCE_SECONDS, dt * 0.15)
    horizon_tolerance = max(120.0, second["windowDurationMins"] * 60 * 0.001)
    return abs(dr - dt) <= tolerance and abs(second["horizonSeconds"] - second["windowDurationMins"] * 60) <= horizon_tolerance


class WindowStateAnalyzer:
    def __init__(self, observations: Iterable[dict[str, Any]] = ()):
        self.series: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
        self.last_stable_state: dict[tuple[Any, Any, Any], str] = {}
        for item in observations:
            self.add(item)

    def add(self, observation: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_observation(observation)
        if not normalized:
            return analyze_window_state([])
        identity = bucket_identity(normalized)
        series = self.series.setdefault(identity, [])
        series.append(normalized)
        series.sort(key=lambda item: item["observedAt"])
        del series[:-500]
        result = analyze_window_state(series)
        if result["state"] in {ROLLING, FIXED}:
            self.last_stable_state[identity] = result["state"]
        return result

    def analyze(self, identity: tuple[Any, Any, Any]) -> dict[str, Any]:
        return analyze_window_state(self.series.get(identity, []))

    def all_states(self) -> dict[str, dict[str, Any]]:
        return {bucket_id(series[-1]): self.analyze(identity) for identity, series in self.series.items() if series}

    def observations(self, identity: tuple[Any, Any, Any] | None = None) -> list[dict[str, Any]]:
        if identity is not None:
            return copy.deepcopy(self.series.get(identity, []))
        return [copy.deepcopy(item) for series in self.series.values() for item in series]


def attribute_anchor_experiment(experiment: dict[str, Any], observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(experiment)
    trigger = as_epoch(result.get("triggerActualAt", result.get("trigger_epoch", result.get("trigger_at"))))
    duration = result.get("windowDurationMins", result.get("window_duration_minutes", result.get("target_window_duration_minutes")))
    identity = (result.get("target_limit_id"), result.get("target_slot"), duration)
    relevant = [item for item in (normalize_observation(raw) for raw in observations) if item and bucket_identity(item) == identity]
    # Historical experiments may be reclassified much later. Never let observations
    # from a later experiment leak backwards into the saved trial's post-state.
    completed = as_epoch(result.get("completed_at", result.get("detected_at")))
    if completed is not None:
        relevant = [item for item in relevant if item["observedAt"] <= completed]
    pre = [item for item in relevant if trigger is not None and item["observedAt"] < trigger]
    post = [item for item in relevant if trigger is not None and item["observedAt"] >= trigger]
    pre_state = analyze_window_state(pre)
    post_state = analyze_window_state(post)
    observed_reset = as_epoch(result.get("observedResetAt", result.get("new_resets_at")))
    expected_reset = as_epoch(result.get("expectedResetAt"))
    anchor_error = None if observed_reset is None or expected_reset is None else observed_reset - expected_reset
    request_success = result.get("requestStatus") == "SUCCESS"
    old_classification = result.get("classification", result.get("status"))

    classification = old_classification
    transition = "NONE"
    attribution = "NONE"
    eligible = False
    evidence: list[str] = []
    if pre_state["state"] == ROLLING:
        evidence.append("pre-window ROLLING")
        if request_success:
            evidence.append("request SUCCESS")
            if post_state["state"] == FIXED:
                transition = "ROLLING_TO_FIXED"
                eligible = True
                evidence.append("post-window FIXED")
                if anchor_error is not None and abs(anchor_error) <= ANCHOR_TOLERANCE_SECONDS:
                    classification = "ANCHOR_CONFIRMED_BY_STATE_TRANSITION"
                    attribution = "CONFIRMED_BY_STATE_TRANSITION"
                    evidence.append("fixed reset within ±120 s of trigger + duration")
                else:
                    classification = "ROLLING_TO_FIXED_ANCHOR_CANDIDATE"
                    attribution = "CANDIDATE"
            elif post_state["state"] == ROLLING:
                classification = "ROLLING_WINDOW_NO_ANCHOR"
                attribution = "NOT_OBSERVED"
                evidence.append("post-window remained ROLLING")
            else:
                classification = "ROLLING_TO_FIXED_ANCHOR_CANDIDATE" if post else "ANCHOR_CANDIDATE"
                attribution = "CANDIDATE"
                evidence.append("insufficient post-request stability evidence")
    elif pre_state["state"] == FIXED:
        evidence.append("pre-window FIXED")
        previous_reset = as_epoch(result.get("previousResetAt", result.get("old_resets_at")))
        if trigger is not None and previous_reset is not None and trigger < previous_reset:
            classification = "IN_WINDOW_CONTROL_NO_REANCHOR" if post_state["state"] in {FIXED, INSUFFICIENT_DATA} else "IN_WINDOW_CONTROL_RESET_CHANGED"
            attribution = "IN_WINDOW_CONTROL"
    else:
        if request_success and anchor_error is not None and abs(anchor_error) <= ANCHOR_TOLERANCE_SECONDS:
            classification = "ANCHOR_CANDIDATE"
            attribution = "CANDIDATE"
            evidence.append("timing aligned but pre/post window-state evidence is insufficient")
        elif old_classification in {"ANCHOR_CONFIRMED", "RESET_CHANGED_NOT_ANCHORED", "IN_WINDOW_CONTROL_RESET_CHANGED"}:
            classification = "INCONCLUSIVE"
            attribution = "NONE"
            evidence.append("window-state evidence is insufficient for v0.7.2 attribution")

    # A1 has neither SUCCESS attribution nor state evidence; preserve its conservative v0.7.1 result.
    if old_classification and old_classification != classification and "legacyClassification" not in result:
        result["legacyClassification"] = old_classification
    result.update({
        "classification": classification,
        "status": classification,
        "preWindowState": pre_state["state"],
        "postWindowState": post_state["state"],
        "preStateSamples": pre_state["samples"],
        "postStateSamples": post_state["samples"],
        "preObservationSpanSeconds": pre_state["observationSpanSeconds"],
        "postObservationSpanSeconds": post_state["observationSpanSeconds"],
        "resetSlopeBefore": pre_state["resetSlope"],
        "resetSlopeAfter": post_state["resetSlope"],
        "horizonMeanBefore": pre_state["horizonMeanSeconds"],
        "horizonMeanAfter": post_state["horizonMeanSeconds"],
        "transitionType": transition,
        "transitionDetectedAt": result.get("detected_at") if transition != "NONE" else None,
        "anchorAttribution": attribution,
        "anchorAttributionEvidence": evidence,
        "classificationVersion": 2,
        "eligibleForConfirmedAnchorStatistics": eligible,
    })
    return result
