"""v12 release evidence: whitelist exports, read-only calibration, honest gates."""
from __future__ import annotations
import json, re, time, platform, os, sys, subprocess
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from orchestration_models import epoch, iso

def redact_text(text):
    text=str(text)
    text=re.sub(r'(?i)bearer\s+\S+','Bearer [REDACTED]',text)
    text=re.sub(r'(?i)(access[_-]?token|refresh[_-]?token|authorization|cookie|account[_-]?id|prompt)\s*[:=]\s*[^,\r\n}]+',r'\1=[REDACTED]',text)
    text=re.sub(r'[\w.+*%-]+@[\w.-]+\.[A-Za-z]{2,}','[EMAIL_REDACTED]',text)
    text=re.sub(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n"\']+','[PATH_REDACTED]',text)
    text=re.sub(r'\\\\[^\s"\']+','[PATH_REDACTED]',text)
    return text

def freshness(snapshot, now=None):
    now=time.time() if now is None else now
    if snapshot.get('app_server') not in {'CONNECTED','MOCK'}:return 'OFFLINE'
    stamp=snapshot.get('last_snapshot')
    if not stamp:return 'UNKNOWN'
    try:
        at=epoch(stamp)
        if now-at>120 or at<epoch(snapshot.get('provider_connected_at') or stamp) or snapshot.get('provider_generation',0)!=snapshot.get('quota_provider_generation',0):return 'STALE'
    except (ValueError,TypeError):return 'UNKNOWN'
    return 'FRESH'

def doctor(snapshot, root, timezone_name='Asia/Shanghai'):
    try:ZoneInfo(timezone_name);tz_ok=True
    except Exception:tz_ok=False
    quota=bool(snapshot.get('windows')) and freshness(snapshot)=='FRESH'
    authenticated=snapshot.get('app_server')=='MOCK' or bool(snapshot.get('account'))
    found=snapshot.get('codex_cli_source') is not None or snapshot.get('app_server')=='MOCK'
    checks={'Application':True,'Local server':True,'Codex executable':found,
            'Codex version':bool(snapshot.get('codex_cli_version')),'App server':snapshot.get('app_server') in {'CONNECTED','MOCK'},
            'Authentication':authenticated,'Quota API':quota,'Lifecycle store':Path(root).is_dir(),
            'Timezone':tz_ok,'Recommendation engine':True,'Data migration':(Path(root).parent/'runtime'/'schema-v012.json').is_file()}
    severity='Healthy' if all(checks.values()) else 'Action required' if not found or not authenticated or not quota else 'Warning'
    return {'status':severity,'checks':checks,'quotaFreshness':freshness(snapshot),'errors':[k+' unavailable' for k,v in checks.items() if not v],
            'realModelTasksSent':0,'officialTasksCreated':0}

def smoke_summary(raw, version, bucket_count=0, timings=None):
    # NEVER export raw doctor/account payload, paths, ids or exception messages.
    cli=raw.get('codexCli',{});match=re.search(r'codex-cli\s+([0-9][A-Za-z0-9.+-]*)',str(cli.get('version','')))
    source=cli.get('source');source=source if source in {'Codex Desktop','PATH','npm','configured','mock','running-process','Explicit configuration'} else 'OTHER_OR_UNAVAILABLE'
    return {'dataSchemaVersion':15 if version.startswith('0.14.') else 14 if version.startswith('0.13.1-') else 13 if version.startswith('0.13.') else 12,'osVersion':platform.platform(),'quotaLabVersion':version,
            'codexCliVersion':'codex-cli '+match[1] if match else None,'cliSourceType':source,
            'cliFound':bool(raw.get('codexCli',{}).get('found')),'authConnected':raw.get('auth')=='connected',
            'quotaReadSuccess':raw.get('quotaApi')=='available','providerConnected':raw.get('appServer')=='connected',
            'bucketCount':bucket_count,'timings':timings or {},'errors':[] if not raw.get('error') else ['READ_ONLY_CONNECTION_FAILED'],
            'realModelTasksSent':0,'modelTaskSent':False,'officialTasksCreated':0,'validationLevel':'LOCAL_MACHINE_PASS' if raw.get('quotaApi')=='available' else 'LOCAL_MACHINE_WARNING'}

def write_smoke(directory, report):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'release-smoke-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'release-smoke-report.txt').write_text('\n'.join(f'{k}: {v}' for k,v in report.items()),encoding='utf-8')

def calibration_record(snapshot, recommendation=None, now=None):
    now=time.time() if now is None else now;r=recommendation or {}
    rows=[]
    for w in snapshot.get('windows',[]):
        minutes=w.get('window_duration_minutes');remaining=w.get('remaining_percent');reset=w.get('resets_at')
        rows.append({'timestamp':iso(now),'mode':'MOCK' if snapshot.get('app_server')=='MOCK' else 'READ_ONLY_REAL',
                     'bucketSemanticType':'FIVE_HOUR' if minutes==300 else 'WEEKLY' if minutes==10080 else 'OTHER',
                     'remainingRounded':round(float(remaining)/5)*5 if remaining is not None else None,
                     'windowState':w.get('window_state','UNKNOWN'),
                     'resetHorizonMinutes':round((epoch(reset)-now)/60) if reset else None,
                     'demandDurationMinutes':r.get('plannedHeavyUseMinutes'),'recommendationAction':r.get('productAction'),
                     'confidence':r.get('productConfidence'),'burnEstimatorStatus':str(r.get('burnRateStatus') or 'LEARNING').upper()})
    return rows

def append_calibration(directory, snapshot, recommendation=None):
    if not snapshot.get('windows'):return
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    with (directory/'calibration_observations.jsonl').open('a',encoding='utf-8') as handle:
        for row in calibration_record(snapshot,recommendation):handle.write(json.dumps(row,ensure_ascii=False)+'\n')

def release_gates(evidence=None):
    e=evidence or {}
    return {'releaseLevel':'Preview','validationLevel':'LOCAL_MACHINE_PASS' if e.get('localInstall')=='PASS' else 'LOCAL_MACHINE_PENDING',
            **{k:e.get(k,'PENDING') for k in ('unitTests','browserRegression','localInstall','portable','crossVersionMigration','realReadOnlyQuota','privacyAudit','accessibility')},
            'crossVersionUpgrade':e.get('crossVersionUpgrade','PENDING'),'upgradeStrategy':'SIDE_BY_SIDE_COPY',
            'inPlaceUpgrade':'NOT_SUPPORTED_BY_DISTINCT_APP_IDS','oldRootRollback':e.get('oldRootRollback','PENDING'),
            'rollback':'ROLLBACK_UNSUPPORTED_FOR_V012_WRITES',
            'multiMachineWindows':'PENDING','MULTI_MACHINE_WINDOWS_VALIDATION':'PENDING_RELEASE_GATE',
            'codeSigning':'PENDING','realModelExecution':'NOT_REQUIRED','autonomousA4':'BLOCKED_BY_SUPPORTED_INTERFACE',
            'realModelTasksSent':0,'officialTasksCreated':0}

def windows_release_matrix():
    return {'requiredEnvironments':['Windows 11 current','Windows 11 clean user profile','Windows 10 (support not certified)'],
            'dimensions':['Desktop installed/missing','CLI PATH/Desktop discovery','authenticated/not authenticated',
                          'v010/v011/fresh data','portable/installer'],
            'executions':[],'multiMachineWindows':'PENDING','warning':'Mocks/folders are never extra real machines'}

def native_smoke(q, directory, mock=False, port=18796):
    """Frozen EXE-native smoke: no dependency on PowerShell/Python installation."""
    from urllib.request import Request,urlopen
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    data=directory/'native-local-data';env=os.environ.copy();env['QUOTALAB_DATA_ROOT']=str(data);env['QUOTALAB_TEST_SCOPE']='native-release-smoke'
    command=[sys.executable]+([] if getattr(sys,'frozen',False) else [str(Path(q.__file__).resolve())])
    command+=['start','--no-browser','--port',str(port)]+(['--mock'] if mock else [])
    process=subprocess.Popen(command,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    base=f'http://127.0.0.1:{port}';started=time.monotonic();owned=False;report=None
    def api(route,payload=None,text=False):
        req=Request(base+route,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=2) as r:return r.read().decode('utf-8') if text else json.load(r)
    stage='STARTUP'
    try:
        snapshot=None;health=None
        for _ in range(80):
            if process.poll() is not None:break
            try:
                health=api('/api/health');owned=health.get('pid')==process.pid
                if not owned:break
                snapshot=api('/api/status')
                if mock or snapshot.get('windows'):break
            except OSError:pass
            time.sleep(.25)
        if not owned or snapshot is None:raise RuntimeError('LOCAL_SERVER_UNAVAILABLE')
        stage='READ_ONLY_DOCTOR';raw=q.run_doctor(host='127.0.0.1',explicit_codex_path=None,mock=mock)
        report=smoke_summary(raw,q.APP_VERSION,raw.get('bucketCount',0))
        stage='UI_LOAD';page=api('/',text=True)
        from datetime import timedelta
        at=datetime.now(timezone.utc)+timedelta(days=1)
        stage='PLANNING';plan=api('/api/orchestration/demand',{'title':'Synthetic release smoke','expectedStart':at.isoformat(),'expectedEnd':(at+timedelta(hours=3)).isoformat()})
        exe=Path(sys.executable)
        report.update(mode='MOCK' if mock else 'READ_ONLY_REAL',checks={'localHealth':health.get('status')=='ok','uiLoad':'releaseFreshLabel' in page,
            'recommendationBasicPath':bool(plan.get('recommendation',{}).get('productAction')),
            'dataLocationScoped':Path(snapshot['diagnostics']['dataDirectory']).resolve()==data,
            'pythonRequired':False,'internalFolderPresent':(exe.parent/'_internal').is_dir() if getattr(sys,'frozen',False) else None,
            'installationMode':'INSTALLER' if (exe.parent/'unins000.exe').exists() else 'PORTABLE_OR_DIST'})
        if not mock:
            # Doctor success cannot hide a disconnected/stale application instance.
            report['providerConnected']=snapshot.get('app_server')=='CONNECTED'
            report['authConnected']=bool(snapshot.get('account'))
            report['quotaReadSuccess']=report['quotaReadSuccess'] and bool(snapshot.get('windows')) and freshness(snapshot)=='FRESH'
            report['bucketCount']=len(snapshot.get('windows',[]))
            if not all(report[k] for k in ('providerConnected','authConnected','quotaReadSuccess')):
                report['validationLevel']='LOCAL_MACHINE_WARNING';report['errors'].append('APPLICATION_PROVIDER_UNAVAILABLE_OR_STALE')
    except Exception as exc:
        report={'quotaLabVersion':q.APP_VERSION,'validationLevel':'LOCAL_MACHINE_FAIL','errors':[stage+'_FAILED'],'exceptionType':type(exc).__name__,
                'realModelTasksSent':0,'officialTasksCreated':0}
    finally:
        if owned:
            try:api('/api/shutdown',{})
            except OSError:pass
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        report.setdefault('checks',{})['shutdown']=process.poll() is not None
        report['totalSeconds']=round(time.monotonic()-started,2)
        write_smoke(directory,report)
    return report
