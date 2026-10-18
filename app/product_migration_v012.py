"""Schema12 copy migration with immutable backup and explicit rollback boundary."""
import hashlib, json, shutil
from pathlib import Path
from orchestration_models import now_iso

def migrate_to_v012(source, target, from_version=11):
    source,target=Path(source).resolve() if source is not None else None,Path(target).resolve()
    if source is not None and (source==target or source in target.parents or target in source.parents):raise ValueError('MIGRATION_ROOTS_OVERLAP')
    marker=target/'runtime'/'schema-v012.json'
    if marker.exists():return json.loads(marker.read_text(encoding='utf-8'))
    result={'dataSchemaVersion':12,'fromVersion':from_version,'toVersion':12,'startedAt':now_iso(),'completedAt':None,
            'fromAppVersion':f'0.{from_version}.0' if from_version else None,'fromSchemaVersion':None,'legacySchemaUnversioned':True,
            'filesAffected':[],'backupManifest':[],'status':'STARTED','executionResumed':False,'rollback':'USE_UNTOUCHED_OLD_ROOT_NOT_V012_DATA'}
    target.mkdir(parents=True,exist_ok=True)
    if source is not None and source.is_dir():
        for folder in ('data','evidence','reports'):
            for src in (source/folder).rglob('*'):
                if not src.is_file() or src.is_symlink() or source not in src.resolve().parents:continue
                rel=src.relative_to(source)
                if 'lab-fallback' in rel.parts:continue
                backup=target/'backups'/f'schema-{from_version}'/rel;backup.parent.mkdir(parents=True,exist_ok=True)
                if not backup.exists():shutil.copy2(src,backup)
                dest=target/rel
                if src.name not in {'execution_jobs.json','execution_settings.json','plan.json','usage_plan.json'} and not dest.exists():
                    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
                result['filesAffected'].append(str(rel));result['backupManifest'].append({'relativePath':str(rel),'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
        # Settings only: safe UI prefs, never execution switches or pending jobs.
        settings=source/'settings.json'
        if settings.exists():
            backup=target/'backups'/f'schema-{from_version}'/'settings.json';backup.parent.mkdir(parents=True,exist_ok=True)
            if not backup.exists():shutil.copy2(settings,backup)
            old=json.loads(settings.read_text(encoding='utf-8'))
            safe={k:old[k] for k in ('setup_completed','language','theme','codex_path') if k in old}
            if not (target/'settings.json').exists():(target/'settings.json').write_text(json.dumps(safe),encoding='utf-8')
            result['filesAffected'].append('settings.json')
            result['backupManifest'].append({'relativePath':'settings.json','sha256':hashlib.sha256(settings.read_bytes()).hexdigest()})
        old_sidecar=target/'data'/'recommendation-lifecycle-v011.json'
        if old_sidecar.exists():
            shutil.copy2(old_sidecar,target/'data'/'recommendation-lifecycle-v012.json')
            result['filesAffected'].append('data/recommendation-lifecycle-v012.json')
    result.update(status='COMPLETED',completedAt=now_iso())
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
