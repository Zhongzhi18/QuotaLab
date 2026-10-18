"""Owned storage only. Cleanup is recoverable; total deletion is explicit and guarded."""
import json,os,shutil,time,uuid
from pathlib import Path
OWNER='.quotalab-data-owner-v014.json'
APP_ID='QuotaLab-v014'
def linked(p):
    return p.is_symlink() or bool(getattr(p.lstat(),'st_file_attributes',0)&0x400)
def checked_root(root,require_owner=True):
    raw=Path(root).absolute();resolved=raw.resolve()
    forbidden={Path.home().resolve(),Path(__file__).resolve().parent,Path(__file__).resolve().parents[2]}
    if len(resolved.parts)<4 or resolved in forbidden or resolved.parent==resolved or raw!=resolved:raise ValueError('UNSAFE_DATA_ROOT')
    if any((resolved/name).exists() for name in ('.git','quota_lab.py','auth.json')):raise ValueError('UNSAFE_DATA_ROOT')
    for p in (raw,*raw.parents):
        if p.exists() and linked(p):raise ValueError('REPARSE_ROOT_REFUSED')
    if require_owner:
        try:owner=json.loads((resolved/OWNER).read_text(encoding='utf-8'))
        except (OSError,ValueError):raise ValueError('DATA_OWNERSHIP_UNVERIFIED')
        if owner.get('appId')!=APP_ID or owner.get('root')!=str(resolved):raise ValueError('DATA_OWNERSHIP_UNVERIFIED')
    return resolved
def register_root(root):
    root=checked_root(root,False);root.mkdir(parents=True,exist_ok=True)
    marker=root/OWNER
    if not marker.exists():marker.write_text(json.dumps({'appId':APP_ID,'root':str(root),'schema':15}),encoding='utf-8')
    return checked_root(root)
def safe_files(root,folder):
    base=root/folder
    if not base.exists():return []
    result=[]
    for current,dirs,files in os.walk(base,followlinks=False):
        path=Path(current)
        if linked(path):dirs[:]=[];continue
        dirs[:]=[d for d in dirs if not linked(path/d)]
        for name in files:
            p=path/name
            if not linked(p) and root in p.resolve().parents:result.append(p)
    return result
class StorageManager:
    def __init__(self,root,clock=time.time,install_dir=None):self.root=register_root(root);self.clock=clock;self.tokens={};self._stats=None;self._at=0;self.install_dir=Path(install_dir).resolve() if install_dir else None
    def summary(self):
        now=self.clock()
        if self._stats is not None and now-self._at<30:return self._stats
        sizes={name:sum(p.stat().st_size for p in safe_files(self.root,folder)) for name,folder in [('data','data'),('logs','logs'),('cache','cache'),('temporary','tmp'),('downloads','downloads'),('backups','backups'),('recovery','trash')]}
        evidence_bytes=sum(p.stat().st_size for folder in ('evidence','reports') for p in safe_files(self.root,folder))
        state_bytes=sum(p.stat().st_size for p in self.root.iterdir() if p.is_file() and not linked(p))+sum(p.stat().st_size for p in safe_files(self.root,'runtime'))
        installation={'status':'NOT_PACKAGED','reviewCandidateFiles':None,'candidateBytes':None}
        if self.install_dir and (self.install_dir/'QuotaLab.exe').is_file() and (self.install_dir/'_internal').is_dir() and not linked(self.install_dir):
            candidates=[p for p in self.install_dir.iterdir() if p.is_file() and not linked(p) and p.name not in {'QuotaLab.exe','LICENSE'} and not p.name.startswith('unins')]
            installation={'status':'CURRENT_INSTALL_READ_ONLY_SCAN','reviewCandidateFiles':len(candidates),'candidateBytes':sum(p.stat().st_size for p in candidates),'automaticRemoval':False}
        self._stats={'bytes':sizes,'totalBytes':sum(sizes.values())+evidence_bytes+state_bytes,'protectedEvidenceBytes':evidence_bytes,'applicationStateBytes':state_bytes,'defaultUninstall':'KEEP_DATA','cleanupIsRecoverable':True,'installation':installation,'installationRemnants':'CURRENT_INSTALL_CANDIDATES_MANUAL_REVIEW','protected':['history','experiments','settings','active log','backups'],'scannedAt':now};self._at=now;return self._stats
    def preview(self,categories):
        if not isinstance(categories,list) or not categories or len(categories)>3 or any(not isinstance(c,str) or c not in {'cache','temporary','logs'} for c in categories):raise ValueError('CLEANUP_CATEGORY_REFUSED')
        checked_root(self.root);files=[]
        for category in set(categories):
            for p in safe_files(self.root,{'temporary':'tmp'}.get(category,category)):
                if category=='logs' and (p.name=='quotalab.log' or p.stat().st_mtime>self.clock()-7*86400):continue
                files.append(p)
        token=uuid.uuid4().hex;self.tokens[token]={'expires':self.clock()+120,'files':[(p,p.stat().st_size,p.stat().st_mtime_ns) for p in files]}
        return {'token':token,'files':len(files),'bytes':sum(p.stat().st_size for p in files),'expiresInSeconds':120,'categories':categories,'mode':'MOVE_TO_LOCAL_RECOVERY'}
    def clean(self,token,confirmed):
        item=self.tokens.pop(token,None)
        if confirmed is not True or not item or item['expires']<self.clock():raise ValueError('CLEANUP_CONFIRMATION_REQUIRED')
        checked_root(self.root);recovery=self.root/'trash'/uuid.uuid4().hex;moved=0
        if (self.root/'trash').exists() and linked(self.root/'trash'):raise ValueError('REPARSE_RECOVERY_REFUSED')
        if self.root not in recovery.resolve().parents:raise ValueError('RECOVERY_OUT_OF_SCOPE')
        for p,size,mtime in item['files']:
            if not p.is_file() or linked(p) or p.stat().st_size!=size or p.stat().st_mtime_ns!=mtime or self.root not in p.resolve().parents:continue
            if any(linked(a) for a in p.parents if a!=self.root and self.root in a.parents):continue
            dest=recovery/p.relative_to(self.root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(p),str(dest));moved+=1
        self._stats=None;return {'movedFiles':moved,'recoverable':True,'recoveryId':recovery.name}
def delete_owned_data(root,confirmation):
    if confirmation!='DELETE_QUOTALAB_V014_DATA':raise ValueError('DELETION_CONFIRMATION_REQUIRED')
    root=checked_root(root)
    # Reject nested links before any destructive traversal.
    for current,dirs,files in os.walk(root,followlinks=False):
        if any(linked(Path(current)/name) for name in dirs+files):raise ValueError('REPARSE_DELETE_REFUSED')
    shutil.rmtree(root);return {'deleted':True,'scope':'OWNED_V014_ROOT_ONLY','recoverable':False}
