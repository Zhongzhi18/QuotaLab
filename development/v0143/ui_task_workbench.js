// A visible preparation workspace, not an unofficial scheduler or executor.
let qlPlannerMode = 'work';
try { qlPlannerMode = sessionStorage.getItem('ql_planner_mode') === 'tasks' ? 'tasks' : 'work'; } catch {}
const qlTaskExtra = { name: '', scope: '', output: null, constraints: null, execution: 'local', notices: 'confirm', model: '', chat: 'confirm' };

function enhanceTaskWorkbench() {
  const grid = document.querySelector('.r11-planner-grid'), old = document.getElementById('ql-task-preparation');
  if (!grid || !old) return;
  if (!document.getElementById('ql-planner-modes')) {
    grid.insertAdjacentHTML('beforebegin', `<div id="ql-planner-modes" class="ql-planner-modes" role="tablist" aria-label="${L('规划方式', 'Planning mode')}"><button type="button" role="tab" data-planner-mode="work" aria-controls="ql-work-panel">${phaseIcon('planner')}${L('工作时段与提醒', 'Work periods and reminders')}</button><button type="button" role="tab" data-planner-mode="tasks" aria-controls="ql-task-preparation">${phaseIcon('tasks')}${L('官方定时任务工作台', 'Official task preparation')}</button></div>`);
    grid.id = 'ql-work-panel'; grid.setAttribute('role', 'tabpanel');
  }
  if (!old.dataset.workbench) {
    const section = document.createElement('section');
    section.id = old.id; section.className = 'ql-task-workbench'; section.dataset.workbench = 'true';
    section.setAttribute('role', 'tabpanel'); section.setAttribute('aria-labelledby', 'ql-task-workbench-title');
    const body = old.querySelector('.ql-task-body');
    section.innerHTML = `<header class="ql-workbench-heading"><div><h2 id="ql-task-workbench-title">${L('把目标，整理成可交接的任务。', 'Turn an objective into a task you can hand off.')}</h2><p>${L('明确任务与范围 → 配置执行偏好 → 核对并复制 → 在官方应用确认创建', 'Define scope → choose preferences → review and copy → confirm in the official app')}</p></div><span>${L('只准备，不代建', 'Preparation only')}</span></header><div class="ql-task-layout"></div>`;
    section.querySelector('.ql-task-layout').append(body);
    old.replaceWith(section); grid.before(section);
    const prompt = document.getElementById('ql-task-prompt');
    const firstField = prompt.closest('.field');
    firstField.insertAdjacentHTML('beforebegin', `<div class="ql-task-templates"><span>${L('从一个例子开始', 'Start from an example')}</span>${['review','research','writing'].map((key,index)=>`<button type="button" class="button secondary" data-task-template="${key}">${[L('项目只读巡检', 'Read-only project review'),L('资料整理', 'Research digest'),L('写作复核', 'Writing review')][index]}</button>`).join('')}</div><label class="field">${L('任务名称', 'Task name')}<input id="ql-task-name" data-task-extra="name" maxlength="80" value="${esc(qlTaskExtra.name)}" placeholder="${L('例如：每日项目巡检', 'For example: Daily project review')}"></label>`);
    firstField.insertAdjacentHTML('afterend', `<label class="field">${L('资料或项目范围', 'Source material or project scope')}<textarea id="ql-task-scope" data-task-extra="scope" rows="2" maxlength="1600" placeholder="${L('具体项目、文件范围或已提供的资料；这里只记录文字，不会读取文件。', 'Name the project, files or supplied material. This field does not read files.')}">${esc(qlTaskExtra.scope)}</textarea></label><label class="field">${L('输出要求与验收标准', 'Output and acceptance criteria')}<textarea id="ql-task-output" data-task-extra="output" rows="2" maxlength="1600">${esc(qlTaskExtra.output ?? L('列出结论、依据和待确认问题；明确说明无法核实的内容。', 'List conclusions, evidence and open questions. Identify anything that could not be verified.'))}</textarea></label><label class="field">${L('禁止事项与失败处理', 'Restrictions and failure handling')}<textarea id="ql-task-constraints" data-task-extra="constraints" rows="2" maxlength="1600">${esc(qlTaskExtra.constraints ?? L('默认只读，不修改文件；资料或权限不足时停止并询问，不自行扩大权限。', 'Read-only by default. If information or permission is missing, stop and ask; do not broaden access.'))}</textarea></label>`);
    const prepare = body.querySelector('[data-prepare-task]');
    prepare.classList.remove('secondary'); prepare.textContent = L('生成官方任务交接说明', 'Prepare official-task instructions');
    prepare.insertAdjacentHTML('beforebegin', `<details class="ql-task-preferences"><summary>${L('执行与通知偏好（交由官方窗口确认）', 'Execution and notification preferences (confirm in the official app)')}</summary><div class="form-grid"><label class="field">${L('执行位置', 'Execution location')}<select data-task-extra="execution"><option value="local">${L('本机项目', 'Local project')}</option><option value="web">${L('网页版 / 已提供资料', 'Web / supplied material')}</option></select></label><label class="field">${L('通知偏好', 'Notification preference')}<select data-task-extra="notices"><option value="confirm">${L('创建时再确认', 'Confirm during creation')}</option><option value="important">${L('重要更新', 'Important updates')}</option><option value="failures">${L('仅失败时', 'Failures only')}</option></select></label><label class="field">${L('聊天方式', 'Chat context')}<select data-task-extra="chat"><option value="confirm">${L('创建时再确认', 'Confirm during creation')}</option><option value="existing">${L('沿用当前聊天', 'Use the current chat')}</option><option value="standalone">${L('每次独立任务', 'Independent runs')}</option></select></label><label class="field">${L('模型偏好（可选）', 'Model preference (optional)')}<input data-task-extra="model" maxlength="120" value="${esc(qlTaskExtra.model)}" placeholder="${L('留空使用官方默认；可用性以账户为准', 'Leave blank for the official default; account availability applies')}"></label></div></details>`);
    body.querySelectorAll('select[data-task-extra]').forEach(input => input.value = qlTaskExtra[input.dataset.taskExtra]);
    section.querySelector('.ql-task-layout').insertAdjacentHTML('beforeend', `<aside class="ql-task-review" aria-label="${L('任务准备检查', 'Task preparation review')}"><h3>${L('创建前，核对这些事', 'Check before creating')}</h3><dl><dt>${L('任务目标', 'Objective')}</dt><dd data-review-objective></dd><dt>${L('时间与时区', 'Time and time zone')}</dt><dd data-review-schedule></dd><dt>${L('任务状态', 'Task status')}</dt><dd>${L('仅准备说明；官方状态未同步', 'Instructions only; official status is not synced')}</dd></dl><p>${L('先在普通聊天中试跑描述，核对输出，再确认正式定时任务。这里只保存当前页面草稿，不会读取项目或调用模型。', 'Test the prompt in a normal chat before scheduling it. This page keeps a temporary draft; it does not read the project or call a model.')}</p><p>${L('本机任务需要电脑开机、官方应用运行。任务可能消耗额度；定时运行不保证额度恢复。', 'Local tasks need the computer on and the official app running. Runs can consume quota; scheduling does not guarantee quota recovery.')}</p></aside>`);
  }
  compactTaskWorkbench(); applyPlannerMode(); updateTaskReview();
}

