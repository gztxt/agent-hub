/* Agent Hub v0.3.0 教室视图 — 复刻 Agent_Manager(Dashboard/Manager/Memory/Ports) 交互语义
   v0.3 chat: 动态模型选择器 + 工作目录 + 会话管理 + hub-self 工具环 */
'use strict';

const $ = id => document.getElementById(id);
let AGENTS = [], PORTS = [], portsLoaded = false, memLoaded = false;

/* ── 基础 ─────────────────────────────────────────── */

async function api(path, opt) {
  const r = await fetch(path, opt);
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!r.ok) {
    const msg = (data && data.detail && (data.detail.error || (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join(';') : data.detail))) || ('HTTP ' + r.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, cls) {
  const el = document.createElement('div');
  el.className = cls || '';
  el.textContent = msg;
  $('toast').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function go(page) {
  curPage = page;
  document.querySelectorAll('.sidebar button[data-page]').forEach(b => {
    const on = b.dataset.page === page;
    b.classList.toggle('on', on);
    if (b.getAttribute('role') === 'tab') b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('section.page').forEach(s => s.classList.toggle('on', s.id === 'page-' + page));
  localStorage.setItem('hub.page', page);  // T9：记忆上次所在页，刷新后回落
  renderNav();            // v0.7：同步左侧手风琴（实体/系统项的选中态）
  renderPageCrumb(page);  // v0.7：系统页面包屑（实体页由 renderModeBar 接管）
  if (page === 'memory' && !memLoaded) { memLoaded = true; loadMemories(); loadDoc('l2'); loadDoc('l3'); }
  if (page === 'ports' && !portsLoaded) { portsLoaded = true; loadPorts(); }
  if (page === 'telemetry') loadTelemetry();
  if (page === 'chat') renderChatSide();
  if (page === 'tasks') { fillAgentSelect($('taskAgent'), true); loadRuns(); }
  if (page === 'jobs') { fillAgentSelect($('jobAgent'), false); loadJobs(); }
  if (page === 'mcp') { loadMcp(); loadAcl(); }
}

function fillAgentSelect(sel, withAuto) {
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = (withAuto ? '<option value="">自动选择执行 Agent</option>' : '<option value="">默认 jcode</option>') +
    AGENTS.map(a => '<option value="' + a.id + '">' + escapeHtml(a.name) + '</option>').join('');
  if (cur) sel.value = cur;
}

/* 上游 loopback→LAN 语义：把 127.0.0.1 换成当前访问主机名 */
function lanUrl(url) {
  if (!url) return url;
  return url.replace(/127\.0\.0\.1|localhost/g, location.hostname);
}

/* ── 教室 ─────────────────────────────────────────── */



let agentFailStreak = 0;
async function loadAgents() {
  try {
    const d = await api('/api/agents');
    AGENTS = d.agents || [];
    agentFailStreak = 0;
    renderHomeStats();
    renderNav();   // v0.7.1：Agent / 基础设施的唯一入口是左侧手风琴，随数据刷新
    const ag = AGENTS.filter(a => a.kind === 'agent');
    $('hAgents').textContent = 'Agents: ' + ag.length + '（在线 ' + ag.filter(a => a.status === 'running').length + '）';
    const errs = AGENTS.filter(a => a.status === 'error').length;
    $('hErrors').innerHTML = errs ? '<span class="hdot r"></span>异常 ' + errs : '';
  } catch (e) {
    // 服务重启窗口容忍瞬时失败；连续 ≥2 次才亮红灯（避免误报）
    agentFailStreak++;
    if (agentFailStreak === 2) {
      setHealthDot('r', 'agent 列表连续拉取失败：' + e.message);
      toast('Hub 数据连续 ' + agentFailStreak + ' 次拉取失败：' + e.message, 'err');
    }
  }
}

/* ── v0.7.1 首页统计摘要（取代 renderSeats：Agent / 基础设施已全收拢至左侧手风琴）── */
function renderHomeStats() {
  const ag = AGENTS.filter(a => a.kind === 'agent');
  const infra = AGENTS.filter(a => a.kind !== 'agent');
  const running = ag.filter(a => a.status === 'running').length;
  const errs = AGENTS.filter(a => a.status === 'error').length;
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set('cntAgent', ag.length);
  set('cntInfra', infra.length);
  set('hsAgent', ag.length);
  set('hsRunning', running);
  set('hsInfra', infra.length);
  set('hsError', errs);
  const errCard = $('hsError');
  if (errCard && errCard.parentElement) errCard.parentElement.classList.toggle('alert', errs > 0);
}

/* ── T5 真实健康灯：定时拉 /health 驱动头部灯色（ok=绿 不可达=红 其它=黄）── */
function setHealthDot(cls, title) {
  const d = $('hHealth');
  if (!d) return;
  d.className = 'hdot ' + cls;
  if (title) d.title = title;
}
async function pollHealth() {
  try {
    const d = await api('/health');
    setHealthDot(d && d.status === 'ok' ? 'g' : 'y', 'status=' + ((d && d.status) || '?'));
  } catch (e) { setHealthDot('r', 'Hub 不可达：' + e.message); }
}


function seatStateOf(a) {
  return a.status === 'running' ? 'running' : (a.status === 'installed' ? 'installed' : (a.status === 'error' ? 'error' : 'stopped'));
}
const SEAT_LABELS = { running: '在线', installed: '可启动', stopped: '离线', error: '异常' };




function showDetail(id) {
  const a = AGENTS.find(x => x.id === id);
  if (!a) return;
  $('detailTitle').textContent = a.name + '（' + (a.type || a.kind || '-') + ' · ' + a.status + '）';
  const rows = [['endpoint', a.endpoint], ['config_path', a.config_path], ['working_dir', a.working_dir], ['description', a.description]];
  let html = rows.filter(([, v]) => v).map(([k, v]) =>
    '<div style="margin:6px 0"><div class="hint">' + k + '</div>' +
    '<div style="font-family:monospace;font-size:12px;word-break:break-all;color:var(--text-1)">' + escapeHtml(v) + '</div></div>').join('');
  const es = a.entries || [];
  html += '<div class="hint" style="margin:10px 0 4px">entries</div>' +
    (es.length ? es.map(e => '<span class="tag agent" style="display:inline-block;margin:2px 4px 2px 0;max-width:100%;overflow-wrap:anywhere">' +
      escapeHtml(e.type) + (e.url ? ': ' + escapeHtml(e.url) : '') + '</span>').join('') : '<span class="hint">无</span>');
  $('detailBody').innerHTML = html || '<span class="hint">无附加信息</span>';
  $('detailDrawer').classList.add('on');
}
function closeDetail() { $('detailDrawer').classList.remove('on'); }

/* ── ⚙设置（口令保护的 TERM_TOKEN 查看/应用）── */
function openSettings() {
  $('settingsDrawer').classList.add('on');
}
function closeSettings() { $('settingsDrawer').classList.remove('on'); }
function settingsPasscode() { return localStorage.getItem('hub.passcode') || ''; }

async function settingsViewToken() {
  let pc = settingsPasscode();
  if (!pc) {
    pc = prompt('请输入设置口令（HUB_PASSCODE，向 hub 索要；仅存本浏览器）') || '';
    if (!pc) return;
  }
  try {
    const d = await api('/api/settings/term-token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-HUB-PASSCODE': pc },
      body: JSON.stringify({ passcode: pc })
    });
    localStorage.setItem('hub.passcode', pc);  // 验证通过才缓存
    const box = $('settingsTokenBox');
    box.style.display = 'block';
    $('settingsTokenVal').dataset.token = d.term_token || '';
    $('settingsTokenVal').textContent = d.set ? (d.term_token || '（空）') : '服务端未配置 TERM_TOKEN（终端鉴权走启动时自动生成的临时 token）';
    $('settingsToggleShow').textContent = '隐藏';
    if (!d.set) toast('服务端未显式配置 TERM_TOKEN', 'err');
  } catch (e) {
    localStorage.removeItem('hub.passcode');  // 口令错/失效则不缓存
    $('settingsTokenBox').style.display = 'none';
    toast(e.message, 'err');
  }
}

function settingsToggleShow() {
  const el = $('settingsTokenVal'), btn = $('settingsToggleShow');
  if (el.textContent.indexOf('••') === 0) { el.textContent = el.dataset.token || ''; btn.textContent = '隐藏'; }
  else { el.textContent = '••••••••••••••••••••'; btn.textContent = '显示'; }
}

function settingsCopyToken() {
  const t = $('settingsTokenVal').dataset.token || '';
  if (!t) return toast('无 token 可复制', 'err');
  (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject())
    .then(() => toast('已复制到剪贴板', 'ok'))
    .catch(() => { prompt('请手动复制：', t); });
}

function settingsApplyToken() {
  const t = $('settingsTokenVal').dataset.token || '';
  if (!t) return toast('无 token 可应用', 'err');
  localStorage.setItem('hub.term.token', t);
  toast('已应用到终端（本浏览器后续自动携带）', 'ok');
}

function settingsClearPasscode() {
  localStorage.removeItem('hub.passcode');
  $('settingsTokenBox').style.display = 'none';
  toast('已清除本机缓存口令，下次查看需重新输入', 'ok');
}
async function delAgent(id) {
  if (!confirm('删除自定义 Agent: ' + id + '？（仅注销视图，不停止其自身服务）')) return;
  try { await api('/api/agents/' + encodeURIComponent(id), { method: 'DELETE' }); toast('已删除', 'ok'); loadAgents(); }
  catch (e) { toast(e.message, 'err'); }
}

function openRegister() { $('regMask').classList.add('on'); }
function closeRegister() { $('regMask').classList.remove('on'); $('regDetect').style.display = 'none'; }
async function detectDir() {
  const dir = $('regDir').value.trim();
  if (!dir) return toast('请输入目录', 'err');
  try {
    const d = await api('/api/agents/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dir: dir }) });
    $('regDetect').style.display = 'block';
    $('regDetect').textContent = JSON.stringify(d, null, 2);
  } catch (e) { toast(e.message, 'err'); }
}
async function registerAgent() {
  const dir = $('regDir').value.trim();
  if (!dir) return toast('请输入目录', 'err');
  try {
    const body = { dir: dir };
    if ($('regName').value.trim()) body.name = $('regName').value.trim();
    const d = await api('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    toast('已注册: ' + d.id, 'ok');
    closeRegister(); loadAgents();
  } catch (e) { toast(JSON.stringify(e.message), 'err'); }
}

/* ── 智管对话（原指挥官功能合并）──────────────────────── */

function renderSteps(steps) {
  const wrap = document.createElement('div');
  wrap.className = 'steps';
  for (const s of (steps || [])) {
    const el = document.createElement('div');
    el.className = 'step ' + s.kind;
    if (s.kind === 'thought') el.textContent = '◇ ' + s.content;
    else if (s.kind === 'toolcall') el.textContent = '⚙ ' + (s.tool || '') + ' ' + JSON.stringify(s.tool_input || {});
    else if (s.kind === 'toolresult') {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      sum.textContent = '▾ ' + (s.tool || '') + ' 结果';
      // v0.5.3 验证错误高亮：tool_result JSON 里 error_type=='validation' 时单独醒目渲染
      try {
        const j = JSON.parse(s.content);
        if (j && j.error_type === 'validation') {
          const v = document.createElement('div');
          v.style.cssText = 'color:var(--danger-text);font-weight:600;margin:4px 0;padding:4px 8px;background:var(--danger-bg);border-radius:4px;';
          const gotStr = (j.got !== null && j.got !== undefined && j.got !== '') ? `，实际 ${esc(j.got)}` : '';
          v.textContent = `❌ 参数错误: 字段 ${esc(j.field||'?')} 期望 ${esc(j.expected||'?')}${gotStr}（tool=${esc(j.tool||'?')}）`;
          det.appendChild(v);
        } else if (j && (j.error_type === 'not_found' || j.error_type === 'rate_limit' || j.error_type === 'acl' || j.error_type === 'timeout')) {
          const v = document.createElement('div');
          v.style.cssText = 'color:var(--warn);font-weight:600;margin:4px 0;padding:4px 8px;background:var(--warn-bg);border-radius:4px;';
          v.textContent = `⚠ ${esc(j.error_type)}: ${esc(j.error || '')}`;
          det.appendChild(v);
        }
      } catch (e) {}
      const pre = document.createElement('pre');
      pre.textContent = s.content;
      det.appendChild(sum); det.appendChild(pre);
      let act = null;
      try { const j = JSON.parse(s.content); if (j && j.action === 'open_url' && j.url) act = j.url; } catch (e) {}
      if (act) {
        const b = document.createElement('button');
        b.className = 'btn sm'; b.style.marginTop = '6px'; b.textContent = '打开界面';
        b.onclick = () => window.open(lanUrl(act), '_blank');
        det.appendChild(b);
      }
      el.appendChild(det);
    } else el.textContent = s.content;
    wrap.appendChild(el);
  }
  return wrap;
}

// v0.5.2.3 表格化 + 紧凑化（用户反馈：段落太松散，要紧凑）
// 设计：两段表格（一段元信息、一段产物）+ 一行下一步/未完成
function renderSummaryCard(s) {
  if (!s) return null;
  const card = document.createElement('div');
  card.className = 'msg summary';
  // 状态色
  let color = 'var(--text-1)';
  if ((s.status || '').includes('部分')) color = 'var(--warn)';
  if ((s.status || '').includes('失败')) color = 'var(--danger-text)';
  const products = s.products || [];
  const actions = s.actions || [];
  const commits = s.commits || [];
  const nextSteps = s.next_steps || [];
  const unfinished = s.unfinished || [];
  // 表 1：元信息（5 行固定）
  const metaRows = [
    ['状态', `<b style="color:${color}">${escapeHtml(s.status || '完成')}</b>`],
    ['耗时', escapeHtml(s.duration_str || '?')],
    ['类型', escapeHtml(s.task_kind || '?')],
    ['模型', `<code>${escapeHtml(s.model || '?')}</code>`],
    ['会话', `<code>${escapeHtml((s.session_id||'').slice(0,12))}</code>`],
  ];
  const metaTable = `<table class="sum-tbl"><tbody>${metaRows.map(([k,v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</tbody></table>`;
  // 表 2：产物（路径 + commit + 动作）
  const productRows = [];
  products.forEach(p => productRows.push(['文件', `<code>${escapeHtml(p)}</code>`]));
  actions.forEach(a => productRows.push(['动作', escapeHtml(a)]));
  commits.forEach(c => {
    if (c.startsWith('commit ')) productRows.push(['提交', c]);
    else productRows.push(['产物', escapeHtml(c)]);
  });
  const prodTable = productRows.length
    ? `<table class="sum-tbl"><tbody>${productRows.slice(0, 8).map(([k,v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</tbody></table>`
    : '';
  // 紧凑下一步/未完成
  const tail = [];
  if (nextSteps.length) {
    tail.push(`<b>下一步:</b> ${nextSteps.map(n => escapeHtml(n)).join(' → ')}`);
  }
  if (unfinished.length) {
    tail.push(`<b style="color:var(--danger-text)">未完成:</b> ${unfinished.map(u => escapeHtml(u)).join('; ')}`);
  }
  card.innerHTML = `
    <div style="margin:0 0 6px 0;padding:0;line-height:1.4"><span class="sum-head" style="color:${color}">任务总结</span><span class="sum-meta" style="margin-left:8px">会话 <code>${escapeHtml((s.session_id||'').slice(0,12))}</code></span></div>
    <div style="margin:0;padding:0;line-height:1.5">${metaTable}${prodTable}</div>
    ${tail.length ? `<div class="sum-tail">${tail.join(' · ')}</div>` : ''}
  `;
  return card;
}

// 可指定输入框 / 消息容器
async function mgrSend(inputId, boxId) {
  const input = $(inputId || 'homeInput');
  if (!input) return;
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = $(boxId || 'homeMsgs');
  if (!box) return;
  const hintEl = box.querySelector('.hint');
  if (hintEl) hintEl.remove();
  const me = document.createElement('div');
  me.className = 'msg user'; me.textContent = msg;
  box.appendChild(me);
  const busy = document.createElement('div');
  busy.className = 'msg assistant'; busy.textContent = '⟳ 思考中（含工具调用，可能较久）…';
  box.appendChild(busy); box.scrollTop = box.scrollHeight;
  try {
    const body = { message: msg };
    const sid = localStorage.getItem('hub.mgr.session');
    if (sid) body.session_id = sid;
    const d = await api('/api/manager/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    busy.remove();
    if (d.session_id) localStorage.setItem('hub.mgr.session', d.session_id);
    if (d.steps) box.appendChild(renderSteps(d.steps.filter(s => s.kind !== 'answer')));
    const ans = document.createElement('div');
    ans.className = 'msg ' + (d.answer ? 'assistant' : 'err');
    ans.textContent = d.answer || ('[错误] ' + (d.error || '未知') + (d.hint ? '\n提示: ' + d.hint : ''));
    box.appendChild(ans);
  } catch (e) { busy.className = 'msg err'; busy.textContent = '[错误] ' + e.message; }
  box.scrollTop = box.scrollHeight;
}

/* 首页「智管对话」：复用 mgrSend 全套（同一会话 / 同一套 steps 渲染），只换目标容器 */
function homeSend() { mgrSend('homeInput', 'homeMsgs'); }
function homeAsk(q) {
  const i = $('homeInput');
  if (!i) return;
  i.value = q;
  homeSend();
}

/* ── 统一对话（三模式：embed 原生UI / term pty终端 / chat 对话框）── */

let chatPick = localStorage.getItem('hub.chat.pick') || 'hub-self';
// 刷新后回落「该实体上次所用形态」（与 pickChatEntity/gotoChat 同一套记忆键），否则会退成对话面板
let chatMode = localStorage.getItem('hub.chatmode.' + chatPick) || 'chat';
function sessKey(id) { return 'hub.sess.' + id; }

function entityById(id) { return AGENTS.find(a => a.id === id); }
function defaultModeOf(a) {
  const es = (a && a.entries) || [];
  if (es.some(e => e.type === 'embed')) return 'embed';
  if (es.some(e => e.type === 'term')) return 'term';
  return 'chat';
}
function gotoChat(id, mode) {
  chatPick = id;
  localStorage.setItem('hub.chat.pick', id);  // T9：记忆上次实体
  const a = entityById(id);
  chatMode = mode || localStorage.getItem('hub.chatmode.' + id) || defaultModeOf(a) || 'chat';
  localStorage.setItem('hub.chatmode.' + id, chatMode);
  go('chat');
  renderChatSide();
}

function renderChatSide() {
  const side = $('chatAgents');
  if (!AGENTS.length) { side.innerHTML = '<div class="hint" style="padding:10px">加载…</div>'; loadAgents().then(renderChatSide); return; }
  // 显示有Web UI可嵌入的实体（agent + 有embed entry的实体）
  const list = AGENTS.filter(a => a.kind === 'agent' || (a.entries || []).some(e => e.type === 'embed'));
  side.innerHTML = list.map(a => {
    const hasEmbed = (a.entries || []).some(e => e.type === 'embed');
    const badge = a.status === 'running' ? 'running' : (a.status === 'installed' ? 'installed' : 'stopped');
    const icon = hasEmbed ? '◉' : '';
    return '<div class="item' + (a.id === chatPick ? ' on' : '') + '" onclick="pickChatEntity(\'' + a.id + '\')">' +
      '<span>' + escapeHtml(a.name) + '</span>' + (icon ? '<span class="kindtag" style="font-size:10px">' + icon + '</span>' : '') + '<span class="s-badge ' + badge + '" style="position:static"></span></div>';
  }).join('');
  applyChatMode();
}

function pickChatEntity(id) {
  chatPick = id;
  localStorage.setItem('hub.chat.pick', id);
  // T9：模式记忆优先——每实体上次用过的形态，无记录才回落默认
  chatMode = localStorage.getItem('hub.chatmode.' + id) || defaultModeOf(entityById(id));
  renderChatSide();
}

function applyChatMode() {
  const a = entityById(chatPick);
  renderModeBar(a);
  if (!a) return;   // 实体已被删除（localStorage 里留着旧 pick）：保持默认面板，不再往下猜模式
  // 记忆的模式对该实体已失效（entry 被删/改）：回落到默认形态，否则三个 pane 会全 off → 右侧空白
  if (!(a.entries || []).some(e => e.type === chatMode)) {
    chatMode = defaultModeOf(a) || 'chat';
    localStorage.setItem('hub.chatmode.' + chatPick, chatMode);
  }
  $('embedPane').classList.toggle('on', chatMode === 'embed');
  $('termPane').classList.toggle('on', chatMode === 'term');
  $('chatPane').classList.toggle('on', chatMode === 'chat');
  if (chatMode === 'embed') {
    const e = (a.entries || []).find(x => x.type === 'embed');
    const url = e ? lanUrl(e.url) : '';
    $('embedTitle').textContent = a.name + ' · 原生界面';
    $('embedUrlHint').textContent = url;
    const f = $('embedFrame');
    if (f.dataset.src !== url) { f.src = url; f.dataset.src = url; }
    probeEmbed(url);
  } else if (chatMode === 'term') {
    $('termTitle').textContent = (a.name || chatPick) + ' · 终端会话';
    ensureTerm();
    // ★ 修复核心：切换实体时解绑异主会话，画面不再残留上一个 Agent
    if (termSid && termSidAgent && termSidAgent !== chatPick) termDetach();
    termRefreshList().then(() => termAutoAttach());
  } else {
    openChatSession();
    // v0.3 对话工具栏三联动：模型/工作目录/会话列表 + hub-self 工具开关显隐
    loadChatModels(false);
    chatCwdLoad();
    chatSessLoad();
    const tr = $('chatToolsRow');
    if (tr) tr.style.display = chatPick === 'hub-self' ? 'inline-flex' : 'none';
    // v0.4 修复模式：仅 hub-self + 工具开时显示，默认从 localStorage 恢复
    const rr = $('chatRepairRow');
    if (rr) {
      const showRepair = chatPick === 'hub-self' && $('chatToolsOn')?.checked;
      rr.style.display = showRepair ? 'inline-flex' : 'none';
      const cb = $('chatRepairOn');
      if (cb) cb.checked = localStorage.getItem('hub.repair.' + chatPick) === '1';
    }
    repairModeWire();
  }
}
// v0.4 修复模式开关联动：改主色、落 localStorage；chatToolsOn 变化时同步显隐
function repairModeWire() {
  const toolsCb = $('chatToolsOn');
  const repairCb = $('chatRepairOn');
  const repairRow = $('chatRepairRow');
  if (!toolsCb || !repairCb || !repairRow) return;
  if (repairCb.dataset.wired === '1') return; // 幂等
  repairCb.dataset.wired = '1';
  const sync = () => {
    const on = toolsCb.checked;
    repairRow.style.display = (chatPick === 'hub-self' && on) ? 'inline-flex' : 'none';
    repairRow.style.color = repairCb.checked ? 'var(--danger-text)' : '';
  };
  toolsCb.addEventListener('change', sync);
  repairCb.addEventListener('change', () => {
    localStorage.setItem('hub.repair.' + chatPick, repairCb.checked ? '1' : '0');
    sync();
    if (repairCb.checked) toast('⚠ 修复模式开启——LLM 可见白名单 restart / 配置写 / 回滚工具；改完请关闭', 'warn');
  });
  sync();
}

/* 模式切换栏 v0.7：右侧顶栏仅显示实体名，无分割线 */
function renderModeBar(a) {
  const bar = $('chatModeBar'), tabs = $('opTabs'), crumb = $('crumb');
  if (bar) bar.style.display = 'none';
  if (!a) { if (tabs) tabs.innerHTML = ''; if (crumb) crumb.innerHTML = ''; return; }
  // 实体页不显示名称行，保持顶部干净
  if (tabs) tabs.innerHTML = '';
}
function switchMode(m) {
  chatMode = m;
  localStorage.setItem('hub.chatmode.' + chatPick, m);  // T9：按实体记忆模式
  applyChatMode();
}

/* 嵌入存活探测：no-cors fetch 失败=目标端口无响应 → 覆盖层引导切换 */
async function probeEmbed(url) {
  const pane = $('embedPane');
  let dead = pane.querySelector('.embed-dead');
  if (dead) dead.remove();
  try {
    await Promise.race([
      fetch(url, { mode: 'no-cors', cache: 'no-store' }),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 4000))
    ]);
  } catch (e) {
    dead = document.createElement('div');
    dead.className = 'embed-dead';
    dead.style.cssText = 'position:absolute;inset:0;display:flex;flex-direction:column;gap:12px;align-items:center;justify-content:center;background:var(--mask);z-index:5';
    dead.innerHTML = '<div style="font-size:14px;color:var(--danger-text)">⚠ 目标界面未响应（' + escapeHtml(url) + '）</div>' +
      '<div style="display:flex;gap:8px"><button class="btn sm" onclick="switchMode(\'chat\')">改用对话模式</button>' +
      '<button class="btn sm ghost" onclick="switchMode(\'term\')">改用终端</button>' +
      '<button class="btn sm ghost" onclick="embedRefresh()">重试嵌入</button></div>';
    if (getComputedStyle(pane).position === 'static') pane.style.position = 'relative';
    pane.appendChild(dead);
  }
}

async function embedRefresh() { const f = $('embedFrame'); f.src = f.src; probeEmbed(f.dataset.src || ''); }
function embedNewTab() { const a = entityById(chatPick); const e = (a.entries || []).find(x => x.type === 'embed'); if (e) window.open(lanUrl(e.url), '_blank'); }

/* ── pty 终端（xterm.js + WebSocket）── 修复：会话按实体隔离，切换即换绑 ── */

let term = null, termFit = null, termWs = null, termSid = null, termSidAgent = null;

function ensureTerm() {
  if (term) return;
  // v0.7.1 白底：终端主题同步反转（否则它是整页唯一一块黑）
  term = new window.Terminal({ fontSize: 13, fontFamily: 'Menlo,Consolas,monospace', theme: { background: '#ffffff', foreground: '#111111', cursor: '#111111', cursorAccent: '#ffffff',
           selectionBackground: '#c5c5c5', black: '#111111', red: '#3a3a3a', green: '#111111', yellow: '#4a4a4a',
           blue: '#5f5f5f', magenta: '#2b2b2b', cyan: '#262626', white: '#111111', brightBlack: '#8a8a8a',
           brightRed: '#1f1f1f', brightGreen: '#000000', brightYellow: '#262626', brightBlue: '#4a4a4a',
           brightMagenta: '#1c1c1c', brightCyan: '#1a1a1a', brightWhite: '#000000' }, cursorBlink: true });
  termFit = new window.FitAddon.FitAddon();
  term.loadAddon(termFit);
  term.open($('termEl'));
  term.onData(d => { if (termWs && termWs.readyState === 1) termWs.send(JSON.stringify({ data: d })); });
  const fit = () => { try { termFit.fit(); if (termWs && termWs.readyState === 1) termWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); } catch (e) {} };
  window.addEventListener('resize', fit);
  new ResizeObserver(fit).observe($('termEl'));
  setTimeout(fit, 80);
}

function termDetach() {
  if (termWs) { try { termWs.close(); } catch (e) {} termWs = null; }
  termSid = null; termSidAgent = null;
}

/* TERM_TOKEN 鉴权（后端强制校验）：首次用终端时 prompt 一次存 localStorage，之后 header+query 双带 */
function termToken() {
  let t = localStorage.getItem('hub.term.token');
  if (!t) {
    t = prompt('请输入终端鉴权 TERM_TOKEN（也可在右上角 ⚙设置 查看后一键应用）') || '';
    if (t) localStorage.setItem('hub.term.token', t);
  }
  return t;
}
function termHeaders(extra) {
  return Object.assign({ 'X-TERM-TOKEN': termToken() }, extra || {});
}

function wsUrl(path) {
  const t = localStorage.getItem('hub.term.token');
  const sep = path.includes('?') ? '&' : '?';
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path + (t ? sep + 'token=' + encodeURIComponent(t) : '');
}

function termConnect(sid, agent) {
  if (termWs) { try { termWs.close(); } catch (e) {} termWs = null; }
  termSid = sid;
  termSidAgent = agent || termSidAgent;
  term.clear();
  const ws = new WebSocket(wsUrl('/ws/term/' + sid));
  ws.binaryType = 'arraybuffer';
  ws.onmessage = ev => { if (termWs === ws) term.write(typeof ev.data === 'string' ? ev.data : new Uint8Array(ev.data)); };
  ws.onopen = () => { term.focus(); if (termFit) termFit.fit(); if (termWs === ws) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); };
  ws.onclose = ev => {
    if (termWs !== ws) return;  // 旧连接的 close 不污染新会话画面
    const gone = ev.code === 4404 || ev.code === 4410;  // 已退出/不存在 → 明确提示并刷新列表
    term.write('\r\n\x1b[90m' + (gone ? '[该会话已结束或不存在，列表已刷新——可点「＋ 新会话」]' : '[连接断开——点「会话」重连或新建]') + '\x1b[0m');
    if (gone) { termDetach(); termRefreshList(); }
  };
  termWs = ws;
}

async function termNew() {
  try {
    const d = await api('/api/term/sessions', { method: 'POST', headers: termHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ agent_id: chatPick }) });
    toast('已拉起 ' + chatPick + ' 终端会话', 'ok');
    termConnect(d.session.id, chatPick);
    termRefreshList();
  } catch (e) { toast(e.message, 'err'); }
}

