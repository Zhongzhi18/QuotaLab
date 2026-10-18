"""HTTPS configured feed, bounded reads, no redirects/auto install, explicit download consent."""
import hashlib,ipaddress,json,re,socket,threading,time,uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request,build_opener,HTTPRedirectHandler
from storage_manager import checked_root,linked
MAX_MANIFEST=262144;MAX_DOWNLOAD=100*1024*1024
def version(value):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?',value or '')
    if not match:raise ValueError('INVALID_UPDATE_VERSION')
    labels=match[4].split('.') if match[4] else []
    if any(not label for label in labels):raise ValueError('INVALID_UPDATE_VERSION')
    prerelease=(1,tuple((0,int(label)) if label.isdigit() else (1,label) for label in labels)) if labels else (2,())
    return tuple(map(int,match.group(1,2,3)))+(prerelease,)
def safe_url(value,host=None):
    p=urlparse(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in {None,443}:raise ValueError('UPDATE_HTTPS_REQUIRED')
    if p.hostname.lower() in {'localhost','localhost.localdomain'} or (host and p.hostname!=host):raise ValueError('UPDATE_HOST_REFUSED')
    try:
        if not ipaddress.ip_address(p.hostname).is_global:raise ValueError('PRIVATE_UPDATE_HOST_REFUSED')
    except ValueError as e:
        if str(e)=='PRIVATE_UPDATE_HOST_REFUSED':raise
    return p
class NoRedirect(HTTPRedirectHandler):
    def __init__(self,github=False):self.github=github;self.count=0
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        self.count+=1;p=urlparse(newurl)
        if not self.github or self.count>3 or p.scheme!='https' or p.hostname not in {'release-assets.githubusercontent.com','objects.githubusercontent.com'} or p.username or p.password or p.port not in {None,443}:raise ValueError('UPDATE_REDIRECT_REFUSED')
        if any(not ipaddress.ip_address(x[4][0]).is_global for x in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)):raise ValueError('PRIVATE_UPDATE_HOST_REFUSED')
        return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch_https(url,limit):
    p=safe_url(url)
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):raise ValueError('PRIVATE_UPDATE_HOST_REFUSED')
    with build_opener(NoRedirect(p.hostname=='github.com')).open(Request(url,headers={'User-Agent':'QuotaLab-Update/0.14','Accept':'application/json,application/octet-stream'}),timeout=15) as response:
        body=response.read(limit+1)
        if len(body)>limit:raise ValueError('UPDATE_SIZE_LIMIT')
        return body
