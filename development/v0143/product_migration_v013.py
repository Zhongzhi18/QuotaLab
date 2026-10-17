"""Copy-only schema13. v12 input and historical records are never modified."""
import json,shutil
from pathlib import Path
from product_migration_v012 import migrate_to_v012

def migrate_to_v013(source,target):
    target=Path(target);marker=target/'runtime/schema-v013.json'
    if marker.exists():return json.loads(marker.read_text(encoding='utf-8'))
    result=migrate_to_v012(source,target,12 if source else 0)
    old=target/'data/recommendation-lifecycle-v012.json';new=target/'data/recommendation-lifecycle-v013.json'
    if old.exists() and not new.exists():shutil.copy2(old,new)
    result={**result,'dataSchemaVersion':13,'fromSchemaVersion':12 if source else None,'toVersion':13,'rollback':'USE_UNTOUCHED_V012_ROOT; V013_WRITES_ROLLBACK_UNSUPPORTED'}
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
