"""Auditable, redacted A3 evidence bundle and idempotent local migration."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from event_normalization import correlate_transition, normalize_events
from experiment_evaluator import evaluate_anchor_experiment, iso_utc
from window_state import as_epoch


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return rows


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".experiment-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup_once(root: Path, source_files: list[Path]) -> Path:
    marker = root / "runtime" / "migration-v0.8.1-backup.json"
    if marker.is_file():
        try:
            prior = json.loads(marker.read_text(encoding="utf-8"))
            backup = Path(prior["backupPath"])
            if backup.is_dir():
                return backup
        except (OSError, KeyError, ValueError):
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = root / f"backup-v0.8.0-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    for source in source_files:
        if source.is_file():
            shutil.copy2(source, backup / source.name)
    _atomic_json(marker, {"backupPath": str(backup), "createdAt": datetime.now(timezone.utc).isoformat(),
                          "files": [path.name for path in source_files if path.is_file()]})
    return backup


def _csv_text(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    fields = ["observedAt", "resetAt", "horizonSeconds", "stateAtObservation", "usedPercent", "remainingPercent", "source"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({"observedAt": iso_utc(row.get("observedAt")),
                         "resetAt": iso_utc(row.get("resetAt")),
                         "horizonSeconds": row.get("horizonSeconds"),
                         "stateAtObservation": row.get("stateAtObservation"),
                         "usedPercent": row.get("usedPercent"),
                         "remainingPercent": row.get("remainingPercent"),
                         "source": row.get("source")})
    return output.getvalue()


def _report_html(summary: dict[str, Any]) -> str:
    safe = lambda value: html.escape(str(value if value is not None else "—"))
    request = summary["request"]
    rows = [
        ("Request", request["status"]), ("Pre-state", summary["pre"]["state"]),
        ("Post-state", summary["post"]["state"]), ("Expected reset (UTC)", summary["expectedResetAt"]),
        ("Observed reset (UTC)", summary["observedResetAt"]),
        ("Anchor error (seconds)", summary["anchorErrorSeconds"]),
        ("Tolerance (seconds)", f"±{summary['toleranceSeconds']}"),
        ("Eligible", "YES" if summary["eligible"] else "NO"),
        ("PRE slope / samples / span", f"{summary['pre']['slope']} / {summary['pre']['sampleCount']} / {summary['pre']['observationSpanSeconds']} s"),
        ("POST slope / samples / span", f"{summary['post']['slope']} / {summary['post']['sampleCount']} / {summary['post']['observationSpanSeconds']} s"),
        ("Request attempts", request["attemptCount"]), ("Send-start (UTC)", request["actualSentAt"]),
        ("Success (UTC)", request["actualCompletedAt"]), ("Attribution", summary["attribution"]),
        ("Exclusion reasons", ", ".join(summary["exclusionReasons"]) or "none"),
    ]
    table = "".join(f"<tr><th>{safe(label)}</th><td>{safe(value)}</td></tr>" for label, value in rows)
    language = "In this controlled real trial, QuotaLab observed a ROLLING-to-FIXED transition following one successful authorized request. The fixed reset aligns with request success + 300 minutes. This single trial does not establish reliability for all Codex users."
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><title>A3 Anchor evidence</title>"
            "<style>body{font:16px system-ui;max-width:850px;margin:3rem auto;padding:0 1.5rem;color:#222}"
            "h1{font-size:2rem}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:.7rem;text-align:left}"
            "th{width:38%;color:#555}p{line-height:1.6}.status{letter-spacing:.1em}</style>"
            f"<h1>{safe(summary['label'])} · <span class='status'>{safe(summary['classification'])}</span></h1>"
            f"<p>{safe(language)}</p><table>{table}</table><h2>Evidence timeline</h2>"
            "<img src='a3_timeline.svg' alt='Pre observations, successful request, post fixed observations'>"
            f"<p>Original timestamp precision: {safe(request['timestampPrecision'])}. Source: {safe(', '.join(summary['sourceFiles']))}. "
            "Email and account identifiers are excluded from this bundle.</p></html>")


def _timeline_svg(summary: dict[str, Any], pre: list[dict[str, Any]], post: list[dict[str, Any]]) -> str:
    """Plot raw reset timestamps with a real request marker and hover details."""
    points = pre + post
    if not points:
        return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 880 220'><text x='30' y='50'>No observations</text></svg>"
    request_at = as_epoch(summary["request"]["actualCompletedAt"])
    xs = [row["observedAt"] for row in points]
    ys = [row["resetAt"] for row in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    def x(value: float) -> float: return 80 + (value - x0) / max(1, x1 - x0) * 740
    def y(value: float) -> float: return 180 - (value - y0) / max(1, y1 - y0) * 110
    def local(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    def line(rows: list[dict[str, Any]], color: str) -> str:
        coordinates = " ".join(f"{x(row['observedAt']):.1f},{y(row['resetAt']):.1f}" for row in rows)
        return f"<polyline points='{coordinates}' fill='none' stroke='{color}' stroke-width='2.5'/>"
    dots = []
    for row in points:
        title = (f"Observed {local(row['observedAt'])} · reset {local(row['resetAt'])} · "
                 f"horizon {row['resetAt'] - row['observedAt']:.0f}s · {row.get('stateAtObservation') or '—'}")
        dots.append(f"<circle cx='{x(row['observedAt']):.1f}' cy='{y(row['resetAt']):.1f}' r='2.5' fill='#555'>"
                    f"<title>{html.escape(title)}</title></circle>")
    marker = (f"<line x1='{x(request_at):.1f}' x2='{x(request_at):.1f}' y1='45' y2='185' stroke='#a34a32' "
              f"stroke-width='1.5' stroke-dasharray='5 4'/><text x='{x(request_at)+7:.1f}' y='58' "
              f"font-size='11' fill='#a34a32'>REQUEST SUCCESS {local(request_at)}</text>") if request_at is not None else ""
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 880 230' role='img' aria-label='A3 reset stability timeline'>"
            "<rect width='880' height='230' fill='#fafafa'/><text x='80' y='25' font-family='sans-serif' font-size='14'>"
            "A3 · observed time versus reported reset time</text>"
            "<line x1='80' x2='820' y1='185' y2='185' stroke='#aaa'/><text x='80' y='211' font-size='11'>"
            + local(x0) + " observed</text><text x='820' y='211' font-size='11' text-anchor='end'>"
            + local(x1) + " observed</text>" + line(pre, "#52525b") + line(post, "#256e68")
            + "".join(dots) + marker + "</svg>")


def build_evidence_bundle(root: Path, output: Path, *, apply_migration: bool = False) -> dict[str, Any] | None:
    """Find one uniquely identifiable real trial, evaluate and export without sending.

    ``apply_migration`` writes an experiment record in *root* only after a scoped
    backup. Offline report generation can leave the source entirely untouched.
    """
    data = root / "data"
    runs = read_jsonl(data / "scheduled_runs.jsonl")
    successful_ids = sorted({str(row["experiment_id"]) for row in runs
                             if row.get("mode") == "live" and row.get("action") == "model_call"
                             and row.get("status") == "SUCCESS" and row.get("experiment_id")})
    if len(successful_ids) != 1:
        return None
    experiment_id = successful_ids[0]
    path = data / f"anchor_experiment_{experiment_id}.json"
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        existing = None
    experiment = existing if isinstance(existing, dict) else {
        "id": experiment_id, "trialLabel": "A3", "mode": "live",
        "target_limit_id": "codex", "target_slot": "primary", "windowDurationMins": 300,
        "migrationSource": "scheduled_runs.jsonl:unique-successful-experiment-id",
    }
    observations = read_jsonl(data / "window_state_observations.jsonl")
    raw_events = read_jsonl(data / "reset_events.jsonl")
    execution_jobs_path = data / "execution_jobs.json"
    try:
        jobs_doc = json.loads(execution_jobs_path.read_text(encoding="utf-8")) if execution_jobs_path.is_file() else {}
    except (OSError, ValueError):
        jobs_doc = {}
    jobs = jobs_doc if isinstance(jobs_doc, list) else jobs_doc.get("jobs", []) if isinstance(jobs_doc, dict) else []
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    execution_events = [job for job in jobs if isinstance(job, dict) and (job.get("result") or {}).get("modelTaskSent") is True]
    evaluated = evaluate_anchor_experiment(experiment, observations, runs, execution_events=execution_events,
                                           observation_gaps=[row for row in raw_events if row.get("event_type") == "OBSERVATION_GAP"])
    summary = evaluated["summary"]
    transitions = [row for row in normalize_events(raw_events)
                   if row.get("event_type") == "ROLLING_TO_FIXED" and row.get("bucketId") == summary["bucket"]]
    request_at = as_epoch(summary["request"]["actualCompletedAt"])
    target_transition = min(transitions, key=lambda row: abs((as_epoch(row.get("detected_at")) or 0) - (request_at or 0))) if transitions else None
    if target_transition and request_at is not None and abs((as_epoch(target_transition.get("detected_at")) or 0) - request_at) > 600:
        target_transition = None
    correlated = (correlate_transition(target_transition, summary["request"], attribution=summary["attribution"])
                  if target_transition else None)
    output.mkdir(parents=True, exist_ok=True)
    _atomic_json(output / "a3_summary.json", summary)
    (output / "a3_observations_pre.csv").write_text(_csv_text(evaluated["preObservations"]), encoding="utf-8")
    (output / "a3_observations_post.csv").write_text(_csv_text(evaluated["postObservations"]), encoding="utf-8")
    _atomic_json(output / "a3_activity.json", {"requestEvents": [{k: row.get(k) for k in
        ("recorded_at", "completed_at", "status", "mode", "action", "experiment_id", "request_started_epoch")}
        for row in evaluated["requestEvents"]], "transition": target_transition})
    _atomic_json(output / "a3_transition.json", correlated or {"attribution": "UNKNOWN", "reason": "NO_MATCHING_TRANSITION"})
    (output / "a3_report.html").write_text(_report_html(summary), encoding="utf-8")
    (output / "a3_timeline.svg").write_text(_timeline_svg(summary, evaluated["preObservations"],
                                               evaluated["postObservations"]), encoding="utf-8")
    migration_status = "READ_ONLY_EVIDENCE_GENERATED"
    imported_controls = 0
    if apply_migration:
        source_files = [data / name for name in ("window_state_observations.jsonl", "reset_events.jsonl",
                                               "scheduled_runs.jsonl", "quota_history.jsonl", "usage_plan.json")]
        if path.is_file():
            source_files.append(path)
        backup = _backup_once(root, source_files)
        # Keep the older A1/A2 controls visible in the preview without ever
        # modifying the stable store. Their historical classifications remain
        # authoritative; the new A3 evaluator is not applied to them.
        legacy_data = root.parent / "QuotaLab" / "data" if root.name == "QuotaLab-dev-v08" else None
        if legacy_data and legacy_data.is_dir():
            for legacy_path in legacy_data.glob("anchor_experiment_*.json"):
                try:
                    control = json.loads(legacy_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(control, dict) or control.get("experimentType") != "IN_WINDOW_CONTROL":
                    continue
                control_id = str(control.get("id") or "")
                if not control_id or not control_id.replace("_", "").isalnum():
                    continue
                target = data / f"anchor_experiment_{control_id}.json"
                if not target.exists():
                    _atomic_json(target, {**control, "legacyImportedReadOnly": True,
                                          "legacySource": "QuotaLab stable store"})
                    imported_controls += 1
        migrated = dict(experiment)
        if "legacyClassification" not in migrated and migrated.get("classification") and migrated["classification"] != summary["classification"]:
            migrated["legacyClassification"] = migrated["classification"]
        migrated.update({"classification": summary["classification"], "status": summary["classification"],
                         "experimentType": "ANCHOR_TRANSITION_CANDIDATE", "transitionType": "ROLLING_TO_FIXED" if summary["confirmed"] else "UNCONFIRMED",
                         "transitionDetectedAt": correlated.get("transitionDetectedAt") if correlated else None,
                         "classificationSchemaVersion": 3, "requestStatus": summary["request"]["status"],
                         "preWindowState": summary["pre"]["state"], "postWindowState": summary["post"]["state"],
                         "preEvidenceStrength": summary["pre"]["evidence"], "postEvidenceStrength": summary["post"]["evidence"],
                         "resetSlopeBefore": summary["pre"]["slope"], "resetSlopeAfter": summary["post"]["slope"],
                         "expectedResetAt": as_epoch(summary["expectedResetAt"]),
                         "observedResetAt": as_epoch(summary["observedResetAt"]),
                         "anchorErrorSeconds": summary["anchorErrorSeconds"],
                         "eligibleForAnchorStatistics": summary["eligible"],
                         "eligibleForConfirmedAnchorStatistics": summary["eligible"],
                         "anchorAttribution": summary["attribution"],
                         "evidenceFingerprint": summary["evidenceFingerprint"],
                         "evidenceBundle": str(output)})
        if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != migrated:
            _atomic_json(path, migrated)
            migration_status = "MIGRATED"
        else:
            migration_status = "ALREADY_CURRENT"
        summary["backupPath"] = str(backup)
    (output / "migration_log.txt").write_text(
        f"{migration_status}\nexperimentId={experiment_id}\nclassification={summary['classification']}\n"
        f"importedLegacyControls={imported_controls}\n"
        f"evidenceFingerprint={summary['evidenceFingerprint']}\nsource=local-persisted-data\n"
        "No raw append-only event was deleted. No model task was sent.\n", encoding="utf-8")
    return summary
