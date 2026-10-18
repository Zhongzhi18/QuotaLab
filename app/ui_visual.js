// Phase / v0.14.2: presentation only. All actions are inherited from the checked v014 card.
const phasePaths={overview:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M14 17.5h7m-3.5-3.5v7"/>',planner:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-12 4h6"/>',usage:'<path d="M4 19V9m8 10V4m8 15v-7M3 21h18"/>',forecast:'<path d="M3 17l6-6 4 3 8-10m-6 0h6v6"/>',signals:'<path d="M2 12h4l3-7 6 14 3-7h4"/>',history:'<path d="M4 8a9 9 0 1 1-1 7M4 3v5H0m12-1v5l3 2"/>',settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',updates:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',research:'<path d="M9 3v6L4 18q-1 3 2 3h12q3 0 2-3l-5-9V3M7 3h10M7 15h10"/>',lab:'<path d="M8 3h8m-7 0v6l-5 10h16L15 9V3M7 15h10"/>','raw-data':'<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-5 2 2-16"/>',diagnostics:'<path d="M4 8h4V4h8v4h4v8h-4v4H8v-4H4zM9 9h6v6H9z"/>',experiments:'<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',tasks:'<path d="M4 6h16m-4-4 4 4-4 4M20 18H4m4-4-4 4 4 4"/>',shield:'<path d="m12 3 8 3v6q0 6-8 9-8-3-8-9V6zM8 12l3 3 5-6"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',sun:'<circle cx="12" cy="12" r="4"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l1.5 1.5m13 13L20 20M4 20l1.5-1.5m13-13L20 4"/>'};
const phaseIcon=key=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${phasePaths[key]||phasePaths.spark}</svg>`;
const phaseMark=`<svg viewBox="0 0 48 48" aria-hidden="true"><rect width="48" height="48" rx="13" fill="#16272c"/><path d="M35.8 14.4a15 15 0 1 0-.9 21.3" fill="none" stroke="#c7e9ae" stroke-width="4.4" stroke-linecap="round"/><path d="m29.4 29.4 9.2 9.2" stroke="#c7e9ae" stroke-width="4.4" stroke-linecap="round"/><circle cx="37.6" cy="10.7" r="2.3" fill="#f0c782"/></svg>`;
function phaseTime(value,kind='time'){
  const d=new Date(typeof value==='number'?value*1000:value);if(!value||Number.isNaN(d.getTime()))return '—';
  const year=new Intl.DateTimeFormat('en',{year:'numeric',timeZone:lifecycleZone()});
  const options=kind==='time'?{hour:'2-digit',minute:'2-digit',hourCycle:'h23'}:{month:'short',day:'numeric',weekday:'short',...(year.format(d)!==year.format(new Date())?{year:'numeric'}:{})};
  return new Intl.DateTimeFormat(locale==='zh'?'zh-CN':'en-GB',{...options,timeZone:lifecycleZone()}).format(d);
}
const phaseBadge=(text,tone='neutral')=>`<span class="phase-badge phase-${tone}"><i aria-hidden="true"></i>${esc(text)}</span>`;
const phaseHead=(title,note)=>`<header class="phase-page-head"><div><h1>${current==='overview'?L('看清额度，再安排工作','See your quota, then plan your work'):title}</h1>${current==='overview'?`<p class="ql-home-description">${L('知道现在有多少余量，为下一段工作留好时间。','Know what is available. Make time for your next work period.')}</p>`:!note?'':`<p>${note}</p>`}</div></header>`;
const phaseEmpty=(title,note)=>`<div class="phase-empty"><div class="phase-empty-mark" aria-hidden="true">${phaseMark}<span></span><span></span></div><h3>${title}</h3><p>${note}</p></div>`;
function syncPhaseSidebarToggle(){
  const toggle=$('#sidebar-toggle'),shell=$('#app-shell');if(!toggle||!shell)return;
  const midWidth=window.matchMedia('(min-width: 761px) and (max-width: 1120px)').matches;
  const desktop=window.matchMedia('(min-width: 761px)').matches;
  const collapsed=desktop&&(shell.classList.contains('collapsed')||(midWidth&&!shell.classList.contains('expanded')));
  shell.classList.toggle('compact-navigation',collapsed);
  const label=collapsed?L('展开菜单','Expand menu'):L('收起菜单','Collapse menu');
  toggle.setAttribute('aria-label',label);toggle.setAttribute('title',label);toggle.setAttribute('aria-controls','sidebar');toggle.setAttribute('aria-expanded',String(!collapsed));
}
window.quotaLabSyncSidebarToggle=syncPhaseSidebarToggle;
window.addEventListener('resize',()=>window.requestAnimationFrame(syncPhaseSidebarToggle),{passive:true});

const phaseCardBase=productRecCard;
productRecCard=(r,actions=true)=>{
  if(!r&&current==='planner')return `<section class="quiet-panel phase-action-card phase-no-plan phase-plan-empty" role="status" aria-live="polite"><p>${L('建议会显示在这里。','Your recommendation will appear here.')}</p></section>`;
  if(!r)return `<section class="quiet-panel phase-action-card phase-no-plan"><div><h2>${L('下一段工作，由你安排。','Your next block starts with you.')}</h2><p>${L('选一个需要 Codex 的时段，我们帮你判断下一步。','Choose when you need Codex. We will help you consider the next step.')}</p></div><a href="#planner" class="button">${L('创建使用计划','Create a work plan')}${phaseIcon('arrow')}</a></section>`;
  const life=r.recommendationLifecycle||{},d=orch().demands?.find(d=>d.id===r.demandId);
  const safe=['FRESH','UPDATED','NO_LONGER_NEEDED'].includes(life.state)&&Date.parse(life.validUntil)>Date.now()&&evoCapacityFresh()&&latest?.status!=='UNAVAILABLE';
  const blocked=life.allocation?.blocked===true;
  const source=document.createElement('template');source.innerHTML=phaseCardBase(r,actions&&safe);
  // Reuse existing protected controls and statuses; no new task instruction or action API.
  const old=source.content.querySelector('.product-recommendation');
  const message=old?.querySelector('[role="status"]')?.textContent||lifecycleMessage(r);
  const controls=safe?[...source.content.querySelectorAll('[data-work-remind],[data-lead-for],[data-product-open],[data-product-copy],[data-product-remind]')].map(e=>e.outerHTML).join(''):'';
  const actionLabel=!safe?L('等待重新核验','Awaiting fresh advice'):blocked?L('需要调整时段','Adjust the work period'):({USE_CURRENT_WINDOW:L('使用当前窗口','Use the current window'),NO_ACTION:L('按计划使用，持续观察','Use as planned. Keep observing.'),MONITOR:L('开工前，再看一眼额度','Recheck capacity before work.'),DEFER_HEAVY_WORK:L('降低强度或调整时间','Reduce intensity or move the block'),SCHEDULE_OFFICIAL_TASK:L('官方安排仍由你确认','You control official scheduling')})[r.productAction]||L('开工前复核','Recheck before work');
  const details=[...source.content.querySelectorAll('details')];
  const quotaSummary=safe?productWindows().all.map(w=>`${productWindowLabel(w.window_duration_minutes)} ${w.remaining_percent??'—'}%`).join(' · '):L('等待快照','Waiting for snapshot');
  const facts=[{icon:'overview',label:L('当前额度','Current quota'),value:quotaSummary,note:L('账户返回的额度窗口','Quota windows returned by account')},{icon:'clock',label:L('时间余量','Safety buffer'),value:`${r.safetyBufferMinutes??15} min`,note:L('规划偏好，不是保证','Planning preference, not a guarantee')},{icon:'planner',label:L('计划冲突','Plan conflicts'),value:blocked?L('需调整','Needs adjustment'):L('见当前核验','See current checks'),note:L('优先级与估计预算','Priority and estimated budget')},{icon:'shield',label:L('使用估计','Usage estimate'),value:r.estimatedUsagePercent==null?L('证据不足','Insufficient evidence'):r.estimatedUsagePercent+'%',note:L('不保证完成或未来重置','No completion or reset guarantee')}];
  return `<section class="quiet-panel product-recommendation phase-action-card ${!safe?'phase-advice-unavailable':''}" data-recommendation-id="${esc(r.id)}" data-for-demand="${esc(r.demandId)}"><div class="phase-card-meta"><span class="phase-kicker">${L('你的下一段工作','YOUR NEXT BLOCK')}</span>${phaseBadge(safe?(blocked?L('需调整','Needs adjustment'):L('已核验','Checked')):L('暂不可用','Unavailable'),safe?(blocked?'amber':'sage'):'amber')}</div><h2 class="phase-plan-title">${esc(d?.title||L('工作建议','Work advice'))}</h2><div class="phase-work-period" aria-label="${L('计划工作时段，不是旧任务指令','Planned work period, not an old task instruction')}"><time datetime="${esc(r.demandStart||'')}">${esc(phaseTime(r.demandStart))}</time><span aria-hidden="true">—</span><time datetime="${esc(r.demandEnd||'')}">${esc(phaseTime(r.demandEnd))}</time></div><p class="phase-period-date">${esc(phaseTime(r.demandStart,'date'))} ${phaseTime(r.demandStart,'date')!==phaseTime(r.demandEnd,'date')?' → '+esc(phaseTime(r.demandEnd,'date')):''} <span title="${esc(lifecycleZone())}">· ${esc(typeof artTimezoneLabel==='function'?artTimezoneLabel(lifecycleZone()):lifecycleZone())}</span></p><div class="phase-advice-summary"><span class="phase-summary-icon">${phaseIcon(safe?'arrow':'clock')}</span><div><h3>${actionLabel}</h3><p role="status">${esc(message)}</p></div></div>${safe?`<div class="phase-reset-line"><span>${L('官方读数中的重置时间','Reset in the official snapshot')}</span><strong>${esc(dt(r.expected_boundary))}</strong></div>`:`<p class="phase-guard-note">${L('旧建议不可用 · 不显示旧任务指令','Old advice unavailable · No old task instructions')}</p>`}<details class="phase-reasons"><summary>${L('为什么这样建议？','Why this suggestion?')}<span>${phaseIcon('arrow')}</span></summary><div class="phase-reason-grid">${facts.map(f=>`<div>${phaseIcon(f.icon)}<span>${f.label}</span><strong>${esc(f.value)}</strong><small>${f.note}</small></div>`).join('')}</div><div class="phase-reason-source">${details.map(e=>e.innerHTML.replace(/<summary[\s\S]*?<\/summary>/,'')).join('')}</div></details><footer class="phase-action-footer"><small>${esc(releaseFreshLabel(r))} · ${safe?L('当前建议已核验','Current advice checked'):L('旧建议不可用','Old advice unavailable')}</small>${controls?`<div class="phase-protected-controls">${controls}</div>`:''}</footer></section>`;
};

productOverview=()=>{
  const fresh=evoCapacityFresh(),c=fresh?(evo().capacity||{}):{state:'UNAVAILABLE'},p=c.remainingPercent;
  const plans=[...(orch().todayTomorrowDemands||[])].sort((a,b)=>Date.parse(a.expectedStart)-Date.parse(b.expectedStart));
  const rec=plans.map(d=>orch().currentRecommendations?.find(r=>r.demandId===d.id)).find(Boolean);
  const titles={AVAILABLE:L('有余量，专注下一步。','Room to focus.'),LOW:L('留一点余量给重要的事。','Save room for what matters.'),EXHAUSTED:L('让下一段工作，稍等片刻。','A pause before the next block.'),UNAVAILABLE:L('等一份新读数，再做判断。','Fresh readings. Clear decisions.')};
  const currentWindow=productWindows().primary;
  const estimate=c.historicalEstimatedMinutes==null?L('百分比不是可工作小时数。','A percentage is not hours of work.'):L(`历史估计约 ${c.historicalEstimatedMinutes} 分钟，非保证。`,`Historical estimate: about ${c.historicalEstimatedMinutes} min. Not guaranteed.`);
  return phaseHead(L('把专注，留给重要的事。','Space for your best work.'),L('额度看得清，下一段工作更从容。','Clear capacity. A considered next step.'),'OVERVIEW')+`<div class="phase-home-grid"><div class="phase-capacity-column"><section class="evo-capacity-hero phase-capacity-well"><div class="phase-card-meta"><span class="phase-kicker">AI CAPACITY</span>${phaseBadge(fresh?L('当前读数','Current snapshot'):L('等待快照','Awaiting snapshot'),fresh?'sage':'amber')}</div><div class="phase-meter"><svg viewBox="0 0 240 240" aria-hidden="true"><circle class="phase-meter-track" cx="120" cy="120" r="98"/><circle class="phase-meter-fill" cx="120" cy="120" r="98" pathLength="100" stroke-dasharray="${p??0} 100" transform="rotate(-90 120 120)"/><path class="phase-meter-ticks" d="M120 5v7M235 120h-7M120 235v-7M5 120h7"/></svg><div><strong data-capacity-number>${p??'—'}<small>${p==null?'':'%'}</small></strong><span>${L('五小时剩余','5-hour remaining')}</span></div></div><h2>${titles[c.state]||titles.UNAVAILABLE}</h2><p>${estimate}</p><details class="phase-capacity-detail"><summary>${L('这个读数意味着什么？','What does this reading mean?')}</summary><p>${L('额度来自官方快照，不保证任务完成。周预算单独计算；本应用不会制造重置。','Quota comes from the official snapshot and does not guarantee task completion. Weekly budget is separate; this app does not create resets.')}</p><p>${fresh?L('本次读数时间','Snapshot read at')+': '+esc(dt(latest?.last_snapshot)):L('额度历史读数仅供对照；等待新快照，旧建议不可使用。','Historical readings are reference only. Wait for a fresh snapshot; old advice is disabled.')}</p></details></section><div class="evo-grid-three phase-secondary-ledger"><article class="evo-metric"><span>${L('周额度','Weekly capacity')}</span><strong>${c.weeklyPercent==null?'—':c.weeklyPercent+'%'}</strong><small>${L('独立于五小时预算','Separate weekly budget')}</small></article><article class="evo-metric"><span>${L('距离官方重置读数','Time to reported reset')}</span><strong data-reset="${esc(c.resetAt||'')}">—</strong><small>${c.resetAt?esc(phaseTime(c.resetAt))+L(' · 官方读数',' · reported'):L('等待快照','Waiting for snapshot')}</small></article></div></div><div class="phase-next-column"><div class="evo-section-title phase-next-title"><h2>${L('下一步建议','Next recommendation')}</h2><a class="subtle-link" href="#planner">${L('全部计划','All plans')}${phaseIcon('arrow')}</a></div>${productRecCard(rec,true)}<section class="phase-schedule"><div class="evo-section-title"><h2>${L('今天 / 明天','Today / tomorrow')}</h2><span class="phase-badge">${plans.length} ${L('段安排','blocks')}</span></div>${plans.length?plans.slice(0,3).map(d=>`<div class="phase-schedule-row"><span class="phase-schedule-icon">${phaseIcon('planner')}</span><div><strong>${esc(d.title)}</strong><small>${esc(phaseTime(d.expectedStart,'date'))} · ${esc(phaseTime(d.expectedStart))}–${esc(phaseTime(d.expectedEnd))}</small></div><span class="phase-badge">${esc({HEAVY:L('重度','Heavy'),MEDIUM:L('中度','Moderate'),LIGHT:L('轻度','Light')}[d.intensity]||'—')}</span></div>`).join(''):`<p class="phase-empty-inline">${L('暂无安排。为重要的工作留一个时段。','No blocks yet. Save a period for work that matters.')}</p>`}<a href="#planner" class="phase-schedule-add">+ ${L('规划下一段工作','Plan your next block')}</a></section></div></div><div class="phase-trust-line">${phaseIcon('shield')}<span>${L('只读额度','Read-only quota')}</span><i></i><span>${L('计划留在本机','Plans stay local')}</span><i></i><span>${L('官方任务由你安排','Official tasks stay in your control')}</span></div>`;
};

const phasePlanBase=productPlan;
productPlan=()=>{
  const template=document.createElement('template');template.innerHTML=phasePlanBase();
  const head=template.content.querySelector('.page-head');if(head)head.outerHTML=phaseHead(L('安排工作时段','Plan a work period'),productPlanningNote());
  const form=template.content.querySelector('#product-plan-form');
  if(form){form.closest('section')?.classList.add('phase-plan-input');form.querySelectorAll('h2').forEach(h=>h.remove());const times=form.querySelector('.form-grid');if(times&&!form.querySelector('[data-phase-midnight-hint]'))times.insertAdjacentHTML('afterend',`<p class="phase-cross-midnight-hint" data-phase-midnight-hint role="status" aria-live="polite" hidden></p>`);const submit=form.querySelector('[type="submit"]');if(submit)submit.innerHTML=L('给我建议','Recommend a plan')+phaseIcon('arrow');}
  const aside=template.content.querySelector('.orch-grid>aside');
  if(aside){
    aside.classList.add('phase-plan-output');aside.setAttribute('aria-label',L('使用建议','Usage recommendation'));aside.querySelector(':scope>h2')?.remove();
    const cards=[...aside.querySelectorAll(':scope>.phase-action-card')].sort((a,b)=>{const ar=orch().currentRecommendations?.find(r=>r.id===a.dataset.recommendationId),br=orch().currentRecommendations?.find(r=>r.id===b.dataset.recommendationId);return Date.parse(ar?.demandStart)-Date.parse(br?.demandStart)});
    cards.forEach((card,index)=>{
      if(index===0)return;
      const r=orch().currentRecommendations?.find(r=>r.id===card.dataset.recommendationId),d=orch().demands?.find(d=>d.id===r?.demandId);
      const wrapper=document.createElement('details');wrapper.className='phase-secondary-plan';wrapper.dataset.forDemand='summary-'+card.dataset.forDemand;
      wrapper.innerHTML=`<summary><span class="phase-secondary-icon">${phaseIcon('planner')}</span><span><strong>${esc(phaseTime(r?.demandStart))}–${esc(phaseTime(r?.demandEnd))}</strong><small>${esc(phaseTime(r?.demandStart,'date'))} · ${esc(d?.title||L('工作计划','Work plan'))}</small></span><span class="phase-secondary-arrow">${phaseIcon('arrow')}</span></summary>`;
      card.replaceWith(wrapper);wrapper.append(card);aside.append(wrapper);
    });
    if(!aside.querySelector('.phase-action-card'))aside.insertAdjacentHTML('afterbegin',productRecCard(null));
  }
  return template.innerHTML;
};

const phaseHistoryBase=productHistory;
productHistory=()=>{
  const labels={EXPIRED:L('工作已结束','Work ended'),SUPERSEDED:L('旧建议已替换','Previous advice replaced'),RECOMPUTING:L('正在重新计算','Recalculating'),NO_LONGER_NEEDED:L('使用建议已生成','Usage advice generated'),STALE:L('旧建议失效','Previous advice unavailable'),FAILED:L('重算未完成','Recalculation incomplete'),FRESH:L('计划已创建','Plan created'),UPDATED:L('建议已更新','Advice updated')};
  const stateEvents=(orch().recommendationRevisionHistory||[]).map(e=>({...e,_kind:'advice',_at:e.at}));
  const localEvents=(orch().history||[]).filter(e=>['LOCAL_REMINDER_SAVED','PUBLIC_SIGNAL_IMPORTED'].includes(e.type)).map(e=>({...e,_kind:e.type,_at:e.timestamp}));
  const rawEvents=[...stateEvents,...localEvents].sort((a,b)=>(Date.parse(b._at)||0)-(Date.parse(a._at)||0)).slice(0,40);
  // Collapse internal refresh transitions in the user timeline only. Stored
  // lifecycle evidence remains intact and is available under original records.
  const seen = new Set();
  const events=rawEvents.filter(e=>{
    if(e._kind!=='advice')return true;
    if(['RECOMPUTING','SUPERSEDED','STALE'].includes(e.state))return false;
    const rec=orch().recommendations?.find(r=>r.id===e.recommendationId);
    const key=(rec?.demandId||e.recommendationId)+':'+Math.floor((Date.parse(e._at)||0)/60000);
    if(seen.has(key))return false;
    seen.add(key);return true;
  }).slice(0,40);
  const plans=(orch().demands||[]).slice(0,50),hasActivity=plans.length||events.length;
  const planRows=plans.map(d=>`<article class="phase-history-plan" data-demand-id="${esc(d.id)}"><span>${phaseIcon('planner')}</span><div><h3>${esc(d.title)}</h3><p>${esc(artLocalDate(d.expectedStart))} — ${esc(artLocalDate(d.expectedEnd))}</p></div></article>`).join('');
  const eventRows=events.map(e=>{
    if(e._kind==='advice'){
      const r=orch().recommendations?.find(r=>r.id===e.recommendationId);
      const demand=plans.find(d=>d.id===r?.demandId);
      return `<article class="phase-event" data-history-event="${esc(e.eventId||e.recommendationId+':'+e.state+':'+e.at)}"><div class="phase-event-dot"></div><div><time>${esc(artLocalDate(e.at))}</time><h3>${labels[e.state]||L('状态已变化','Status changed')}</h3><p>${L('所属计划','For plan')}: ${esc(demand?.title||r?.title||artLocalDate(r?.demandStart))}</p><details><summary>${L('查看记录','Record details')}</summary><p>${L('版本','Revision')} ${e.revision??r?.recommendationLifecycle?.revision??'—'} · ${esc(e.reason||'')}</p></details></div></article>`;
    }
    const reminder=e._kind==='LOCAL_REMINDER_SAVED';
    return `<article class="phase-event"><div class="phase-event-dot"></div><div><time>${esc(artLocalDate(e._at))}</time><h3>${reminder?L('已保存本地提醒','Local reminder saved'):L('已保存公开片段','Public excerpt saved')}</h3></div></article>`;
  }).join('')+(rawEvents.length?`<details class="ql-raw-history"><summary>${L('查看原始变化记录','View original activity records')} (${rawEvents.length})</summary><p>${L('同一分钟、同一计划的建议合并显示；内部重算过程保留在这里，不代表创建了多个计划。','Advice for one plan within one minute is grouped above. Internal recalculations remain here; they do not represent extra plans.')}</p>${rawEvents.map(e=>`<p><time>${esc(artLocalDate(e._at))}</time> · ${esc(labels[e.state]||e.type||L('状态变化','Status change'))}</p>`).join('')}</details>`:'');
  const empty=`<div class="phase-history-empty"><div class="phase-empty-mark" aria-hidden="true">${phaseMark}</div><div><h2>${L('先安排一段重要工作','Start with one work plan')}</h2><p>${L('保存第一段安排后，可在这里回看计划和建议的变化。','Save your first work period to review plans and changes to your advice here.')}</p><a class="button secondary" href="#planner">${L('安排工作','Plan work')}${phaseIcon('arrow')}</a></div></div>`;
  return phaseHead(L('每一段安排，都有来处。','A record of considered work.'),L('计划、提醒与建议变化，保留在你的设备上。','Plans, reminders and advice changes stay on this device.'),'HISTORY')+`<div class="phase-history-grid">${hasActivity?`<section class="phase-history-plans"><div class="evo-section-title"><h2>${L('已保存的计划','Saved plans')}</h2><span class="phase-badge">${plans.length}</span></div>${planRows||`<p class="phase-history-inline-empty">${L('这段时间没有保存计划。','No saved plans in this period.')}</p>`}</section><section class="phase-history-events"><div class="evo-section-title"><h2>${L('最近变化','Recent activity')}</h2><span class="phase-badge">${L(`最近 ${events.length} 条`,`${events.length} recent`)}</span></div>${eventRows||`<p class="phase-history-inline-empty">${L('安排保存后，相关变化会显示在这里。','Changes will appear here after you save a plan.')}</p>`}</section>`:empty}</div>`;
};

function phaseRefineDOM(){
  document.body.classList.add('phase-shell');document.body.dataset.phaseRoute=current;
  syncPhaseSidebarToggle();
  const brand=$('.brand-mark');if(brand&&!brand.querySelector('svg'))brand.innerHTML=phaseMark;
  const version=$('.version');if(version)version.textContent='v0.14.2';
  const theme=$('#theme-toggle')||$('.theme-button');if(theme){theme.innerHTML=phaseIcon('sun');const themeLabel=L('切换深浅主题','Switch color theme');theme.setAttribute('aria-label',themeLabel);theme.title=themeLabel;}
  document.querySelectorAll('[data-nav]').forEach(a=>{const key=a.dataset.nav,n=a.querySelector('.nav-icon');if(n)n.innerHTML=phaseIcon(key)});
  const page=$('#page-content');if(!page)return;
  if(current==='overview'){
    const well=page.querySelector('.phase-capacity-well'),note=well?.querySelector(':scope>p'),details=well?.querySelector('.phase-capacity-detail');
    if(note&&details&&!note.dataset.phaseDisclosure){note.dataset.phaseDisclosure='true';details.append(note);}
  }
  const head=page.querySelector(':scope>.evo-page-head,:scope>.page-head');
  if(head&&!head.classList.contains('phase-page-head')){
    head.classList.add('phase-page-head');head.querySelector('.evo-eyebrow')?.remove();
    const copy=document.createElement('div');copy.className='phase-head-copy';while(head.firstChild)copy.append(head.firstChild);head.append(copy);
  }
  page.querySelectorAll('.evo-empty').forEach(empty=>{if(empty.dataset.phaseDone)return;empty.dataset.phaseDone='true';const text=empty.textContent;empty.innerHTML=phaseEmpty(L('让证据慢慢积累。','Let evidence build.'),esc(text));});
  // Explanation-heavy supplementary material is available on demand; primary warnings stay visible.
  page.querySelectorAll('.evo-metric>small').forEach(note=>{if(current==='forecast'||note.closest('details')||note.dataset.phaseDone)return;note.dataset.phaseDone='true';const detail=document.createElement('details');detail.className='phase-metric-note';detail.innerHTML=`<summary>${L('依据与边界','Basis & limits')}</summary>`;note.replaceWith(detail);detail.append(note);});
  const form=$('#product-plan-form');if(form&&!form.querySelector('[data-phase-timezone]')&&!form.querySelector('.r11-timezone'))form.insertAdjacentHTML('beforeend',`<p class="phase-timezone" data-phase-timezone>${phaseIcon('clock')}${L('规划时区','Planning timezone')} · ${esc(lifecycleZone())}</p>`);
  if(current==='planner')page.querySelectorAll('.orch-grid').forEach(e=>e.classList.add('phase-plan-layout'));
  if(current==='forecast'){
    const days=evo().forecast?.horizons?.[String(forecastHorizon)]?.days||[];const max=Math.max(1,...days.map(d=>d.plannedMinutes));
    page.querySelectorAll('.evo-forecast-list article').forEach((row,i)=>{if(!row.querySelector('.phase-workload-bar')&&days[i]){const bar=document.createElement('div');bar.className='phase-workload-bar';bar.setAttribute('aria-hidden','true');bar.innerHTML=`<i style="width:${days[i].plannedMinutes/max*100}%"></i>`;row.append(bar)}});
    if(!page.querySelector('.phase-forecast-boundary'))page.querySelector('.evo-segment')?.insertAdjacentHTML('afterend',`<p class="phase-forecast-boundary">${phaseIcon('shield')}${L('工作量情景 · 不是官方未来额度','Workload only · Not official future quota')}</p>`);
  }
  if(current==='settings'){
    page.querySelectorAll('.settings-section').forEach(section=>{if(!section.querySelector(':scope>h2')&&!section.querySelector('.evo-section-title')){const key=section.querySelector('#lifecycle-timezone-form')?'clock':section.querySelector('#exit-app')?'settings':'updates';const h=document.createElement('h2');h.innerHTML=phaseIcon(key)+L(key==='clock'?'时间与日历':key==='settings'?'应用运行':'本机偏好',key==='clock'?'Time & calendar':key==='settings'?'Application':'Local preferences');section.prepend(h)}});
    const storage=page.querySelector('.evo-storage');
    if(storage){const note=[...storage.querySelectorAll('p.caption')].find(p=>!p.hasAttribute('data-install-scan'));
      if(note)note.textContent=L('安装残留仅人工复核。v0.14.x 共享数据根，卸载默认保留；彻底删除会同时影响本家族数据，需明确确认。其他版本的独立数据根不受影响。','Installation candidates require manual review. v0.14.x shares a data root; uninstall keeps it by default. Explicit permanent deletion affects this family. Other independent version roots are not touched.');}
  }
  if(current==='updates'){page.querySelectorAll('section.quiet-panel').forEach((e,i)=>e.classList.add('phase-update-block','phase-update-'+i));}
  if(current==='signals'){page.querySelectorAll('.quiet-panel').forEach(e=>e.classList.add('phase-signal-card'));}
  document.querySelectorAll('.button:not([data-phase-button])').forEach(b=>{b.dataset.phaseButton='true';if(b.closest('.phase-protected-controls')&&!b.querySelector('svg'))b.insertAdjacentHTML('afterbegin',phaseIcon('clock'))});
  restoreInteraction();lifecycleStripUnsafe();
}
const phaseRenderBase=render;
forecastPage=()=>{
  const f=evo().forecast||{},h=f.horizons?.[String(forecastHorizon)]||{},days=h.days||[],minutes=h.plannedMinutes??0;
  const hasPlannedWork=days.some(d=>Number(d.plannedMinutes)>0||Number(d.heavyPlans)>0);
  const history=f.historicalPredictionStatus==='PENDING_CALIBRATION'?L('等待样本','Learning'):L('历史中位数','Historical median');
  const rows=days.map(d=>`<article><time>${esc(d.date)}</time><div><strong>${L(`${d.plannedMinutes} 分钟`,`${d.plannedMinutes} min`)}</strong><span>${d.heavyPlans?L(`${d.heavyPlans} 段重度工作`,`${d.heavyPlans} intensive blocks`):L('已保存的安排','Saved plan')}</span></div><small>${d.historicalWorkload?L(`历史中位数 ${d.historicalWorkload.medianMinutes} 分钟 · ${d.historicalWorkload.sampleDays} 天样本`,`Median ${d.historicalWorkload.medianMinutes} min · ${d.historicalWorkload.sampleDays} days`):L('暂无历史样本','No history yet')}</small></article>`).join('');
  return evoHead(L('为未来留出余量。','Make space for what comes next.'),L('展示已保存的工作计划与历史工作量。','Saved work plans and historical workload.'),'RHYTHM FORECAST / CONDITIONAL SCENARIOS')+`<div class="evo-segment" role="group" aria-label="${L('预测范围','Forecast horizon')}">${[3,7,30].map(n=>`<button class="button ${n===forecastHorizon?'':'secondary'}" aria-pressed="${n===forecastHorizon}" data-forecast-days="${n}">${n} ${L('天','days')}</button>`).join('')}</div><div class="evo-grid-three phase-forecast-metrics">${metric(L('已安排工作','Scheduled workload'),hasPlannedWork?L(`${minutes} 分钟`,`${minutes} min`):L('尚无已保存安排','No saved plans yet'),hasPlannedWork?'':L('保存安排后，这里会显示计划时长。','Save a plan to see scheduled time here.'))}${metric(L('历史工作量','Historical workload'),history)}${metric(L('未来额度','Future quota'),L('不预测','Not predicted'))}</div><section class="quiet-panel"><h2>${L('未来工作负荷','Upcoming workload')}</h2><div class="evo-forecast-list">${rows}</div></section>`;
};
render=()=>{phaseRenderBase();phaseRefineDOM()};
const phaseStripBase=lifecycleStripUnsafe;
lifecycleStripUnsafe=()=>{
  phaseStripBase();
  if(!evoCapacityFresh()){
    const well=$('.phase-capacity-well');
    well?.querySelector('.phase-meter-fill')?.setAttribute('stroke-dasharray','0 100');
    const badge=well?.querySelector('.phase-card-meta .phase-badge');if(badge){badge.classList.remove('phase-sage');badge.classList.add('phase-amber');badge.textContent=L('历史读数','Historical reading');}
    const note=well?.querySelector(':scope>p');if(note)note.textContent=L('等待新快照；不提供旧的可用时长估计。','Awaiting a fresh snapshot. Old duration estimates are unavailable.');
    const reset=$('.phase-secondary-ledger [data-reset]');if(reset){reset.dataset.reset='';reset.textContent='—';const note=reset.parentElement.querySelector('small');if(note)note.textContent=L('等待快照','Awaiting snapshot');}
  }
  document.querySelectorAll('.phase-action-card[data-recommendation-id]').forEach(card=>{
    const r=orch().currentRecommendations?.find(r=>r.id===card.dataset.recommendationId),life=r?.recommendationLifecycle||{};
    const safe=evoCapacityFresh()&&['FRESH','UPDATED','NO_LONGER_NEEDED'].includes(life.state)&&Date.parse(life.validUntil)>Date.now()&&latest?.status!=='UNAVAILABLE';
    if(safe)return;
    card.classList.add('phase-advice-unavailable');card.querySelector('.phase-reset-line')?.remove();
    card.querySelectorAll('[data-work-remind],[data-lead-for],[data-product-copy],[data-product-open],[data-product-remind]').forEach(e=>e.remove());
    const badge=card.querySelector('.phase-card-meta .phase-badge');if(badge){badge.classList.remove('phase-sage');badge.classList.add('phase-amber');badge.textContent=L('暂不可用','Unavailable');}
    const title=card.querySelector('.phase-advice-summary h3');if(title)title.textContent=L('等待重新核验','Awaiting fresh advice');
    const message=card.querySelector('[role="status"]');if(message)message.textContent=!evoCapacityFresh()?L('当前快照不可用。旧建议不可执行，请恢复连接后重新核验。','Current snapshot unavailable. Old advice cannot be used; reconnect and revalidate.'):lifecycleMessage(r||{});
    const fact=card.querySelector('.phase-reason-grid>div:first-child strong');if(fact)fact.textContent=L('等待快照','Awaiting snapshot');
    const source=card.querySelector('.phase-reason-source');if(source)source.hidden=true;
    const footer=card.querySelector('.phase-action-footer>small');if(footer)footer.textContent=L('旧建议不可用','Old advice unavailable');
    if(!card.querySelector('.phase-guard-note'))card.querySelector('.phase-reasons')?.insertAdjacentHTML('beforebegin',`<p class="phase-guard-note">${L('旧建议不可用 · 不显示旧任务指令','Old advice unavailable · No old task instructions')}</p>`);
  });
};
// A narrow visual adjustment does not alter lifecycle/debounce, provider reads, or action handlers.

/* v0.14.2 r5: simplify the product around the two decisions users make most. */
const phaseCompactRecommendationBase=productRecCard;
productRecCard=(r,actions=true)=>{
  if(productResearchOn())return phaseCompactRecommendationBase(r,actions);
  if(!r)return `<section class="quiet-panel phase-action-card phase-no-plan"><div><span class="phase-no-plan-label">${L('下一步','NEXT')}</span><h2>${L('先安排一段工作','Choose a work period')}</h2><p>${L('选择时段，再看当前额度是否合适。','Choose a time, then check current capacity.')}</p></div><a href="#planner" class="button">${L('创建计划','Create a plan')}${phaseIcon('arrow')}</a></section>`;
  const html=phaseCompactRecommendationBase(r,actions),template=document.createElement('template');template.innerHTML=html;
  const card=template.content.querySelector('.phase-action-card');if(!card)return html;
  const life=r.recommendationLifecycle||{},safe=['FRESH','UPDATED','NO_LONGER_NEEDED'].includes(life.state)&&Date.parse(life.validUntil)>Date.now()&&evoCapacityFresh()&&latest?.status!=='UNAVAILABLE';
  if(safe)return html;
  card.classList.add('phase-advice-unavailable');
  card.querySelector('.phase-plan-title')?.replaceChildren(document.createTextNode(L('建议需要更新','Advice needs refreshing')));
  card.querySelector('.phase-work-period')?.remove();card.querySelector('.phase-period-date')?.remove();
  card.querySelector('.phase-reset-line')?.remove();card.querySelector('.phase-reasons')?.remove();
  card.querySelector('.phase-action-footer')?.remove();card.querySelector('.phase-guard-note')?.remove();
  card.querySelectorAll('[data-work-remind],[data-lead-for],[data-product-open],[data-product-copy],[data-product-remind]').forEach(el=>el.remove());
  const status=card.querySelector('.phase-advice-summary [role="status"]');
  if(status)status.textContent=!evoCapacityFresh()?L('当前额度读数不可用，请恢复连接后重新规划。','Current quota readings are unavailable. Reconnect and plan again.'):lifecycleMessage(r);
  const action=document.createElement('a');action.className='button secondary phase-replan';action.href='#planner';action.dataset.phaseReplan='true';action.innerHTML=`${L('重新规划','Plan again')}${phaseIcon('arrow')}`;
  card.append(action);
  return template.innerHTML;
};

const phasePlanPageR5Base=productPlan;
productPlan=()=>{
  const html=phasePlanPageR5Base();
  const template=document.createElement('template');template.innerHTML=html;
  template.content.querySelectorAll('.phase-secondary-plan').forEach(wrapper=>{
    const card=wrapper.querySelector('.phase-action-card[data-recommendation-id]');if(!card)return;
    const r=orch().currentRecommendations?.find(item=>item.id===card.dataset.recommendationId),life=r?.recommendationLifecycle||{};
    const safe=!!r&&evoCapacityFresh()&&['FRESH','UPDATED','NO_LONGER_NEEDED'].includes(life.state)&&Date.parse(life.validUntil)>Date.now()&&latest?.status!=='UNAVAILABLE';
    if(safe)return;
    wrapper.classList.add('phase-secondary-stale');
    const summary=wrapper.querySelector(':scope>summary');
    if(summary)summary.innerHTML=`<span class="phase-secondary-icon">${phaseIcon('clock')}</span><span><strong>${L('建议已失效','Advice expired')}</strong><small>${L('展开查看状态，旧时段已隐藏','Open for status; old time is hidden')}</small></span><span class="phase-secondary-arrow">${phaseIcon('arrow')}</span>`;
  });
  return template.innerHTML;
};

const phaseProductOverviewBase=productOverview;
productOverview=()=>{
  if(productResearchOn())return phaseProductOverviewBase();
  const fresh=evoCapacityFresh(),c=fresh?(evo().capacity||{}):{state:'UNAVAILABLE'},p=c.remainingPercent;
  const plans=[...(orch().todayTomorrowDemands||[])].sort((a,b)=>Date.parse(a.expectedStart)-Date.parse(b.expectedStart));
  const rec=plans.map(d=>orch().currentRecommendations?.find(r=>r.demandId===d.id)).find(Boolean);
  const titles={AVAILABLE:L('额度可用','Capacity available'),LOW:L('额度偏低','Capacity is low'),EXHAUSTED:L('窗口已耗尽','Window exhausted'),UNAVAILABLE:L('等待新读数','Waiting for a fresh reading')};
  const width=Math.max(0,Math.min(100,Number(p)||0));
  const schedule=plans.length?`<section class="phase-overview-agenda"><div class="phase-overview-section-head"><h2>${L('今天与明天','Today and tomorrow')}</h2><a href="#planner">${L('查看计划','View plans')}${phaseIcon('arrow')}</a></div><div class="phase-agenda-list">${plans.slice(0,3).map(d=>`<article class="phase-agenda-row"><time>${esc(phaseTime(d.expectedStart))}<span>–</span>${esc(phaseTime(d.expectedEnd))}</time><div><strong>${esc(d.title)}</strong><small>${esc(phaseTime(d.expectedStart,'date'))}</small></div><span class="phase-agenda-intensity">${esc({HEAVY:L('重度','Heavy'),MEDIUM:L('中等','Medium'),LIGHT:L('轻度','Light')}[d.intensity]||'')}</span></article>`).join('')}</div></section>`:'';
  return phaseHead('','')+`<section class="phase-overview-top"><div class="phase-overview-readout phase-capacity-well"><div class="phase-card-meta"><span>${L('五小时窗口','5-hour window')}</span>${phaseBadge(fresh?L('实时读数','Live reading'):L('等待更新','Awaiting update'),fresh?'sage':'amber')}</div><div class="phase-capacity-primary"><strong data-capacity-number>${p??'—'}<small>${p==null?'':'%'}</small></strong><span>${L('剩余额度','remaining')}</span></div><div class="phase-capacity-bar" role="progressbar" aria-label="${L('五小时剩余额度','5-hour quota remaining')}" aria-valuemin="0" aria-valuemax="100" ${p==null?'aria-valuetext="'+L('等待新读数','Waiting for a fresh reading')+'"':'aria-valuenow="'+Number(p)+'"'}><i class="phase-capacity-fill" style="width:${width}%"></i></div><div class="phase-overview-state"><h2>${titles[c.state]||titles.UNAVAILABLE}</h2></div><details class="phase-capacity-detail"><summary>${L('读数说明','About this reading')}</summary><p>${L('来自官方快照。五小时与周额度分别计算；不代表可用小时数或任务保证。','From the official snapshot. Five-hour and weekly budgets are separate; this is not a promise of hours or completed tasks.')}</p></details></div><div class="phase-overview-next">${productRecCard(rec,true)}</div></section><section class="phase-overview-ledger phase-secondary-ledger" aria-label="${L('额度摘要','Quota summary')}"><article><span>${L('周额度','Weekly')}</span><strong>${c.weeklyPercent==null?'—':c.weeklyPercent+'%'}</strong></article><article><span>${L('官方重置读数','Reported reset')}</span><strong data-reset="${esc(c.resetAt||'')}">—</strong><small>${c.resetAt?esc(phaseTime(c.resetAt)):L('等待快照','Waiting for a snapshot')}</small></article><article><span>${L('已安排','Planned blocks')}</span><strong>${plans.length}</strong></article></section>${schedule}<p class="phase-overview-footnote">${L('只读额度 · 计划留在本机 · 任务由你决定','Read-only quota · Local plans · You stay in control')}</p>`;
};

productOverview=()=>{
  const fresh=evoCapacityFresh()&&latest?.status!=='UNAVAILABLE',c=fresh?(evo().capacity||{}):{state:'UNAVAILABLE'},windows=productWindows();
  const plusPlan=productIsPlus(),featured=fresh?(plusPlan?windows.fiveHour:windows.primary):null,p=featured?.remaining_percent,duration=featured?.window_duration_minutes;
  const hasPercent=typeof p==='number'&&Number.isFinite(p),missingPlusFiveHour=fresh&&plusPlan&&!windows.fiveHour,width=Math.max(0,Math.min(100,Number(p)||0));
  const heading=plusPlan?L('Plus · 五小时额度','Plus · 5-hour quota'):featured?productWindowHeading(duration):L('账户额度','Account quota');
  const plans=[...(orch().todayTomorrowDemands||[])].sort((a,b)=>Date.parse(a.expectedStart)-Date.parse(b.expectedStart));
  const rec=plans.map(d=>orch().currentRecommendations?.find(r=>r.demandId===d.id)).find(Boolean);
  const titles={AVAILABLE:L('额度可用','Quota available'),LOW:L('剩余额度偏低','Running low'),EXHAUSTED:L('当前额度窗口已用完','This quota window is used up'),UNAVAILABLE:L('等待额度更新','Waiting for a quota update')};
  const schedule=plans.length?`<section class="phase-overview-agenda"><div class="phase-overview-section-head"><h2>${L('今天与明天','Today and tomorrow')}</h2><a href="#planner">${L('查看计划','View plans')}${phaseIcon('arrow')}</a></div><div class="phase-agenda-list">${plans.slice(0,3).map(d=>`<article class="phase-agenda-row"><time>${esc(phaseTime(d.expectedStart))}<span>–</span>${esc(phaseTime(d.expectedEnd))}</time><div><strong>${esc(d.title)}</strong><small>${esc(phaseTime(d.expectedStart,'date'))}</small></div><span class="phase-agenda-intensity">${esc({HEAVY:L('重度','Heavy'),MEDIUM:L('中等','Medium'),LIGHT:L('轻度','Light')}[d.intensity]||'')}</span></article>`).join('')}</div></section>`:'';
  const unavailableTitle=!fresh?(latest?.status==='UNAVAILABLE'||!navigator.onLine?L('暂时读不到额度','Quota temporarily unavailable'):L('额度读数已过期','Quota reading is out of date')):missingPlusFiveHour?L('本次快照没有返回五小时额度','The 5-hour quota was not returned in this snapshot'):featured?L('该窗口没有返回剩余百分比','This window did not report a remaining percentage'):L('本次快照没有返回额度窗口','No quota window was returned in this snapshot');
  const unavailableCopy=!fresh?(latest?.status==='UNAVAILABLE'||!navigator.onLine?L('请检查网络或 Codex 连接后重试；旧读数不会用于当前建议。','Check your connection to Codex and try again. Older readings are not used for current advice.'):L('为避免误导，旧额度不会用于当前规划。请重新读取后再安排。','Older readings are hidden to keep planning reliable. Refresh before making a plan.')):missingPlusFiveHour?L('Plus 五小时读数未出现在当前账户快照中。下方其他额度窗口会单独列出，不会替代五小时额度。请重新读取，或检查 Codex 登录状态。','The Plus 5-hour reading is missing from this account snapshot. Other quota windows are listed separately below and do not replace it. Refresh or check your Codex sign-in.'):featured?L('窗口已返回，但没有可显示的剩余百分比。重新读取后再参考这项数据。','The window was returned without a remaining percentage. Refresh before relying on it.'):L('请稍后重试，或检查 Codex 是否已登录。','Try again shortly, or check that Codex is signed in.');
  const readout=featured&&hasPercent?`<div class="phase-capacity-primary"><strong data-capacity-number>${p}<small>%</small></strong><span>${L('剩余额度','remaining')}</span></div><div class="phase-capacity-bar" role="progressbar" aria-label="${esc(heading)} · ${L('剩余额度','remaining')}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"><i class="phase-capacity-fill" style="width:${width}%"></i></div><div class="phase-overview-state"><h2>${titles[c.state]||titles.UNAVAILABLE}</h2></div>`:`<div class="phase-capacity-unavailable" role="status"><strong>${unavailableTitle}</strong><p>${unavailableCopy}</p>${!navigator.onLine?`<span class="phase-connection-hint">${L('网络恢复后可重新读取。','Reconnect to refresh this reading.')}</span>`:`<button type="button" class="button secondary small" data-action="refresh">${L('重新读取','Refresh')}</button>`}</div>`;
  const weekly=fresh?windows.weekly:null;
  const secondaryWindows=(fresh?windows.all:[]).filter(w=>w!==featured&&w!==weekly);
  const secondaryDisclosure=secondaryWindows.length?`<details class="phase-extra-windows" ${missingPlusFiveHour?'open':''}><summary>${missingPlusFiveHour?L('其他额度窗口 · 不替代五小时额度','Other quota windows · not a replacement for 5-hour quota'):L('其他额度窗口','Other quota windows')} <span>${secondaryWindows.length}</span></summary><div class="phase-extra-window-list">${secondaryWindows.map(w=>`<article data-window-duration="${w.window_duration_minutes}"><span>${esc(productWindowLabel(w.window_duration_minutes))}</span><strong>${typeof w.remaining_percent==='number'?`${w.remaining_percent}%`:'—'}</strong><small>${w.resets_at?`${L('重置','Resets')} ${esc(phaseTime(w.resets_at))}`:L('暂无重置读数','Reset not reported')}</small>${missingPlusFiveHour?`<small>${L('独立额度窗口，不替代 Plus 五小时额度。','Separate quota window; it does not replace the Plus 5-hour quota.')}</small>`:''}</article>`).join('')}</div></details>`:'';
  const weeklyMetric=`<article><span>${L('周额度','Weekly quota')}</span><strong>${!fresh||!weekly||typeof weekly.remaining_percent!=='number'?'—':`${weekly.remaining_percent}%`}</strong><small>${!fresh?L('等待新读数', 'Waiting for a fresh reading'):weekly?L('独立额度窗口','Separate quota window'):L('当前快照未提供','Not reported in this snapshot')}</small></article>`;
  const resetAt=featured?.resets_at;
  const resetMetric=`<article class="phase-reported-reset"><span>${L('下次重置','Next reset')}</span><strong ${resetAt?`data-reset="${esc(resetAt)}"`:''}>${resetAt?'—':L('暂无读数','Not available')}</strong></article>`;
  const snapshotMeta=fresh?`${L('账户快照 · 读取于','Account snapshot · Read at')} ${esc(dt(latest?.last_snapshot))}`:latest?.last_snapshot?`${L('上次读取','Last read')} ${esc(dt(latest.last_snapshot))} · ${L('当前数据需更新','Refresh needed')}`:L('尚无可用快照','No available snapshot');
  const freshnessBadge=fresh?L('刚刚读取','Recently read'):latest?.status==='UNAVAILABLE'?L('读取失败','Read failed'):L('需要更新','Refresh needed');
  return phaseHead('','')+`<section class="phase-overview-top"><div class="phase-overview-readout phase-capacity-well"><div class="phase-card-meta"><span>${esc(heading)}</span><span class="phase-plan-chip">${esc(productPlanName())}</span></div><div class="phase-snapshot-meta" data-fresh="${fresh?'true':'false'}"><i aria-hidden="true"></i><span>${freshnessBadge} · ${snapshotMeta}</span></div>${readout}<details class="phase-capacity-detail"><summary>${L('读数说明','About this reading')}</summary><p>${L('这张卡只显示其标题所指的额度窗口；数据来自当前账户快照。不同套餐或模型会返回不同窗口，其他窗口不会代替本项读数。额度不代表可用小时数或工作结果保证。','This card only shows the quota window named in its heading, from the current account snapshot. Plans and models may return different windows; another window never substitutes for this reading. Quota is not a guarantee of work time or outcomes.')}</p></details></div><div class="phase-overview-next">${productRecCard(rec,true)}</div></section><section class="phase-overview-ledger phase-secondary-ledger" aria-label="${L('额度摘要','Quota summary')}">${weeklyMetric}${resetMetric}<article><span>${L('已安排工作','Planned blocks')}</span><strong>${plans.length}</strong></article></section>${secondaryDisclosure}${schedule}`;
};

window.addEventListener('click',async event=>{
  const button=event.target.closest?.('[data-action="refresh"]');
  if(!button)return;
  event.preventDefault();event.stopImmediatePropagation();
  if(button.dataset.r13Busy)return;
  const label=button.textContent;
  button.dataset.r13Busy='true';button.disabled=true;button.setAttribute('aria-busy','true');button.textContent=L('读取中…','Refreshing…');
  try{await refresh()}finally{if(button.isConnected){button.disabled=false;button.removeAttribute('aria-busy');delete button.dataset.r13Busy;button.textContent=label}}
},true);

document.addEventListener('click',event=>{
  const link=event.target.closest?.('[data-phase-replan]');if(!link||current!=='planner')return;
  event.preventDefault();const form=$('#product-plan-form');form?.scrollIntoView({behavior:'smooth',block:'start'});form?.querySelector('input,select')?.focus({preventScroll:true});
});
document.addEventListener('input',event=>{
  const input=event.target;if(!input.matches?.('#product-plan-form [name="start"], #product-plan-form [name="end"]'))return;
  const form=input.form,start=form?.elements.start?.value,end=form?.elements.end?.value,hint=form?.querySelector('[data-phase-midnight-hint]');if(!hint)return;
  const nextDay=!!start&&!!end&&end<=start;hint.hidden=!nextDay;hint.textContent=nextDay?L('结束时间为次日','Ends the next day.'):'';
});

const phaseUsagePageBase=usagePage;
usagePage=()=>{
  const u=evo().usage||{},days=(u.daily||[]).filter(d=>d&&d.date&&Number.isFinite(Number(d.observedQuotaDrop))).slice(-30),hours=u.hours||[];
  const windowLabel=productWindowLabel(Number(u.quotaWindowDurationMinutes));
  const latestObservedDay=days.at(-1)?.date;
  const started=(orch().demands||[]).filter(d=>Date.parse(d.expectedStart)<=Date.now()).slice(0,100);
  const valueText=value=>`${Number(value).toFixed(Number.isInteger(Number(value))?0:1)} ${L('个百分点','percentage points')}`;
  const dayRows=rows=>rows.map(d=>`<div class="phase-observation-row"><time>${esc(d.date)}</time><strong>${valueText(d.observedQuotaDrop)}</strong></div>`).join('');
  let trend;
  if(days.length<4){
    trend=days.length?`<div class="phase-small-sample"><div><strong>${days.length}</strong><span>${L('个观测日','observed days')}</span></div><div class="phase-observation-list">${dayRows(days)}</div></div>`:`<div class="phase-no-observations">${L('还没有连续的真实额度观测。连接保持只读即可开始积累。','No continuous live quota observations yet. Keep the read-only connection active to begin collecting them.')}</div>`;
  }else{
    const values=days.map(d=>Number(d.observedQuotaDrop)),min=Math.min(0,...values),max=Math.max(1,...values),range=Math.max(1,max-min),w=720,h=220,left=48,right=18,top=18,bottom=34;
    const pts=values.map((v,i)=>({x:left+(w-left-right)*(i/Math.max(1,values.length-1)),y:top+(h-top-bottom)*(1-(v-min)/range),v,d:days[i]}));
    const polyline=pts.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),last=pts.at(-1),peak=pts.reduce((a,b)=>b.v>a.v?b:a,pts[0]);
    const labelIndexes=[...new Set([0,Math.floor((pts.length-1)/2),pts.length-1])];
    trend=`<figure class="phase-trend-figure"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${L(`${days.length} 个观测日，最新 ${valueText(last.v)}，最高 ${valueText(peak.v)}`,`${days.length} observed days; latest ${valueText(last.v)}, highest ${valueText(peak.v)}`)}"><line class="phase-trend-axis" x1="${left}" y1="${h-bottom}" x2="${w-right}" y2="${h-bottom}"/><line class="phase-trend-grid" x1="${left}" y1="${top}" x2="${w-right}" y2="${top}"/><polyline class="phase-trend-line" points="${polyline}"/>${pts.map(p=>`<circle class="phase-trend-point" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3"><title>${esc(p.d.date)} · ${valueText(p.v)}</title></circle>`).join('')}${labelIndexes.map(i=>`<text class="phase-trend-date" x="${pts[i].x.toFixed(1)}" y="${h-8}" text-anchor="${i===0?'start':i===pts.length-1?'end':'middle'}">${esc(pts[i].d.date.slice(5))}</text>`).join('')}</svg><figcaption>${L(`${days.length} 个观测日 · 最新 ${valueText(last.v)} · 最高 ${valueText(peak.v)}`,`${days.length} observed days · latest ${valueText(last.v)} · highest ${valueText(peak.v)}`)}</figcaption><details class="phase-chart-values"><summary>${L('查看具体读数','View readings')}</summary><div class="phase-observation-list">${dayRows(days.slice(-7))}</div></details></figure>`;
  }
  const activeHours=hours.filter(h=>Number(h.observedQuotaDrop||0)!==0||Number(h.reportedTasks||0)>0);
  const hourDetails=activeHours.length?`<div class="phase-hour-readings">${activeHours.map(h=>`<div><time>${String(h.hour).padStart(2,'0')}:00</time><strong>${valueText(h.observedQuotaDrop||0)}</strong><span>${Number(h.reportedTasks||0)} ${L('条自报结果','reports')}</span></div>`).join('')}</div>`:`<p class="phase-no-observations">${L('暂无时段读数或自报结果。','No hourly readings or self-reported outcomes yet.')}</p>`;
  const form=started.length?`<form id="evo-outcome-form"><label class="field">${L('工作计划','Work plan')}<select name="demandId">${started.map(d=>`<option value="${esc(d.id)}">${esc(d.title)} · ${esc(dt(d.expectedStart))}</option>`).join('')}</select></label><div class="evo-form-row"><label class="field">${L('结果','Outcome')}<select name="status"><option value="COMPLETED">${L('完成','Completed')}</option><option value="PARTIAL">${L('部分完成','Partial')}</option><option value="ABANDONED">${L('未完成','Not completed')}</option></select></label><label class="field">${L('实际用时（分钟）','Actual minutes')}<input name="actualMinutes" type="number" min="0" max="10080" value="30" required></label></div><button class="button">${L('保存结果','Save result')}</button></form>`:`<p class="phase-no-observations">${L('没有已开始的工作计划可记录。','There are no started plans to record yet.')}</p>`;
  const completion=u.highIntensityCompletionPercent==null?L('样本不足','Not enough data'):`${u.highIntensityCompletionPercent}%`,best=u.bestObservedHour==null?L('样本不足','Not enough data'):`${String(u.bestObservedHour).padStart(2,'0')}:00`;
  const pointNote=L(`${days.length>0&&days.length<4?'当前不足 4 个观测日，因此只列实测摘要，不展示趋势。':''}这里只汇总账户快照中「${windowLabel}」的真实百分比变化。百分点表示百分比之间的差值（例如 80% 到 70% 是下降 10 个百分点），不代表可用分钟或工作效率。`,`${days.length>0&&days.length<4?'Fewer than 4 observed days, so readings are shown without a trend. ':''}This summarizes actual percentage changes in the “${windowLabel}” window returned by account snapshots. Percentage points express the difference between percentages (for example, 80% to 70% is a 10-point drop), not available minutes or productivity.`);
  const observationScope=latestObservedDay?`${L('最近观测','Latest observation')} · ${esc(latestObservedDay)}`:L('尚无真实额度观测','No live quota observations yet');
  return phaseHead(L('使用洞察','Usage insights'),L('额度读数与自报结果分开呈现。','Quota readings and self-reported outcomes are kept separate.'))+`<section class="phase-insight-stats" aria-label="${L('使用摘要','Usage summary')}"><article><span>${L('已记录结果','Outcomes')}</span><strong>${u.outcomeCount||0}</strong></article><article><span>${L('重度工作完成','Intensive work')}</span><strong data-empty="${u.highIntensityCompletionPercent==null}">${esc(completion)}</strong></article><article><span>${L('常见时段','Observed hour')}</span><strong data-empty="${u.bestObservedHour==null}">${esc(best)}</strong></article></section><section class="quiet-panel phase-usage-panel"><div class="phase-overview-section-head"><div><h2>${L('额度变化 · 百分点','Quota change · percentage points')}</h2><p class="phase-reading-scope">${L(`窗口：${windowLabel} · 最近30天`,`Window: ${windowLabel} · last 30 days`)}</p></div><span class="phase-reading-latest">${observationScope}</span></div>${trend}<details class="phase-reading-definition"><summary>${L('如何理解这些读数', 'How to read these figures')}</summary><p class="phase-data-definition">${pointNote}</p></details></section><details class="phase-usage-detail"><summary><span>${L('按时段查看','Hourly readings')}</span><span class="phase-detail-caret">${phaseIcon('arrow')}</span></summary><div class="phase-usage-detail-content">${hourDetails}</div></details><details class="phase-usage-detail"><summary><span>${L('记录工作结果','Record a work outcome')}</span><span class="phase-detail-caret">${phaseIcon('arrow')}</span></summary><div class="phase-usage-detail-content"><p>${L('仅记录已经开始的计划。结果由你自报并保存在本机。','Record a plan after it starts. Your report stays on this device.')}${u.medianPlannedToActualRatio==null?'':` · ${L('历史用时比','Historical time ratio')}: ${u.medianPlannedToActualRatio}`}</p>${form}</div></details>`;
};
const phaseRefineDOMR5=phaseRefineDOM;
phaseRefineDOM=()=>{phaseRefineDOMR5();const version=$('.version');if(version)version.textContent='v0.14.2 · r5.1';};
const phaseStripR5=lifecycleStripUnsafe;
lifecycleStripUnsafe=()=>{phaseStripR5();if(!evoCapacityFresh())$('.phase-capacity-fill')?.setAttribute('style','width:0%');};
