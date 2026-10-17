"""Persisted local reminder delivery. FIRED is not DISPLAYED without a UI ACK."""
import uuid
from orchestration_models import epoch,iso

ACTIVE={'SCHEDULED','FIRED','DELIVERY_UNCONFIRMED','DISPLAYED'}
def transition(service,row,state,reason,now):
    if row.get('state')==state:return False
    row.update(state=state,stateReason=reason,stateChangedAt=iso(now))
    if state in {'INVALIDATED','CANCELLED','EXPIRED'}:row['invalidated']=True
    if state=='FIRED':row['firedAt']=iso(now)
    if state=='DISPLAYED':row['displayedAt']=iso(now)
    service.event('REMINDER_'+state,{'eventId':uuid.uuid4().hex,'id':row['id'],'demandId':row.get('demandId'),
        'recommendationId':row.get('recommendationId'),'revision':row.get('revision'),'remindAt':row['remindAt'],'reason':reason,'at':iso(now)})
    return True

def tick(service,snapshot):
    now=service.recommendation_lifecycle.clock();changed=False
    for row in service.data['reminders']:
        if not row.get('state'):
            row['state']='CANCELLED' if row.get('dismissed') else 'INVALIDATED' if row.get('invalidated') else 'SCHEDULED';changed=True
        if row['state'] not in ACTIVE:continue
        rec=next((r for r in service.data['recommendations'] if r['id']==row['recommendationId']),None)
        record=service.recommendation_lifecycle.data['records'].get(row['recommendationId'],{})
        # Lifecycle marks ended advice invalid first. Preserve the more precise
        # reminder terminal reason instead of making EXPIRED unreachable.
        if rec is not None and epoch(rec['demandEnd'])<=now:
            changed=transition(service,row,'EXPIRED','WORK_PERIOD_ENDED',now) or changed;continue
        valid=rec is not None and service.recommendation_lifecycle.data['current'].get(rec['demandId'])==rec['id'] and record.get('state') in {'FRESH','UPDATED','NO_LONGER_NEEDED'}
        if not valid or row.get('invalidated'):
            changed=transition(service,row,'INVALIDATED',record.get('reason','REVISION_UNAVAILABLE'),now) or changed;continue
        if row['state']=='SCHEDULED' and epoch(row['remindAt'])<=now:
            changed=transition(service,row,'FIRED','LOCAL_CLOCK_DUE',now) or changed
        elif row['state']=='FIRED' and now-epoch(row['firedAt'])>=60:
            changed=transition(service,row,'DELIVERY_UNCONFIRMED','NO_VISIBLE_UI_ACK',now) or changed
    if changed:service.save()

def acknowledge(service,payload,snapshot):
    tick(service,snapshot)
    row=next((r for r in service.data['reminders'] if r['id']==payload.get('id')),None)
    if not row:raise ValueError('REMINDER_NOT_FOUND')
    if payload.get('recommendationId')!=row['recommendationId'] or payload.get('revision')!=row.get('revision'):raise ValueError('REMINDER_REVISION_MISMATCH')
    if row.get('invalidated') or row.get('dismissed'):raise ValueError('REMINDER_INVALIDATED')
    if row['state'] not in {'FIRED','DELIVERY_UNCONFIRMED','DISPLAYED'}:raise ValueError('REMINDER_NOT_FIRED')
    if transition(service,row,'DISPLAYED','VISIBLE_BROWSER_ACK',service.recommendation_lifecycle.clock()):service.save()
    return row
