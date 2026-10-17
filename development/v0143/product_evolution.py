"""Additive v014 services; no model dispatch and no mutation of research evidence."""
import copy,json,threading,time,uuid,sys
from collections import deque
from pathlib import Path
from orchestration_models import epoch,iso
from file_persistence import replace_file
from storage_manager import StorageManager
from update_checker import UpdateChecker
from usage_intelligence import analyze,capacity,forecast,research_summary
class ProductEvolution:
    def __init__(self,root,current):
        self.root=Path(root);self.path=self.root/'data/product-evolution-v014.json';self.lock=threading.RLock();self.storage=StorageManager(root,install_dir=Path(sys.executable).parent if getattr(sys,'frozen',False) else None);self.updates=UpdateChecker(root,current);self.cache=None;self.cache_at=0
        try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.data={}
        self.data.setdefault('onboarding',{'step':0,'purpose':'Coding','intensity':'MEDIUM','completed':False});self.data.setdefault('outcomes',[]);self.data.setdefault('receipts',{});self.updates.start()
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8');replace_file(temp,self.path);self.cache=None
    def public(self,snapshot,service):
        with self.lock:
            now=time.time();zone=service.data['preferences']['timezone'];capacity_data=capacity(snapshot,service.public_burn());duration=capacity_data.get('windowDurationMinutes')
            if self.cache is None or now-self.cache_at>=60 or self.cache.get('timezone')!=zone or self.cache.get('quotaWindowDurationMinutes')!=duration:
                observations=[];history=self.root/'data/quota_history.jsonl'
                if history.exists():
                    with history.open('rb') as f:
                        offset=max(0,history.stat().st_size-8*1024*1024);f.seek(offset);raw=f.read(8*1024*1024)
                        lines=raw.splitlines()
                        if offset and lines:lines=lines[1:]
                        for line in lines[-10000:]:
                            try:
                                row=json.loads(line)
                                if isinstance(row,dict):observations.append(row)
                            except (ValueError,UnicodeError):pass
                self.cache=analyze(self.data['outcomes'],service.data['demands'],observations,zone,now,duration);self.cache_at=now
                self.cache['historyScope']='LAST_10000_ROWS_MAX_8_MIB';self.cache['historyIsSampled']=True
            onboarding=copy.deepcopy(self.data['onboarding']);onboarding['completed']=bool(snapshot.get('setup',{}).get('completed'))
            return {'schemaVersion':15,'sampleMode':'MOCK' if snapshot.get('status')=='MOCK DATA' else 'LIVE','onboarding':onboarding,'updates':self.updates.public(),'storage':self.storage.summary(),'capacity':capacity_data,'usage':copy.deepcopy(self.cache),'forecast':forecast(service.data['demands'],zone,self.cache,now),'research':research_summary(snapshot.get('experiments',[])) if service.data['preferences'].get('researchMode') else None,'outcomes':[dict(x) for x in self.data['outcomes'][-30:]]}
    def handle(self,action,payload,state):
        # Network I/O never holds the product-data lock or blocks quota polling.
        if action=='update-source':return self.updates.configure(payload.get('url'))
        if action=='update-check':return self.updates.check()
        if action=='update-offer':return self.updates.offer()
        if action=='update-download':return self.updates.download(payload.get('token'),payload.get('confirmed'))
        with self.lock:
            if action=='onboarding':
                step=payload.get('step');purpose=payload.get('purpose',self.data['onboarding']['purpose']);intensity=payload.get('intensity',self.data['onboarding']['intensity'])
                if type(step) is not int or step not in range(4) or purpose not in {'Coding','Research','Writing'} or intensity not in {'LIGHT','MEDIUM','HEAVY'}:raise ValueError('INVALID_ONBOARDING_PREFERENCES')
                self.data['onboarding'].update(step=step,purpose=purpose,intensity=intensity);self.save();return self.data['onboarding']
            if action=='onboarding-complete':
                if payload.get('confirmed') is not True:raise ValueError('ONBOARDING_CONFIRMATION_REQUIRED')
                self.data['onboarding']['completed']=True;self.save();state.settings['setup_completed']=True
                # Persist through the same settings serializer as the existing app.
                import quota_lab
                quota_lab.save_settings(state.settings)
                return {'completed':True,'connected':state.public()['setup']['ready']}
            if action=='onboarding-restart':
                self.data['onboarding']['step']=0;self.save();state.settings['setup_completed']=False
                import quota_lab
                quota_lab.save_settings(state.settings);return {'completed':False}
            if action=='outcome':
                did=payload.get('demandId');d=next((d for d in state.orchestrator.data['demands'] if d['id']==did),None);key=payload.get('submissionId');status=payload.get('status');minutes=payload.get('actualMinutes')
                if not d or epoch(d['expectedStart'])>time.time():raise ValueError('OUTCOME_REQUIRES_STARTED_DEMAND')
                if status not in {'COMPLETED','PARTIAL','ABANDONED'} or type(minutes) not in {int,float} or not 0<=minutes<=10080 or not isinstance(key,str) or not 0<len(key)<=80:raise ValueError('INVALID_OUTCOME')
                fingerprint=(did,status,minutes)
                if key in self.data['receipts']:
                    receipt=self.data['receipts'][key]
                    if tuple(receipt['fingerprint'])!=fingerprint:raise ValueError('OUTCOME_IDEMPOTENCY_CONFLICT')
                    return next(x for x in self.data['outcomes'] if x['id']==receipt['id'])
                row={'id':uuid.uuid4().hex,'demandId':did,'status':status,'actualMinutes':minutes,'recordedAt':iso(time.time()),'source':'USER_REPORTED'};self.data['outcomes'].append(row);self.data['receipts'][key]={'fingerprint':fingerprint,'id':row['id']};self.save();return row
            if action=='update-source':return self.updates.configure(payload.get('url'))
            if action=='update-check':return self.updates.check()
            if action=='update-offer':return self.updates.offer()
            if action=='update-download':return self.updates.download(payload.get('token'),payload.get('confirmed'))
            if action=='storage-preview':return self.storage.preview(payload.get('categories'))
            if action=='storage-clean':return self.storage.clean(payload.get('token'),payload.get('confirmed'))
            raise ValueError('UNKNOWN_EVOLUTION_ACTION')
