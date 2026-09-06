/* Agent Hub v0.2.0 教室视图 — 复刻 Agent_Manager(Dashboard/Manager/Memory/Ports) 交互语义 */
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
  document.querySelectorAll('nav.tabs button').forEach(b => b.classList.toggle('on', b.dataset.page === page));
  document.querySelectorAll('section.page').forEach(s => s.classList.toggle('on', s.id === 'page-' + page));
  if (page === 'memory' && !memLoaded) { memLoaded = true; loadMemories(); loadDoc('l2'); loadDoc('l3'); }
  if (page === 'ports' && !portsLoaded) { portsLoaded = true; loadPorts(); }
  if (page === 'telemetry') loadTelemetry();
  if (page === 'chat') renderChatSide();
  if (page === 'tasks') { fillAgentSelect($('taskAgent'), true); loadRuns(); }
  if (page === 'jobs') { fillAgentSelect($('jobAgent'), false); loadJobs(); }
  if (page === 'mcp') loadMcp();
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
function seatOpenUrl(a) {
  if (a.port) return location.protocol + '//' + location.hostname + ':' + a.port;
  return lanUrl(a.endpoint || '');
}

/* ── 教室 ─────────────────────────────────────────── */

const AVATAR_COLORS = [
  'linear-gradient(135deg,#667eea,#764ba2)', 'linear-gradient(135deg,#f093fb,#f5576c)',
  'linear-gradient(135deg,#4facfe,#00f2fe)', 'linear-gradient(135deg,#43e97b,#38f9d7)',
  'linear-gradient(135deg,#fa709a,#fee140)', 'linear-gradient(135deg,#a18cd1,#fbc2eb)',
  'linear-gradient(135deg,#ffecd2,#fcb69f)', 'linear-gradient(135deg,#a1c4fd,#c2e9fb)'];

function hashColor(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}
function initials(name) {
  return String(name).split(/[\s_\-·]+/).slice(0, 2).map(w => w[0] ? w[0].toUpperCase() : '').join('') || '?';
}

async function loadAgents() {
  try {
    const d = await api('/api/agents');
    AGENTS = d.agents || [];
    renderSeats();
    const ag = AGENTS.filter(a => a.kind === 'agent');
    $('hAgents').textContent = 'Agents: ' + ag.length + '（在线 ' + ag.filter(a => a.status === 'running').length + '）';
    const errs = AGENTS.filter(a => a.status === 'error').length;
    $('hErrors').innerHTML = errs ? '<span class="hdot r"></span>异常 ' + errs : '';
  } catch (e) { /* 服务重启窗口容忍瞬时失败 */ }
}