// Show the essentials first; advanced fields retain their values and labels.
function compactTaskWorkbench() {
  const section = document.getElementById('ql-task-preparation');
  if (!section || section.dataset.compact) return;
  section.dataset.compact = 'true';
  section.querySelector('.ql-task-review')?.remove();
  section.querySelector('.ql-workbench-heading p')?.remove();
  section.querySelector('#ql-task-workbench-title').textContent = L('填写任务与时间', 'Describe the task and choose a time');
  const body = section.querySelector('.ql-task-body');
  body.querySelector(':scope > p:first-child')?.remove();
  body.querySelector('.ql-task-boundary').textContent = L('仅生成说明，不创建或同步官方任务。已创建的任务请在官方应用查看。', 'Prepares instructions only; official tasks are not created or synced. View existing tasks in the official app.');
  const advanced = document.createElement('details');
  advanced.className = 'ql-task-advanced';
  advanced.dataset.interactionKey = 'official-task-advanced';
  advanced.innerHTML = `<summary>${L('更多设置（可选）', 'More settings (optional)')}</summary><div class="ql-task-advanced-body"></div>`;
  body.querySelector('[data-prepare-task]').before(advanced);
  const content = advanced.lastElementChild;
  content.append(body.querySelector('.ql-task-templates'));
  ['name','scope','output','constraints'].forEach(key => content.append(document.getElementById('ql-task-'+key).closest('label')));
  const preferences = body.querySelector('.ql-task-preferences');
  content.append(preferences.querySelector('.form-grid')); preferences.remove();
  content.append(body.querySelector('.ql-task-caution'));
  const link = body.querySelector('a.subtle-link'); if (link) content.append(link);
  body.querySelector('[data-prepare-task]').textContent = L('生成并核对说明', 'Prepare and review instructions');
}

