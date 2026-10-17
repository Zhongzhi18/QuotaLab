"""Descriptive local evidence, not fabricated productivity or quota guarantees."""
import math,statistics,time
from collections import defaultdict
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from orchestration_models import epoch
def number(value):return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)
def select_quota_windows(windows):
    """Choose a live primary bucket without assuming every account has a 5h limit."""
    valid=sorted((w for w in windows if isinstance(w,dict) and isinstance(w.get('window_duration_minutes'),int)
           and not isinstance(w.get('window_duration_minutes'),bool) and w['window_duration_minutes']>0),
           key=lambda w:w['window_duration_minutes'])
    weekly=next((w for w in valid if w['window_duration_minutes']==10080),None)
    non_weekly=[w for w in valid if w['window_duration_minutes']!=10080]
    # Prefer the actual five-hour bucket when returned. It is the focused
    # planning window even when a longer quota is marked primary by the provider.
    primary=next((w for w in non_weekly if w['window_duration_minutes']==300),None)
    if primary is None:
        primary=next((w for w in non_weekly if str(w.get('slot','')).lower()=='primary'),None)
    if primary is None and non_weekly:
        primary=min(non_weekly,key=lambda w:w['window_duration_minutes'])
    if primary is None and weekly is not None:primary=weekly
    if primary is None and valid:primary=min(valid,key=lambda w:w['window_duration_minutes'])
    return primary,weekly
def research_summary(experiments):
    live=[e for e in experiments if e.get('mode') in {'live','real'}]
    eligible=[e for e in live if (e.get('eligibleForAnchorStatistics') is True or e.get('eligibleForConfirmedAnchorStatistics') is True) and e.get('experimentType')!='IN_WINDOW_CONTROL']
    completed=[e for e in eligible if e.get('classification') in {'ANCHOR_CONFIRMED','ANCHOR_CONFIRMED_BY_STATE_TRANSITION','NO_REANCHOR','RESET_CHANGED_NOT_ANCHORED'}]
    confirmed=[e for e in completed if e.get('classification') in {'ANCHOR_CONFIRMED','ANCHOR_CONFIRMED_BY_STATE_TRANSITION'}]
    errors=[abs(e['anchorErrorSeconds']) for e in completed if number(e.get('anchorErrorSeconds'))]
    return {'liveTrials':len(live),'mockTrialsExcluded':len(experiments)-len(live),'eligibleCompleted':len(completed),'confirmed':len(confirmed),'observedAlignmentPercent':round(len(confirmed)/len(completed)*100,1) if completed else None,'meanAbsoluteErrorSeconds':round(statistics.mean(errors),2) if errors else None,'errorSampleCount':len(errors),'probability':None,'originalClassificationsUnchanged':True,'timeline':[{'id':e.get('id'),'classification':e.get('classification'),'expected':e.get('expectedResetAt'),'observed':e.get('observedResetAt'),'eligible':e in completed,'mode':e.get('mode')} for e in experiments[-30:]]}
def analyze(outcomes,demands,observations,zone,now=None,window_duration_minutes=300):
    now=time.time() if now is None else now;tz=ZoneInfo(zone);by_id={d['id']:d for d in demands};latest={}
    for row in outcomes:latest[row['demandId']]=row
    rows=[r for r in latest.values() if r['demandId'] in by_id];daily=defaultdict(lambda:{'observedQuotaDrop':0,'intervals':0});hours=[{'hour':h,'observedQuotaDrop':0,'reportedTasks':0,'completed':0} for h in range(24)];previous={};sample_days=set()
    for row in sorted(observations,key=lambda r:r.get('observed_at','')):
        if row.get('mode')!='live' or not isinstance(window_duration_minutes,int) or row.get('window_duration_minutes')!=window_duration_minutes:continue
        try:at=epoch(row.get('observed_at'))
        except ValueError:continue
        left=row.get('remaining_percent');reset=row.get('resets_at');key=(row.get('limit_id'),row.get('slot'),window_duration_minutes)
        if not at or at>now+5 or not number(left) or not 0<=left<=100:continue
        day=datetime.fromtimestamp(at,tz).date().isoformat();hour=datetime.fromtimestamp(at,tz).hour;old=previous.get(key);previous[key]=(at,left,reset)
        if old and 0<at-old[0]<=3600 and reset==old[2] and left<=old[1]:
            drop=old[1]-left;daily[day]['observedQuotaDrop']+=drop;daily[day]['intervals']+=1;hours[hour]['observedQuotaDrop']+=drop;sample_days.add(day)
    ratios=[];heavy=[];hour_days=defaultdict(set);reported_daily=defaultdict(float)
    for row in rows:
        d=by_id[row['demandId']];hour=datetime.fromtimestamp(epoch(d['expectedStart']),tz).hour;hours[hour]['reportedTasks']+=1;hours[hour]['completed']+=row['status']=='COMPLETED';hour_days[hour].add(datetime.fromtimestamp(epoch(d['expectedStart']),tz).date())
        reported_daily[datetime.fromtimestamp(epoch(d['expectedStart']),tz).date()]+=row.get('actualMinutes',0)
        if d['intensity']=='HEAVY':heavy.append(row)
        if row['status']=='COMPLETED' and row.get('actualMinutes',0)>0:ratios.append((epoch(d['expectedEnd'])-epoch(d['expectedStart']))/60/row['actualMinutes'])
    candidates=[h for h in hours if h['reportedTasks']>=5 and len(hour_days[h['hour']])>=3]
    best=max(candidates,key=lambda h:h['completed']/h['reportedTasks']) if len(rows)>=20 and len(candidates)>=2 else None
    pattern={}
    if len(rows)>=20 and len(reported_daily)>=7:
        for weekday in range(7):
            samples=[v for d,v in reported_daily.items() if d.weekday()==weekday and 0<=now-datetime.combine(d,datetime.min.time(),tz).timestamp()<=31*86400]
            if len(samples)>=3:pattern[str(weekday)]={'medianMinutes':round(statistics.median(samples),1),'minMinutes':round(min(samples),1),'maxMinutes':round(max(samples),1),'sampleDays':len(samples)}
    return {'basis':'LOCAL_OBSERVATION_AND_USER_REPORTED_OUTCOMES','timezone':zone,'quotaWindowDurationMinutes':window_duration_minutes,'outcomeCount':len(rows),'recordedHighIntensityCount':len(heavy),'highIntensityCompletionPercent':round(sum(r['status']=='COMPLETED' for r in heavy)/len(heavy)*100,1) if len(heavy)>=5 else None,'medianPlannedToActualRatio':round(statistics.median(ratios),2) if len(ratios)>=5 else None,'ratioSampleCount':len(ratios),'bestObservedHour':best['hour'] if best else None,'bestHourIsCausal':False,'weekdayPattern':pattern,'daily':[{'date':day,**{k:round(v,2) for k,v in data.items()}} for day,data in sorted(daily.items())[-30:]],'hours':hours,'observedDays':len(sample_days),'maturity':'DESCRIPTIVE' if len(sample_days)>=7 else 'LEARNING','confidence':'LOW','probability':None}
