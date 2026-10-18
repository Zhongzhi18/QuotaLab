// v0.14.3 r12: natural bilingual copy and restrained motion polish.
// Historical research evidence and backend lifecycle rules remain untouched.
const artDirectionMark = '<svg data-art-direction-mark viewBox="0 0 48 48" role="img" aria-label="QuotaLab Phase"><rect width="48" height="48" rx="13" fill="#16272c"/><path d="M34.2 14.2a15 15 0 1 0 .1 19.6" fill="none" stroke="#cbe9b6" stroke-width="3.7" stroke-linecap="round"/><path d="M24 24 33.4 14.6" fill="none" stroke="#cbe9b6" stroke-width="2.8" stroke-linecap="round"/><circle cx="24" cy="24" r="2.3" fill="#f3f2ec"/><circle cx="34" cy="14" r="2.6" fill="#efc68b"/><path d="M24 4v3M24 41v3M4 24h3" stroke="#82978e" stroke-width="1.3" stroke-linecap="round"/></svg>';

// NO_LONGER_NEEDED means no official scheduling handoff is needed, not that the
// work plan or its ordinary advice has expired. Freshness checks still apply.
const artSafeLifeStates = new Set(['FRESH', 'UPDATED', 'NO_LONGER_NEEDED']);
let artRouteMotionTimer = 0;

function countdown(value) {
  const raw = String(value ?? '').trim();
  const target = typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(raw)
    ? Number(value) : Date.parse(raw) / 1000;
  if (!Number.isFinite(target) || target <= 0) return '—';
  const seconds = Math.floor(target - Date.now() / 1000);
  if (seconds <= 0) return '—';
  if (seconds < 60) return locale === 'zh' ? '不到 1 分钟' : 'under 1 min';
  const minutes = Math.floor(seconds / 60);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor(minutes % 1440 / 60);
  const remainder = minutes % 60;
  if (locale === 'zh') return [days && `${days}天`, hours && `${hours}小时`, remainder && `${remainder}分钟`].filter(Boolean).join(' ');
  return [days && `${days}d`, hours && `${hours}h`, remainder && `${remainder}m`].filter(Boolean).join(' ');
}

function artTimezoneLabel(zone) {
  try {
    return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-US', {
      timeZone: zone, timeZoneName: 'longGeneric'
    }).formatToParts(new Date()).find(part => part.type === 'timeZoneName')?.value || zone;
  } catch { return zone; }
}

function artLocalDate(value) {
  if (value === null || value === undefined || value === '') return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '—';
  try {
    return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-GB', {
      timeZone: lifecycleZone(), dateStyle: 'medium', timeStyle: 'short'
    }).format(date);
  } catch { return '—'; }
}

function artAdviceSafety(recommendation) {
  const life = recommendation?.recommendationLifecycle || {};
  return artSafeLifeStates.has(life.state) && Date.parse(life.validUntil) > Date.now() &&
    evoCapacityFresh() && latest?.status !== 'UNAVAILABLE';
}

productRecCard = (recommendation, actions = true) => {
  if (!recommendation) {
    const hasPlan = (orch().todayTomorrowDemands || []).length > 0;
    return `<section class="quiet-panel phase-action-card phase-no-plan r12-invite"><span class="r12-invite-mark" aria-hidden="true"><svg viewBox="0 0 48 48" fill="none"><circle class="r12-dial-track" cx="24" cy="24" r="18"/><circle class="r12-dial-arc" cx="24" cy="24" r="18" pathLength="100" stroke-dasharray="72 28" transform="rotate(-92 24 24)"/><path class="r12-dial-hand" d="M24 24V14m0 10 8 5"/><circle class="r12-dial-pin" cx="24" cy="24" r="2.5"/></svg></span><div class="r12-invite-copy"><span class="phase-no-plan-label">${L('下一步', 'Up next')}</span><h2>${hasPlan ? L('安排已保存，建议尚未就绪', 'Plan saved; advice is not ready yet') : L('先选一段工作时间', 'Choose a time to work')}</h2><p>${hasPlan ? L('额度或安排更新后，再回来查看建议。', 'Check back after quota or schedule details update.') : L('安排好时段，我们会对照账户当前的额度读数。', 'We’ll check your work period against the quota your account reports.')}</p></div><a href="#planner" class="button">${hasPlan ? L('查看安排', 'View plan') : L('安排工作', 'Plan a work block')}${phaseIcon('arrow')}</a></section>`;
  }
  if (!artAdviceSafety(recommendation)) {
    const life = recommendation.recommendationLifecycle || {};
    const text = L('额度或安排已变化，旧建议暂不可用。重新读取额度后再规划。', 'Quota or plans have changed. Refresh quota before planning again.');
    return `<section class="quiet-panel phase-action-card phase-advice-unavailable" role="status"><span class="phase-no-plan-label">${L('建议状态', 'ADVICE')}</span><h2>${L('建议需要更新', 'Advice needs an update')}</h2><p>${text}</p><a class="button secondary phase-replan" href="#planner">${L('重新规划', 'Plan again')}${phaseIcon('arrow')}</a></section>`;
  }
  return phaseCompactRecommendationBase(recommendation, actions);
};

let artSelectedDemandId = '';
try { artSelectedDemandId = sessionStorage.getItem('quotalab_selected_plan') || ''; } catch {}
function selectedPlannerDemand() {
  const upcoming = (orch().demands || []).filter(d => Date.parse(d.expectedEnd) > Date.now());
  return upcoming.find(d => d.id === artSelectedDemandId) ||
    upcoming.sort((a, b) => Date.parse(a.expectedStart) - Date.parse(b.expectedStart))[0] || null;
}
function selectPlannerDemand(id, repaint = true) {
  artSelectedDemandId = String(id || '');
  try { sessionStorage.setItem('quotalab_selected_plan', artSelectedDemandId); } catch {}
  if (repaint) { captureInteraction(); lastPage = ''; render(); }
}
document.addEventListener('click', event => {
  const button = event.target.closest('[data-review-plan]');
  if (!button) return;
  selectPlannerDemand(button.dataset.reviewPlan);
  document.querySelectorAll('[data-review-plan]').forEach(item => {
    if (item.dataset.reviewPlan === artSelectedDemandId) item.focus({ preventScroll: true });
  });
});