class UpdateChecker:
    def __init__(self,root,current,fetch=fetch_https,clock=time.time):
        self.root=Path(root);self.path=self.root/'runtime/update-source-v014.json';self.current=current;self.fetch=fetch;self.clock=clock;self.lock=threading.RLock();self.feed=None;self.receipts={};self.last=0;self.release=None
        try:self.feed=json.loads(self.path.read_text(encoding='utf-8')).get('feed')
        except (OSError,ValueError):pass
        self.state={'status':'NOT_CONFIGURED','currentVersion':current,'latestVersion':None,'automaticInstall':False,'downloadNeedsConfirmation':True}
    def configure(self,feed):
        if feed:safe_url(feed)
        with self.lock:
            self.feed=feed or None;self.release=None;self.receipts.clear();self.last=0
            self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_text(json.dumps({'feed':self.feed}),encoding='utf-8');self.state.update(status='READY' if self.feed else 'NOT_CONFIGURED',latestVersion=None)
        return self.public()
    def public(self):
        with self.lock:return {**self.state,'configured':bool(self.feed)}
    def start(self):
        if self.feed:threading.Thread(target=self.check,name='update-check-readonly',daemon=True).start()
    def check(self):
        with self.lock:
            if not self.feed:return self.public()
            if self.state['status']=='CHECKING' or self.clock()-self.last<60:return self.public()
            self.state.update(status='CHECKING',latestVersion=None,notes={});self.release=None;self.receipts.clear();feed=self.feed;self.last=self.clock()
        try:
            document=json.loads(self.fetch(feed,MAX_MANIFEST));v=document['version'];new=version(v)>version(self.current);asset=document['windows'];url=asset['url'];source=safe_url(feed);destination=safe_url(url)
            if destination.hostname!=source.hostname:
                repo=source.path.strip('/').split('/')[:2]
                if source.hostname!='raw.githubusercontent.com' or len(repo)!=2 or destination.hostname!='github.com' or not destination.path.startswith('/'+'/'.join(repo)+'/releases/download/'):raise ValueError('UPDATE_HOST_REFUSED')
            h=asset['sha256'];size=asset['size'];notes=document.get('notes',{})
            if not re.fullmatch(r'[A-Fa-f0-9]{64}',h) or type(size) is not int or not 0<size<=MAX_DOWNLOAD:raise ValueError('INVALID_UPDATE_ASSET')
            if not isinstance(notes,dict) or any(not isinstance(notes.get(k,[]),list) or any(not isinstance(x,str) or len(x)>500 for x in notes.get(k,[])) for k in ('features','fixes','performance')):raise ValueError('INVALID_UPDATE_NOTES')
            with self.lock:
                if self.feed!=feed:return self.public()
                self.release={'version':v,'url':url,'sha256':h.upper(),'size':size};self.state.update(status='AVAILABLE' if new else 'UP_TO_DATE',latestVersion=v,notes={k:notes.get(k,[])[:12] for k in ('features','fixes','performance')},checkedAt=self.clock(),error=None)
        except Exception as exc:
            with self.lock:
                if self.feed==feed:self.state.update(status='CHECK_FAILED',latestVersion=None,error=type(exc).__name__,notes={});self.release=None
        return self.public()
    def offer(self):
        with self.lock:
            if self.state['status']!='AVAILABLE' or not self.release:raise ValueError('NO_VERIFIED_UPDATE')
            token=uuid.uuid4().hex;self.receipts[token]={'release':dict(self.release),'expires':self.clock()+120}
            return {'token':token,'version':self.release['version'],'bytes':self.release['size'],'sha256':self.release['sha256'],'expiresInSeconds':120}
    def download(self,token,confirmed):
        with self.lock:
            receipt=self.receipts.pop(token,None)
            if confirmed is not True or not receipt or receipt['expires']<self.clock() or receipt['release']!=self.release or self.state['status']!='AVAILABLE':raise ValueError('UPDATE_CONFIRMATION_REQUIRED')
            self.state['status']='DOWNLOADING';asset=receipt['release'];feed=self.feed
        temp=None
        try:
            body=self.fetch(asset['url'],MAX_DOWNLOAD)
            if len(body)!=asset['size'] or hashlib.sha256(body).hexdigest().upper()!=asset['sha256']:raise ValueError('UPDATE_HASH_MISMATCH')
            with self.lock:
                if self.feed!=feed or self.release!=asset:raise ValueError('UPDATE_SOURCE_CHANGED')
            checked_root(self.root);folder=self.root/'downloads'
            if folder.exists() and linked(folder):raise ValueError('UPDATE_DOWNLOAD_LINK_REFUSED')
            folder.mkdir(parents=True,exist_ok=True);temp=folder/(uuid.uuid4().hex+'.partial');temp.write_bytes(body);name='QuotaLab-Setup-'+asset['version']+'-win64.exe';destination=folder/name
            if destination.exists() and linked(destination):raise ValueError('UPDATE_DOWNLOAD_LINK_REFUSED')
            temp.replace(destination)
            with self.lock:self.state.update(status='DOWNLOADED',downloadFile=name,verifiedSha256=asset['sha256'])
        except Exception as exc:
            if temp and temp.exists():temp.unlink()
            with self.lock:self.state.update(status='DOWNLOAD_FAILED',downloadFile=None,error=type(exc).__name__)
        return self.public()