async function termRefreshList() {
  try {
    const d = await api('/api/term/sessions', { headers: termHeaders() });
    // 只显示当前选中 agent 的会话，避免显示其他 agent 的终端
    const live = (d.sessions || []).filter(s => s.alive && s.agent_id === chatPick);
    const el = $('termSessList');
    if (!el) return live;
    el.innerHTML = live.length
      ? live.map(s =>
          '<span class="sess-item" data-sid="' + s.id + '"' + (s.id === termSid ? ' style="border-color:var(--accent-2)"' : '') + '>' +
          '<a href="javascript:void(0)" onclick="termConnect(\'' + s.id + '\',\'' + s.agent_id + '\')" style="color:var(--text-1)">' + escapeHtml(s.id.slice(0, 4)) + '</a>' +
          '<button class="btn sm danger" title="销毁此会话" onclick="termKillOne(\'' + s.id + '\')">×</button></span>').join('')
      : '<span class="hint" style="font-size:12px;line-height:26px">暂无活会话</span>';
    // 当前正看的会话已被服务端回收（进程退出/超时）→ 清绑并提示，避免对着幽灵 sid 重连
    if (termSid && !live.some(s => s.id === termSid)) {
      termDetach();
      if (term) term.write('\r\n\x1b[90m[当前会话已结束——点「＋ 新会话」重新开始]\x1b[0m');
    }
    return live;
  } catch (e) { return []; }
}