function renderSeats() {
  const grid = $('seats');
  const agents = AGENTS.filter(a => a.kind === 'agent');
  const infra = AGENTS.filter(a => a.kind !== 'agent');
  // 基础设施区（②服务/工具只留快捷方式）
  $('infraCount').textContent = '（' + infra.length + ' 项）';
  $('infraGrid').innerHTML = infra.map(a => {
    const btns = (a.entries || []).map(e => {
      if (e.type === 'open') return '<button class="btn sm ghost" title="快捷方式：' + escapeHtml(e.url) + '" onclick="window.open(\'' + lanUrl(e.url) + '\',\'_blank\')">↗</button>';
      if (e.type === 'term') return '<button class="btn sm ghost" title="终端" onclick="gotoChat(\'' + a.id + '\',\'term\')">⌨</button>';
      return '<button class="btn sm ghost" title="详情" onclick="showDetail(\'' + a.id + '\')">i</button>';
    }).join('');
    const kt = { gateway: '🔀网关', service: '⚙️服务', memory: '🧠记忆', tool: '🧰工具' }[a.kind] || a.kind;
    return '<div class="chip" title="' + escapeHtml(a.description) + (a.port ? ' :' + a.port : '') + '">' +
      '<span class="dot ' + (a.status === 'running' ? 'on' : 'off') + '"></span>' +
      '<b>' + escapeHtml(a.name) + '</b><span class="kindtag">' + kt + '</span>' + btns + '</div>';
  }).join('');
  if (!agents.length) { grid.innerHTML = '<div class="hint" style="grid-column:1/-1;text-align:center;padding:30px">🏫 暂无 Agent — 注册一个吧</div>'; return; }
  if (!grid.dataset.tapBound) {  // US-002：触屏无 hover，点击卡片展开/收起操作行（容器常驻，绑一次即可）
    grid.dataset.tapBound = '1';
    grid.addEventListener('click', e => {
      if (e.target.closest('.s-tools')) return;
      const card = e.target.closest('.seat');
      if (!card) return;
      const wasOpen = card.classList.contains('open');
      grid.querySelectorAll('.seat.open').forEach(s => s.classList.remove('open'));
      if (!wasOpen) card.classList.add('open');
    });
  }
  grid.innerHTML = agents.map(a => {
    const st = a.status === 'running' ? 'running' : (a.status === 'installed' ? 'installed' : (a.status === 'error' ? 'error' : 'stopped'));
    const label = { running: '⚡在线', installed: '🟡可启动', stopped: '💤离线', error: '⚠️异常' }[st];
    const btns = (a.entries || []).map(e => {
      if (e.type === 'embed') return '<button class="btn sm" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'embed\')">原生会话</button>';
      if (e.type === 'open') return '<button class="btn sm ghost" onclick="event.stopPropagation();window.open(\'' + lanUrl(e.url) + '\',\'_blank\')">↗UI</button>';
      if (e.type === 'term') return '<button class="btn sm" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'term\')">终端</button>';
      if (e.type === 'chat') return '<button class="btn sm ghost" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'chat\')">对话</button>';
      if (e.type === 'detail') return '<button class="btn sm ghost" onclick="event.stopPropagation();showDetail(\'' + a.id + '\')">详情</button>';
      return '';
    }).join('') +
      (a.builtin ? '' : '<button class="btn sm danger" onclick="event.stopPropagation();delAgent(\'' + a.id + '\')">删除</button>');
    return '<div class="seat s-' + st + '" data-id="' + escapeHtml(a.id) + '" title="' + escapeHtml(a.description) + '">' +
      '<span class="s-badge ' + st + '"></span>' +
      '<div class="avatar ' + st + '" style="background:' + hashColor(a.id) + '">' + escapeHtml(initials(a.name)) +
      (st === 'stopped' ? '<span class="zsleep">💤</span>' : '') + '</div>' +
      '<div class="s-info">' +
      '<div class="s-name">' + escapeHtml(a.name) + (a.builtin ? '' : ' 📌') + '</div>' +
      '<div class="s-meta">' +
      (a.port ? '<span class="s-port">:' + a.port + '</span>' : '') +
      '<span class="s-status ' + st + '">' + label + '</span></div></div>' +
      '<div class="s-tools">' + btns + '</div></div>';
  }).join('');
}

function showDetail(id) {
  const a = AGENTS.find(x => x.id === id);
  if (a) toast(a.name + ' · ' + a.type + ' · ' + a.status + ' · ' + (a.endpoint || '-') + (a.working_dir ? ' · ' + a.working_dir : ''));
}
function gotoChat(id) { chatPick = id; go('chat'); renderChatSide(); }
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

/* ── Manager 指挥官 ───────────────────────────────── */

function mgrSession() { return localStorage.getItem('hub.mgr.session') || ''; }
function clearMgr() { localStorage.removeItem('hub.mgr.session'); $('mgrMsgs').innerHTML = '<div class="hint" style="margin:auto">向指挥官下达指令</div>'; }