function artPlanPage() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const dateValue = localIso(tomorrow).slice(0, 10);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
  const selectedDemand = selectedPlannerDemand();
  const upcoming = (orch().demands || []).filter(d => Date.parse(d.expectedEnd) >= Date.now())
    .sort((a, b) => Date.parse(a.expectedStart) - Date.parse(b.expectedStart)).slice(0, 4);
  if (selectedDemand && !upcoming.some(d => d.id === selectedDemand.id)) upcoming.push(selectedDemand);
  const plans = upcoming.length ? `<section class="r11-upcoming"><div class="r11-section-heading"><h2>${L('已保存的安排', 'Saved plans')}</h2><a href="#history">${L('全部记录', 'History')}${phaseIcon('arrow')}</a></div><div class="r11-plan-list">${upcoming.map(d => {
    const r = (orch().currentRecommendations || []).find(item => item.demandId === d.id);
    const life = r?.recommendationLifecycle || {};
    const state = life.state === 'RECOMPUTING' ? L('正在更新', 'Updating') :
      life.state === 'FAILED' || life.state === 'STALE' || life.state === 'EXPIRED' || life.state === 'SUPERSEDED' ? L('需重新核对', 'Check again') :
      life.state === 'NO_LONGER_NEEDED' ? L('查看工作建议', 'Review work advice') : L('已保存', 'Saved');
    const conflict = !!life.allocation?.blocked;
    const selected = selectedDemand?.id === d.id;
    return `<article class="r11-plan-row" data-selected="${selected}"><span class="r11-plan-mark" aria-hidden="true">${phaseIcon('planner')}</span><div><strong>${esc(d.title)}</strong><small>${esc(phaseTime(d.expectedStart, 'date'))} · ${esc(phaseTime(d.expectedStart))}–${esc(phaseTime(d.expectedEnd))}</small></div><button type="button" class="r11-plan-state ql-plan-select" data-review-plan="${esc(d.id)}" aria-pressed="${selected}" aria-label="${esc(L('查看计划建议：', 'View advice for: ') + d.title)}" data-tone="${conflict ? 'warning' : ''}">${selected ? L('正在查看', 'Viewing') : L('查看建议', 'View advice')}${conflict ? ' · ' + L('时段冲突', 'Conflict') : ''}</button></article>`;
  }).join('')}</div></section>` : '';
  // Selection is demand-based. Missing/refreshing advice must never fall back
  // to another plan's card, reminder or task instructions.
  const advice = selectedDemand ? (orch().currentRecommendations || []).find(r => r.demandId === selectedDemand.id && r.product) || null : null;
  return phaseHead(L('安排重要工作', 'Plan important work'), productPlanningNote()) +
    `<div class="r11-planner-grid"><section class="quiet-panel r11-plan-form-panel"><form id="product-plan-form" novalidate><div class="r11-form-heading"><h2>${L('使用时段', 'Work period')}</h2></div>
      <label class="field">${L('计划名称', 'Plan name')}<input name="title" maxlength="80" value="${esc(L('工作计划', 'Work plan'))}" required autocomplete="off"></label>
      <label class="field">${L('日期', 'Date')}<input name="date" type="date" value="${dateValue}" required></label>
      <div class="form-grid r11-time-fields"><label class="field">${L('开始', 'Start')}<input name="start" type="time" value="09:00" required></label><label class="field">${L('结束', 'End')}<input name="end" type="time" value="12:00" required></label></div>
      <p class="r11-timezone" title="${esc(timezone)}">${L('本机时间', 'Local time')} · ${esc(artTimezoneLabel(timezone))} <span data-phase-midnight-hint hidden>${L('结束时间为次日', 'Ends the next day')}</span></p>
      <label class="field">${L('工作强度', 'Work intensity')}<select name="intensity"><option value="HEAVY">${L('重度', 'Intensive')}</option><option value="MEDIUM" selected>${L('中等', 'Moderate')}</option><option value="LIGHT">${L('轻度', 'Light')}</option></select></label>
      <details class="r11-optional"><summary>${L('更多选项', 'More options')}</summary><div class="r11-optional-grid"><label class="field">${L('优先级', 'Priority')}<select name="priority"><option value="HIGH">${L('高', 'High')}</option><option value="MEDIUM" selected>${L('普通', 'Normal')}</option><option value="LOW">${L('低', 'Low')}</option></select></label><label class="field">${L('复核时机', 'Review timing')}<select name="targetBoundaryPolicy"><option value="EARLY_REFRESH">${L('开工附近', 'Near the start')}</option><option value="BALANCED" selected>${L('时段中间', 'Midway')}</option><option value="LATE_REFRESH">${L('结束附近', 'Near the end')}</option><option value="CUSTOM">${L('自定义', 'Custom')}</option></select></label><label class="field">${L('自定义分钟数', 'Custom minutes')}<input name="boundaryMinutes" type="number" value="30" min="0" max="10080"></label></div></details>
      <button type="submit" class="button r11-submit">${L('保存安排', 'Save plan')}${phaseIcon('arrow')}</button><p class="r11-form-note">${L('计划保存在本机。额度建议只依据账户实际返回的数据；不会启动 Codex 或发送任务。', 'Plans stay on this device. Advice uses only quota data your account reports; Codex will not be launched and no task is sent.')}</p><p id="product-feedback" role="status" aria-live="polite"></p></form></section>
      <aside class="r11-plan-aside"><section class="r11-advice-wrap"><div class="r11-section-heading"><h2>${L('额度建议', 'Quota advice')}</h2>${advice && artAdviceSafety(advice) ? `<span class="r11-fresh-dot">${L('已核验', 'Current')}</span>` : ''}</div>${advice ? productRecCard(advice, true) : `<div class="r11-advice-empty"><h3>${L('保存安排后，在这里查看建议', 'Save a plan to see advice here')}</h3><p>${L('填写左侧时段并保存。我们会结合当前额度核对；没有额度读数时，仍可保留时间安排。', 'Save a work period on the left. We will check it against current quota; you can keep a plan even without a reading.')}</p></div>`}</section>${plans}</aside></div>`;
}

productPlan = artPlanPage;