function termAutoAttach() {
  // 只接本实体的活会话；没有就清屏给提示（确保不残留上一实体画面）
  api('/api/term/sessions', { headers: termHeaders() }).then(d => {
    const mine = (d.sessions || []).filter(s => s.agent_id === chatPick && s.alive);
    if (mine.length) {
      termConnect(mine[mine.length - 1].id, chatPick);
      return;
    }
    // 确保 detached：切换实体时清除旧绑定，避免显示错误会话
    if (termSid && termSidAgent !== chatPick) termDetach();
    term.clear();
    const a = entityById(chatPick);
    term.write('\x1b[90m' + chatPick + ' · ' + (a?.name || chatPick) + ' 终端\x1b[0m\r\n');
    term.write('\x1b[90m提示：点「＋ 新会话」拉起 ' + (a?.name || chatPick) + ' 的原生终端\x1b[0m\r\n');
  });
}

async function termKill() { if (termSid) await termKillOne(termSid); }
async function termKillOne(sid) {
  // 乐观移除：先摘 DOM 芯片，用户即时看到"删掉了"，再与后端对账
  const el = $('termSessList');
  const chip = el && el.querySelector('.sess-item[data-sid="' + sid + '"]');
  if (chip) chip.remove();
  try {
    await api('/api/term/sessions/' + sid, { method: 'DELETE', headers: termHeaders() });
    toast('会话已销毁', 'ok');
    if (sid === termSid) termDetach();
    termRefreshList();
    termAutoAttach();
  } catch (e) {
    toast(e.message, 'err');
    termRefreshList();  // 404 等异常：以服务端真实状态重绘，DOM 不骗人
  }
}

async function openChatSession() {
  const box = $('chatMsgs');
  box.innerHTML = '';
  chatSessLoad();
  chatCwdLoad();
  const sid = localStorage.getItem(sessKey(chatPick));
  if (!sid) { box.innerHTML = '<div class="hint" style="margin:auto">开始新会话（' + escapeHtml(chatPick) + '）</div>'; return; }
  try {
    const d = await api('/api/sessions/' + encodeURIComponent(sid) + '/messages');
    for (const m of d.messages || []) {
      box.appendChild(renderChatMsg(m));
    }
  } catch (e) { localStorage.removeItem(sessKey(chatPick)); }
}

function renderChatMsg(m) {
  // v0.5.2 任务总结消息：role='summary' 走独立卡片（不回放成普通 assistant 文字）
  if (m.role === 'summary') {
    // 后端 summary.content 已是结构化 markdown（**做了什么** / **下一步** / 元信息行）
    // 这里做最小化渲染：转 <br/> + 把 **加粗** 转 <b>，不去碰其他字符
    const card = document.createElement('div');
    card.className = 'msg summary';
    let color = 'var(--text-1)';
    const text = m.content || '';
    if (text.includes('部分')) color = 'var(--warn)';
    if (text.includes('失败')) color = 'var(--danger-text)';
    // 极简 markdown → html
    let html = escapeHtml(text)
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br/>');
    card.innerHTML = `<div class="sum-head" style="color:${color}">${html.split('<br/>')[0]}</div><div class="sum-body">${html.split('<br/>').slice(1).join('<br/>')}</div>`;
    return card;
  }
  const el = document.createElement('div');
  el.className = 'msg ' + (m.role === 'user' ? 'user' : m.role === 'error' ? 'err' : 'assistant');
  el.textContent = m.content || '';
  // hub-self 工具环：meta.steps 内嵌时一并展示（折叠的 step 流）
  if (m.meta) {
    try {
      const meta = JSON.parse(m.meta);
      if (meta && Array.isArray(meta.steps) && meta.steps.length) {
        const det = document.createElement('details');
        det.style.cssText = 'margin-top:6px;font-size:12px;color:var(--text-2)';
        const sum = document.createElement('summary');
        sum.textContent = '工具调用 (' + meta.steps.filter(s => s.kind === 'toolcall').length + ')';
        det.appendChild(sum);
        det.appendChild(renderSteps(meta.steps.filter(s => s.kind !== 'answer')));
        el.appendChild(det);
      }
    } catch (e) { /* ignore */ }
  }
  return el;
}

