"""v10 copy-only migration; frozen source bytes are always retained in imports."""
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path
from orchestration_models import now_iso


def migrate_v091(source, target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target or source in target.parents or target in source.parents: raise ValueError("MIGRATION_ROOTS_OVERLAP")
    marker=target/"runtime"/"migration-v0.10.json"
    if marker.exists(): return json.loads(marker.read_text(encoding="utf-8"))
    if not (source/"data").is_dir(): return {"status":"NO_SOURCE","modelTaskSent":False}
    manifest={"status":"IMPORTED_READ_ONLY","createdAt":now_iso(),"sourceVersion":"0.9.1","sourceUntouched":True,
              "executionResumed":False,"copied":[],"modelTaskSent":False}
    for folder in ("data","evidence","reports"):
        for src in (source/folder).rglob('*') if (source/folder).is_dir() else []:
            if not src.is_file() or src.is_symlink() or source not in src.resolve().parents: continue
            # No credentials/settings/locks, and no resumed execution runtime.
            if "lab-fallback" in src.relative_to(source).parts: continue
            rel=src.relative_to(source); archive=target/"imports"/"v0.9.1"/rel
            archive.parent.mkdir(parents=True,exist_ok=True)
            if not archive.exists(): shutil.copy2(src,archive)
            dest=target/rel
            if not dest.exists():
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
            manifest["copied"].append({"path":str(rel),"sha256":hashlib.sha256(src.read_bytes()).hexdigest(),"archive":str(archive.relative_to(target))})
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return manifest
