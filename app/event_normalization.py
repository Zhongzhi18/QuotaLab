"""Lossless normalization of append-only window events for presentation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from window_state import as_epoch


def event_bucket(row: dict[str, Any]) -> str:
    return ".".join(str(value if value is not None else "unknown") for value in
                    (row.get("limit_id"), row.get("slot"), row.get("window_duration_minutes")))


def event_fingerprint(row: dict[str, Any]) -> str:
    """Identify an exact logical transition, including bucket and event epoch.

    Two distinct buckets at the same time are deliberately never merged.
    """
    key = (row.get("event_type"), event_bucket(row), as_epoch(row.get("new_resets_at")),
           as_epoch(row.get("detected_at")), row.get("experiment_id"))
    return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def normalize_events(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        fingerprint = event_fingerprint(raw)
        raw_id = raw.get("event_id") or f"raw:{index}:{hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}"
        source_ids = raw.get("rawEventIds") if isinstance(raw.get("rawEventIds"), list) else [raw_id]
        if fingerprint not in canonical:
            canonical[fingerprint] = {**raw, "bucketId": event_bucket(raw),
                                      "eventFingerprint": fingerprint, "rawEventIds": []}
        canonical[fingerprint]["rawEventIds"].extend(source_ids)
    return list(canonical.values())


def correlate_transition(row: dict[str, Any], request: dict[str, Any] | None,
                         *, attribution: str = "UNKNOWN", observation_gap: bool = False) -> dict[str, Any]:
    detected = as_epoch(row.get("detected_at"))
    request_at = as_epoch((request or {}).get("actualCompletedAt"))
    return {
        "bucket": event_bucket(row),
        "transitionDetectedAt": row.get("detected_at"),
        "transitionEstimatedAt": None,
        "newResetAt": row.get("new_resets_at"),
        "nearestRequestEvent": request,
        "nearestRequestDeltaSeconds": None if detected is None or request_at is None else detected - request_at,
        "nearestSchedulerEvent": None,
        "usageDelta": row.get("usage_delta"),
        "observationGap": observation_gap,
        "attribution": attribution,
        "eventFingerprint": event_fingerprint(row),
    }