async function chatSend() {
  const input = $('chatInput');
  const model = $('chatModel')?.value || '';
  const cwd = $('chatCwd')?.value || '';
  const tools = chatPick === 'hub-self' && $('chatToolsOn')?.checked;
  const repair = chatPick === 'hub-self' && $('chatRepairOn')?.checked;
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = $('chatMsgs');
  const me = document.createElement('div');
  me.className = 'msg user'; me.textContent = msg;
  box.appendChild(me); box.scrollTop = box.scrollHeight;
  let sid = localStorage.getItem(sessKey(chatPick));
  if (!sid) { sid = Math.random().toString(36).slice(2, 14); localStorage.setItem(sessKey(chatPick), sid); }
  const busy = document.createElement('div');
  busy.className = 'msg assistant'; busy.textContent = '⟳ 回复中…';
  box.appendChild(busy);
  const body = { message: msg, session_id: sid, model: model, cwd: cwd || null };
  if (tools) body.tools = true;
  if (repair) body.repair_mode = true;
  // v0.5 流式：hub-self 走 /chat/stream SSE 实时显示思考/工具/答案（修"假死"）
  if (chatPick === 'hub-self') {
    return chatSendStream(busy, box, body, sid);
  }
  // 其他 Agent：仍走老 /chat
  try {
    const d = await api('/api/agents/' + encodeURIComponent(chatPick) + '/chat',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    busy.className = 'msg ' + (d.success ? 'assistant' : 'err');
    let txt = (d.response || d.error || JSON.stringify(d.hint || d));
    if (d.model) txt += '\n· model: ' + d.model;
    if (d.usage) txt += '\n· tokens: in ' + (d.usage.prompt_tokens || d.usage.input_tokens || '-') + ' / out ' + (d.usage.completion_tokens || d.usage.output_tokens || '-');
    busy.textContent = txt;
    // hub-self 工具环：step 流展示（折叠 details 在 busy 下方）
    if (Array.isArray(d.steps) && d.steps.length) {
      const det = document.createElement('details');
      det.style.cssText = 'margin-top:6px;font-size:12px;color:var(--text-2)';
      det.open = true;
      const sum = document.createElement('summary');
      sum.textContent = '工具调用 (' + d.steps.filter(s => s.kind === 'toolcall').length + ' 步)';
      det.appendChild(sum);
      det.appendChild(renderSteps(d.steps.filter(s => s.kind !== 'answer')));
      busy.appendChild(det);
    }
    chatSessLoad();  // 刷新会话列表（title 等信息更新）
  } catch (e) { busy.className = 'msg err'; busy.textContent = '[错误] ' + e.message; }
  box.scrollTop = box.scrollHeight;
}

// v0.5 SSE 流式版本：SSE 拿到 step 事件就 append 到 busy 内，实时渲染思考/工具/答案
function chatSendStream(busy, box, body, sid) {
  // 一次性注入流式样式
  if (!document.getElementById('stream-css')) {
    const s = document.createElement('style');
    s.id = 'stream-css';
    s.textContent = `
.stream-status { font-size:12px; color:var(--text-2); padding:4px 0; font-family:monospace; }
.stream-steps { margin-top:6px; font-size:12px; }
.stream-step { padding:3px 0; border-left:2px solid var(--line); padding-left:8px; margin:2px 0; font-family:monospace; word-break:break-all; }
.stream-step.thought { border-left-color:var(--muted); color:var(--muted); font-style:italic; }
.stream-step.toolcall { border-left-color:var(--text-2); color:var(--text-1); }
.stream-step.toolresult { border-left-color:var(--text-2); color:var(--text-2); }
.stream-step.answer { border-left-color:var(--text-1); color:var(--text-1); border-left-width:3px; font-weight:500; }
.stream-step .badge { display:inline-block; width:1.5em; }
.msg.summary { background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:8px 12px; margin:4px 0; font-size:12.5px; color:var(--text-1); line-height:1.45; }
.msg.summary .sum-head { font-size:14px; font-weight:600; display:inline-block; margin:0; padding:0 8px 0 0; border-right:1px solid var(--border); }
.msg.summary .sum-meta { display:inline-block; color:var(--text-2); font-size:12px; margin:0; padding:0; }
.msg.summary .sum-meta b { color:var(--text-1); font-weight:500; }
.msg.summary .sum-meta code { background:var(--bg); color:var(--text-1); padding:0 3px; border-radius:2px; font-size:11.5px; }
.msg.summary .sum-tbl { display:table; border-collapse:collapse; margin:0 !important; padding:0; font-size:12.5px; line-height:1.5; table-layout:fixed; width:auto; min-width:240px; max-width:100%; border-spacing:0; }
.msg.summary .sum-tbl + .sum-tbl { margin:2px 0 0 0 !important; }
.msg.summary .sum-tbl th { color:var(--text-2); font-weight:400; padding:0 6px 0 0; text-align:left; width:48px; min-width:48px; white-space:nowrap; vertical-align:top; }
.msg.summary .sum-tbl td { padding:0 0 0 6px; margin:0; color:var(--text-1); border-left:1px dotted var(--border); overflow-wrap:anywhere; word-break:break-word; vertical-align:top; }
.msg.summary .sum-tbl td code { background:var(--bg); color:var(--text-1); padding:0 3px; border-radius:2px; font-size:11.5px; word-break:break-all; overflow-wrap:anywhere; }
/* 窄屏：表格 100% 宽 */
@media (max-width: 500px) {
  .msg.summary .sum-tbl { width:100%; }
}
.msg.summary .sum-tail { display:block; margin:6px 0 0 0; padding:6px 0 0 0; border-top:1px dashed var(--border); color:var(--text-2); font-size:12px; line-height:1.5; }
.msg.summary .sum-tail b { color:var(--text-1); }
.msg.summary .sum-row { display:flex; gap:8px; padding:2px 0; }
.msg.summary .sum-row .k { color:var(--text-2); min-width:48px; flex-shrink:0; }
.msg.summary .sum-row .v { color:var(--text-1); }
.msg.summary .sum-row .pill { display:inline-block; background:var(--surface-2); color:var(--text-1); padding:1px 6px; border-radius:8px; margin-right:3px; font-size:11px; }
.msg.summary .sum-row .muted { color:var(--muted); }
.msg.summary .sum-row code { background:var(--bg); color:var(--text-1); padding:0 4px; border-radius:3px; }
.msg.summary .sum-preview { margin-top:6px; padding-top:6px; border-top:1px dashed var(--border); color:var(--text-2); font-style:italic; word-break:break-all; }
.msg.summary .sum-section { margin:6px 0 4px 0; padding-left:8px; border-left:2px solid var(--border); }
.msg.summary .sum-section b { color:var(--text-1); display:block; margin-bottom:2px; font-size:12px; }
.msg.summary .sum-section ul { margin:0; padding-left:18px; color:var(--text-1); }
.msg.summary .sum-section li { padding:1px 0; }
.msg.summary .sum-section code { background:var(--bg); color:var(--text-1); padding:0 4px; border-radius:3px; font-size:11px; }
.msg.summary .sum-fail { border-left-color:var(--danger-text); }
.msg.summary .sum-fail b { color:var(--danger-text); }
.msg.summary .sum-fail li { color:var(--danger-text); }
`;
    document.head.appendChild(s);
  }
  // busy 改容器：上 status bar + 下 step 流（每步都 append）+ 终态 message
  busy.textContent = '';
  const status = document.createElement('div');
  status.className = 'stream-status';
  status.textContent = '思考中…';
  const stepsBox = document.createElement('div');
  stepsBox.className = 'stream-steps';
  busy.appendChild(status);
  busy.appendChild(stepsBox);
  const allSteps = [];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function appendStep(s) {
    allSteps.push(s);
    const row = document.createElement('div');
    row.className = 'stream-step ' + (s.kind || '');
    if (s.kind === 'thought') {
      row.innerHTML = '<span class="badge">◇</span><span class="text">' + esc((s.content||'').slice(0,200)) + '</span>';
    } else if (s.kind === 'toolcall') {
      const args = JSON.stringify(s.tool_input || {}, null, 0).slice(0,200);
      row.innerHTML = '<span class="badge">⚙</span><span class="text">调用 <b>' + esc(s.tool) + '</b>(<code>' + esc(args) + '</code>)</span>';
    } else if (s.kind === 'toolresult') {
      // v0.5.3 验证错误内联醒目（红框 + 字段/期望/实际）
      let valHtml = '';
      try {
        const j = JSON.parse(s.content || '');
        if (j && j.error_type === 'validation') {
          const gotStr = (j.got !== null && j.got !== undefined && j.got !== '') ? ` 实际 ${esc(j.got)}` : '';
          valHtml = '<div style="color:var(--danger-text);font-weight:600;background:var(--danger-bg);padding:3px 6px;border-radius:3px;margin:2px 0;">❌ 参数错误: 字段 ' + esc(j.field||'?') + ' 期望 ' + esc(j.expected||'?') + gotStr + '（tool=' + esc(j.tool||'?') + '）</div>';
        } else if (j && (j.error_type === 'not_found' || j.error_type === 'rate_limit' || j.error_type === 'acl' || j.error_type === 'timeout')) {
          valHtml = '<div style="color:var(--warn);font-weight:600;background:var(--warn-bg);padding:3px 6px;border-radius:3px;margin:2px 0;">⚠ ' + esc(j.error_type) + ': ' + esc(j.error || '') + '</div>';
        }
      } catch (e) {}
      row.innerHTML = '<span class="badge">▾</span><span class="text">' + esc((s.content||'').slice(0,200)) + '</span>' + valHtml;
    } else if (s.kind === 'answer') {
      row.innerHTML = '<span class="badge">◆</span><span class="text">' + esc(s.content||'') + '</span>';
    } else {
      row.textContent = JSON.stringify(s).slice(0,200);
    }
    stepsBox.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }
  fetch('/api/agents/hub-self/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  }).then(r => {
    if (!r.ok || !r.body) throw new Error('HTTP ' + r.status);
    const rd = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    function pump() {
      return rd.read().then(({value, done}) => {
        if (done) return;
        buf += dec.decode(value, { stream: true });
        // SSE 一帧以 \n\n 切分
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = frame.split('\n').find(l => l.startsWith('data: '));
          if (!line) continue;
          try {
            const obj = JSON.parse(line.slice(6));
            if (obj.event === 'start') {
              status.textContent = '思考中… (' + (obj.session_id || sid).slice(0,8) + ')';
            } else if (obj.event === 'step') {
              appendStep(obj.step);
              const tc = allSteps.filter(s => s.kind === 'toolcall').length;
              status.textContent = '▸ 进度: 思考 ' + allSteps.filter(s=>s.kind==='thought').length + ' / 工具 ' + tc + ' / 结果 ' + allSteps.filter(s=>s.kind==='toolresult').length;
            } else if (obj.event === 'final') {
              busy.className = 'msg ' + (obj.success ? 'assistant' : 'err');
              busy.textContent = obj.response || obj.error || JSON.stringify(obj.hint || obj);
              if (obj.model) busy.textContent += '\n· model: ' + obj.model;
              // v0.5.2 任务总结：把 summary 渲染为独立卡片（紧跟 assistant 答案之后）
              if (obj.summary) {
                const sumCard = renderSummaryCard(obj.summary);
                if (sumCard) box.appendChild(sumCard);
              }
              status.remove();
              chatSessLoad();
            } else if (obj.event === 'error') {
              busy.className = 'msg err';
              busy.textContent = '[错误] ' + (obj.error || 'unknown');
              status.remove();
            }
          } catch (e) { /* ignore frame */ }
        }
        return pump();
      }).catch(e => {
        status.remove();
        busy.className = 'msg err';
        busy.textContent = '[流式中断] ' + e.message + '（已自动回退非流式）';
        // 回退非流式
        api('/api/agents/hub-self/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
          .then(d => { busy.textContent = d.response || d.error || JSON.stringify(d); busy.className = 'msg ' + (d.success ? 'assistant' : 'err'); chatSessLoad(); });
      });
    }
    return pump();
  });
  box.scrollTop = box.scrollHeight;
}

