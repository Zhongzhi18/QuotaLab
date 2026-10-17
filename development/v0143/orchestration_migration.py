"""Copy-once preview import: never writes into v08/stable roots, never resumes jobs."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from orchestration_models import now_iso


def migrate_v081(source: Path, target: Path, controls: Path | None = None):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("MIGRATION_ROOTS_OVERLAP")
    marker = target / "runtime" / "migration-v0.9.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    if not (source/"data").is_dir():
        return {"status": "NO_SOURCE", "copied": [], "modelTaskSent": False}
    manifest = {"status": "IMPORTED_READ_ONLY", "createdAt": now_iso(), "sourceVersion": "0.8.1-evidence",
                "copied": [], "sourceUntouched": True, "executionResumed": False, "modelTaskSent": False}
    # Only app data/reports/evidence. Never credentials, runtime locks, cookies or user settings.
    for folder in ("data", "evidence", "reports"):
        for src in (source/folder).rglob("*") if (source/folder).is_dir() else []:
            if not src.is_file() or src.is_symlink() or source not in src.resolve().parents:
                continue
            relative = src.relative_to(source)
            dest = target/relative
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            if dest.exists():
                # Existing preview evidence is never overwritten; preserve source in archive instead.
                dest = target/"imports"/"v0.8.1"/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): shutil.copy2(src, dest)
            manifest["copied"].append({"path": str(relative), "destination": str(dest.relative_to(target)), "sha256": digest})
    # Imported pending jobs kept byte-for-byte as evidence, but not handed to v09 runtime.
    jobs = target/"data"/"execution_jobs.json"
    if jobs.exists():
        archived = target/"imports"/"v0.8.1"/"execution_jobs.json"
        archived.parent.mkdir(parents=True, exist_ok=True)
        if not archived.exists(): shutil.copy2(jobs, archived)
    if controls and controls.is_dir():
        for src in (controls/"data").glob("anchor_experiment_*.json"):
            try:
                record = json.loads(src.read_text(encoding="utf-8"))
            except (OSError, ValueError): continue
            if record.get("experimentType") != "IN_WINDOW_CONTROL": continue
            dest = target/"data"/src.name
            if dest.exists(): continue
            # Preserve exact original in import archive and existing read-only legacy semantics in Lab.
            archive = target/"imports"/"stable-controls"/src.name
            archive.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, archive)
            record["legacyImportedReadOnly"] = True
            record["eligibleForAnchorStatistics"] = False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["copied"].append({"path": src.name, "destination": str(dest.relative_to(target)), "sha256": hashlib.sha256(src.read_bytes()).hexdigest(), "control": True})
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
