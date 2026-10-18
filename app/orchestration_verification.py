"""Post-hoc evidence grading without inventing continuity across offline gaps."""
from __future__ import annotations

from orchestration_models import epoch, iso
from window_state import analyze_window_state


def restart_transition(event, lifecycle_events, tolerance=180):
    if event.get("event_type") != "ROLLING_TO_FIXED":
        return event
    at = epoch(event["detected_at"])
    nearby = [e for e in lifecycle_events if abs(epoch(e["timestamp"])-at) <= tolerance]
    if not nearby:
        return event
    return {**event, "originalEventType": event["event_type"], "event_type": "RESTART_ASSOCIATED_TRANSITION",
            "eligibleForAnchorStatistics": False, "eligibleForConfirmedAnchorStatistics": False,
            "lifecycleEvidence": nearby, "attribution": "INCONCLUSIVE"}


class VerificationEngine:
    def verify(self, recommendation, observations, *, run=None, lifecycle_events=(), max_gap=180):
        run = run or {}
        expected = recommendation.get("recommendedExecutionAt")
        run_at = run.get("observedRunAt")
        official_verified = run.get("provenance") == "DOCUMENTED_OFFICIAL_STATUS"
        direct_verified = run.get("provenance") == "LOCAL_SUCCESS_EVENT"
        pivot = epoch(run_at or expected) if (run_at or expected) else None
        rows = sorted([o for o in observations if o.get("bucketId") == recommendation.get("bucketId")], key=lambda o: epoch(o["observedAt"]))
        if pivot is not None:
            previous = [o for o in rows if epoch(o["observedAt"]) < pivot][-4:]
            subsequent = [o for o in rows if pivot <= epoch(o["observedAt"]) <= pivot+24*3600]
            rows = previous + subsequent
        before = [o for o in rows if pivot is not None and epoch(o["observedAt"]) < pivot]
        after = [o for o in rows if pivot is not None and epoch(o["observedAt"]) >= pivot]
        gaps = [{"start": a["observedAt"], "end": b["observedAt"], "seconds": epoch(b["observedAt"])-epoch(a["observedAt"])}
                for a,b in zip(rows, rows[1:]) if epoch(b["observedAt"])-epoch(a["observedAt"]) > max_gap]
        bracketed = bool(before and after and epoch(after[0]["observedAt"])-epoch(before[-1]["observedAt"])<=max_gap)
        pre = analyze_window_state(before[-4:]) if before else {"state": "INSUFFICIENT_DATA"}
        # Use the first post-event confirmation, not a far-future segment after another reset.
        post_segment = []
        for row in after:
            if post_segment and epoch(row["observedAt"])-epoch(post_segment[-1]["observedAt"]) > max_gap:
                break
            post_segment.append(row)
            if len(post_segment) >= 4: break
        post = analyze_window_state(post_segment) if post_segment else {"state": "INSUFFICIENT_DATA"}
        observed_reset = post_segment[-1].get("resetAt") if post_segment else None
        expected_reset = (pivot+float(recommendation["windowDuration"])*60) if pivot is not None else None
        error = epoch(observed_reset)-expected_reset if observed_reset is not None and expected_reset is not None else None
        nearby_restart = [e for e in lifecycle_events if pivot is not None and abs(epoch(e["timestamp"])-pivot)<=max_gap]
        matching = post.get("state") == "FIXED" and error is not None and abs(error)<=120
        confidence = "INCONCLUSIVE"
        if after:
            confidence = "PARTIAL"
        if official_verified and matching and not nearby_restart:
            confidence = "STRONG"
        if direct_verified and bracketed and not gaps and not nearby_restart and pre.get("state") == "ROLLING" and matching:
            confidence = "DIRECT"
        delta = None
        if before and after and before[-1].get("remainingPercent") is not None and after[0].get("remainingPercent") is not None:
            delta = float(after[0]["remainingPercent"])-float(before[-1]["remainingPercent"])
        return {"recommendationId": recommendation["id"], "plannedAt": recommendation["generatedAt"],
                "executionBackend": recommendation["backend"], "expectedRunAt": expected,
                "observedRunAt": run_at, "runProvenance": run.get("provenance", "UNAVAILABLE"),
                "preWindowState": pre.get("state"), "postWindowState": post.get("state"),
                "expectedResetAt": iso(expected_reset) if expected_reset is not None else None,
                "observedResetAt": iso(observed_reset) if observed_reset is not None else None,
                "anchorError": error, "quotaDelta": delta, "verificationConfidence": confidence,
                "gaps": gaps, "observationGap": not bracketed or bool(gaps),
                "method": "CONTINUOUS_OBSERVATION" if confidence == "DIRECT" else "POST_HOC",
                "restartAssociated": bool(nearby_restart), "eligibleForAnchorStatistics": confidence == "DIRECT" and not nearby_restart,
                "sameBucketProven": False, "causalClaim": False,
                "note": "User-reported scheduling/run times are not official status. Cloud same-bucket causality remains unverified."}