def capacity(snapshot,burn):
    windows=snapshot.get('windows',[]);primary,weekly=select_quota_windows(windows)
    fresh=snapshot.get('orchestration',{}).get('quotaFreshness')=='FRESH';left=primary.get('remaining_percent') if primary else None;duration=primary.get('window_duration_minutes') if primary else None
    bucket=(primary or {}).get('bucket_id') or (f"{primary.get('limit_id')}.{primary.get('slot')}.{duration}" if primary else None)
    rate=burn.get('percentPerMinute');available=left/rate if fresh and primary and burn.get('status')=='ESTIMATED' and burn.get('bucketId')==bucket and number(rate) and rate>0 and number(left) else None
    values=[w.get('remaining_percent') for w in windows if number(w.get('remaining_percent'))]
    state='UNAVAILABLE' if not fresh or not primary or not number(left) else 'EXHAUSTED' if any(v==0 for v in values) else 'LOW' if any(v<=10 for v in values) else 'AVAILABLE'
    public_windows=[{'windowDurationMinutes':w.get('window_duration_minutes'),'remainingPercent':w.get('remaining_percent'),'resetsAt':w.get('resets_at'),'limitId':w.get('limit_id'),'slot':w.get('slot')} for w in windows if isinstance(w,dict)] if fresh else []
    return {'state':state,'remainingPercent':left if fresh and primary else None,'windowDurationMinutes':duration if fresh else None,'weeklyPercent':weekly.get('remaining_percent') if fresh and weekly else None,'historicalEstimatedMinutes':round(available,1) if available is not None else None,'guaranteedHours':None,'guaranteedTasks':None,'basis':'SAME_BUCKET_HISTORICAL_ESTIMATE' if available is not None else 'OFFICIAL_SNAPSHOT_ONLY','resetAt':primary.get('resets_at') if fresh and primary else None,'weeklyResetAt':weekly.get('resets_at') if fresh and weekly else None,'quotaWindows':public_windows}
def forecast(demands,zone,intelligence,now=None):
    now=time.time() if now is None else now;tz=ZoneInfo(zone);today=datetime.fromtimestamp(now,tz).date();daily=[]
    for offset in range(30):
        day=today+timedelta(days=offset);start=datetime.combine(day,datetime.min.time(),tz).timestamp();end=datetime.combine(day+timedelta(days=1),datetime.min.time(),tz).timestamp();active=[d for d in demands if epoch(d['expectedEnd'])>now and epoch(d['expectedStart'])<end and epoch(d['expectedEnd'])>start]
        minutes=sum(max(0,min(epoch(d['expectedEnd']),end)-max(epoch(d['expectedStart']),start,now))/60 for d in active)
        pattern=intelligence.get('weekdayPattern',{}).get(str(day.weekday()))
        daily.append({'date':day.isoformat(),'plannedMinutes':round(minutes,1),'heavyPlans':sum(d['intensity']=='HEAVY' for d in active),'historicalWorkload':pattern,'quotaEstimatePercent':None,'resetPrediction':None})
    return {'horizons':{str(n):{'days':daily[:n],'plannedMinutes':round(sum(x['plannedMinutes'] for x in daily[:n]),1)} for n in (3,7,30)},'mode':'KNOWN_PLAN_AND_CONSERVATIVE_HISTORICAL_SCENARIO','historicalPredictionStatus':'DESCRIPTIVE_MEDIAN_UNVALIDATED' if intelligence.get('weekdayPattern') else 'PENDING_CALIBRATION','confidence':'LOW','probability':None,'officialRulesModified':False,'note':'Saved plans and historical workload medians, not a guarantee of replenishment or completion.'}
