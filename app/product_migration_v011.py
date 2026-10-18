"""Copy-only v0.10 migration. No writes to prior release data/evidence."""
from pathlib import Path
import hashlib, json, shutil
from orchestration_models import now_iso

def migrate_v010(source,target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target or source in target.parents or target in source.parents:raise ValueError('MIGRATION_ROOTS_OVERLAP')
    marker=target/'runtime'/'migration-v011.json'
    if marker.exists():return json.loads(marker.read_text(encoding='utf-8'))
    if not (source/'data').is_dir():return {'status':'NO_SOURCE'}
    result={'sourceVersion':'0.10','createdAt':now_iso(),'sourceUntouched':True,'executionResumed':False,'files':[]}
    for folder in ('data','evidence','reports'):
        for src in (source/folder).rglob('*'):
            if not src.is_file() or src.is_symlink() or source not in src.resolve().parents:continue
            rel=src.relative_to(source)
            if 'lab-fallback' in rel.parts:continue
            for dest in (target/rel,target/'imports'/'v0.10'/rel):
                if not dest.exists():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
            result['files'].append({'path':str(rel),'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
