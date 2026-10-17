// Session-local interaction state is independent from quota/recommendation state.
const interactionState={draft:null,submission:null,lastSuccess:null,expanded:new Set(),displayAcks:new Set()};
try{interactionState.draft=JSON.parse(sessionStorage.getItem('quotalab_v0131_draft')||'null')}catch(_){}
const interactionFields=['title','date','start','end','intensity','priority','targetBoundaryPolicy','boundaryMinutes'];
function saveDraft(form){if(!form)return;interactionState.draft=Object.fromEntries(interactionFields.map(k=>[k,form.elements[k]?.value||'']));try{sessionStorage.setItem('quotalab_v0131_draft',JSON.stringify(interactionState.draft))}catch(_){} }
function detailIdentity(detail){if(detail.dataset.interactionKey)return 'named:'+detail.dataset.interactionKey;const card=detail.closest('[data-for-demand]');if(card)return 'demand:'+card.dataset.forDemand+':'+[...card.querySelectorAll('details')].indexOf(detail);return current+':details:'+ [...($('#page-content')?.querySelectorAll('details')||[])].indexOf(detail)}
function captureInteraction(){const form=$('#product-plan-form');if(form&&!productResearchOn())saveDraft(form);document.querySelectorAll('#page-content details').forEach(d=>{const key=detailIdentity(d);if(d.open)interactionState.expanded.add(key);else interactionState.expanded.delete(key)})}
function restoreInteraction(){const form=$('#product-plan-form');if(form&&!productResearchOn()){if(interactionState.draft)interactionFields.forEach(k=>{if(form.elements[k]&&interactionState.draft[k]!==undefined)form.elements[k].value=interactionState.draft[k]});if(!form.querySelector('[data-next-plan]'))form.insertAdjacentHTML('beforeend',`<button type="button" class="button secondary" data-next-plan>${L('再安排一段（保留时间）','Plan another block (keep times)')}</button>`);const button=form.querySelector('[type="submit"]');if(button){button.disabled=!!interactionState.submission?.busy;if(!button.disabled&&button.dataset.originalLabel){button.textContent=button.dataset.originalLabel;delete button.dataset.originalLabel}}if(interactionState.lastSuccess&&!form.querySelector('[data-submit-feedback]'))form.insertAdjacentHTML('afterbegin',`<p role="status" data-submit-feedback>${esc(interactionState.lastSuccess)}</p>`)}document.querySelectorAll('#page-content details').forEach(d=>{d.open=interactionState.expanded.has(detailIdentity(d))});if(interactionState.timeError&&form&&interactionState.timeError===form.elements.date.value+'T'+form.elements.start.value)showPlanTimeError(form);renderReminderDelivery()}
const interactionRender=render;
render=()=>{captureInteraction();interactionRender();restoreInteraction();const version=$('.version');if(version)version.textContent='v0.13.1 · interaction reliability preview'};
document.addEventListener('input',e=>{const f=e.target.closest('#product-plan-form');if(!f||productResearchOn())return;saveDraft(f);if(!interactionState.submission?.busy)interactionState.submission=null});
document.addEventListener('change',e=>{const f=e.target.closest('#product-plan-form');if(f&&!productResearchOn())saveDraft(f)});
document.addEventListener('toggle',e=>{if(!e.target.isConnected||e.target.tagName!=='DETAILS'||!e.target.closest('#page-content'))return;const key=detailIdentity(e.target);if(e.target.open)interactionState.expanded.add(key);else interactionState.expanded.delete(key)},true);
function planTimePassed(form) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:lifecycleZone(),year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(p=>[p.type,p.value]));
  const now = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  return form.elements.date.value && form.elements.start.value && form.elements.date.value+'T'+form.elements.start.value <= now;
}
function showPlanTimeError(form) {
  interactionState.timeError=form.elements.date.value+'T'+form.elements.start.value;
  let error = form.querySelector('#ql-plan-time-error');
  if (!error) { error = document.createElement('p'); error.id='ql-plan-time-error'; error.setAttribute('role','alert'); form.elements.date.closest('label').before(error); }
  error.textContent=L('尚未保存：开始时间已过去。草稿可能沿用了昨天的日期，请修改日期或开始时间后重新保存。右侧仍是之前已保存计划的建议。','Not saved: the start time has passed. The draft may still use yesterday’s date. Change the date or start time and save again. The advice on the right belongs to an earlier saved plan.');
  form.elements.date.setAttribute('aria-describedby','ql-plan-time-error');
  form.elements.start.setAttribute('aria-describedby','ql-plan-time-error');
}
document.addEventListener('input', e=>{
  const form=e.target.closest('#product-plan-form'); if(!form||productResearchOn())return;
  interactionState.lastSuccess=null; form.querySelector('[data-submit-feedback]')?.remove();
  if(form.querySelector('#ql-plan-time-error')&&!planTimePassed(form)){interactionState.timeError=null;form.querySelector('#ql-plan-time-error').remove();form.elements.date.removeAttribute('aria-describedby');form.elements.start.removeAttribute('aria-describedby');}
});
window.addEventListener('submit',async e=>{
  const f=e.target;
  if(f.id!=='product-plan-form'||productResearchOn())return;
  e.preventDefault();e.stopImmediatePropagation();
  if(interactionState.submission?.busy)return;
  if(!f.reportValidity())return;
  saveDraft(f);const p={...interactionState.draft};
  interactionState.lastSuccess=null;f.querySelector('[data-submit-feedback]')?.remove();clearInteractionFeedback();
  if(p.start===p.end){showInteractionFeedback(L('开始与结束时间相同，无法判断是 0 小时还是整整 24 小时，请调整其中一个时间。','The start and end are identical, so it is unclear whether you mean 0 or 24 hours. Please adjust one of the times.'));f.elements.end?.focus();return}
  // A retry with the same receipt must reach the server, even after its start time.
  const fingerprint=JSON.stringify(p);
  if(planTimePassed(f)&&interactionState.submission?.fingerprint!==fingerprint){showPlanTimeError(f);f.elements.date.focus();return}
  if(interactionState.submission?.fingerprint!==fingerprint)interactionState.submission={id:crypto.randomUUID(),fingerprint,busy:false,title:String(p.title||'').trim()||L('工作计划','Work plan')};
  const submission=interactionState.submission;submission.busy=true;
  const submitButton=f.querySelector('[type="submit"]');
  if(submitButton){submitButton.disabled=true;submitButton.dataset.originalLabel=submitButton.textContent;submitButton.textContent=L('正在保存…','Saving…')}
  try {
    const response=await post('/api/orchestration/demand',{...p,submissionId:submission.id,title:submission.title});
    const d=response.demand;
    if(!d?.id)throw new Error('INVALID_SAVE_RESPONSE');
    interactionState.timeError=null;
    if(typeof selectPlannerDemand==='function')selectPlannerDemand(d.id,false);
    interactionState.lastSuccess=L(`已保存「${d.title}」：${dt(d.expectedStart)} — ${dt(d.expectedEnd)}。安排保存在本机，不会发送任务；额度建议只依据账户实际读数。`,`Saved “${d.title}”: ${dt(d.expectedStart)} — ${dt(d.expectedEnd)}. The plan stays on this device; no task is sent. Advice uses only the quota data your account reports.`);
    submission.success=true;lastPage='';
    try {await refresh()} catch {
      showInteractionFeedback(L('计划已保存，但列表刷新失败。请刷新页面查看，不必重复新建。','The plan was saved, but the list could not refresh. Reload the page; do not create another plan.'));
    }
  } catch(err) {
    if(err.message==='FUTURE_DEMAND_REQUIRED') {interactionState.submission=null;showPlanTimeError(f);}
    else if(err.status>=400&&err.status<500){
      interactionState.submission=null;
      interactionState.lastSuccess=L('未保存：服务拒绝了本次输入。请检查日期、时段及表单选项；原输入已保留。','Not saved: the service rejected these inputs. Check the date, time and form options; inputs are retained.');
      showInteractionFeedback(L('本次输入未通过校验，请检查表单后重试。','These inputs did not pass validation. Check the form and try again.'));
    } else {
      interactionState.lastSuccess=L('尚未确认保存结果。输入和提交编号已保留，可重试同一次提交。','Save result is unconfirmed. Inputs and submission ID are retained; retry the same submission.');
      showInteractionFeedback(errorMessage(err));
    }
  } finally {submission.busy=false;restoreInteraction()}
},true);
function errorMessage(err){const text=String(err?.message||err);const codes={PLAN_SELECTION_CHANGED:L('当前查看的计划已变化，请重新选择该计划再设置提醒。','The selected plan changed. Select it again before setting a reminder.'),REMINDER_TIME_PASSED:L('提醒时间已过，请选择更晚的开工时间或更短提前量。','Reminder time has passed. Choose a later start or shorter lead time.'),RECOMMENDATION_INVALIDATED:L('建议已失效，请等待重新核验后再设置提醒。','The advice is invalid. Wait for revalidation before scheduling.'),RECOMMENDATION_EXPIRED:L('工作时间已过，无法设置提醒。','The work time has passed.'),QUOTA_SNAPSHOT_UNAVAILABLE:L('额度快照不可用，请恢复连接后重新核验。','Quota snapshot unavailable. Reconnect and revalidate.'),REMINDER_REVISION_MISMATCH:L('提醒所属建议已变化，请重新设置。','The reminder revision changed. Schedule it again.')};return codes[text]||L('本地服务连接失败或请求未完成，原输入已保留。','Local connection failed or the request did not complete; your inputs are retained.')}
function clearInteractionFeedback(){interactionState.feedback=null;document.querySelector('#interaction-feedback')?.remove()}
function showInteractionFeedback(text){let area=$('#interaction-feedback');if(!area){area=document.createElement('p');area.id='interaction-feedback';area.setAttribute('role','status');$('#page-content')?.prepend(area)}area.textContent=text;interactionState.feedback=text}
window.addEventListener('click',async e=>{const next=e.target.closest('[data-next-plan]');if(next){e.preventDefault();e.stopImmediatePropagation();saveDraft($('#product-plan-form'));interactionState.submission=null;interactionState.lastSuccess=null;document.querySelector('[data-submit-feedback]')?.remove();showInteractionFeedback(L('新任务已准备；保留上次时段，请修改后提交。','Ready for a new task. Previous times are retained; edit and submit.'));return}const b=e.target.closest('[data-work-remind]');if(!b)return;e.preventDefault();e.stopImmediatePropagation();if(b.disabled)return;b.disabled=true;try{if(current==='planner'&&typeof selectedPlannerDemand==='function'){const selected=selectedPlannerDemand(),card=b.closest('[data-for-demand]');if(!selected||card?.dataset.forDemand!==selected.id)throw new Error('PLAN_SELECTION_CHANGED')}const plan=orch().demands?.find(d=>d.id===b.closest('[data-for-demand]')?.dataset.forDemand);const lead=Number(document.querySelector(`[data-lead-for="${b.dataset.workRemind}"]`)?.value||5);const result=await post('/api/orchestration/reminder',{recommendationId:b.dataset.workRemind,beforeMinutes:lead});showInteractionFeedback(L(`提醒已设置：「${plan?.title||'工作计划'}」，开工 ${dt(plan?.expectedStart)}；将在 ${dt(result.remindAt)} 提醒。相同提醒重复点击不会新增。`,`Reminder set for “${plan?.title||'Work plan'}”, starting ${dt(plan?.expectedStart)}; reminder at ${dt(result.remindAt)}. Repeated clicks do not create duplicates.`));await refresh()}catch(err){showInteractionFeedback(errorMessage(err))}finally{if(b.isConnected)b.disabled=false}},true);
const deliveryLabels=()=>({SCHEDULED:L('等待触发','Scheduled'),FIRED:L('已到点，等待页面确认','Fired; awaiting page acknowledgement'),DISPLAYED:L('页面已确认呈现','Page presentation acknowledged'),CANCELLED:L('已取消','Cancelled'),INVALIDATED:L('建议失效，提醒停用','Advice invalid; reminder disabled'),EXPIRED:L('工作时间已过','Expired'),DELIVERY_UNCONFIRMED:L('尚未确认页面呈现','Page delivery unconfirmed')});
function renderReminderDelivery(){if(desktopShutdown!=='RUNNING')return;let area=$('#reminder-delivery-region');if(!area){area=document.createElement('aside');area.id='reminder-delivery-region';area.setAttribute('aria-label',L('使用提醒','Usage reminders'));area.style.cssText='position:fixed;right:16px;bottom:16px;max-width:440px;z-index:1000;background:var(--surface,#17171a);border:1px solid var(--border,#555);border-radius:12px;padding:16px;box-shadow:0 8px 30px #0006';document.body.append(area)}const active=(orch().reminderRecords||[]).filter(r=>['FIRED','DELIVERY_UNCONFIRMED','DISPLAYED'].includes(r.state)&&!r.invalidated&&!r.dismissed);area.hidden=!active.length;area.innerHTML=active.map(r=>`<section role="status" aria-live="polite" data-delivery-reminder="${esc(r.id)}"><h2>${L('开工提醒','Work reminder')}</h2><p>${esc(dt(r.remindAt))} · ${esc(orch().demands?.find(d=>d.id===r.demandId)?.title||L('工作计划','Work plan'))}</p><p>${L('请复核最新额度后开始工作。','Review the current quota before starting work.')}</p><button class="button secondary" data-product-dismiss="${esc(r.id)}">${L('知道了','Dismiss')}</button></section>`).join('');if(interactionState.feedback&&!$('#interaction-feedback'))showInteractionFeedback(interactionState.feedback);requestAnimationFrame(()=>{if(document.visibilityState!=='visible'||!navigator.onLine)return;active.filter(r=>r.state!=='DISPLAYED').forEach(r=>{const element=area.querySelector(`[data-delivery-reminder="${r.id}"]`);if(!element||!element.getClientRects().length||interactionState.displayAcks.has(r.id))return;interactionState.displayAcks.add(r.id);post('/api/orchestration/reminder-displayed',{id:r.id,recommendationId:r.recommendationId,revision:r.revision}).catch(()=>interactionState.displayAcks.delete(r.id))})})}
const interactionPlan=productPlan;
productPlan=()=>interactionPlan()+`<section class="quiet-panel"><h2>${L('已设置的提醒','Scheduled reminders')}</h2>${(orch().reminderRecords||[]).slice(0,30).map(r=>`<p data-reminder-record="${esc(r.id)}">${esc(dt(r.remindAt))} · ${esc(deliveryLabels()[r.state]||L('历史记录待核验','Legacy record requires review'))} · ${L('计划','Plan')}: ${esc(dt(orch().demands?.find(d=>d.id===r.demandId)?.expectedStart))} · ${esc(r.stateReason||'')}</p>`).join('')||L('暂无提醒','No reminders')}</section>`;
productHistory=()=>releaseHistory()+(orch().recommendationRevisionHistory||[]).slice(-40).reverse().map(e=>{const r=orch().recommendations?.find(r=>r.id===e.recommendationId);const labels={EXPIRED:L('工作已结束，建议停止更新','Work ended; advice updates stopped'),SUPERSEDED:L('旧建议被替换','Previous advice replaced'),RECOMPUTING:L('正在重新计算','Recalculating'),NO_LONGER_NEEDED:L('使用建议已生成','Usage advice generated'),STALE:L('建议失效，等待核验','Advice invalid; awaiting checks'),FAILED:L('重算失败','Recalculation failed'),FRESH:L('建议已生成','Advice generated'),UPDATED:L('建议已更新','Advice updated')};return `<p data-history-event="${esc(e.eventId||e.recommendationId+':'+e.state+':'+e.at)}">${esc(dt(e.at))} · ${labels[e.state]||L('状态变化','State changed')} · ${esc(dt(r?.demandStart))} · ${L('版本','Revision')} ${e.revision??r?.recommendationLifecycle?.revision??'—'} · ${esc(e.reason)}</p>`}).join('');
window.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')renderReminderDelivery()});