productSignals = () => {
  const o = orch(), signals = (o.signals || []).filter(s => s.classification !== 'UNRELATED');
  const cards = signals.length ? signals.map(s => `<article class="r11-signal-row"><div class="r11-signal-top"><strong>${esc(s.sourceAuthor || L('公开消息', 'Public update'))}</strong><span class="r11-plan-state" data-tone="${s.expired ? 'warning' : ''}">${s.expired ? L('已过期', 'Expired') : L('公开信息', 'Public information')}</span></div><p>${esc(s.rawText)}</p><div class="r11-signal-meta">${esc(dt(s.publishedAt))} · ${esc(s.signalType || L('状态更新', 'Status update'))}</div><details><summary>${L('查看来源', 'View source')}</summary><div class="r11-source-details"><span>${L('来源等级', 'Source tier')}: ${esc(s.sourceTier ?? '—')}</span><a href="${esc(s.sourceUrl)}" target="_blank" rel="noopener noreferrer">${L('打开原始来源', 'Open source')}</a></div></details></article>`).join('') : `<div class="r11-empty r11-signal-empty"><h2>${L('目前没有公开更新', 'No public updates yet')}</h2><p>${L('公开公告不会证明个人额度已恢复。', 'Public announcements do not prove your quota has replenished.')}</p></div>`;
  const actions = `<div class="r11-signal-actions"><button class="button secondary" data-orch-action="signals-fetch" ${o.preferences?.publicSignalFetchEnabled ? '' : 'disabled'}>${L('检查公开更新', 'Check for updates')}</button>${o.preferences?.publicSignalFetchEnabled ? '' : `<a class="subtle-link" href="#settings">${L('在设置中启用', 'Enable in Settings')}</a>`}</div>`;
  const signalContent = signals.length
    ? `<section class="quiet-panel r11-signal-controls"><p>${L('目前没有足够可靠的样本，暂不提供恢复概率。', 'There is not enough reliable evidence to estimate a recovery probability.')}</p>${actions}</section><div class="r11-signal-list">${cards}</div>`
    : `<section class="r11-signal-empty" role="status"><div><div class="r11-signal-tags"><span>${L('个人样本不足', 'Personal sample is limited')}</span><span>${o.preferences?.publicSignalFetchEnabled ? L('尚无已保存的公开动态', 'No saved public updates') : L('公开信息读取未启用', 'Public status checks are off')}</span></div><h2>${o.preferences?.publicSignalFetchEnabled ? L('还没有可参考的公开动态', 'No public updates to review yet') : L('按需查看公开服务动态', 'Check public service updates when needed')}</h2><p>${o.preferences?.publicSignalFetchEnabled ? L('点击“检查公开更新”读取服务状态。公开公告仅供参考，个人额度请以概览页读数为准。', 'Choose “Check for updates” to read service status. Use the Overview reading to check your own quota.') : L('在设置中启用后，可手动读取公开服务状态。个人额度仍以概览页读数为准。', 'Enable public status checks in Settings to read service updates manually. Your own quota is shown in Overview.')}</p></div>${actions}</section>`;
  return phaseHead(L('额度动态', 'Quota updates'), L('公开信息仅作参考，不代表个人额度已恢复。', 'Public information is a reference, not proof of personal quota recovery.')) +
    `${signalContent}<details class="r11-import"><summary>${L('导入公开片段', 'Add a public excerpt')}</summary><form id="signal-form"><label class="field">${L('来源 URL', 'Source URL')}<input name="sourceUrl" type="url" required></label><label class="field">${L('原公告 URL（可选）', 'Original URL (optional)')}<input name="originUrl" type="url"></label><label class="field">${L('作者', 'Author')}<input name="sourceAuthor" required></label><label class="field">${L('发布时间', 'Published at')}<input name="publishedAt" type="datetime-local" required></label><label class="field">${L('公开文本', 'Public text')}<textarea name="rawText" required maxlength="2200"></textarea></label><button type="submit" class="button secondary">${L('保存片段', 'Save excerpt')}</button></form></details>`;
};

productSettings = () => {
  const preferences = orch().preferences || {}, storage = evo().storage || {};
  const fresh = evoCapacityFresh() && latest?.status !== 'UNAVAILABLE';
  const readState = !navigator.onLine ? L('网络暂时不可用', 'Offline') :
    latest?.status === 'UNAVAILABLE' ? L('暂时读不到额度', 'Quota unavailable') :
    fresh ? L('额度读数已更新', 'Quota reading updated') :
    latest?.last_snapshot ? L('额度读数需要更新', 'Quota reading needs a refresh') : L('尚未读取额度', 'No quota reading yet');
  const readTime = latest?.last_snapshot ? `${L('上次读取', 'Last read')} · ${esc(artLocalDate(latest.last_snapshot))}` : L('连接 Codex 后即可读取账户实际返回的额度窗口。', 'Connect to Codex to read the quota windows reported by your account.');
  const byteRows = Object.entries(storage.bytes || {}).filter(([, value]) => Number(value) > 0);
  const storageRows = byteRows.length
    ? byteRows.map(([key, value]) => `<div><span>${esc(({data:L('计划与设置','Plans and preferences'),logs:L('运行记录','Activity records'),cache:L('缓存','Cache'),temporary:L('临时文件','Temporary files'),downloads:L('下载','Downloads'),backups:L('备份','Backups'),recovery:L('可恢复项目','Recoverable items')})[key] || L('本机数据','Local data'))}</span><strong>${esc(bytes(value))}</strong></div>`).join('')
    : `<p>${L('本机数据目录已就绪。', 'Local storage is ready.')}</p>`;
  const researchMode = productResearchOn() ? `<section class="r15-advanced-block"><h3>${L('研究工具', 'Research tools')}</h3><p>${L('研究功能已开启。关闭后会收起实验与诊断页面，不会影响日常计划。', 'Research tools are on. Turning them off hides experiments and diagnostics without changing everyday plans.')}</p><label class="switch-row"><input id="product-research-mode" type="checkbox" role="switch" checked><span class="switch-copy"><strong>${L('显示研究工具', 'Show research tools')}</strong></span></label>${productResearchSettings()}</section>` : '';
  return pageHead(t('settings'), L('额度和安排都由你掌控；偏好与计划保存在本机。', 'You stay in control of quota and plans. Preferences and plans stay on this device.')) +
    `<div class="r15-settings-grid"><section class="quiet-panel r15-settings-card"><div class="r15-settings-card-head"><span class="r15-settings-icon" aria-hidden="true">${phaseIcon('clock')}</span><span class="phase-badge ${fresh ? 'phase-sage' : 'phase-amber'}">${esc(readState)}</span></div><h2>${L('Codex 额度读取', 'Codex quota')}</h2><p>${esc(readTime)}</p><button type="button" class="button secondary" data-action="refresh">${L('重新读取额度', 'Refresh quota')}</button></section><section class="quiet-panel r15-settings-card"><div class="r15-settings-card-head"><span class="r15-settings-icon" aria-hidden="true">${phaseIcon('shield')}</span></div><h2>${L('你的数据留在本机', 'Your data stays on this device')}</h2><p>${L('额度只读自当前 Codex 账户；计划和历史保存在这台电脑。QuotaLab 不会替你启动 Codex 或提交任务。', 'Quota is read from the connected Codex account. Plans and history stay on this computer. QuotaLab will not launch Codex or submit tasks for you.')}</p></section></div>
    <details class="quiet-panel r15-advanced"><summary><span>${L('更多设置', 'More settings')}</span><small>${L('计划偏好、连接排查与本机数据', 'Plan preferences, connection help and local data')}</small></summary><div class="r15-advanced-content">
      ${researchMode}
      <form id="product-settings-form" class="r15-advanced-block"><h3>${L('计划建议偏好', 'Planning preferences')}</h3><p>${L('这些选项只影响建议方式，不会触发任务或改变 Codex 额度。', 'These options affect recommendations only; they do not run tasks or change your Codex quota.')}</p><label class="field">${L('安排风格', 'Planning style')}<select name="riskPreference"><option value="Conservative" ${preferences.riskPreference==='Conservative'?'selected':''}>${L('更稳妥','More cautious')}</option><option value="Balanced" ${!preferences.riskPreference||preferences.riskPreference==='Balanced'?'selected':''}>${L('均衡','Balanced')}</option><option value="Aggressive" ${preferences.riskPreference==='Aggressive'?'selected':''}>${L('更灵活','More flexible')}</option></select></label><label class="field">${L('工作时段预留余量（分钟）','Time to leave in reserve (minutes)')}<input name="safetyBufferMinutes" type="number" min="0" max="120" value="${preferences.safetyBufferMinutes??15}"></label><label class="switch-row"><input name="publicSignalFetchEnabled" type="checkbox" role="switch" ${preferences.publicSignalFetchEnabled?'checked':''}><span class="switch-copy"><strong>${L('允许手动查看公开服务动态', 'Allow manual public status checks')}</strong><small>${L('仅在你点击“检查更新”时读取公开信息；不会读取登录态或个人额度。', 'Reads public information only when you choose to check; it does not access sign-in data or personal quota.')}</small></span></label><button class="button secondary" type="submit">${L('保存偏好','Save preferences')}</button></form>
      <section class="r15-advanced-block"><h3>${L('连接排查', 'Connection help')}</h3><p>${L('通常不需要手动设置。若自动连接失败，可重新检测 Codex，或填写 CLI 程序路径。', 'Manual setup is usually unnecessary. If automatic connection fails, detect Codex again or provide the CLI path.')}</p><button type="button" class="button secondary" data-action="reconnect">${L('重新连接 Codex','Reconnect Codex')}</button><details class="r15-path-details"><summary>${L('手动指定 CLI 路径', 'Set the CLI path manually')}</summary><form id="codex-path-form"><label class="field">${L('Codex CLI 程序路径','Codex CLI path')}<input id="manual-codex-path" autocomplete="off"></label><button class="button secondary" type="submit">${L('验证并保存路径','Validate and save path')}</button></form></details></section>
      <section class="r15-advanced-block"><h3>${L('本机数据', 'Local data')}</h3><p>${L('总计', 'Total')}: <strong>${esc(bytes(storage.totalBytes || 0))}</strong></p><div class="r15-storage-grid">${storageRows}</div><button class="button secondary" data-storage-clean>${L('查看可清理项目','Review cleanup options')}</button></section>
      <section class="r15-advanced-block r15-app-links"><h3>${L('应用', 'App')}</h3><div class="button-row"><a class="button secondary" href="#updates">${L('版本与更新','Version and updates')}</a><button type="button" class="button secondary" data-onboard-revisit>${L('重新查看引导','Review the introduction')}</button><button type="button" class="button danger" id="exit-app">${L('退出 QuotaLab','Quit QuotaLab')}</button></div></section>
    </div></details>`;
};

