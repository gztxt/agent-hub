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
    $('hAgents').textContent = 'Agents: ' + AGENTS.length + '（运行 ' + AGENTS.filter(a => a.status === 'running').length + '）';
    const errs = AGENTS.filter(a => a.status === 'error').length;
    $('hErrors').innerHTML = errs ? '<span class="hdot r"></span>异常 ' + errs : '';
  } catch (e) { /* 服务重启窗口容忍瞬时失败 */ }
}

function renderSeats() {
  const grid = $('seats');
  if (!AGENTS.length) { grid.innerHTML = '<div class="hint" style="grid-column:1/-1;text-align:center;padding:30px">🏫 暂无 Agent — 注册一个吧</div>'; return; }
  grid.innerHTML = AGENTS.map(a => {
    const st = a.status === 'running' ? 'running' : (a.status === 'error' ? 'error' : 'stopped');
    const label = { running: '⚡ 授课中', stopped: '💤 休息中', error: '⚠️ 异常' }[st];
    return '<div class="seat s-' + st + '" title="' + escapeHtml(a.description) + '">' +
      '<div class="s-badge ' + st + '"></div>' +
      '<div style="display:flex;flex-direction:column;align-items:center">' +
      '<div class="avatar ' + st + '" style="background:' + hashColor(a.id) + '">' + escapeHtml(initials(a.name)) +
      (st === 'stopped' ? '<span class="zsleep">💤</span>' : '') + '</div>' +
      '<div class="s-status ' + st + '">' + label + '</div></div>' +
      '<div><div class="s-name">' + escapeHtml(a.name) + (a.builtin ? '' : ' 📌') + '</div>' +
      '<div class="s-desc">' + escapeHtml(a.description || '') + '</div>' +
      (a.port ? '<div class="s-port">:' + a.port + (a.auth_required ? ' 🔒' : '') + '</div>' : '') + '</div>' +
      '<div class="s-tools">' +
      (a.port && st === 'running' ? '<button class="btn sm" onclick="event.stopPropagation();window.open(\'' + seatOpenUrl(a) + '\',\'_blank\')">打开UI</button>' : '') +
      '<button class="btn sm ghost" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\')">对话</button>' +
      '<button class="btn sm ghost" onclick="event.stopPropagation();showDetail(\'' + a.id + '\')">详情</button>' +
      (a.builtin ? '' : '<button class="btn sm danger" onclick="event.stopPropagation();delAgent(\'' + a.id + '\')">删除</button>') +
      '</div></div>';
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

/* ── 统一对话 ─────────────────────────────────────── */

let chatPick = 'jcode';
function sessKey(id) { return 'hub.sess.' + id; }

function renderChatSide() {
  const side = $('chatAgents');
  if (!AGENTS.length) { side.innerHTML = '<div class="hint" style="padding:10px">加载…</div>'; loadAgents().then(renderChatSide); return; }
  side.innerHTML = AGENTS.map(a =>
    '<div class="item' + (a.id === chatPick ? ' on' : '') + '" onclick="chatPick=\'' + a.id + '\';renderChatSide();openChatSession()">' +
    '<span>' + escapeHtml(a.name) + '</span><span class="s-badge ' + (a.status === 'running' ? 'running' : 'stopped') + '" style="position:static"></span></div>').join('');
  openChatSession();
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
    '<tr><td>' + r.proto + '</td><td><b>' + r.port + '</b></td><td style="font-family:monospace;font-size:11px">' + escapeHtml(r.address) + '</td>' +
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
    const e = await api('/telemetry/events?limit=20');
    $('eventTable').innerHTML = '<table><thead><tr><th>时间</th><th>来源</th><th>会话</th><th>事件</th></tr></thead><tbody>' +
      (e.events || []).map(x => '<tr><td>' + (x.created_at || '').slice(5, 16).replace('T', ' ') + '</td><td>' + x.source + '</td><td style="font-family:monospace;font-size:11px">' + escapeHtml((x.session_id || '').slice(0, 14)) + '</td><td>' + x.event + '</td></tr>').join('') +
      '</tbody></table>';
  } catch (err) { toast(err.message, 'err'); }
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
loadAgents();
