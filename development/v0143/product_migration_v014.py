"""Schema15 additive/copy migration. Historical evidence is never reclassified."""
import json,shutil
from pathlib import Path
from product_migration_v012 import migrate_to_v012
from storage_manager import register_root
def migrate_to_v014(source,target):
    target=Path(target);marker=target/'runtime/schema-v014.json'
    if marker.exists():return json.loads(marker.read_text(encoding='utf-8'))
    result=migrate_to_v012(source,target,14 if source else 0)
    old=target/'data/recommendation-lifecycle-v0131.json';new=target/'data/recommendation-lifecycle-v014.json'
    if old.exists() and not new.exists():shutil.copy2(old,new)
    store=target/'data/orchestration.json'
    if store.exists():
        data=json.loads(store.read_text(encoding='utf-8'))
        for row in data.get('reminders',[]):row.update(invalidated=True,state='CANCELLED' if row.get('dismissed') else 'INVALIDATED',stateReason='UPGRADE_REVALIDATION_REQUIRED')
        store.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    register_root(target);result.update(dataSchemaVersion=15,fromSchemaVersion=14 if source else None,toVersion='0.14.0',rollback='USE_UNTOUCHED_V0131_ROOT; NO_REVERSE_MERGE')
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps(result,indent=2),encoding='utf-8');return result