const artForecastBase = forecastPage;
forecastPage = () => {
  const f = evo().forecast || {}, h = f.horizons?.[String(forecastHorizon)] || {}, allDays = h.days || [];
  const days = allDays.filter(d => Number(d.plannedMinutes) > 0 || Number(d.heavyPlans) > 0);
  const hasHistoricalSample = allDays.some(d => Number(d.historicalWorkload?.sampleDays) > 0 && Number(d.historicalWorkload?.medianMinutes) > 0);
  const html = artForecastBase();
  const template = document.createElement('template'); template.innerHTML = html;
  const list = template.content.querySelector('.evo-forecast-list');
  const metrics = template.content.querySelector('.phase-forecast-metrics');
  if (metrics && !days.length) metrics.remove();
  else if (metrics?.children.length > 2) metrics.lastElementChild.remove();
  if (list && !days.length) {
    list.innerHTML = `<div class="r11-forecast-empty"><div class="r22-forecast-empty-copy"><span class="r22-forecast-mark" aria-hidden="true">${phaseIcon('clock')}</span><div><h3>${L('还没有已安排的工作', 'No upcoming plans')}</h3><p>${hasHistoricalSample ? L('历史工作量仅作背景参考，不代表未来额度。保存安排后，这里会只列出有工作的日期。', 'Historical workload is context only, not a forecast of future quota. Save a plan to see only the dates with scheduled work.') : L('目前也没有足够的历史样本。保存一段工作安排后，这里会列出对应日期与时长。', 'There is not enough history yet either. Save a work period to see its date and duration here.')}</p></div></div><a class="button secondary" href="#planner">${L('安排工作', 'Plan work')}</a></div>`;
    list.closest('.quiet-panel')?.classList.add('phase-forecast-empty-panel');
  }
  else if (list) list.querySelectorAll('article').forEach(row => { const date = row.querySelector('time')?.textContent; if (!days.some(d => d.date === date)) row.remove(); });
  return template.innerHTML;
};

function bindArtPlanForm() {
  const form = $('#product-plan-form');
  if (!form || form.dataset.r11Bound) return;
  form.dataset.r11Bound = 'true';
  form.addEventListener('submit', async event => {
    event.preventDefault(); event.stopPropagation();
    if (!form.reportValidity()) return;
    const fields = Object.fromEntries(new FormData(form));
    const feedback = $('#product-feedback'), button = form.querySelector('[type="submit"]');
    if (fields.start === fields.end) {
      if (feedback) feedback.textContent = L('结束时间需要晚于开始时间。', 'End time must differ from start time.');
      form.elements.end.focus(); return;
    }
    const start = new Date(`${fields.date}T${fields.start}`), end = new Date(`${fields.date}T${fields.end}`);
    if (fields.end < fields.start) end.setDate(end.getDate() + 1);
    if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) {
      if (feedback) feedback.textContent = L('请检查日期和时间。', 'Check the date and time.');
      return;
    }
    if (button) { button.disabled = true; button.dataset.originalLabel = button.textContent; button.textContent = L('正在保存…', 'Saving…'); }
    if (feedback) feedback.textContent = '';
    try {
      await post('/api/orchestration/demand', {
        title: String(fields.title || '').trim(), expectedStart: start.toISOString(), expectedEnd: end.toISOString(),
        intensity: fields.intensity, priority: fields.priority || 'MEDIUM',
        targetBoundaryPolicy: fields.targetBoundaryPolicy || 'BALANCED', boundaryMinutes: Number(fields.boundaryMinutes || 30)
      });
      lastPage = ''; await refresh();
      const result = $('#product-feedback');
      if (result) result.textContent = L('工作安排已保存在本机。额度建议会依照账户实际返回的窗口更新；没有读数时不会推测。', 'Your work plan is saved on this device. Advice follows the quota windows your account reports; we will not guess when data is missing.');
    } catch (error) {
      const result = $('#product-feedback');
      if (result) result.textContent = typeof errorMessage === 'function' ? errorMessage(error) : L('保存没有完成，请检查输入后重试。', 'The plan was not saved. Check the form and try again.');
      if (button) { button.disabled = false; button.textContent = button.dataset.originalLabel || L('保存安排', 'Save plan'); }
    }
  });
}

