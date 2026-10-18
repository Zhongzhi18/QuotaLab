"""Fail-closed recommendation revisions. No model or cloud execution."""
from __future__ import annotations
import copy, hashlib, json, math, time, threading, logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from orchestration_models import epoch, iso
from file_persistence import replace_file

SAFE = {'FRESH', 'UPDATED', 'NO_LONGER_NEEDED'}
DEBOUNCE_SECONDS = 5
MIN_RECOMPUTE_SECONDS = 30

def local_interval(payload, zone):
    """Reject nonexistent/ambiguous DST wall times; never guess browser timezone."""
    tz=ZoneInfo(zone)
    start=datetime.fromisoformat(payload['date']+'T'+payload['start'])
    end=datetime.fromisoformat(payload['date']+'T'+payload['end'])
    if end<=start:end+=timedelta(days=1)
    def convert(wall):
        candidates={wall.replace(tzinfo=tz,fold=f).timestamp() for f in (0,1)
                    if datetime.fromtimestamp(wall.replace(tzinfo=tz,fold=f).timestamp(),tz).replace(tzinfo=None)==wall}
        if len(candidates)!=1:raise ValueError('LOCAL_TIME_AMBIGUOUS_OR_NONEXISTENT')
        return iso(candidates.pop())
    return convert(start),convert(end)

