"""Ordinary planning uses observed quota only. No Anchor proposals or execution."""
import uuid
from orchestration_models import epoch,iso
from recommendation_engine import finite_percent

class OrdinaryPlanner:
    def recommend(self,demand,quota,window,weekly,burn,prefs,now,fresh=True):
        start,end=epoch(demand['expectedStart']),epoch(demand['expectedEnd'])
        remaining=finite_percent(quota.get('remainingPercent'));week=finite_percent(weekly.get('remainingPercent'))
        reset=quota.get('resetAt');reset=iso(reset) if reset is not None else None
        buffer=float(prefs.get('safetyBufferMinutes',15));minutes=(end-start)/60
        estimate=None
        if burn.get('status')=='ESTIMATED' and burn.get('bucketId')==quota.get('bucketId'):
            import math
            rate=burn.get('percentPerMinute')
            if isinstance(rate,(int,float)) and math.isfinite(rate) and rate>=0:estimate=rate*(minutes+buffer)
        action='MONITOR';reasons=['OBSERVED_QUOTA_ONLY'];risks=[]
        if estimate is None:risks.append('BURN_RATE_LEARNING')
        if remaining is None:risks.append('CURRENT_QUOTA_UNKNOWN')
        if week is None:risks.append('WEEKLY_UNKNOWN')
        extra_checks=[]
        if fresh:
            for item in quota.get('quotaWindows',[]):
                if not isinstance(item,dict):continue
                duration=item.get('windowDuration')
                if duration==10080:continue
                bucket=item.get('bucketId')
                if bucket and bucket==quota.get('bucketId'):continue
                if not bucket and duration==quota.get('windowDuration') and item.get('remainingPercent')==remaining and item.get('resetAt')==reset:continue
                percent=finite_percent(item.get('remainingPercent'))
                if percent is None:continue
                item_reset=item.get('resetAt')
                reset_before_start=False
                if item_reset:
                    try:reset_before_start=epoch(item_reset)<=start
                    except (TypeError,ValueError):pass
                status='AVAILABLE'
                if percent<=0 and not reset_before_start:status='EXHAUSTED'
                elif percent<=10 and not reset_before_start:status='LOW'
                extra_checks.append({'windowDurationMinutes':duration,'remainingPercent':percent,'resetAt':item_reset,'status':status})
            if any(item['status']=='LOW' for item in extra_checks):risks.append('ADDITIONAL_WINDOW_LOW')
            if any(item['status']=='EXHAUSTED' for item in extra_checks):risks.append('ADDITIONAL_WINDOW_EXHAUSTED')
        if not fresh:risks.append('QUOTA_NOT_FRESH')
        elif week==0:action='DEFER_HEAVY_WORK';reasons.append('WEEKLY_EXHAUSTED')
        elif remaining==0 and (not reset or epoch(reset)>start):action='DEFER_HEAVY_WORK';reasons.append('CURRENT_WINDOW_EXHAUSTED')
        elif week is not None and week<=10 and demand['priority']!='HIGH' and demand['intensity']=='HEAVY':
            action='DEFER_HEAVY_WORK';reasons.append('PRESERVE_WEEKLY_FOR_PRIORITY_WORK');risks.append('WEEKLY_LOW')
        elif remaining is not None and remaining>0:
            if reset and epoch(reset)<=start:reasons.append('RECHECK_AFTER_OBSERVED_RESET')
            elif estimate is not None and reset and epoch(reset)>=end+buffer*60:
                if estimate<=remaining:action='USE_CURRENT_WINDOW';reasons.append('CURRENT_WINDOW_COVERS_DEMAND_AND_BUFFER')
                else:action='DEFER_HEAVY_WORK';reasons.append('ESTIMATED_USAGE_EXCEEDS_CURRENT');risks.append('ESTIMATED_USAGE_EXCEEDS_CURRENT')
            elif start<=now+buffer*60 and (not reset or epoch(reset)>start):action='NO_ACTION';reasons.append('START_AND_MONITOR_NO_DURATION_GUARANTEE')
            else:reasons.append('RECHECK_BEFORE_WORK')
        if fresh and any(item['status']=='EXHAUSTED' for item in extra_checks):
            action='DEFER_HEAVY_WORK';reasons.append('ADDITIONAL_WINDOW_EXHAUSTED')
        elif fresh and demand['intensity']=='HEAVY' and demand['priority']!='HIGH' and any(item['status']=='LOW' for item in extra_checks):
            action='DEFER_HEAVY_WORK';reasons.append('PRESERVE_ADDITIONAL_WINDOW')
        if end<=now:action='NO_ACTION';reasons.append('DEMAND_EXPIRED')
        risk='HIGH' if action=='DEFER_HEAVY_WORK' else 'MEDIUM'
        rec={'id':uuid.uuid4().hex,'demandId':demand['id'],'provider':demand['provider'],'bucketId':quota.get('bucketId'),
            'generatedAt':iso(now),'demandStart':demand['expectedStart'],'demandEnd':demand['expectedEnd'],
            'planningPolicy':'OFFICIAL_SNAPSHOT_ONLY','action':action,'productAction':action,
            'recommended_time':None,'recommendedExecutionAt':None,'expected_boundary':reset,'expectedResetAt':reset,
            'policyTargetResetAt':None,'windowDuration':quota.get('windowDuration',300),
            'productConfidence':'LOW' if estimate is None or not fresh else 'MEDIUM','reason':reasons,'risk':risks,'riskLevel':risk,
            'quotaChecks':extra_checks,
            'plannedHeavyUseMinutes':minutes,'safetyBufferMinutes':buffer,'estimatedUsagePercent':estimate,
            'burnRateStatus':burn.get('status','Learning'),'probability':None,'officialSchedulingProgrammatic':False,
            'sameBucketConfirmed':False,'automaticExecution':False,'modelTaskSent':False,
            'reminderAt':demand['expectedStart'] if start>now and fresh else None}
        rec['product']={'action':action,'recommended_time':None,'expected_boundary':reset,'confidence':rec['productConfidence'],'reason':reasons,'risk':risks,'riskLevel':risk}
        return rec