function artBindMidnightHint() {
  const form = $('#product-plan-form');
  if (!form || form.dataset.r11MidnightBound) return;
  form.dataset.r11MidnightBound = 'true';
  const emptyAdvice = document.querySelector('.r11-plan-aside .r11-advice-empty');
  if (emptyAdvice && !document.getElementById('ql-plan-preview')) {
    emptyAdvice.insertAdjacentHTML('beforebegin', `<section id="ql-plan-preview" class="ql-plan-preview" aria-label="${L('工作安排预览','Work plan preview')}"><span>${L('计划工作时长','Planned work duration')}</span><strong data-preview-duration></strong><p data-preview-period></p><small>${L('这是你安排的工作时间，不是可用额度时长。','This is the time you plan to work, not available quota time.')}</small></section>`);
  }
  const update = () => {
    const start = form.elements.start.value, end = form.elements.end.value, hint = form.querySelector('[data-phase-midnight-hint]');
    if (!hint) return;
    hint.hidden = !(start && end && end < start);
    const preview = document.getElementById('ql-plan-preview');
    if (preview) {
      const parse = value => { const parts = value.split(':').map(Number); return parts[0] * 60 + parts[1]; };
      let minutes = parse(end) - parse(start);
      if (minutes < 0) minutes += 1440;
      const hours = Math.floor(minutes / 60), remainder = minutes % 60;
      preview.querySelector('[data-preview-duration]').textContent = !start || !end || !Number.isFinite(minutes) || minutes === 0 ? L('请选择有效时段','Choose a valid period') : locale === 'zh' ? [hours ? `${hours} 小时` : '', remainder ? `${remainder} 分钟` : ''].filter(Boolean).join(' ') : [hours ? `${hours} h` : '', remainder ? `${remainder} min` : ''].filter(Boolean).join(' ');
      preview.querySelector('[data-preview-period]').textContent = `${form.elements.date.value}  ${start}–${end}${end < start ? L('（次日结束）',' (ends the next day)') : ''}`;
    }
  };
  form.addEventListener('input', update); form.addEventListener('change', update); update();
}

function applyArtDirection() {
  const routeChanged = document.body.dataset.artRoute !== current;
  document.body.classList.add('art-direction');
  document.body.dataset.artRoute = current;
  if (routeChanged) {
    window.clearTimeout(artRouteMotionTimer);
    document.body.dataset.artMotion = '';
    const enteredRoute = current;
    window.requestAnimationFrame(() => {
      if (document.body.dataset.artRoute !== enteredRoute) return;
      document.body.dataset.artMotion = 'enter';
      artRouteMotionTimer = window.setTimeout(() => {
        if (document.body.dataset.artRoute === enteredRoute) document.body.dataset.artMotion = 'idle';
      }, 720);
    });
  }
  const brand = $('.brand-mark');
  if (brand && !brand.querySelector('[data-art-direction-mark]')) brand.innerHTML = artDirectionMark;
  const version = $('.version');
  if (version) version.textContent = 'v1.0.0';
  applyUsageGuide();
  applyTaskPreparation();
  enhanceTaskWorkbench();
  applyDesktopNotificationControls();
  applyContentReview();
  const page = $('#page-content');
  if (page) {
    const textWalker = document.createTreeWalker(page, NodeFilter.SHOW_TEXT);
    let textNode;
    while ((textNode = textWalker.nextNode())) if (textNode.nodeValue.includes('v0.14.1')) textNode.nodeValue = textNode.nodeValue.replaceAll('v0.14.1', 'v1.0.0');
  }
  const icon = document.querySelector('link[rel="icon"]');
  if (icon && !icon.dataset.artDirection) { icon.href = 'data:image/svg+xml,' + encodeURIComponent(artDirectionMark); icon.dataset.artDirection = 'true'; }
  if (current === 'planner') { bindArtPlanForm(); artBindMidnightHint(); }
  if (current === 'usage') artChartAxisLabels();
}

function artChartAxisLabels() {
  const svg = $('#page-content .phase-trend-figure svg');
  if (!svg || svg.querySelector('.r11-chart-axis-label')) return;
  const values = [...svg.querySelectorAll('.phase-trend-point title')].map(node => Number(node.textContent.match(/([-+]?\d+(?:\.\d+)?)\s*(?:pp|percentage points|个百分点)/i)?.[1])).filter(Number.isFinite);
  if (values.length < 4) return;
  const min = Math.min(...values), max = Math.max(...values), mid = (min + max) / 2;
  const ns = 'http://www.w3.org/2000/svg';
  [max, mid, min].forEach((value, index) => {
    const label = document.createElementNS(ns, 'text'); label.setAttribute('class', 'r11-chart-axis-label');
    label.setAttribute('x', '2'); label.setAttribute('y', String(22 + index * 70)); label.textContent = `${value.toFixed(value % 1 ? 1 : 0)} pp`; svg.append(label);
  });
}

const artRenderBefore = render;
function applyTrustChrome() {
  const routes = new Set(['overview', 'planner', 'signals', 'history', 'settings', 'usage', 'forecast', 'updates']);
  const indicator = $('#status'), label = $('#status-text');
  if (!indicator || !label || !routes.has(current)) return;
  const appServer = latest?.app_server;
  const text = !latest ? L('正在读取','Loading') : !navigator.onLine ? L('网络已断开','Offline') :
    latest.status === 'MOCK DATA' || appServer === 'MOCK' ? L('模拟数据','Sample data') :
    latest.status === 'UNAVAILABLE' ? (appServer === 'CONNECTED' ? L('额度暂不可用','Quota unavailable') : L('读取遇到问题','Read issue')) :
    appServer === 'CONNECTED' ? L('连接正常','Connection healthy') :
    latest.status === 'LIVE' ? L('本机服务在线','Local service online') : L('连接状态待确认','Connection unverified');
  label.textContent = text;
  indicator.dataset.scope = 'connection';
  indicator.setAttribute('aria-label', L(`连接状态：${text}。额度是否新鲜请查看额度卡片的更新时间。`, `Connection status: ${text}. Check the quota card timestamp for data freshness.`));
  indicator.title = L('这里只显示连接状态；额度可用性和更新时间以额度卡片为准。', 'Connection only; use the quota card for availability and last-updated time.');
}
render = () => {
  const userRoutes = new Set(['overview', 'planner', 'signals', 'history', 'settings', 'usage', 'forecast', 'updates']);
  if (!latest || latest.setup?.completed === false || !userRoutes.has(current)) {
    artRenderBefore(); applyTrustChrome(); applyArtDirection(); return;
  }
  captureInteraction(); productNavigation(); updateChrome(); applyTrustChrome();
  const page = $('#page-content'), editing = page.contains(document.activeElement) && document.activeElement.matches('input,textarea,select');
  if (lastPage !== current || !editing) {
    const error = latest.status === 'UNAVAILABLE' && current !== 'overview' ? `<div class="error-state" role="alert"><span>${L('暂时无法更新额度，请检查连接后重试。', 'Quota could not be refreshed. Check the connection and try again.')}</span><button type="button" class="button secondary" data-action="refresh">${t('retry')}</button></div>` : '';
    const route = { overview: productOverview, planner: productPlan, signals: productSignals, history: productHistory, settings: productSettings, usage: usagePage, forecast: forecastPage, updates: updatePage }[current] || productOverview;
    page.innerHTML = error + route();
  }
  lastPage = current; document.body.classList.add('evo-shell');
  if (evo().sampleMode === 'MOCK' && !$('#evo-example-note')) page.insertAdjacentHTML('afterbegin', `<p id="evo-example-note" class="evo-example-note">${L('隔离模拟数据，仅用于界面验收。', 'Isolated mock data for UI review only.')}</p>`);
  if (typeof evoNotice !== 'undefined' && evoNotice) {
    let notice = $('#evo-feedback'); if (!notice) { notice = document.createElement('p'); notice.id = 'evo-feedback'; notice.setAttribute('role', 'status'); page.prepend(notice); }
    notice.textContent = evoNotice;
  }
  restoreInteraction(); updateTickers(); phaseRefineDOM(); applyArtDirection(); restoreInteraction();
};