function applyPlannerMode() {
  const task = document.getElementById('ql-task-preparation'), grid = document.getElementById('ql-work-panel');
  if (!task || !grid) return;
  task.hidden = qlPlannerMode !== 'tasks'; grid.hidden = qlPlannerMode !== 'work';
  document.querySelectorAll('[data-planner-mode]').forEach(button => {
    const selected = button.dataset.plannerMode === qlPlannerMode;
    button.id = 'ql-mode-' + button.dataset.plannerMode;
    button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
  });
  grid.setAttribute('aria-labelledby', 'ql-mode-work');
  const title = document.querySelector('.phase-page-head h1');
  if (title) title.textContent = qlPlannerMode === 'tasks' ? L('官方定时任务准备', 'Official task preparation') : L('安排重要工作', 'Plan important work');
  const note = document.querySelector('.phase-page-head p');
  if (note) note.textContent = qlPlannerMode === 'tasks' ? L('把目标、范围与执行约定整理好，再到官方应用确认创建。', 'Define the objective, scope and execution preferences, then confirm creation in the official app.') : productPlanningNote();
  const zone = document.querySelector('.ql-task-zone');
  if (zone) zone.textContent = L('时区：', 'Time zone: ') + (Intl.DateTimeFormat().resolvedOptions().timeZone || 'local') + L('。此处与工作时段计划独立，不会自动同步。', '. These settings are independent of your work-period plans and do not sync automatically.');
}

function updateTaskReview() {
  const objective = document.querySelector('[data-review-objective]'), schedule = document.querySelector('[data-review-schedule]');
  if (objective) objective.textContent = document.getElementById('ql-task-name')?.value.trim() || document.getElementById('ql-task-prompt')?.value.trim().slice(0,160) || L('请先填写目标与任务内容', 'Enter the objective and task first');
  if (schedule) schedule.textContent = [document.getElementById('ql-task-date')?.value, document.getElementById('ql-task-time')?.value, Intl.DateTimeFormat().resolvedOptions().timeZone].filter(Boolean).join(' · ');
}

function extendTaskInstructions() {
  const output = document.getElementById('ql-task-instructions'); if (!output) return;
  const values = [...document.querySelectorAll('[data-task-extra]')].map(input => {
    qlTaskExtra[input.dataset.taskExtra] = input.value;
    return `${input.closest('label').childNodes[0].textContent.trim()}：${input.tagName === 'SELECT' ? input.options[input.selectedIndex].text : input.value.trim() || L('未指定，创建时确认', 'Not specified; confirm during creation')}`;
  });
  output.value += '\n\n' + L('任务范围与执行约定（请先核对再创建）：\n', 'Scope and execution preferences (confirm before creating):\n') + values.join('\n');
  qlTaskDraft.instructions = output.value;
}