function renderSteps(steps) {
  const wrap = document.createElement('div');
  wrap.className = 'steps';
  for (const s of (steps || [])) {
    const el = document.createElement('div');
    el.className = 'step ' + s.kind;
    if (s.kind === 'thought') el.textContent = '💭 ' + s.content;
    else if (s.kind === 'toolcall') el.textContent = '🔧 ' + (s.tool || '') + ' ' + JSON.stringify(s.tool_input || {});
    else if (s.kind === 'toolresult') {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      sum.textContent = '📥 ' + (s.tool || '') + ' 结果';
      const pre = document.createElement('pre');
      pre.textContent = s.content;
      det.appendChild(sum); det.appendChild(pre);
      let act = null;
      try { const j = JSON.parse(s.content); if (j && j.action === 'open_url' && j.url) act = j.url; } catch (e) {}
      if (act) {
        const b = document.createElement('button');
        b.className = 'btn sm'; b.style.marginTop = '6px'; b.textContent = '🌐 打开界面';
        b.onclick = () => window.open(lanUrl(act), '_blank');
        det.appendChild(b);
      }
      el.appendChild(det);
    } else el.textContent = s.content;
    wrap.appendChild(el);
  }
  return wrap;
}

async function mgrSend() {
  const input = $('mgrInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = $('mgrMsgs');
  const me = document.createElement('div');
  me.className = 'msg user'; me.textContent = msg;
  box.appendChild(me);
  const busy = document.createElement('div');
  busy.className = 'msg assistant'; busy.textContent = '⏳ 指挥官思考中（含工具调用，可能较久）…';
  box.appendChild(busy); box.scrollTop = box.scrollHeight;
  try {
    const body = { message: msg };
    if (mgrSession()) body.session_id = mgrSession();
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

/* ── 统一对话（三模式：embed 原生UI / term pty终端 / chat 对话框）── */

let chatPick = 'hub-self', chatMode = 'chat';
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
  const a = entityById(id);
  chatMode = mode || defaultModeOf(a) || 'chat';
  go('chat');
  renderChatSide();
}

function renderChatSide() {
  const side = $('chatAgents');
  if (!AGENTS.length) { side.innerHTML = '<div class="hint" style="padding:10px">加载…</div>'; loadAgents().then(renderChatSide); return; }
  const list = AGENTS.filter(a => a.kind === 'agent');
  side.innerHTML = list.map(a =>
    '<div class="item' + (a.id === chatPick ? ' on' : '') + '" onclick="pickChatEntity(\'' + a.id + '\')">' +
    '<span>' + escapeHtml(a.name) + '</span><span class="s-badge ' + (a.status === 'running' ? 'running' : (a.status === 'installed' ? 'installed' : 'stopped')) + '" style="position:static"></span></div>').join('');
  applyChatMode();
}

function pickChatEntity(id) {
  chatPick = id;
  chatMode = defaultModeOf(entityById(id));
  renderChatSide();
}

function applyChatMode() {
  const a = entityById(chatPick);
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
  } else if (chatMode === 'term') {
    $('termTitle').textContent = (a.name || chatPick) + ' · 终端会话';
    ensureTerm();
    termRefreshList().then(() => { if (!termSid) termAutoAttach(); });
  } else {
    if (a && a.id !== chatPaneInit) { chatPaneInit = a.id; openChatSession(); }
  }
}
let chatPaneInit = null;

async function embedRefresh() { const f = $('embedFrame'); f.src = f.src; }
function embedNewTab() { const a = entityById(chatPick); const e = (a.entries || []).find(x => x.type === 'embed'); if (e) window.open(lanUrl(e.url), '_blank'); }

/* ── pty 终端（xterm.js + WebSocket） ── */

let term = null, termFit = null, termWs = null, termSid = null;

function ensureTerm() {
  if (term) return;
  term = new window.Terminal({ fontSize: 13, fontFamily: 'Menlo,Consolas,monospace', theme: { background: '#0b1220' }, cursorBlink: true });
  termFit = new window.FitAddon.FitAddon();
  term.loadAddon(termFit);
  term.open($('termEl'));
  term.onData(d => { if (termWs && termWs.readyState === 1) termWs.send(JSON.stringify({ data: d })); });
  const fit = () => { try { termFit.fit(); if (termWs && termWs.readyState === 1) termWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); } catch (e) {} };
  window.addEventListener('resize', fit);
  new ResizeObserver(fit).observe($('termEl'));
  setTimeout(fit, 80);
}