function applyUsageGuide() {
  const actions = document.querySelector('.top-actions');
  if (actions && !actions.querySelector('[data-usage-guide]')) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'ql-guide-trigger';
    button.dataset.usageGuide = ''; actions.prepend(button);
  }
  const trigger = document.querySelector('[data-usage-guide]');
  if (trigger) {
    trigger.innerHTML = `<span aria-hidden="true">?</span><span class="ql-guide-trigger-label">${L('使用指南','How to use')}</span>`;
    trigger.setAttribute('aria-label', L('打开 QuotaLab 使用指南','Open the QuotaLab usage guide'));
    trigger.title = L('从查看额度到安排工作','From quota to a work plan');
  }
  let guide = document.getElementById('ql-usage-guide');
  if (!guide) {
    guide = document.createElement('dialog'); guide.id = 'ql-usage-guide';
    guide.className = 'ql-guide'; guide.setAttribute('aria-labelledby','ql-guide-title');
    document.body.append(guide);
  }
  if (guide.dataset.language === locale) return;
  guide.dataset.language = locale;
  guide.innerHTML = `<div class="ql-guide-heading"><span>QuotaLab</span><button type="button" data-close-guide aria-label="${L('关闭使用指南','Close usage guide')}">×</button></div>
    <h2 id="ql-guide-title">${L('把额度，变成有把握的安排。','Turn quota into a considered plan.')}</h2>
    <p class="ql-guide-lead">${L('适合经常使用 Codex、希望提前安排工作的人。查看当前额度，保存工作时段，再根据建议调整节奏。','For people who use Codex and want to plan ahead. Read your quota, save a work period, and use the advice to adjust your schedule.')}</p>
    <ol class="ql-guide-steps">
      <li><h3>${L('先看概览','Read Overview')}</h3><p>${L('看剩余额度、更新时间和重置读数。五小时与周额度分别计算；未返回的额度会明确标出。','Check remaining quota, the timestamp and reported reset. Five-hour and weekly limits are separate. Missing readings are labeled.')}</p></li>
      <li><h3>${L('告诉它你什么时候工作','Choose when you will work')}</h3><p>${L('进入需求规划，例如安排明天 09:00–12:00，选择工作强度并保存。跨午夜时段也可以。','In Plan, choose a period such as tomorrow 09:00–12:00, select the intensity and save. Overnight periods are supported.')}</p></li>
      <li><h3>${L('读建议，再决定下一步','Read the advice and decide')}</h3><p>${L('保存后在右侧查看建议和原因。额度较低时可调整时段或强度；读数缺失时可以先保留计划。保存计划不会替你运行 Codex。','After saving, read the advice and its reasons on the right. Adjust timing or intensity when quota is low. Keep a plan even when readings are missing. Saving does not run Codex for you.')}</p></li>
      <li><h3>${L('工作后，留下结果','Record what happened')}</h3><p>${L('在使用洞察中为已开始的计划记录结果和用时。历史记录帮你回看安排，节奏预测帮你查看接下来几天的工作量。','In Usage, report the outcome and time spent on a started plan. History keeps your plans; Forecast shows the upcoming workload.')}</p></li>
    </ol>
    <p class="ql-guide-lead">${L('想让 AI 到点执行？在需求规划展开“准备官方定时任务”，生成设置说明，再到官方应用确认创建。保存工作计划、本地提醒和官方定时任务是三种不同操作。','Want AI to run at a set time? Open “Prepare an official scheduled task” in Plan, prepare the instructions, and confirm creation in the official app. Work plans, local reminders and official scheduled tasks are separate actions.')}</p>
    <div class="ql-guide-actions"><button type="button" class="button" data-guide-route="planner">${L('安排我的第一段工作','Plan my first work period')}</button><button type="button" class="button secondary" data-guide-route="usage">${L('记录工作结果','Record an outcome')}</button></div>
    <p class="ql-guide-note">${L('额度百分比不等于可用小时数。公开服务动态不能证明你的个人额度已恢复。计划和记录保存在这台电脑。','Quota percentage is not hours of work. Public status does not prove your personal quota has recovered. Plans and records stay on this computer.')}</p>`;
}

document.addEventListener('click', event => {
  if (event.target.closest('[data-usage-guide]')) {
    document.getElementById('ql-usage-guide')?.showModal(); return;
  }
  const close = event.target.closest('[data-close-guide]'), route = event.target.closest('[data-guide-route]');
  if (close || route) {
    document.getElementById('ql-usage-guide')?.close();
    if (route) location.hash = '#' + route.dataset.guideRoute;
  }
});

// Preparation only: no scheduler, execution API, credential access or network
// request is involved. Draft text stays in memory until this page is closed.
const qlTaskDraft = { prompt: '', repeat: 'once', date: '', time: '', open: false, instructions: '' };
function qlTimeOrbit() {
  return `<svg class="ql-time-orbit" viewBox="0 0 240 120" fill="none" aria-hidden="true" focusable="false"><g class="ql-orbit-lines" stroke="currentColor"><ellipse cx="120" cy="60" rx="104" ry="36"/><ellipse cx="120" cy="60" rx="76" ry="25" transform="rotate(-26 120 60)"/><path d="M16 60h208M120 12v96" stroke-dasharray="2 6"/><path class="ql-orbit-trace" d="M22 72C48 21 174 11 219 54" pathLength="100"/><circle cx="120" cy="60" r="17"/><path d="M120 49v11l8 5"/></g><circle class="ql-orbit-point" cx="200" cy="37" r="4" fill="currentColor"/></svg>`;
}