document.addEventListener('input', event => {
  if (!event.target.closest('#ql-task-preparation')) return;
  const key = event.target.dataset.taskExtra;
  if (key) { qlTaskExtra[key] = event.target.value; qlTaskDraft.instructions = ''; const result = document.getElementById('ql-task-result'); if (result) result.hidden = true; const feedback = document.getElementById('ql-task-feedback'); if (feedback) feedback.textContent = ''; }
  updateTaskReview();
});
document.addEventListener('click', event => {
  const mode = event.target.closest('[data-planner-mode]');
  if (mode) { qlPlannerMode = mode.dataset.plannerMode; try { sessionStorage.setItem('ql_planner_mode', qlPlannerMode); } catch {} applyPlannerMode(); }
  const template = event.target.closest('[data-task-template]');
  if (template) {
    const templates = {
      review: [L('每日项目巡检', 'Daily project review'), L('检查指定项目已有的测试结果和近期变更，汇总失败原因与风险；不执行命令、不修改文件。', 'Review existing test results and recent changes in the specified project. Summarize failures and risks without running commands or changing files.')],
      research: [L('资料整理', 'Research digest'), L('整理我提供的资料，按主题汇总要点并标注出处；没有提供资料时先询问，不编造来源。', 'Organize supplied material by topic with source references. Ask for missing material; do not invent sources.')],
      writing: [L('写作复核', 'Writing review'), L('检查我提供的文本，指出表达、事实依据和前后不一致的问题，只提出建议，不覆盖原文。', 'Review supplied text for clarity, evidence and inconsistencies. Suggest changes without overwriting the original.')]
    };
    const [name, prompt] = templates[template.dataset.taskTemplate];
    qlTaskExtra.name = name; qlTaskDraft.prompt = prompt; qlTaskDraft.instructions = '';
    document.getElementById('ql-task-name').value = name; document.getElementById('ql-task-prompt').value = prompt;
    document.getElementById('ql-task-result').hidden = true; updateTaskReview();
    document.getElementById('ql-task-feedback').textContent = '';
  }
});
document.addEventListener('keydown', event => {
  const tab = event.target.closest('[data-planner-mode]');
  if (!tab || !['ArrowLeft','ArrowRight'].includes(event.key)) return;
  event.preventDefault(); const target = document.querySelector(`[data-planner-mode="${tab.dataset.plannerMode === 'work' ? 'tasks' : 'work'}"]`); target.click(); target.focus();
});

function desktopDeliveryText(state) {
  if (!state.enabled) return L('桌面通道尚未启用，目前仅网页提示。', 'Desktop delivery is disabled; only in-page alerts are active.');
  const delivery = state.lastDelivery;
  if (!delivery) return L('尚未测试桌面通道。请发送测试并最小化网页，确认独立窗口出现。', 'Desktop delivery is not tested. Send a test, minimize the browser and check for the separate window.');
  const when = dt(delivery.at);
  if (state.desktopWindowVisible) return L(`最近投递 ${when}：独立桌面窗口已创建，等待你确认；不等于你已看到。`, `Last delivery ${when}: a separate desktop window was created; still awaiting your confirmation.`);
  if (delivery.status === 'FAILED') return L(`最近投递 ${when}：后台投递失败，请重测；网页提示仍可使用。`, `Last delivery ${when}: background delivery failed. Retest; in-page alerts remain available.`);
  return L(`最近投递 ${when}：${delivery.desktopWindowVisible ? '独立桌面窗口曾创建，现已关闭' : '系统通知已提交，实际显示未确认'}。`, `Last delivery ${when}: ${delivery.desktopWindowVisible ? 'desktop window was created and is now closed' : 'system notification submitted; display unconfirmed'}.`);
}