function wsUrl(path) { return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path; }

function termConnect(sid) {
  if (termWs) { try { termWs.close(); } catch (e) {} termWs = null; }
  termSid = sid;
  term.clear();
  const ws = new WebSocket(wsUrl('/ws/term/' + sid));
  ws.binaryType = 'arraybuffer';
  ws.onmessage = ev => term.write(typeof ev.data === 'string' ? ev.data : new Uint8Array(ev.data));
  ws.onopen = () => { term.focus(); if (termFit) termFit.fit(); if (termWs === ws) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); };
  ws.onclose = () => { term.write('\r\n\x1b[90m[连接断开——点右上「会话」重连或新建]\x1b[0m'); };
  termWs = ws;
}

async function termNew() {
  try {
    const d = await api('/api/term/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: chatPick }) });
    toast('已拉起 ' + chatPick + ' 终端会话', 'ok');
    termConnect(d.session.id);
    termRefreshList();
  } catch (e) { toast(e.message, 'err'); }
}

async function termRefreshList() {
  try {
    const d = await api('/api/term/sessions');
    $('termSessList').innerHTML = (d.sessions || []).map(s =>
      '<span class="sess-item' + (s.id === termSid ? '" style="border-color:var(--accent-2)' : '') + '">' +
      '<a href="javascript:void(0)" onclick="termConnect(\'' + s.id + '\')" style="color:#93c5fd">' + escapeHtml(s.agent_id) + ':' + s.id.slice(0, 4) + '</a>' +
      (s.alive ? '' : ' ·已退出') +
      '<button class="btn sm danger" onclick="termKillOne(\'' + s.id + '\')">×</button></span>').join('');
    return d.sessions || [];
  } catch (e) { return []; }
}

function termAutoAttach() {
  // 已有本 agent 的活会话则接上，否则提示新建
  api('/api/term/sessions').then(d => {
    const mine = (d.sessions || []).filter(s => s.agent_id === chatPick && s.alive);
    if (mine.length) termConnect(mine[mine.length - 1].id);
    else term.write('\x1b[90m提示：点「＋ 新会话」拉起 ' + chatPick + ' 的原生终端\x1b[0m\r\n');
  });
}

async function termKill() { if (termSid) await termKillOne(termSid); }
async function termKillOne(sid) {
  try { await api('/api/term/sessions/' + sid, { method: 'DELETE' }); if (sid === termSid) { termSid = null; } termRefreshList(); } catch (e) { toast(e.message, 'err'); }
}

async function openChatSession() {
  const box = $('chatMsgs');
  box.innerHTML = '';
  const sid = localStorage.getItem(sessKey(chatPick));
  if (!sid) { box.innerHTML = '<div class="hint" style="margin:auto">开始新会话（' + escapeHtml(chatPick) + '）</div>'; return; }
  try {
    const d = await api('/api/sessions/' + encodeURIComponent(sid) + '/messages');
    for (const m of d.messages || []) {
      const el = document.createElement('div');
      el.className = 'msg ' + (m.role === 'user' ? 'user' : m.role === 'error' ? 'err' : 'assistant');
      el.textContent = m.content;
      box.appendChild(el);
    }
  } catch (e) { localStorage.removeItem(sessKey(chatPick)); }
}

async function chatSend() {
  const input = $('chatInput');
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
  busy.className = 'msg assistant'; busy.textContent = '⏳ 回复中…';
  box.appendChild(busy);
  try {
    const d = await api('/api/agents/' + encodeURIComponent(chatPick) + '/chat',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: msg, session_id: sid }) });
    busy.className = 'msg ' + (d.success ? 'assistant' : 'err');
    busy.textContent = (d.response || d.error || JSON.stringify(d.hint || d)) +
      (d.usage ? ('\n· tokens: in ' + (d.usage.prompt_tokens || d.usage.input_tokens || '-') + ' / out ' + (d.usage.completion_tokens || d.usage.output_tokens || '-')) : '');
  } catch (e) { busy.className = 'msg err'; busy.textContent = '[错误] ' + e.message; }
  box.scrollTop = box.scrollHeight;
}

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
        '</td><td>' + (p.success_rate >= 99 ? '🟢' : p.success_rate >= 80 ? '🟡' : '🔴') + ' ' + p.success_rate + '%</td>' +
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
  const btn = $('btnDecompose'); btn.disabled = true; btn.textContent = '⏳ 拆解中…';
  try {
    const body = { goal: goal, auto_run: true };
    if ($('taskAgent').value) body.default_agent = $('taskAgent').value;
    const d = await api('/api/tasks/decompose', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    currentRun = d.run_id;
    toast('已拆解 ' + d.tasks.length + ' 个子任务并启动', 'ok');
    loadRuns(); openRun(d.run_id);
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '🧩 拆解并启动';
}

async function loadRuns() {
  try {
    const d = await api('/api/tasks/runs');
    $('runsBody').innerHTML = (d.runs || []).map(r =>
      '<tr><td style="font-family:monospace">' + r.run_id + '</td>' +
      '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(r.goal || '') + '">' + escapeHtml((r.goal || '').slice(0, 40)) + '</td>' +
      '<td>' + ({ running: '🔵执行中', success: '🟢成功', failed: '🔴失败', partial: '🟡部分', pending: '⚪待跑' }[r.state] || r.state) + '</td>' +
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

const TASK_COLORS = { pending: '#475569', running: '#1d4ed8', success: '#15803d', failed: '#b91c1c', blocked: '#7c2d12' };

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
  svg += '<defs><marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="#64748b"/></marker></defs>';
  tasks.forEach(t => (t.deps || []).forEach(dp => {
    const a = pos[dp], b = pos[t.task_id];
    if (a && b) svg += '<line x1="' + (a.x + NW) + '" y1="' + (a.y + NH / 2) + '" x2="' + b.x + '" y2="' + (b.y + NH / 2) + '" stroke="#64748b" stroke-width="1.5" marker-end="url(#arw)"/>';
  }));
  tasks.forEach(t => {
    const p = pos[t.task_id];
    svg += '<g><rect x="' + p.x + '" y="' + p.y + '" width="' + NW + '" height="' + NH + '" rx="10" fill="' + (TASK_COLORS[t.status] || '#334155') + '22" stroke="' + (TASK_COLORS[t.status] || '#334155') + '" stroke-width="1.5"/>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 18) + '" fill="#e2e8f0" font-size="12" font-weight="bold">' + t.task_id + ' · ' + (t.agent_id || '') + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 34) + '" fill="#94a3b8" font-size="12">' + escapeHtml((t.prompt || '').slice(0, 18)) + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 48) + '" fill="#cbd5e1" font-size="12">' + t.status + (t.duration_ms != null ? ' · ' + Math.round(t.duration_ms / 100) / 10 + 's' : '') + '</text></g>';
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
      '<td><button class="btn sm ' + (j.enabled ? '' : 'ghost') + '" onclick="toggleJob(\'' + j.id + '\',' + (j.enabled ? 0 : 1) + ')">' + (j.enabled ? '✔ 启用' : '⏸ 停用') + '</button></td>' +
      '<td class="hint">' + (j.last_run || '').slice(5, 16).replace('T', ' ') + '</td>' +
      '<td>' + (j.last_status === 'success' ? '🟢' : j.last_status === 'fail' ? '🔴' : '-') + '</td>' +
      '<td style="max-width:220px"><details><summary class="hint">' + escapeHtml((j.last_result || '').slice(0, 30)) + '</summary><pre style="font-size:12px;white-space:pre-wrap">' + escapeHtml(j.last_result || '') + '</pre></details></td>' +
      '<td style="white-space:nowrap"><button class="btn sm ghost" onclick="runJobNow(\'' + j.id + '\')">▶ 立即</button> ' +
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
  $('mcProbe').style.display = 'block'; $('mcProbe').textContent = '⏳ 连接探测…';
  try {
    const d = await api('/mcp/servers/probe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd, args: $('mcArgs').value.trim() ? $('mcArgs').value.trim().split(/\s+/) : [] }) });
    $('mcProbe').textContent = JSON.stringify(d.tools, null, 1);
  } catch (e) { $('mcProbe').textContent = '探测失败: ' + e.message; }
}
async function delServer(id) { try { await api('/mcp/servers/' + id, { method: 'DELETE' }); loadMcp(); } catch (e) { toast(e.message, 'err'); } }
async function loadMcp() {
  try {
    const s = await api('/mcp/servers');
    $('mcServers').innerHTML = '<table><thead><tr><th>名称</th><th>传输</th><th>目标</th><th></th></tr></thead><tbody>' +
      (s.servers || []).map(x => '<tr><td><b>' + escapeHtml(x.name) + '</b></td><td>' + x.transport + '</td>' +
        '<td style="font-family:monospace;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis">' + escapeHtml(x.transport === 'stdio' ? (x.command || '') + ' ' + (x.args || []).join(' ') : x.url || '') + '</td>' +
        '<td><button class="btn sm danger" onclick="delServer(\'' + x.id + '\')">×</button></td></tr>').join('') +
      '</tbody></table>';
    $('mcTools').innerHTML = '⏳ 聚合工具中（stdio 会临时拉起进程）…';
    const t = await api('/mcp/tools');
    const errs = Object.entries(t.errors || {});
    $('mcTools').innerHTML = (t.tools || []).map(x =>
      '<div class="mem-item" style="cursor:pointer" onclick="pickTool(\'' + x.server + '\',\'' + x.name + '\')" id="mt_' + x.server + '_' + x.name + '"><span class="tag agent">' + escapeHtml(x.server_name) + '</span><p><b>' + x.name + '</b> <span class="hint">' + escapeHtml(x.description) + '</span></p></div>').join('') ||
      '<div class="hint">无工具——注册 server 后此处聚合</div>' +
      (errs.length ? '<div class="hint" style="color:#fca5a5">异常 server: ' + errs.map(x => x[0] + '(' + x[1].slice(0, 40) + ')').join('; ') + '</div>' : '');
  } catch (e) { $('mcTools').innerHTML = '<span style="color:#fca5a5">' + e.message + '</span>'; }
}
function pickTool(server, tool) {
  mcpToolSel = { server: server, tool: tool };
  document.querySelectorAll('[id^=mt_]').forEach(el => el.style.background = '');
  const el = document.getElementById('mt_' + server + '_' + tool);
  if (el) el.style.background = '#1d4ed844';
}
async function callToolSel() {
  if (!mcpToolSel) return toast('先点击选择一个工具', 'err');
  let args = {};
  const raw = $('mcCallArgs').value.trim();
  if (raw) { try { args = JSON.parse(raw); } catch (e) { return toast('args 需为 JSON', 'err'); } }
  $('mcCallOut').style.display = 'block'; $('mcCallOut').textContent = '⏳ 调用中…';
  try {
    const d = await api('/mcp/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server: mcpToolSel.server, tool: mcpToolSel.tool, args: args, agent_id: 'manager' }) });
    $('mcCallOut').textContent = JSON.stringify(d, null, 1);
  } catch (e) { $('mcCallOut').textContent = '失败: ' + e.message; }
}

/* ── 启动 ─────────────────────────────────────────── */

document.querySelectorAll('nav.tabs button').forEach(b => b.onclick = () => go(b.dataset.page));
$('regMask').addEventListener('click', ev => { if (ev.target === $('regMask')) closeRegister(); });

function tick() {
  const t = new Date().toLocaleString('zh-CN', { hour12: false });
  $('hTime').textContent = t;
  $('ftTime').textContent = t;
}
setInterval(tick, 1000); tick();
setInterval(loadAgents, 8000);
setInterval(() => {
  if (document.getElementById('page-tasks').classList.contains('on')) { loadRuns(); if (currentRun) openRun(currentRun); }
  if (document.getElementById('page-jobs').classList.contains('on')) loadJobs();
}, 6000);
loadAgents();