function applyContentReview() {
  if (current === 'planner') {
    const selected = selectedPlannerDemand();
    const title = document.querySelector('.r11-advice-wrap .r11-section-heading h2');
    if (selected && title) title.textContent = L('额度建议：', 'Quota advice: ') + selected.title;
    const pending = document.querySelector('.r11-advice-empty');
    if (selected && pending) {
      pending.querySelector('h3').textContent = L('安排已保存，建议暂未就绪', 'Plan saved; advice is not ready');
      pending.querySelector('p').textContent = L('当前查看「', 'Viewing “') + selected.title + L('」。等待当前计划的建议更新；不会使用其他计划代替。', '”. Wait for its advice to update; no other plan is substituted.');
    }
    const advice = document.querySelector('.r11-advice-wrap');
    const reminders = (orch().reminderRecords || []).filter(r => r.demandId === selected?.id && !r.dismissed && !r.invalidated && ['SCHEDULED','FIRED','DISPLAYED','DELIVERY_UNCONFIRMED'].includes(r.state));
    if (advice && reminders.length && !advice.querySelector('.ql-selected-reminders')) {
      advice.insertAdjacentHTML('beforeend', `<section class="ql-selected-reminders" aria-label="${L('当前计划的提醒', 'Reminders for the selected plan')}"><h3>${L('此计划已设置的本地提醒', 'Local reminders for this plan')}</h3>${reminders.map(r => `<div><p>${esc(selected.title)}<br><time>${esc(artLocalDate(r.remindAt))}</time></p><button type="button" class="button secondary" data-product-dismiss="${esc(r.id)}">${L('取消此提醒', 'Cancel this reminder')}</button></div>`).join('')}</section>`);
    }
  }
  document.querySelector('.r11-fresh-dot')?.remove();
  document.querySelectorAll('.phase-action-card:not(.phase-advice-unavailable)[data-recommendation-id]').forEach(card => {
    const badge = card.querySelector('.phase-card-meta .phase-badge');
    if (badge && !badge.classList.contains('phase-amber')) {
      badge.replaceChildren(document.createTextNode(L('基于当前读数', 'Based on current readings')));
      badge.title = L('只代表建议使用了当前读数，不保证未来额度或任务完成。', 'The advice uses current readings; future quota and task completion are not guaranteed.');
    }
    const footer = card.querySelector('.phase-action-footer > small');
    if (footer) footer.textContent = footer.textContent.replace('当前建议已核验', '不保证未来额度').replace('Current advice checked', 'Future quota is not guaranteed');
  });
  document.querySelectorAll('select[data-lead-for]').forEach(select => {
    select.setAttribute('aria-label', L('开工前提前多少分钟提醒', 'Minutes before work to remind you'));
    select.querySelectorAll('option').forEach(option => {
      const value = option.value; option.value = value;
      if (/^\d+$/.test(value)) option.textContent = L(`提前 ${value} 分钟`, `${value} min before`);
    });
    const footer = select.closest('.phase-action-footer');
    if (footer && !footer.parentElement.querySelector('.ql-reminder-boundary')) footer.insertAdjacentHTML('afterend', `<p class="ql-reminder-boundary">${latest?.desktopNotifications?.enabled ? L('桌面提醒已启用，不会自动执行任务；网页可最小化或关闭，但 QuotaLab 后台需继续运行。显示受 Windows 通知设置影响。', 'Desktop reminders enabled; they do not execute tasks. The page may be minimized or closed, but QuotaLab must keep running. Windows notification settings control display.') : L('这是网页内提醒，不会自动执行任务。要在最小化后收到桌面提示，请启用上方的 Windows 桌面提醒。', 'This is an in-page reminder, not an executing task. Enable Windows desktop reminders above for notifications while minimized.')}</p>`);
  });
  if (current === 'usage') {
    const stats = document.querySelectorAll('.phase-insight-stats article');
    const labels = [L('已记录结果（自报）', 'Recorded outcomes (self-reported)'), L('重度工作完成率（自报）', 'Intensive completion rate (self-reported)'), L('历史完成率较高时段', 'Higher historical completion-rate hour')];
    const notes = [L('每个计划以最新一次结果为准', 'Latest report per plan'), L('至少 5 条重度工作结果后展示', 'Shown after at least 5 intensive reports'), L('至少 20 条且覆盖多个日期和时段；不是效率保证', 'At least 20 reports across dates and hours; not an efficiency guarantee')];
    stats.forEach((stat, index) => {
      const label = stat.querySelector('span'); if (label) label.textContent = labels[index];
      if (!stat.querySelector('.ql-stat-note')) { const note = document.createElement('small'); note.className = 'ql-stat-note'; note.textContent = notes[index]; stat.append(note); }
    });
    const title = document.querySelector('.phase-usage-panel h2');
    if (title) title.textContent = L('观测到的额度下降 · 百分点', 'Observed quota drops · percentage points');
    const definition = document.querySelector('.phase-data-definition');
    if (definition && !definition.dataset.reviewed) {
      definition.dataset.reviewed = 'true';
      definition.append(document.createTextNode(L(' 当日数值是符合条件的相邻快照下降量之和；跨重置、间隔超过一小时或上涨不计入。它可能不覆盖全天，合计也可能超过 100 个百分点，不是当天剩余额度。', ' Daily values sum eligible drops between adjacent snapshots. Resets, gaps over one hour and increases are excluded. Coverage may be partial and totals may exceed 100 points; this is not remaining quota.')));
    }
  }
}
function applyTaskPreparation() {
  const heading = document.querySelector('.phase-page-head');
  if (current === 'overview' && heading && !heading.querySelector('.ql-time-orbit')) {
    heading.classList.add('ql-orbit-heading'); heading.insertAdjacentHTML('beforeend', qlTimeOrbit());
  }
  const aside = document.querySelector('.r11-plan-aside');
  if (!aside || document.getElementById('ql-task-preparation')) return;
  const plan = document.getElementById('product-plan-form');
  aside.insertAdjacentHTML('beforeend', `<details id="ql-task-preparation" class="ql-task-preparation"><summary><span>${phaseIcon('clock')}<strong>${L('准备官方定时任务', 'Prepare an official scheduled task')}</strong></span><span aria-hidden="true">+</span></summary><div class="ql-task-body"><p>${L('想让 AI 到点执行，而不只是保存工作时段？在这里整理任务，再到 ChatGPT / Codex 的官方定时任务窗口确认创建。', 'Want AI to run at a set time, rather than just save a work period? Prepare the task here, then confirm it in the official ChatGPT / Codex scheduled-task interface.')}</p><p class="ql-task-boundary">${L('这里只准备设置说明，不会创建任务、运行模型或消耗额度。', 'This prepares instructions only. It does not create a task, run a model or consume quota.')}</p><label class="field">${L('到点要做什么', 'What should run')}<textarea id="ql-task-prompt" maxlength="4000" rows="3" placeholder="${L('例如：检查项目的测试结果，汇总失败原因；不要修改文件。', 'Example: Review project test results and summarize failures without changing files.')}">${esc(qlTaskDraft.prompt)}</textarea></label><div class="form-grid"><label class="field">${L('重复', 'Repeat')}<select id="ql-task-repeat"><option value="once">${L('仅一次', 'Once')}</option><option value="daily">${L('每天', 'Daily')}</option><option value="weekdays">${L('工作日（周一至周五）', 'Weekdays (Mon–Fri)')}</option></select></label><label class="field">${L('时间', 'Time')}<input id="ql-task-time" type="time" value="${esc(qlTaskDraft.time || plan?.elements.start.value || '09:00')}"></label><label class="field">${L('日期 / 首次运行日期', 'Date / First run date')}<input id="ql-task-date" type="date" value="${esc(qlTaskDraft.date || plan?.elements.date.value || '')}"></label></div><p class="ql-task-zone">${L('时区', 'Time zone')}：${esc(Intl.DateTimeFormat().resolvedOptions().timeZone || 'local')} · ${L('此处独立设置，不会修改左侧计划。', 'These settings do not change the work plan.')}</p><button class="button secondary" type="button" data-prepare-task>${L('生成设置说明', 'Prepare instructions')}</button><p id="ql-task-feedback" role="status" aria-live="polite"></p><div id="ql-task-result" hidden><label class="field">${L('复制到官方应用，由你确认创建', 'Copy to the official app and confirm creation')}<textarea id="ql-task-instructions" rows="9" readonly></textarea></label><button class="button" type="button" data-copy-prepared-task>${L('复制设置说明', 'Copy instructions')}</button><ol class="ql-task-steps"><li>${L('先在普通聊天中试跑任务描述，确认结果符合预期。', 'Test the task prompt in a regular chat first.')}</li><li>${L('在官方应用中创建定时任务，核对重复、日期、时间和时区。', 'Create a scheduled task in the official app; check repeat, date, time and time zone.')}</li><li>${L('按需选择模型、通知和本地运行；官方应用显示创建成功后才算生效。', 'Choose model, notifications and local execution as needed. It is active only after the official app confirms creation.')}</li></ol></div><p class="ql-task-caution">${L('需要本机文件时，请在官方桌面应用选择对应项目，并保持电脑开机、应用运行。定时执行不保证额度恢复，也不会把这里的额度建议自动同步到官方任务。', 'For local files, select the project in the official desktop app and keep the computer on and app running. A schedule does not guarantee quota recovery; quota advice here does not sync automatically to official tasks.')}</p><a class="subtle-link" href="https://learn.chatgpt.com/docs/automations" target="_blank" rel="noopener noreferrer">${L('官方定时任务说明（联网打开）', 'Official scheduled-task guide (opens online)')}</a></div></details>`);
  document.getElementById('ql-task-repeat').value = qlTaskDraft.repeat;
  // Do not seed a new task brief with yesterday's retained work-plan draft.
  if (!qlTaskDraft.date && !qlTaskDraft.time) {
    const dateInput = document.getElementById('ql-task-date'), timeInput = document.getElementById('ql-task-time');
    if (new Date(dateInput.value+'T'+timeInput.value).getTime() <= Date.now()) {
      const next = new Date(Date.now()+10*60*1000);
      dateInput.value = `${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}-${String(next.getDate()).padStart(2,'0')}`;
      timeInput.value = `${String(next.getHours()).padStart(2,'0')}:${String(next.getMinutes()).padStart(2,'0')}`;
    }
  }
  document.getElementById('ql-task-preparation').open = qlTaskDraft.open;
  if (qlTaskDraft.instructions) {
    document.getElementById('ql-task-instructions').value = qlTaskDraft.instructions;
    document.getElementById('ql-task-result').hidden = false;
  }
}
document.addEventListener('toggle', event => {
  if (event.target.id === 'ql-task-preparation' && event.target.isConnected) qlTaskDraft.open = event.target.open;
}, true);
document.addEventListener('input', event => {
  const keys = { 'ql-task-prompt': 'prompt', 'ql-task-repeat': 'repeat', 'ql-task-date': 'date', 'ql-task-time': 'time' };
  if (!keys[event.target.id]) return;
  qlTaskDraft[keys[event.target.id]] = event.target.value;
  qlTaskDraft.instructions = '';
  const result = document.getElementById('ql-task-result');
  if (result) result.hidden = true;
  const feedback = document.getElementById('ql-task-feedback');
  if (feedback) feedback.textContent = '';
});
document.addEventListener('click', async event => {
  if (event.target.closest('[data-prepare-task]')) {
    const prompt = document.getElementById('ql-task-prompt'), time = document.getElementById('ql-task-time'), date = document.getElementById('ql-task-date');
    const feedback = document.getElementById('ql-task-feedback');
    if (!prompt.value.trim()) { feedback.textContent = L('请先填写到点要做的事。', 'Describe what should run first.'); prompt.focus(); return; }
    if (!date.value || !time.value || !date.checkValidity() || !time.checkValidity()) { feedback.textContent = L('请填写有效的日期和时间。', 'Enter a valid date and time.'); return; }
    const firstRun = new Date(`${date.value}T${time.value}`).getTime();
    if (!Number.isFinite(firstRun) || firstRun <= Date.now()) { feedback.textContent = L('首次运行时间已过去或无效，请选择将来的时间。', 'Choose a valid future first run time.'); date.focus(); return; }
    const repeat = document.getElementById('ql-task-repeat');
    Object.assign(qlTaskDraft, { prompt: prompt.value, date: date.value, time: time.value, repeat: repeat.value });
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
    document.getElementById('ql-task-instructions').value = L(
      `请帮我设置一个定时任务，确认设置后再创建。\n任务内容：\n${prompt.value.trim()}\n\n重复：${repeat.options[repeat.selectedIndex].text}\n日期 / 首次运行日期：${date.value}\n时间：${time.value}\n时区：${zone}\n请与我核对：模型、通知、是否在本地项目运行、是否沿用当前聊天。\n如需本地文件，请先让我选择项目；不要自行扩大读写权限。\n这不是额度恢复触发器，不保证届时有可用额度。`,
      `Help me set up a scheduled task. Confirm these settings before creating it.\nTask:\n${prompt.value.trim()}\n\nRepeat: ${repeat.options[repeat.selectedIndex].text}\nDate / first run date: ${date.value}\nTime: ${time.value}\nTime zone: ${zone}\nConfirm with me: model, notifications, whether it runs in a local project and whether it uses the current chat.\nIf local files are needed, ask me to select the project. Do not broaden read/write permissions.\nThis is not a quota-recovery trigger and does not guarantee available quota.`);
    document.getElementById('ql-task-result').hidden = false;
    qlTaskDraft.instructions = document.getElementById('ql-task-instructions').value;
    extendTaskInstructions();
    feedback.textContent = L('说明已生成；尚未创建任何官方任务。关闭页面后草稿不会保留。', 'Instructions prepared; no official task has been created. This draft is not kept after closing the page.');
  }
  if (event.target.closest('[data-copy-prepared-task]')) {
    const output = document.getElementById('ql-task-instructions'), feedback = document.getElementById('ql-task-feedback');
    try { await navigator.clipboard.writeText(output.value); feedback.textContent = L('已复制。请到官方应用粘贴并确认创建。', 'Copied. Paste into the official app and confirm creation.'); }
    catch { output.focus(); output.select(); feedback.textContent = L('浏览器未允许复制，已选中文本，请按 Ctrl+C 手动复制。', 'Clipboard unavailable. Text selected; press Ctrl+C to copy.'); }
  }
});