function applyDesktopNotificationControls() {
  if (!['planner','settings'].includes(current)) return;
  const target = current === 'planner' ? document.querySelector('.r11-plan-aside') : document.getElementById('page-content');
  if (!target || document.getElementById('ql-desktop-settings')) return;
  const state = latest?.desktopNotifications || {}, supported = state.supported === true, enabled = state.enabled === true;
  target.insertAdjacentHTML('afterbegin', `<section id="ql-desktop-settings" class="ql-desktop-settings"><h2>${L('Windows 桌面开工提醒', 'Windows desktop work reminders')}</h2><p>${enabled ? L('已启用：后台提交系统通知；勾选独立桌面窗口后，另有窗口持续显示，直到你关闭。', 'Enabled: background system notifications, plus a persistent desktop window when selected below.') : L('目前只有网页内提示。启用桌面提醒后，最小化或关闭网页也可由后台提交通知。', 'Currently only in-page alerts. Enable desktop reminders for background notifications even with the page minimized or closed.')}</p><div class="ql-notification-actions"><button type="button" class="button secondary" data-desktop-enable="${!enabled}" ${supported ? '' : 'disabled'}>${enabled ? L('关闭桌面提醒', 'Disable desktop reminders') : L('启用桌面提醒', 'Enable desktop reminders')}</button><button type="button" class="button secondary" data-desktop-test ${enabled && supported ? '' : 'disabled'}>${L('发送测试通知', 'Send a test notification')}</button></div><p class="caption" data-desktop-delivery-status>${esc(desktopDeliveryText(state))}</p><div class="ql-notification-options"><label><input type="checkbox" data-desktop-option="desktopWindow" ${state.desktopWindow !== false ? 'checked' : ''} ${supported ? '' : 'disabled'}>${L('独立桌面提醒窗口（网页最小化或关闭后仍显示）', 'Separate desktop reminder window (works with the browser minimized or closed)')}</label><label><input type="checkbox" data-desktop-option="sound" ${state.sound ? 'checked' : ''} ${supported ? '' : 'disabled'}>${L('允许通知声音', 'Allow notification sound')}</label><label><input type="checkbox" data-desktop-option="includeTitle" ${state.includeTitle ? 'checked' : ''} ${supported ? '' : 'disabled'}>${L('通知显示计划名称（锁屏可能可见）', 'Include plan name (may appear on lock screen)')}</label></div><p class="caption">${supported ? L('默认静音、隐藏计划名。桌面窗口不会抢走键盘焦点，需手动关闭。保持 QuotaLab 后台运行；休眠或关机时无法提醒。系统通知仍受勿扰设置影响，不会自动修改 Windows 设置。', 'Names are hidden and sound is off by default. Keep QuotaLab running. Do Not Disturb, notification settings, sleep or shutdown may prevent display. Windows settings are not changed.') : L('桌面通知仅在实际 Windows 运行环境可用；模拟验收不会发送真实通知。', 'Available in the actual Windows app only. Mock acceptance does not send real notifications.')}</p><p id="ql-desktop-feedback" role="status" aria-live="polite"></p></section>`);
}
document.addEventListener('click', async event => {
  const enable = event.target.closest('[data-desktop-enable]'), test = event.target.closest('[data-desktop-test]');
  if (!enable && !test) return;
  const button = enable || test; button.disabled = true;
  try {
    const result = await post('/api/desktop-notifications/' + (enable ? 'settings' : 'test'), enable ? {enabled: enable.dataset.desktopEnable === 'true'} : {});
    if (enable) { lastPage = ''; await refresh(); }
    const feedback = document.getElementById('ql-desktop-feedback');
    if (feedback) feedback.textContent = enable ? L('设置已保存。请发送测试通知，再最小化网页确认桌面窗口。', 'Settings saved. Send a test, then minimize the browser and check the desktop window.') : result.desktopWindowVisible ? L('独立桌面窗口已创建，请最小化网页确认。它会保留，直到你关闭。', 'A separate desktop window was created. Minimize the browser to check; it stays until closed.') : result.status === 'SUBMITTED' ? L('系统通知已提交，但没有确认独立窗口。请勾选“独立桌面提醒窗口”后重测。', 'System notification submitted, but no separate window was confirmed. Enable the desktop-window option and retest.') : L('后台投递失败，请重测；网页提示仍可使用。', 'Background delivery failed. Retest; in-page alerts remain available.');
  } catch { const feedback = document.getElementById('ql-desktop-feedback'); if (feedback) feedback.textContent = L('操作未完成。测试通知至少间隔 10 秒；检查本地连接后再试。', 'Not completed. Allow 10 seconds between tests and check the local connection.'); }
  finally { if (button.isConnected) button.disabled = false; }
});
document.addEventListener('change', async event => {
  const option = event.target.closest('[data-desktop-option]'); if (!option) return;
  option.disabled = true;
  try { await post('/api/desktop-notifications/settings', {[option.dataset.desktopOption]: option.checked}); await refresh(); }
  catch { option.checked = !option.checked; }
  finally { if (option.isConnected) option.disabled = false; }
});
