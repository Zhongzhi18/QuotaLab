"""Schema14 copy migration; never claim old reminder delivery as DISPLAYED."""
import json,shutil
from pathlib import Path
from product_migration_v012 import migrate_to_v012
def migrate_to_v0131(source,target):
    target=Path(target);marker=target/'runtime/schema-v0131.json'
    if marker.exists():return json.loads(marker.read_text(encoding='utf-8'))
    result=migrate_to_v012(source,target,13 if source else 0)
    old=target/'data/recommendation-lifecycle-v013.json';new=target/'data/recommendation-lifecycle-v0131.json'
    if old.exists() and not new.exists():shutil.copy2(old,new)
    data=target/'data/orchestration.json'
    if data.exists():
        document=json.loads(data.read_text(encoding='utf-8'))
        for row in document.get('reminders',[]):
            row.setdefault('state','CANCELLED' if row.get('dismissed') else 'INVALIDATED')
            row.setdefault('stateReason','LEGACY_DELIVERY_UNCONFIRMED_REVALIDATION_REQUIRED');row['invalidated']=True
        data.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    result={**result,'dataSchemaVersion':14,'fromSchemaVersion':13 if source else None,'legacySchemaUnversioned':False,
            'toVersion':'0.13.1','rollback':'USE_UNTOUCHED_V013_ROOT; V0131_WRITES_ROLLBACK_UNSUPPORTED'}
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps(result,indent=2),encoding='utf-8');return result