class RecommendationLifecycle:
    def __init__(self, service, clock=None):
        self.service=service;self.clock=clock or time.time
        self.path=service.path.with_name('recommendation-lifecycle-v014.json')
        try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.data={}
        self.data.setdefault('records',{});self.data.setdefault('current',{});self.data.setdefault('events',[])
        for record in self.data['records'].values():
            if record.get('state')=='RECOMPUTING':record.update(state='FAILED',reason='INTERRUPTED_RECOMPUTE',pendingSince=self.clock())
            elif record.get('state') in SAFE:record.update(state='STALE',reason='SESSION_REVALIDATION',pendingSince=self.clock())
        # Legacy recommendation/evidence bytes are untouched; sidecar alone invalidates old plans.
        for rec in reversed(service.data['recommendations']):
            if rec['id'] not in self.data['records']:
                self.data['records'][rec['id']]={'state':'STALE','revision':0,'reason':'IMPORTED_OR_UNTRACKED',
                    'input':None,'pendingSince':self.clock(),'lastRecomputedAt':0,'supersedes':None}
            self.data['current'].setdefault(rec['demandId'],rec['id'])
        # newest stored legacy revision is the current one
        for rec in service.data['recommendations']:
            if self.data['records'][rec['id']]['revision']==0:self.data['current'][rec['demandId']]=next(r['id'] for r in service.data['recommendations'] if r['demandId']==rec['demandId'])

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8');replace_file(temp,self.path)

    def state(self, rid, state, reason):
        record=self.data['records'][rid]
        if record.get('state')!=state or record.get('reason')!=reason:
            record.update(state=state,reason=reason,changedAt=iso(self.clock()))
            self.data['events'].append({'eventId':uuid.uuid4().hex,'demandId':record.get('demandId'),'revision':record.get('revision'),
                'recommendationId':rid,'state':state,'reason':reason,'at':iso(self.clock())})
        if state not in SAFE:
            for reminder in self.service.data['reminders']:
                if reminder['recommendationId']==rid:reminder['invalidated']=True

    def inputs(self, snapshot):
        s=self.service;now=self.clock();quota,window,weekly=s.usage(snapshot,'openai-codex')
        reset=quota.get('resetAt');reset=epoch(reset) if reset else None
        signals=s.signal_provider.deduplicate(s.data['signals'],now)
        weekly_window=next((w for w in snapshot.get('windows',[]) if w.get('window_duration_minutes')==10080),{})
        weekly_reset=epoch(weekly['resetAt']) if weekly.get('resetAt') else None
        observed=snapshot.get('last_snapshot')
        fresh=not observed or -5<=now-epoch(observed)<=120
        if snapshot.get('provider_connected_at'):
            fresh=bool(observed) and fresh and epoch(observed)>=epoch(snapshot['provider_connected_at'])
        fresh=fresh and snapshot.get('provider_generation',0)==snapshot.get('quota_provider_generation',0)
        prefs={k:v for k,v in s.data['preferences'].items() if k not in {'publicSignalFetchEnabled'}}
        return {'remaining':quota.get('remainingPercent'),'weekly':weekly.get('remainingPercent'),
                'reset':reset,'resetCoordinate':reset-now if reset and window['state']=='ROLLING' else reset,
                'weeklyReset':weekly_reset-now if weekly_reset and weekly_window.get('window_state')=='ROLLING' else weekly_reset,
                'weeklyState':weekly_window.get('window_state'),'state':window['state'],'bucket':quota.get('bucketId'),
                'connected':fresh and snapshot.get('app_server') in {'CONNECTED','MOCK'} and snapshot.get('status')!='UNAVAILABLE',
                'provider':snapshot.get('provider',{}),'connectionGeneration':snapshot.get('provider_generation',0),'account':hashlib.sha256(json.dumps(snapshot.get('account',{}),sort_keys=True).encode()).hexdigest(),
                'signals':[(x['id'],x.get('classification'),x.get('expired'),x.get('reliability')) for x in signals if x.get('classification')!='UNRELATED'],
                'prefs':prefs,'demands':s.data['demands'],'burn':{k:s.public_burn().get(k) for k in ('status','bucketId','percentPerMinute')}, 'observedAt':iso(now)}

    def changed(self, old, new):
        if not old:return True
        for key in ('connected','provider','connectionGeneration','account','bucket','state','weeklyState','signals','prefs','demands'):
            if old.get(key)!=new.get(key):return True
        a,b=old.get('burn',{}),new.get('burn',{})
        if a.get('status')!=b.get('status') or a.get('bucketId')!=b.get('bucketId'):return True
        if a.get('percentPerMinute') is not None and b.get('percentPerMinute') is not None and abs(a['percentPerMinute']-b['percentPerMinute'])>=max(.001,abs(a['percentPerMinute'])*.2):return True
        for key in ('remaining','weekly'):
            a,b=old.get(key),new.get(key)
            if a is None or b is None:
                if a!=b:return True
            elif abs(a-b)>=5 or any((a<=x)!=(b<=x) for x in (0,10)):return True
        a,b=old.get('resetCoordinate'),new.get('resetCoordinate')
        for a,b in ((a,b),(old.get('weeklyReset'),new.get('weeklyReset'))):
            if (a is None)!=(b is None) or (a is not None and b is not None and abs(a-b)>=120):return True
        return False

    def digest(self, data):
        return hashlib.sha256(json.dumps({k:v for k,v in data.items() if k not in {'observedAt','reset'}},sort_keys=True).encode()).hexdigest()

    def allocations(self, snapshot):
        s=self.service;now=self.clock();quota,_,_=s.usage(snapshot,'openai-codex');burn=s.public_burn()
        rate=burn.get('percentPerMinute') if burn.get('status')=='ESTIMATED' and burn.get('bucketId')==quota.get('bucketId') else None
        reset=quota.get('resetAt');boundary=epoch(reset) if reset else None
        available=quota.get('remainingPercent');reserved=0;accepted=[];result={}
        demands=sorted((d for d in s.data['demands'] if epoch(d['expectedEnd'])>now),key=lambda d:({'HIGH':0,'MEDIUM':1,'LOW':2}[d['priority']],epoch(d['expectedStart']),d['id']))
        for d in demands:
            start,end=epoch(d['expectedStart']),epoch(d['expectedEnd'])
            conflicts=[x['id'] for x in accepted if start<epoch(x['expectedEnd']) and end>epoch(x['expectedStart']) and (d['intensity']=='HEAVY' or x['intensity']=='HEAVY')]
            estimate=(end-start)/60+s.data['preferences']['safetyBufferMinutes']
            estimate=estimate*rate if isinstance(rate,(int,float)) and math.isfinite(rate) and rate>=0 else None
            # Never reserve an assumed replenishment. Post-reset budgets are UNKNOWN.
            in_current=boundary is not None and end<=boundary
            shortage=in_current and estimate is not None and available is not None and reserved+estimate>available
            blocked=bool(conflicts or shortage)
            result[d['id']]={'conflictDemandIds':conflicts,'blocked':blocked,'estimatedPercent':estimate,
                'reservedPercent':estimate if not blocked and in_current and estimate is not None else None,
                'budgetStatus':'OVERCOMMITTED' if shortage else 'UNKNOWN' if not in_current or estimate is None else 'ESTIMATED_CURRENT_WINDOW',
                'weeklyReservation':'UNKNOWN_NOT_CONVERTIBLE_FROM_PRIMARY','guaranteed':False}
            if not blocked:
                accepted.append(d)
                if in_current and estimate is not None:reserved+=estimate
        return result

    def register(self, rec, inputs, allocation, supersedes=None):
        rid=rec['id'];previous=self.data['records'].get(supersedes,{})
        record={'state':'FRESH' if not supersedes else 'UPDATED','revision':previous.get('revision',0)+1,'demandId':rec['demandId'],
                'supersedes':supersedes,'input':copy.deepcopy(inputs),'inputFingerprint':self.digest(inputs),
                'generatedAt':iso(self.clock()),'lastRecomputedAt':self.clock(),'allocation':allocation,
                'validUntil':rec.get('recommended_time') or rec.get('recommendedExecutionAt') or rec['demandEnd']}
        self.data['records'][rid]=record;self.data['current'][rec['demandId']]=rid
        if supersedes:self.state(supersedes,'SUPERSEDED','REPLACED_BY_REVISION')
        if rec.get('productAction')!='SCHEDULE_OFFICIAL_TASK':record['state']='NO_LONGER_NEEDED'
        if allocation and allocation['blocked']:
            rec.update(productAction='DEFER_HEAVY_WORK',recommended_time=None,recommendedExecutionAt=None,riskLevel='HIGH')
            if rec.get('product'):rec['product'].update(action='DEFER_HEAVY_WORK',recommended_time=None,riskLevel='HIGH')
            rec.setdefault('risk',[]).append('DEMAND_CONFLICT_OR_OVERCOMMIT');record['state']='NO_LONGER_NEEDED'
        self.data['events'].append({'eventId':uuid.uuid4().hex,'demandId':rec['demandId'],'revision':record['revision'],
            'recommendationId':rid,'state':record['state'],'reason':'GENERATED','at':iso(self.clock())})
        self.save()

    def tick(self, snapshot):
        now=self.clock();inputs=self.inputs(snapshot);allocations=self.allocations(snapshot);dirty=False
        for did,rid in list(self.data['current'].items()):
            rec=next((r for r in self.service.data['recommendations'] if r['id']==rid),None)
            if not rec:continue
            record=self.data['records'][rid]
            if epoch(rec['demandEnd'])<=now:
                if record['state']!='EXPIRED':self.state(rid,'EXPIRED','WORK_PERIOD_ENDED');dirty=True
                continue  # terminal ended demand: never manufacture an already-expired revision
            if record.get('validUntil') and epoch(record['validUntil'])<=now and record['state'] in SAFE:
                self.state(rid,'EXPIRED','TIME_PASSED');record['pendingSince']=now;dirty=True
            if self.changed(record.get('input'),inputs):
                # Trailing debounce tracks meaningful changes, not noise/normal rolling drift.
                if self.changed(record.get('pendingInput'),inputs):
                    record.update(pendingInput=copy.deepcopy(inputs),pendingSince=now)
                if record['state'] in SAFE:self.state(rid,'STALE','INPUT_CHANGED')
                record.setdefault('pendingSince',now);dirty=True
            if record['state'] in {'STALE','EXPIRED','FAILED'}:
                if not inputs['connected']:
                    self.state(rid,'STALE','CONNECTION_UNAVAILABLE');dirty=True;continue
                if now-record.get('pendingSince',now)<DEBOUNCE_SECONDS or now-record.get('lastRecomputedAt',0)<MIN_RECOMPUTE_SECONDS:continue
                demand=next((d for d in self.service.data['demands'] if d['id']==did),None)
                if not demand:continue
                self.state(rid,'RECOMPUTING','INPUT_CHANGED');self.save()
                try:
                    new=self.service.generate(demand,snapshot)
                    self.register(new,inputs,allocations.get(did),supersedes=rid)
                    self.service.data['recommendations'].insert(0,new)
                except Exception:
                    self.state(rid,'FAILED','RECOMPUTE_FAILED');record.update(lastRecomputedAt=now,pendingSince=now)
                dirty=True
        if dirty:self.save();self.service.save()

    def allowed(self, rec):
        if not rec:return False
        if rec.get('productAction')=='SCHEDULE_OFFICIAL_TASK' and not self.service.data['preferences'].get('researchMode'):return False
        record=self.data['records'].get(rec['id'],{})
        deadline=record.get('validUntil')
        return (self.data['current'].get(rec['demandId'])==rec['id'] and record.get('state') in SAFE
                and deadline is not None and epoch(deadline)>self.clock()
                and (rec.get('productAction')=='SCHEDULE_OFFICIAL_TASK' or not rec.get('product')))

    def reminder_allowed(self,rec):
        if not rec:return False
        if rec.get('planningPolicy')!='OFFICIAL_SNAPSHOT_ONLY':return self.allowed(rec)
        record=self.data['records'].get(rec['id'],{})
        return self.data['current'].get(rec['demandId'])==rec['id'] and record.get('state') in SAFE and epoch(record['validUntil'])>self.clock() and bool(rec.get('reminderAt')) and epoch(rec['reminderAt'])>self.clock()

    def require(self, rec):
        if not self.allowed(rec):raise ValueError('RECOMMENDATION_NOT_ACTIONABLE')

    def view(self, result):
        now=self.clock();tz=ZoneInfo(self.service.data['preferences']['timezone']);today=datetime.fromtimestamp(now,tz).date()
        result['currentRecommendations']=[]
        for rec in result['recommendations']:
            record=copy.deepcopy(self.data['records'].get(rec['id'],{}));rec['recommendationLifecycle']=record
            rec['actionable']=self.allowed(rec)
            if not rec['actionable']:
                rec['recommended_time']=None;rec['recommendedExecutionAt']=None
                if rec.get('product'):rec['product']['recommended_time']=None
            if self.data['current'].get(rec['demandId'])==rec['id']:result['currentRecommendations'].append(rec)
        for handoff in result['handoffs']:
            rec=next((r for r in self.service.data['recommendations'] if r['id']==handoff['recommendationId']),None)
            if not self.allowed(rec):handoff['generatedInstruction']=None;handoff['actionable']=False
        # Existing A4/A5 records are not changed or reclassified; public old instructions are redacted.
        for exp in result['cloudExperiments']:exp['generatedInstruction']=None
        result['reminderRecords']=copy.deepcopy(self.service.data['reminders'])
        result['reminders']=[r for r in result['reminders'] if not r.get('invalidated') and r.get('state') in {'SCHEDULED','FIRED','DELIVERY_UNCONFIRMED','DISPLAYED'}]
        for rec in result['currentRecommendations']:rec['reminderEligible']=self.reminder_allowed(rec)
        result['calendar']={'timezone':str(tz),'today':str(today),'tomorrow':str(today+timedelta(days=1))}
        result['todayTomorrowDemands']=[d for d in result['demands'] if epoch(d['expectedEnd'])>now and
            datetime.fromtimestamp(epoch(d['expectedStart']),tz).date()<=today+timedelta(days=1) and datetime.fromtimestamp(epoch(d['expectedEnd'])-0.001,tz).date()>=today]
        result['recommendationRevisionHistory']=copy.deepcopy(self.data['events'])

class RecommendationRefreshLoop(threading.Thread):
    """Evaluate cached read-only inputs while the UI is closed; no provider requests."""
    def __init__(self,state):
        super().__init__(name='recommendation-refresh',daemon=True);self.state=state
    def run(self):
        while not self.state.stop_event.wait(1):
            try:
                with self.state.lock:snapshot=copy.deepcopy(self.state.state)
                with self.state.orchestrator.lock:
                    self.state.orchestrator.recommendation_lifecycle.tick(snapshot)
                    from reminder_delivery import tick
                    tick(self.state.orchestrator,snapshot)
            except Exception:logging.exception('Recommendation refresh unavailable; action endpoints remain fail-closed')