/* ── 模型选择器（动态拉取 CCR /v1/models）── */
let CHAT_MODELS = [];
async function loadChatModels(force) {
  const sel = $('chatModel');
  if (!sel) return;
  const cached = !force && CHAT_MODELS.length && (Date.now() - (CHAT_MODELS._ts || 0) < 60000);
  if (!cached) {
    try {
      const d = await api('/api/models');
      CHAT_MODELS = (d.models || []).map(m => ({ ...m, _groups: d.groups || {} }));
      CHAT_MODELS._ts = Date.now();
      CHAT_MODELS._src = d.source;
    } catch (e) {
      sel.innerHTML = '<option value="">模型加载失败</option>';
      return;
    }
  }
  // 当前 agent 默认 model（localStorage 记忆）
  const saved = localStorage.getItem('hub.model.' + chatPick) || '';
  // 按 vendor 分组
  const groups = (CHAT_MODELS._groups) || {};
  const groupedKeys = Object.keys(groups);
  let html = '<option value="">默认（网关路由）</option>';
  for (const gk of groupedKeys) {
    html += '<optgroup label="' + escapeHtml(gk) + '">';
    for (const mid of groups[gk]) {
      const m = CHAT_MODELS.find(x => x.id === mid);
      const label = m && m.name && m.name !== mid ? (mid + ' — ' + m.name) : mid;
      html += '<option value="' + escapeHtml(mid) + '"' + (mid === saved ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
    }
    html += '</optgroup>';
  }
  // 兜底：未分组的也展示
  const grouped = new Set(groupedKeys.flatMap(k => groups[k]));
  const leftovers = CHAT_MODELS.filter(m => !grouped.has(m.id));
  if (leftovers.length) {
    html += '<optgroup label="其他">';
    for (const m of leftovers) {
      html += '<option value="' + escapeHtml(m.id) + '"' + (m.id === saved ? ' selected' : '') + '>' + escapeHtml(m.name || m.id) + '</option>';
    }
    html += '</optgroup>';
  }
  sel.innerHTML = html;
}
$('chatModel')?.addEventListener?.('change', e => {
  localStorage.setItem('hub.model.' + chatPick, e.target.value);
});

/* ── 工作目录（CWD 选择器）── */
const DEFAULT_CWDS = ['/fs/1000/ftp/技术文档', '/home/gztxt', '/home/gztxt/agent-hub', '/vol1/1000/技术文档', '/tmp'];
function chatCwdLoad() {
  const sel = $('chatCwd');
  if (!sel) return;
  // 1) agent 画像默认 cwd（hub-self/claude/jcode/hermes/bash）
  const a = entityById(chatPick);
  const profCwd = a && a.working_dir;
  // 2) localStorage 历史选择
  const saved = localStorage.getItem('hub.cwd.' + chatPick) || '';
  // 3) 合并去重
  const seen = new Set();
  const list = [];
  if (profCwd) { list.push({ v: profCwd, label: profCwd + ' ★画像' }); seen.add(profCwd); }
  if (saved && !seen.has(saved)) { list.push({ v: saved, label: saved + ' ★最近' }); seen.add(saved); }
  for (const d of DEFAULT_CWDS) {
    if (!seen.has(d)) { list.push({ v: d, label: d }); seen.add(d); }
  }
  sel.innerHTML = list.map(x => '<option value="' + escapeHtml(x.v) + '"' +
    (x.v === saved || (!saved && x.v === profCwd) ? ' selected' : '') + '>' + escapeHtml(x.label) + '</option>').join('');
}
function chatCwdCustom() {
  const cur = $('chatCwd')?.value || '';
  const v = prompt('自定义工作目录（绝对路径）', cur);
  if (v && v.trim()) {
    $('chatCwd').value = v.trim();
    localStorage.setItem('hub.cwd.' + chatPick, v.trim());
    toast('已设 cwd: ' + v.trim(), 'ok');
  }
}
$('chatCwd')?.addEventListener?.('change', e => {
  localStorage.setItem('hub.cwd.' + chatPick, e.target.value);
});

/* ── 会话管理 ── */
let CHAT_SESSIONS = [];
async function chatSessLoad() {
  const sel = $('chatSessList');
  if (!sel) return;
  const cur = localStorage.getItem(sessKey(chatPick)) || '';
  try {
    const d = await api('/api/sessions?agent_id=' + encodeURIComponent(chatPick) + '&limit=30');
    CHAT_SESSIONS = d.sessions || [];
    const opts = ['<option value="">当前会话</option>'];
    opts.push('<option value="__new__">＋ 新会话</option>');
    for (const s of CHAT_SESSIONS) {
      const title = (s.title || '未命名').slice(0, 24);
      const ts = (s.updated_at || '').slice(5, 16).replace('T', ' ');
      opts.push('<option value="' + escapeHtml(s.id) + '"' + (s.id === cur ? ' selected' : '') + '>' +
        escapeHtml(ts + ' · ' + title) + '</option>');
    }
    sel.innerHTML = opts.join('');
  } catch (e) { /* ignore */ }
}
function chatSessNew() {
  localStorage.removeItem(sessKey(chatPick));
  openChatSession();
  toast('已开新会话', 'ok');
}
async function chatSessRename() {
  const sid = localStorage.getItem(sessKey(chatPick));
  if (!sid) return toast('当前无会话', 'err');
  const cur = CHAT_SESSIONS.find(s => s.id === sid);
  const title = prompt('新标题', (cur && cur.title) || '');
  if (!title) return;
  try {
    await api('/api/sessions/' + encodeURIComponent(sid), { method: 'PATCH',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title }) });
    chatSessLoad();
    toast('已重命名', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
$('chatSessList')?.addEventListener?.('change', async e => {
  const v = e.target.value;
  if (v === '__new__') { chatSessNew(); return; }
  if (!v) return;
  localStorage.setItem(sessKey(chatPick), v);
  openChatSession();
});
// T7：删除当前会话（后端 DELETE /api/sessions/{id} 连同消息一并清理）
async function chatSessDel() {
  const sid = localStorage.getItem(sessKey(chatPick));
  if (!sid) return toast('当前无会话', 'err');
  if (!confirm('删除当前会话 ' + sid.slice(0, 8) + ' 及其全部消息？不可恢复。')) return;
  try {
    await api('/api/sessions/' + encodeURIComponent(sid), { method: 'DELETE' });
    localStorage.removeItem(sessKey(chatPick));
    openChatSession();
    toast('会话已删除', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* applyChatMode 已内置：切换到 chat 模式时三联动刷新 */

/* ── 记忆中心 ─────────────────────────────────────── */

async function loadMemories() {
  const p = new URLSearchParams();
  if ($('memQ').value.trim()) p.set('q', $('memQ').value.trim());
  if ($('memCat').value) p.set('category', $('memCat').value);
  try {
    const d = await api('/api/memory/l1?' + p.toString());
    $('memList').innerHTML = (d.memories || []).map(m =>
      '<div class="mem-item"><span class="tag ' + m.category + '" style="align-self:flex-start">' + m.category + '</span>' +
      '<p>' + escapeHtml(m.content) + '<br><span class="hint">' + escapeHtml(m.source || '') + ' · ' + (m.created_at || '').slice(0, 10) + '</span></p>' +
      '<button class="btn sm danger" onclick="delMemory(' + m.id + ')">×</button></div>').join('') ||
      '<div class="hint">空空如也。可手动添加，或由指挥官/外部 Hook 写入。</div>';
  } catch (e) { toast(e.message, 'err'); }
}
async function addMemory() {
  const c = $('memNew').value.trim();
  if (!c) return;
  try {
    await api('/api/memory/l1', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: c, category: 'fact', source: 'web' }) });
    $('memNew').value = ''; toast('已保存', 'ok'); loadMemories();
  } catch (e) { toast(e.message, 'err'); }
}
async function delMemory(id) {
  if (!confirm('删除这条记忆？')) return;
  try { await api('/api/memory/l1/' + id, { method: 'DELETE' }); loadMemories(); } catch (e) { toast(e.message, 'err'); }
}

async function loadDoc(lv) {
  try {
    const d = await api('/api/memory/' + lv);
    $(lv + 'content').value = d.content || '';
    $(lv + 'manual').value = d.manual || '';
  } catch (e) { /* 首次为空 */ }
}
async function saveDoc(lv) {
  const body = {};
  if (lv === 'l3') body.content = $(lv + 'content').value;
  body.manual = $(lv + 'manual').value;
  try { await api('/api/memory/' + lv, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); toast(lv.toUpperCase() + ' 已保存', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}
async function rebuildL2() {
  toast('LLM 压缩近30天 L1 → L2，请稍候…');
  try { const d = await api('/api/memory/l2/rebuild', { method: 'POST' }); toast('L2 重建完成（' + d.items + ' 条，LLM: ' + d.llm + '）', 'ok'); loadDoc('l2'); }
  catch (e) { toast(e.message, 'err'); }
}
async function previewCtx() {
  try {
    const d = await api('/api/memory/context?q=' + encodeURIComponent($('memQ').value.trim()));
    $('ctxPanel').style.display = 'block';
    $('ctxBody').textContent = d.context || '（空）';
  } catch (e) { toast(e.message, 'err'); }
}

/* ── 端口 ─────────────────────────────────────────── */

async function loadPorts() {
  try { const d = await api('/api/ports'); PORTS = d.listeners || []; renderPorts(); }
  catch (e) { toast(e.message, 'err'); }
}
function renderPorts() {
  const f = ($('portFilter') ? $('portFilter').value.trim() : '').toLowerCase();
  const rows = PORTS.filter(r => !f || (r.port + ' ' + (r.process || '') + ' ' + r.address + ' ' + (r.agent || '')).toLowerCase().includes(f));
  $('portsBody').innerHTML = rows.map(r =>
    '<tr><td>' + r.proto + '</td><td><b>' + r.port + '</b></td><td style="font-family:monospace;font-size:12px">' + escapeHtml(r.address) + '</td>' +
    '<td>' + (r.pid || '-') + '</td><td>' + escapeHtml(r.process || '-') + '</td>' +
    '<td>' + (r.agent ? '<span class="tag agent">' + escapeHtml(r.agent) + '</span>' : '') + '</td></tr>').join('');
}

/* ── 遥测 ─────────────────────────────────────────── */

async function loadTelemetry() {
  $('curlExample').textContent =
    '# Claude/pi 等 Agent 会话结束后上报用量（同 session 重报=覆盖）\n' +
    "curl -X POST http://" + location.hostname + ":3102/telemetry/events/claude \\\n" +
    "  -H 'Content-Type: application/json' \\\n" +
    "  -d '{\"session_id\":\"abc123\",\"event\":\"session_usage\",\"cwd\":\"/path/proj\",\n" +
    "       \"usage\":{\"input_tokens\":1088,\"output_tokens\":256,\"cached_tokens\":1024},\n" +
    "       \"usage_scope\":\"session\"}'";
  try {
    const u = await api('/telemetry/usage/summary');
    const rows = Object.entries(u.by_source || {});
    $('usageTable').innerHTML = rows.length ?
      '<table><thead><tr><th>来源</th><th>会话</th><th>输入</th><th>输出</th><th>缓存命中</th></tr></thead><tbody>' +
      rows.map(([k, v]) => '<tr><td><b>' + k + '</b></td><td>' + v.sessions + '</td><td>' + v.input_tokens + '</td><td>' + v.output_tokens + '</td><td>' + v.cached_tokens + '</td></tr>').join('') +
      '</tbody></table>' : '<div class="hint">暂无 session_usage 数据</div>';
    const prof = u.profile || [];
    $('profTable').innerHTML = prof.length ?
      '<table><thead><tr><th>来源</th><th>对象</th><th>次数</th><th>成功率</th><th>均耗时</th><th>峰值</th></tr></thead><tbody>' +
      prof.map(p => '<tr><td>' + p.source + '</td><td><b>' + escapeHtml(p.subject) + '</b></td><td>' + p.calls +
        '</td><td>' + (p.success_rate >= 99 ? '●' : p.success_rate >= 80 ? '◐' : '▲') + ' ' + p.success_rate + '%</td>' +
        '<td>' + p.avg_ms + 'ms</td><td>' + (p.max_ms || '-') + 'ms</td></tr>').join('') + '</tbody></table>'
      : '<div class="hint">暂无画像数据（对话/指挥官/DAG/定时执行后自动生成）</div>';
    const e = await api('/telemetry/events?limit=20');
    $('eventTable').innerHTML = '<table><thead><tr><th>时间</th><th>来源</th><th>会话</th><th>事件</th></tr></thead><tbody>' +
      (e.events || []).map(x => '<tr><td>' + (x.created_at || '').slice(5, 16).replace('T', ' ') + '</td><td>' + x.source + '</td><td style="font-family:monospace;font-size:12px">' + escapeHtml((x.session_id || '').slice(0, 14)) + '</td><td>' + x.event + '</td></tr>').join('') +
      '</tbody></table>';
  } catch (err) { toast(err.message, 'err'); }
}

/* ── S2 自动扫描 ──────────────────────────────────── */

async function runScan() {
  toast('扫描发现源（docker / systemd / CLI 名单）…');
  try {
    const d = await api('/api/scan/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"auto_register":true}' });
    toast('发现 ' + d.found.length + '，新增 ' + d.added.length + '，已存在 ' + d.skipped_existing.length, 'ok');
    loadAgents();
  } catch (e) { toast(e.message, 'err'); }
}

/* ── S3 协同 DAG ──────────────────────────────────── */

let currentRun = null;

async function decomposeRun() {
  const goal = $('taskGoal').value.trim();
  if (!goal) return toast('请输入目标', 'err');
  const btn = $('btnDecompose'); btn.disabled = true; btn.textContent = '⟳ 拆解中…';
  try {
    const body = { goal: goal, auto_run: true };
    if ($('taskAgent').value) body.default_agent = $('taskAgent').value;
    const d = await api('/api/tasks/decompose', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    currentRun = d.run_id;
    toast('已拆解 ' + d.tasks.length + ' 个子任务并启动', 'ok');
    loadRuns(); openRun(d.run_id);
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '拆解并启动';
}

async function loadRuns() {
  try {
    const d = await api('/api/tasks/runs');
    $('runsBody').innerHTML = (d.runs || []).map(r =>
      '<tr><td style="font-family:monospace">' + r.run_id + '</td>' +
      '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(r.goal || '') + '">' + escapeHtml((r.goal || '').slice(0, 40)) + '</td>' +
      '<td>' + ({ running: '◉执行中', success: '●成功', failed: '▲失败', partial: '◐部分', pending: '○待跑' }[r.state] || r.state) + '</td>' +
      '<td>' + r.success + '/' + r.total + '</td><td class="hint">' + (r.created_at || '').slice(5, 16).replace('T', ' ') + '</td>' +
      '<td><button class="btn sm ghost" onclick="openRun(\'' + r.run_id + '\')">查看</button></td></tr>').join('') ||
      '<tr><td colspan="6" class="hint">尚无协同任务</td></tr>';
  } catch (e) { /* ignore */ }
}

async function openRun(runId) {
  currentRun = runId;
  try {
    const d = await api('/api/tasks/' + runId);
    $('runPanel').style.display = 'block';
    $('runTitle').textContent = 'Run ' + runId + ' · ' + (d.goal || '').slice(0, 60);
    renderTaskTable(d.tasks);
    renderDag(d.tasks);
  } catch (e) { toast(e.message, 'err'); }
}

// v0.7.1 白底：节点描边用深灰，fill 由该色 +22 透明度派生
const TASK_COLORS = { pending: '#767676', running: '#333333', success: '#111111', failed: '#000000', blocked: '#909090' };

function renderTaskTable(tasks) {
  $('taskBody').innerHTML = tasks.map(t =>
    '<tr><td><b>' + t.task_id + '</b></td><td>' + (t.agent_id || '-') + '</td>' +
    '<td>' + (t.deps || []).join(',') + '</td><td>' + t.status + '</td>' +
    '<td>' + (t.duration_ms != null ? t.duration_ms + 'ms' : '-') + '</td>' +
    '<td><details><summary class="hint">' + escapeHtml((t.output || t.error || '').slice(0, 50)) + '</summary><pre style="white-space:pre-wrap;font-size:12px;max-height:220px;overflow:auto">' + escapeHtml(t.output || t.error || '') + '</pre></details></td></tr>').join('');
}

function renderDag(tasks) {
  const byId = {};
  tasks.forEach(t => byId[t.task_id] = t);
  const depthMemo = {};
  function depth(id, guard) {
    guard = guard || new Set();
    if (depthMemo[id] != null) return depthMemo[id];
    if (guard.has(id)) return 0;
    guard.add(id);
    const t = byId[id];
    const dv = (t && t.deps && t.deps.length) ? 1 + Math.max(...t.deps.map(d => depth(d, guard))) : 0;
    depthMemo[id] = dv;
    return dv;
  }
  const layers = {};
  tasks.forEach(t => { const L = depth(t.task_id); (layers[L] = layers[L] || []).push(t); });
  const NW = 170, NH = 56, GX = 220, GY = 86;
  const maxRows = Math.max(1, ...Object.values(layers).map(l => l.length));
  const W = (Math.max(...Object.keys(layers).map(Number), 0) + 1) * GX + 40;
  const H = maxRows * GY + 20;
  const pos = {};
  Object.entries(layers).forEach(([L, list]) => list.forEach((t, i) => { pos[t.task_id] = { x: 30 + L * GX, y: 20 + i * GY }; }));
  let svg = '<svg width="' + W + '" height="' + H + '" style="min-width:' + W + 'px">';
  svg += '<defs><marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="#808080"/></marker></defs>';
  tasks.forEach(t => (t.deps || []).forEach(dp => {
    const a = pos[dp], b = pos[t.task_id];
    if (a && b) svg += '<line x1="' + (a.x + NW) + '" y1="' + (a.y + NH / 2) + '" x2="' + b.x + '" y2="' + (b.y + NH / 2) + '" stroke="#808080" stroke-width="1.5" marker-end="url(#arw)"/>';
  }));
  tasks.forEach(t => {
    const p = pos[t.task_id];
    svg += '<g><rect x="' + p.x + '" y="' + p.y + '" width="' + NW + '" height="' + NH + '" rx="10" fill="' + (TASK_COLORS[t.status] || 'var(--border)') + '22" stroke="' + (TASK_COLORS[t.status] || 'var(--border)') + '" stroke-width="1.5"/>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 18) + '" fill="#111111" font-size="12" font-weight="bold">' + t.task_id + ' · ' + (t.agent_id || '') + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 34) + '" fill="#555555" font-size="12">' + escapeHtml((t.prompt || '').slice(0, 18)) + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 48) + '" fill="#333333" font-size="12">' + t.status + (t.duration_ms != null ? ' · ' + Math.round(t.duration_ms / 100) / 10 + 's' : '') + '</text></g>';
  });
  svg += '</svg>';
  $('dagSvg').innerHTML = svg;
}

async function startRun(id) { if (!id) return; try { await api('/api/tasks/' + id + '/start', { method: 'POST' }); toast('已调度', 'ok'); setTimeout(() => openRun(id), 1200); } catch (e) { toast(e.message, 'err'); } }
async function retryRun(id) { if (!id) return; try { await api('/api/tasks/' + id + '/retry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); toast('失败项已重置', 'ok'); setTimeout(() => openRun(id), 1200); } catch (e) { toast(e.message, 'err'); } }
async function delRun(id) { if (!id || !confirm('删除 run ' + id + '？')) return; try { await api('/api/tasks/' + id, { method: 'DELETE' }); currentRun = null; $('runPanel').style.display = 'none'; loadRuns(); } catch (e) { toast(e.message, 'err'); } }

/* ── S4 定时任务 ──────────────────────────────────── */

async function createJob() {
  const body = { name: $('jobName').value.trim(), cron: $('jobCron').value.trim(), kind: $('jobKind').value, payload: $('jobPayload').value.trim() };
  if (!$('jobName').value.trim() || !body.cron || !body.payload) return toast('名称/cron/载荷必填', 'err');
  if ($('jobAgent').value) body.agent_id = $('jobAgent').value;
  try {
    await api('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    toast('已创建', 'ok'); $('jobName').value = $('jobCron').value = $('jobPayload').value = '';
    loadJobs();
  } catch (e) { toast(e.message, 'err'); }
}
function jobKindChange() {
  const k = $('jobKind').value;
  $('jobAgent').style.display = k === 'agent_prompt' ? '' : 'none';
  $('jobPayload').placeholder = k === 'shell' ? '白名单命令，如 echo hello' : k === 'agent_prompt' ? '发给 Agent 的 Prompt' : 'http://127.0.0.1:3102/health';
}
async function loadJobs() {
  try {
    const d = await api('/api/jobs');
    $('jobAllow').textContent = 'shell 白名单: ' + (d.shell_allow || []).join(', ');
    $('jobsBody').innerHTML = (d.jobs || []).map(j =>
      '<tr><td><b>' + escapeHtml(j.name) + '</b><br><span class="hint">' + j.id + '</span></td>' +
      '<td style="font-family:monospace">' + j.cron + '</td><td>' + j.kind + '</td>' +
      '<td><button class="btn sm ' + (j.enabled ? '' : 'ghost') + '" onclick="toggleJob(\'' + j.id + '\',' + (j.enabled ? 0 : 1) + ')">' + (j.enabled ? '✔ 启用' : '‖ 停用') + '</button></td>' +
      '<td class="hint">' + (j.last_run || '').slice(5, 16).replace('T', ' ') + '</td>' +
      '<td>' + (j.last_status === 'success' ? '●' : j.last_status === 'fail' ? '▲' : '-') + '</td>' +
      '<td style="max-width:220px"><details><summary class="hint">' + escapeHtml((j.last_result || '').slice(0, 30)) + '</summary><pre style="font-size:12px;white-space:pre-wrap">' + escapeHtml(j.last_result || '') + '</pre></details></td>' +
      '<td style="white-space:nowrap"><button class="btn sm ghost" onclick="runJobNow(\'' + j.id + '\')">▸ 立即</button> ' +
      '<button class="btn sm danger" onclick="delJob(\'' + j.id + '\')">×</button></td></tr>').join('') ||
      '<tr><td colspan="8" class="hint">暂无任务</td></tr>';
  } catch (e) { toast(e.message, 'err'); }
}
async function toggleJob(id, en) { try { await api('/api/jobs/' + id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !!en }) }); loadJobs(); } catch (e) { toast(e.message, 'err'); } }
async function runJobNow(id) { toast('执行中…'); try { const d = await api('/api/jobs/' + id + '/run', { method: 'POST' }); toast(d.status === 'success' ? '执行成功: ' + (d.result || '').slice(0, 60) : '执行失败: ' + (d.result || ''), d.status === 'success' ? 'ok' : 'err'); loadJobs(); } catch (e) { toast(e.message, 'err'); } }
async function delJob(id) { if (!confirm('删除任务 ' + id + '？')) return; try { await api('/api/jobs/' + id, { method: 'DELETE' }); loadJobs(); } catch (e) { toast(e.message, 'err'); } }

/* ── S4 MCP 网关 ──────────────────────────────────── */

let mcpToolSel = null;

async function addServer() {
  const body = { name: $('mcName').value.trim(), transport: $('mcTransport').value };
  const argsRaw = $('mcArgs').value.trim();
  if (!body.name) return toast('名称必填', 'err');
  if (body.transport === 'stdio') {
    if (!$('mcCmd').value.trim()) return toast('stdio 需 command', 'err');
    body.command = $('mcCmd').value.trim();
    body.args = argsRaw ? argsRaw.split(/\s+/) : [];
  } else {
    if (!argsRaw) return toast('http 需 URL', 'err');
    body.url = argsRaw;
  }
  try { await api('/mcp/servers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); toast('已注册，聚合刷新中…', 'ok'); $('mcName').value = $('mcCmd').value = $('mcArgs').value = ''; loadMcp(); } catch (e) { toast(e.message, 'err'); }
}
async function probeServer() {
  const cmd = $('mcCmd').value.trim();
  if (!cmd) return toast('先填 command', 'err');
  $('mcProbe').style.display = 'block'; $('mcProbe').textContent = '⟳ 连接探测…';
  try {
    const d = await api('/mcp/servers/probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd, args: $('mcArgs').value.trim() ? $('mcArgs').value.trim().split(/\s+/) : [] }) });
    $('mcProbe').textContent = JSON.stringify(d.tools, null, 1);
  } catch (e) { $('mcProbe').textContent = '探测失败: ' + e.message; }
}
async function delServer(id) {
  if (!confirm('删除 MCP server: ' + id + '？（聚合工具列表将随之刷新）')) return;
  try { await api('/mcp/servers/' + id, { method: 'DELETE' }); loadMcp(); } catch (e) { toast(e.message, 'err'); }
}
async function loadMcp() {
  try {
    const s = await api('/mcp/servers');
    $('mcServers').innerHTML = '<table><thead><tr><th>名称</th><th>传输</th><th>目标</th><th></th></tr></thead><tbody>' +
      (s.servers || []).map(x => '<tr><td><b>' + escapeHtml(x.name) + '</b></td><td>' + x.transport + '</td>' +
        '<td style="font-family:monospace;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis">' + escapeHtml(x.transport === 'stdio' ? (x.command || '') + ' ' + (x.args || []).join(' ') : x.url || '') + '</td>' +
        '<td><button class="btn sm danger" onclick="delServer(\'' + x.id + '\')">×</button></td></tr>').join('') +
      '</tbody></table>';
    $('mcTools').innerHTML = '⟳ 聚合工具中（stdio 会临时拉起进程）…';
    const t = await api('/mcp/tools');
    const errs = Object.entries(t.errors || {});
    $('mcTools').innerHTML = (t.tools || []).map(x =>
      '<div class="mem-item" style="cursor:pointer" onclick="pickTool(\'' + x.server + '\',\'' + x.name + '\')" id="mt_' + x.server + '_' + x.name + '"><span class="tag agent">' + escapeHtml(x.server_name) + '</span><p><b>' + x.name + '</b> <span class="hint">' + escapeHtml(x.description) + '</span></p></div>').join('') ||
      '<div class="hint">无工具——注册 server 后此处聚合</div>' +
      (errs.length ? '<div class="hint" style="color:var(--danger-text)">异常 server: ' + errs.map(x => x[0] + '(' + x[1].slice(0, 40) + ')').join('; ') + '</div>' : '');
  } catch (e) { $('mcTools').innerHTML = '<span style="color:var(--danger-text)">' + e.message + '</span>'; }
}
function pickTool(server, tool) {
  mcpToolSel = { server: server, tool: tool };
  document.querySelectorAll('[id^=mt_]').forEach(el => el.style.background = '');
  const el = document.getElementById('mt_' + server + '_' + tool);
  if (el) el.style.background = 'var(--surface-2)';
}

/* ── T3 MCP ACL 管理（契约见 src/mcpgw.py：POST {agent_id, tool_pattern, server_id?, allow}；GET {rules:[{id,agent_id,server_id,tool_pattern,allow}]}；DELETE /mcp/acl/{id}）── */
async function loadAcl() {
  const el = $('aclList');
  if (!el) return;
  try {
    const d = await api('/mcp/acl');
    el.innerHTML = (d.rules || []).map(r =>
      '<div class="mem-item"><span class="tag ' + (r.allow ? 'fact' : 'constraint') + '">' + (r.allow ? 'allow' : 'deny') + '</span>' +
      '<p><b>' + escapeHtml(r.agent_id) + '</b> · <code>' + escapeHtml(r.tool_pattern) + '</code>' +
      (r.server_id ? ' · server=' + escapeHtml(r.server_id) : ' · 任意server') + '</p>' +
      '<button class="btn sm danger" aria-label="删除规则 ' + r.id + '" onclick="delAcl(' + r.id + ')">×</button></div>').join('') ||
      '<div class="hint">暂无规则——无规则 = 所有 agent 默认放行（首次接入零摩擦）</div>';
  } catch (e) { el.innerHTML = '<span style="color:var(--danger-text)">' + escapeHtml(e.message) + '</span>'; }
}
async function addAcl() {
  const body = {
    agent_id: $('aclAgent').value.trim(),
    tool_pattern: $('aclPattern').value.trim(),
    allow: $('aclAllow').value === '1'
  };
  const sid = $('aclServer').value.trim();
  if (sid) body.server_id = sid;  // 后端按 id 或 name 解析（mcpgw.add_acl）
  if (!body.agent_id || !body.tool_pattern) return toast('agent_id 与工具模式必填', 'err');
  try {
    await api('/mcp/acl', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    toast('ACL 规则已添加', 'ok');
    $('aclAgent').value = $('aclPattern').value = $('aclServer').value = '';
    loadAcl();
  } catch (e) { toast(e.message, 'err'); }
}
async function delAcl(id) {
  try { await api('/mcp/acl/' + id, { method: 'DELETE' }); toast('规则已删除', 'ok'); loadAcl(); }
  catch (e) { toast(e.message, 'err'); }
}
async function callToolSel() {
  if (!mcpToolSel) return toast('先点击选择一个工具', 'err');
  let args = {};
  const raw = $('mcCallArgs').value.trim();
  if (raw) { try { args = JSON.parse(raw); } catch (e) { return toast('args 需为 JSON', 'err'); } }
  $('mcCallOut').style.display = 'block'; $('mcCallOut').textContent = '⟳ 调用中…';
  try {
    const d = await api('/mcp/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server: mcpToolSel.server, tool: mcpToolSel.tool, args: args, agent_id: 'manager' }) });
    $('mcCallOut').textContent = JSON.stringify(d, null, 1);
  } catch (e) { $('mcCallOut').textContent = '失败: ' + e.message; }
}

/* ── T8 命令面板（⌘K/Ctrl+K）：搜实体直达对话；/路径 对 hub-self 发 list_dir ── */
function openCmd() {
  $('cmdMask').classList.add('on');
  const i = $('cmdInput');
  i.value = '';
  renderCmdList('');
  i.focus();
}
function closeCmd() { $('cmdMask').classList.remove('on'); }
function renderCmdList(q) {
  const box = $('cmdList');
  if (q.startsWith('/')) {
    box.innerHTML = '<div class="cmd-item" onclick="cmdListDir(' + JSON.stringify(q).replace(/"/g, '&quot;') + ')">' +
      '<span>对 hub-self 执行 list_dir</span><span class="hint">' + escapeHtml(q) + '</span></div>';
    return;
  }
  const ql = q.toLowerCase();
  const items = AGENTS.filter(a => !ql || a.id.toLowerCase().includes(ql) || (a.name || '').toLowerCase().includes(ql)).slice(0, 12);
  box.innerHTML = items.map(a =>
    '<div class="cmd-item" onclick="cmdGo(\'' + a.id + '\')"><span>' + escapeHtml(a.name) + '</span><span class="hint">' + escapeHtml(a.id) + '</span></div>').join('') ||
    '<div class="hint" style="padding:8px">无匹配实体</div>';
}
function cmdGo(id) { closeCmd(); gotoChat(id); }
function cmdListDir(path) {
  closeCmd();
  gotoChat('hub-self', 'chat');  // 走已有对话通道，tools=true，不新造后端
  const cb = $('chatToolsOn');
  if (cb) cb.checked = true;
  $('chatInput').value = 'list_dir ' + path;
  chatSend();
}

/* ── 启动 ─────────────────────────────────────────── */

/* ── v0.7 左侧手风琴导航：单开模式 + 搜索 + 展开态持久化 ──
   20 个实体全部收拢进左栏（AGENTS 8 / 基础设施 12），系统功能仍走 go(page) ── */
const NAV_GROUPS = { agents: 'AGENTS', infra: '基础设施', system: '系统' };
const NAV_ICONS = { agents: '◈', infra: '▦', system: '⊞' };   // 收起成图标条时仍可辨认
const NAV_ORDER = ['agents', 'infra', 'system'];
const NAV_SUB_KINDS = [['gateway', '网关'], ['service', '服务'], ['tool', '工具'], ['memory', '记忆']];
const SYS_PAGES = [['ports', '端口', '≡'], ['telemetry', '遥测', '◉'], ['memory', '记忆中心', '▤'],
                   ['mcp', '工具', '⚙'], ['jobs', '定时', '⟳'], ['tasks', '协同', '⇄']];
const MODE_LABEL = { embed: '嵌入', term: '终端', chat: '对话', detail: '详情', open: '↗ 新窗口' };
const PAGE_LABELS = { classroom: '总览', chat: '统一对话', tasks: '协同', jobs: '定时',
                      memory: '记忆中心', mcp: '工具', ports: '端口', telemetry: '遥测' };
const navOpenStored = localStorage.getItem('hub.nav.open');
let navOpen = navOpenStored === null ? 'agents' : navOpenStored;   // 首屏默认展开 AGENTS；'' = 用户主动全收起
let curPage = '';
/* 排序：error > running > installed > stopped（异常置顶），同级按名称 */
const NAV_RANK = { error: 0, running: 1, installed: 2, stopped: 3 };
function navRank(a) { return NAV_RANK[a.status] == null ? 9 : NAV_RANK[a.status]; }
function navMatch(a, q) {
  const ql = q.toLowerCase();
  return String(a.name || '').toLowerCase().includes(ql) ||
         String(a.id || '').toLowerCase().includes(ql) ||
         String(a.port || '').includes(q);
}
// 搜索词高亮
function hlMatch(text, q) {
  if (!q) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const ql = q.toLowerCase();
  const lower = text.toLowerCase();
  let result = '', last = 0;
  let idx = lower.indexOf(ql);
  while (idx !== -1) {
    result += escaped.slice(last, idx) + '<mark>' + escaped.slice(idx, idx + q.length) + '</mark>';
    last = idx + q.length;
    idx = lower.indexOf(ql, last);
  }
  return result + escaped.slice(last);
}
function navItemHtml(a) {
  const st = seatStateOf(a);
  const on = (curPage === 'chat' && a.id === chatPick) ? ' on' : '';
  // 窄栏里名称会截断，title 里给全量信息（名称 · 状态 · 端口 · 描述）
  const tip = a.name + ' · ' + SEAT_LABELS[st] + (a.port ? ' · :' + a.port : '') + (a.description ? '\n' + a.description : '');
  // 搜索高亮：名称用 hlMatch，ID 和端口保持原样
  const nameHtml = hlMatch(a.name || a.id, ($('navSearch') ? $('navSearch').value.trim() : ''));
  // 操作按钮：按 entries 类型渲染（最多3个）
  const es = a.entries || [];
  const actBtns = [];
  if (es.some(e => e.type === 'embed')) actBtns.push('<span class="act-btn" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'embed\')" title="嵌入会话">◉</span>');
  if (es.some(e => e.type === 'term')) actBtns.push('<span class="act-btn" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'term\')" title="终端">⌨</span>');
  if (es.some(e => e.type === 'chat')) actBtns.push('<span class="act-btn" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'chat\')" title="对话">💬</span>');
  // 启动按钮：installed/stopped 状态的 agent
  if (a.kind === 'agent' && (st === 'installed' || st === 'stopped') && es.some(e => e.type === 'term')) {
    actBtns.push('<span class="act-btn start-btn" onclick="event.stopPropagation();startAgent(\'' + a.id + '\')" title="启动">▶</span>');
  }
  const actsHtml = actBtns.length ? '<span class="nav-acts">' + actBtns.join('') + '</span>' : '';
  return '<button class="nav-item' + on + '" data-entity="' + escapeHtml(a.id) + '"' +
    ' title="' + escapeHtml(tip) + '" aria-label="' + escapeHtml(a.name + ' ' + SEAT_LABELS[st]) + '">' +
    '<span class="s-badge ' + st + '"></span>' +
    '<span class="lbl">' + nameHtml + '</span>' +
    (a.port ? '<span class="nav-port">:' + a.port + '</span>' : '') +
    actsHtml +
    '<span class="nav-st">' + escapeHtml(SEAT_LABELS[st]) + '</span></button>';
}
// 启动 agent：创建新的终端会话
async function startAgent(id) {
  try {
    const d = await api('/api/term/sessions', { method: 'POST', headers: termHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ agent_id: id }) });
    toast('已拉起 ' + id + ' 终端', 'ok');
    gotoChat(id, 'term');
    setTimeout(() => termConnect(d.session.id, id), 100);
  } catch (e) { toast(e.message, 'err'); }
}
function renderNav() {
  const box = $('navTree');
  if (!box) return;
  const q = ($('navSearch') ? $('navSearch').value.trim() : '').toLowerCase();
  const all = AGENTS.filter(a => a.kind === 'agent');
  const infra = AGENTS.filter(a => a.kind !== 'agent');
  const lists = {
    agents: q ? all.filter(a => navMatch(a, q)) : all.slice().sort((x, y) => navRank(x) - navRank(y) || String(x.name||'').localeCompare(String(y.name||''), 'zh')),
    infra: q ? infra.filter(a => navMatch(a, q)) : infra.slice().sort((x, y) => navRank(x) - navRank(y) || String(x.name||'').localeCompare(String(y.name||''), 'zh')),
    system: q ? [] : SYS_PAGES,
  };
  // 搜索结果计数
  let totalMatch = 0;
  if (q) {
    totalMatch = all.filter(a => navMatch(a, q)).length + infra.filter(a => navMatch(a, q)).length;
  }
  box.innerHTML = NAV_ORDER.map(g => {
    const list = lists[g];
    if (q && !list.length) return '';
    const open = q ? true : navOpen === g;
    let body = '';
    if (g === 'infra') {
      NAV_SUB_KINDS.forEach(([k, label]) => {
        const sub = list.filter(a => a.kind === k);
        if (sub.length) body += '<div class="nav-sub">' + label + '</div>' + sub.map(navItemHtml).join('');
      });
      const rest = list.filter(a => !NAV_SUB_KINDS.some(([k]) => k === a.kind));
      if (rest.length) body += '<div class="nav-sub">其他</div>' + rest.map(navItemHtml).join('');
    } else if (g === 'system') {
      body = list.map(([p, label, ico]) =>
        '<button class="nav-item' + (curPage === p ? ' on' : '') + '" data-sys="' + p + '">' +
        '<span class="ico">' + ico + '</span><span class="lbl">' + label + '</span></button>').join('');
    } else {
      body = list.map(navItemHtml).join('');
    }
    if (!body) body = '<div class="nav-empty">' + (AGENTS.length ? '无匹配' : '加载中…') + '</div>';
    return '<div class="nav-acc' + (open ? ' open' : '') + '">' +
      '<button class="nav-acc-head" data-group="' + g + '" aria-expanded="' + (open ? 'true' : 'false') + '">' +
      '<span class="caret">' + (open ? '▾' : '▸') + '</span>' +
      '<span class="ico">' + NAV_ICONS[g] + '</span>' +
      '<span class="lbl">' + NAV_GROUPS[g] + '</span>' +
      '<span class="badge">' + list.length + '</span></button>' +
      '<div class="nav-acc-body">' + body + '</div></div>';
  }).join('');
  // 搜索结果计数提示
  const countEl = $('navSearchCount');
  if (countEl) countEl.textContent = q ? ' 共 ' + totalMatch + ' 项' : '';
}
function toggleGroup(g) {
  navOpen = (navOpen === g) ? '' : g;   // 单开：展开一个自动收起其他
  localStorage.setItem('hub.nav.open', navOpen);
  renderNav();
}
/* 点左侧实体 → 右侧加载该实体工作台（复用既有 gotoChat / showDetail，不另起炉灶） */
function openEntity(id) {
  const a = entityById(id);
  if (!a) return;
  const es = a.entries || [];
  const has = t => es.some(e => e.type === t);
  if (has('embed')) return gotoChat(id, 'embed');
  if (has('term')) return gotoChat(id, 'term');
  if (has('chat')) return gotoChat(id, 'chat');
  if (has('detail')) return showDetail(id);
  const o = es.find(e => e.type === 'open');
  if (o) return window.open(lanUrl(o.url), '_blank');
  showDetail(id);
}
/* 右侧操作区：系统页面包屑（实体页由 renderModeBar 写） */
function renderPageCrumb(page) {
  const crumb = $('crumb'), tabs = $('opTabs');
  if (!crumb) return;
  if (page === 'chat') return;   // 实体工作台由 renderModeBar 接管，别互相覆盖
  if (tabs) tabs.innerHTML = '';
  const label = escapeHtml(PAGE_LABELS[page] || page);
  const top = '';
  crumb.innerHTML = top + '<b>' + label + '</b>';
}
/* 模式 tab 点击：embed/term/chat 走既有 switchMode，open/detail 各自直行 */
function navMode(m) {
  if (m === 'detail') { showDetail(chatPick); return; }
  if (m === 'open') {
    const a = entityById(chatPick);
    const e = ((a && a.entries) || []).find(x => x.type === 'open');
    if (e) window.open(lanUrl(e.url), '_blank');
    return;
  }
  switchMode(m);
}

/* ── 侧栏：手风琴导航委托绑定 + 折叠记忆 ───────────── */
function initSidebar() {
  const sb = document.getElementById('sidebar'), btn = document.getElementById('btnSideToggle');
  if (!sb || !btn) return;
  const narrow = () => window.innerWidth < 768;
  const apply = c => {
    sb.classList.toggle('collapsed', c);
    btn.textContent = c ? '»' : '« 收起';
    localStorage.setItem('hub.sidebar', c ? '1' : '0');
    const m = document.getElementById('sideMask');
    if (m) m.classList.toggle('on', !c && narrow());   // 窄屏展开时才有遮罩
  };
  const stored = localStorage.getItem('hub.sidebar');
  apply(stored === '1' || (stored === null && narrow()));   // 窄屏首次默认收成图标条
  btn.onclick = () => apply(!sb.classList.contains('collapsed'));
  // 事件委托：静态常驻项 + 手风琴动态项（含系统页）统一走这里
  sb.addEventListener('click', e => {
    let el = e.target.closest('button[data-page]');
    if (el) { go(el.dataset.page); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-sys]');
    if (el) { go(el.dataset.sys); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-entity]');
    if (el) { openEntity(el.dataset.entity); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-group]');
    if (el) {
      // 收起态下点组图标 = 先展开侧栏并定位到该组（否则手风琴体被 display:none，点了没反应）
      if (sb.classList.contains('collapsed')) {
        navOpen = el.dataset.group;
        localStorage.setItem('hub.nav.open', navOpen);
        apply(false);
        renderNav();
      } else toggleGroup(el.dataset.group);
    }
  });
  const mask = document.getElementById('sideMask');
  if (mask) mask.addEventListener('click', () => apply(true));
  const si = document.getElementById('navSearch');
  if (si) {
    let _t;
    si.addEventListener('input', () => {
      clearTimeout(_t);
      _t = setTimeout(renderNav, 250);  // 防抖 250ms
    });
  }
}

function setBadge(page, n) {
  const el = document.getElementById('badge-' + page);
  if (el) el.textContent = (n > 0 ? String(n) : '');
}
/* 徽章只取现有接口的现成数据，不新增后端 */
async function updateBadges() {
  try {
    const d = await (await fetch('/api/agents')).json();
    const all = d.agents || [];
    setBadge('classroom', all.filter(a => a.kind === 'agent').length);
    setBadge('chat', all.length);
  } catch (e) { /* 静默：徽章是增强，失败不影响主流程 */ }
  try {
    const d = await (await fetch('/mcp/servers')).json();
    setBadge('mcp', (d.servers || []).length);
  } catch (e) {}
  try {
    const d = await (await fetch('/api/jobs')).json();
    setBadge('jobs', (d.jobs || []).filter(j => j.enabled).length);
  } catch (e) {}
}
initSidebar();
updateBadges();
setInterval(updateBadges, 60000);
$('regMask').addEventListener('click', ev => { if (ev.target === $('regMask')) closeRegister(); });
// T8：chip 键盘可达（Enter/Space 等价点击）
const HOME_CHIPS = $('homeChips');   // 首页快捷问句 chip 键盘可达（原 infraGrid，随座位矩阵一并移除）
if (HOME_CHIPS) HOME_CHIPS.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && e.target.classList.contains('chip')) {
    e.preventDefault(); e.target.click();
  }
});
// T8：命令面板输入/键盘
$('cmdInput').addEventListener('input', e => renderCmdList(e.target.value.trim()));
$('cmdInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const q = e.target.value.trim();
    if (q.startsWith('/')) cmdListDir(q);
    else { const first = $('cmdList').querySelector('.cmd-item'); if (first && !q) closeCmd(); else if (first) first.click(); }
  } else if (e.key === 'Escape') closeCmd();
});
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    $('cmdMask').classList.contains('on') ? closeCmd() : openCmd();
  } else if (e.key === 'Escape') {
    closeDetail();
    closeSettings();
    // Esc 清空搜索并收起手风琴
    const si = $('navSearch');
    if (si && si.value) { si.value = ''; renderNav(); return; }
  } else if (e.key === '/') {
    // / 聚焦搜索框
    const si = $('navSearch');
    if (si) { e.preventDefault(); si.focus(); si.select(); }
  }
});

function tick() {
  const t = new Date().toLocaleString('zh-CN', { hour12: false });
  // // $('hTime').textContent = t; // 已移除  // 已移除
  $('ftTime').textContent = t;
}
setInterval(tick, 1000); tick();
setInterval(loadAgents, 30000);  // T4：8s→30s（左栏手风琴与首页摘要随 loadAgents 一起刷新，无需高频）
pollHealth(); setInterval(pollHealth, 15000);  // T5：健康灯独立于 agent 列表轮询
setInterval(() => {
  if (document.getElementById('page-tasks').classList.contains('on')) { loadRuns(); if (currentRun) openRun(currentRun); }
  if (document.getElementById('page-jobs').classList.contains('on')) loadJobs();
  // 终端面板可见时轮询会话记录：进程退出/超时/TTL 回收都会让死条目自动消失，无需手动点「会话」
  if (document.getElementById('termPane').classList.contains('on')) termRefreshList();
}, 6000);
loadAgents();
go(localStorage.getItem('hub.page') || 'classroom');  // T9：默认落点 = 上次所在页（chatPick/chatMode 已在声明处恢复）
