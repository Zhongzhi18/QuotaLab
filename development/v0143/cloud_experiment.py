"""Read-only A4/A5 evidence model. No model/cloud execution interfaces."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from orchestration_models import epoch, iso, now_iso
from window_state import analyze_window_state, bucket_id

BUCKET = "codex.primary.300"
TYPES = {"OFFICIAL_CLOUD_CONTROLLED_TEST", "CLOUD_OFFLINE_LOCAL_TEST"}


@dataclass(frozen=True)
class OfficialRunReceipt:
    """Future trusted status-adapter output; never constructed from an HTTP payload."""
    experimentId: str
    runAt: str
    reference: str
    sourceUrl: str


def rows_for(observations):
    return sorted([copy.deepcopy(o) for o in observations if bucket_id(o) == BUCKET],
                  key=lambda o: epoch(o["observedAt"]))


def new_experiment(kind, observations):
    if kind not in TYPES:
        raise ValueError("INVALID_CLOUD_EXPERIMENT_TYPE")
    rows = rows_for(observations)[-4:]
    pre = analyze_window_state(rows)
    return {"experimentId": uuid.uuid4().hex, "experimentType": kind, "provider": "openai-codex",
            "bucket": BUCKET, "createdAt": now_iso(), "status": "RECOMMENDED",
            "preState": pre.get("state"), "preEvidence": pre, "preObservations": rows,
            "handoffId": None, "officialScheduledAt": None, "officialRunAt": None,
            "officialRunTimeSource": "UNAVAILABLE", "postState": "NOT_OBSERVED", "postEvidence": {},
            "expectedResetAt": None, "observedResetAt": None, "anchorErrorSeconds": None,
            "observationGap": kind == "CLOUD_OFFLINE_LOCAL_TEST", "confounders": [],
            "classification": "CLOUD_INCONCLUSIVE", "verificationConfidence": "INCONCLUSIVE",
            "officialCreatedByQuotaLab": False, "modelTaskSent": False}


def cloud_instruction(rec):
    return (f"Experimental ONE cloud-only run at {rec['recommendedExecutionAt']} UTC.\n"
            f"Heavy demand: {rec['demandStart']} — {rec['demandEnd']}.\n"
            f"Target policy: {rec.get('targetBoundaryPolicy', rec['objective'])}; expected boundary: {rec['expectedResetAt']} (not guaranteed).\n"
            "Only if your official UI supports a cloud scheduled task, schedule ONE run. Do not select a local-project automation.\n"
            "Prompt: Experimental minimal task. Reply exactly OK. Do not use tools, read files, access private data, modify anything or perform real work.\n"
            "No recurrence, retry or immediate catch-up. Consumption is not guaranteed negligible. "
            "Record the official actual run time, not the planned time. QuotaLab has NOT created any task; "
            "cloud participation in the observed subscription bucket is experimental and unproven.")


class CloudExperimentVerifier:
    def verify(self, experiment, observations, *, lifecycle=(), requests=(), official_receipt=None):
        result = copy.deepcopy(experiment)
        trusted = isinstance(official_receipt, OfficialRunReceipt) and official_receipt.experimentId == experiment["experimentId"] and bool(official_receipt.reference) and official_receipt.sourceUrl.startswith("https://")
        run = official_receipt.runAt if trusted else experiment.get("officialRunAt")
        if trusted:
            result.update(officialRunAt=iso(run), officialRunTimeSource="DOCUMENTED_OFFICIAL_STATUS")
        if not run:
            result.update(classification="CLOUD_REQUEST_NOT_RUN" if experiment.get("userReportedNotRun") else "CLOUD_INCONCLUSIVE",
                          verificationConfidence="INCONCLUSIVE")
            return result
        pivot = epoch(run)
        pre_rows = experiment.get("preObservations", [])
        pre = analyze_window_state(pre_rows)
        after = [o for o in rows_for(observations) if pivot <= epoch(o["observedAt"]) <= pivot+86400]
        # First contiguous post-run segment only; never stitch across missing observations.
        post_rows = []
        for row in after:
            if post_rows and epoch(row["observedAt"])-epoch(post_rows[-1]["observedAt"]) > 180:
                break
            post_rows.append(row)
            if len(post_rows) == 4: break
        post = analyze_window_state(post_rows)
        before = [o for o in rows_for(observations) if epoch(o["observedAt"]) < pivot]
        immediate_pre = analyze_window_state(before[-4:])
        bracket = bool(before and after and epoch(after[0]["observedAt"])-epoch(before[-1]["observedAt"]) <= 180)
        relevant = [o for o in rows_for(observations) if pre_rows and epoch(pre_rows[0]["observedAt"]) <= epoch(o["observedAt"]) <= (epoch(post_rows[-1]["observedAt"]) if post_rows else pivot)]
        gaps = [{"start": a["observedAt"], "end": b["observedAt"], "seconds": epoch(b["observedAt"])-epoch(a["observedAt"])}
                for a,b in zip(relevant,relevant[1:]) if epoch(b["observedAt"])-epoch(a["observedAt"]) > 180]
        confounders = list(experiment.get("reportedConfounders", []))
        end = epoch(post_rows[-1]["observedAt"]) if post_rows else pivot+180
        begin = epoch(pre_rows[0]["observedAt"]) if pre_rows else pivot-180
        if any(begin-180 <= epoch(e["timestamp"]) <= end+180 for e in lifecycle):
            confounders.append("RESTART_ASSOCIATED_TRANSITION")
        if any(begin <= epoch(r.get("started_at") or r.get("recorded_at")) <= end for r in requests if r.get("started_at") or r.get("recorded_at")):
            confounders.append("OTHER_KNOWN_REQUEST")
        gap = not bracket or bool(gaps) or experiment["experimentType"] == "CLOUD_OFFLINE_LOCAL_TEST"
        observed = post_rows[-1].get("resetAt") if post_rows else None
        error = epoch(observed)-(pivot+18000) if observed is not None else None
        valid_pre = pre.get("state") == "ROLLING" and pre.get("evidence") == "Strong"
        if immediate_pre.get("state") == "FIXED":
            valid_pre = False; confounders.append("FIXED_BEFORE_RUN")
        valid_post = post.get("evidence") == "Strong"
        if pre_rows and epoch(pre_rows[-1]["observedAt"]) >= pivot:
            valid_pre = False; confounders.append("PRE_NOT_BEFORE_RUN")
        classification, confidence = "CLOUD_INCONCLUSIVE", "PARTIAL" if post_rows else "INCONCLUSIVE"
        if valid_pre and valid_post and not confounders:
            if post.get("state") == "ROLLING": classification = "CLOUD_NO_REANCHOR"
            elif post.get("state") == "FIXED":
                classification = "CLOUD_ANCHOR_CANDIDATE" if abs(error) <= 120 else "CLOUD_RESET_CHANGED_NOT_MATCHED"
            if not gap: confidence = "STRONG"
        if gap and experiment["experimentType"] == "OFFICIAL_CLOUD_CONTROLLED_TEST":
            classification = "CLOUD_INCONCLUSIVE"
        if trusted and classification == "CLOUD_ANCHOR_CANDIDATE" and not gap:
            classification, confidence = "CLOUD_ANCHOR_CONFIRMED", "DIRECT"
        # With an offline gap a reset match is a post-hoc candidate, never a continuous/confirmed anchor.
        # No public timestamp reader is implemented; user reports cannot enable CONFIRMED or DIRECT.
        result.update(preState=pre.get("state"), preEvidence=pre, postState=post.get("state"), postEvidence=post,
                      postObservations=post_rows, expectedResetAt=iso(pivot+18000),
                      observedResetAt=iso(observed) if observed is not None else None, anchorErrorSeconds=error,
                      observationGap=gap, gaps=gaps, confounders=sorted(set(confounders)),
                      classification=classification, verificationConfidence=confidence,
                      verificationMethod="POST_HOC" if gap else "CONTINUOUS_OBSERVATION",
                      eligibleForCloudAnchorStatistics=classification in {"CLOUD_ANCHOR_CANDIDATE", "CLOUD_ANCHOR_CONFIRMED"} and not gap,
                      eligibleForConfirmedCloudAnchorStatistics=classification == "CLOUD_ANCHOR_CONFIRMED" and not gap,
                      status="VERIFIED", verifiedAt=now_iso(), sameBucketProven=False, causalClaim=False)
        return result
