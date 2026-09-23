/* Agent Hub v0.3.0 教室视图 — 复刻 Agent_Manager(Dashboard/Manager/Memory/Ports) 交互语义
   v0.10 chat: 动态模型选择器 + 工作目录 + 会话管理（服务 claude/jcode）*/
'use strict';

/* ── v0.7.3 统一图标：全站图形唯一出口 ──────────────────────────────
   sprite 定义在 index.html（24 网格 / stroke=currentColor / 粗细由 CSS 统一）。
   尺寸只允许 xs|sm(默认)|md|lg|xl 五档，任何地方都不要再给图标写 font-size。
   例：ico('server') / ico('plus','xs') / ico('chevron-down',null,'caret') */
function ico(name, size, cls) {
  return '<svg class="i' + (size ? ' ' + size : '') + (cls ? ' ' + cls : '') +
         '" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
}


const $ = id => document.getElementById(id);

/* ── v0.8.0 CSS token 读取：JS 侧不再写死任何颜色 / 字号 ─────────────
   改配色只需动 index.html 的 :root，JS 自动跟随。
   注意：只能读「具体值」的 token。getPropertyValue 返回的是未解析的原始声明，
   读 var() 包装的别名会拿到字符串 "var(--ok)" 而不是颜色。 */
function cssToken(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function cssNum(name, fallback) {
  const v = parseFloat(cssToken(name, ''));
  return Number.isFinite(v) ? v : fallback;
}

let AGENTS = [], PORTS = [], portsLoaded = false, memLoaded = false;

/* ── 基础 ─────────────────────────────────────────── */

/* v0.13.6 P1-7：写端点服务端强制凭据。**只有写操作才索取 token** ——
   GET 也 prompt 的话，用户一打开页面就被口令框糊脸（读路径本来不需要）。 */
const WRITE_METHODS = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };
function isWriteMethod(m) { return !!WRITE_METHODS[String(m || 'GET').toUpperCase()]; }
async function api(path, opt) {
  const o = opt || {};
  if (isWriteMethod(o.method)) {
    const tk = termToken();
    if (tk) o.headers = Object.assign({}, o.headers, { 'x-hub-token': tk });
  }
  let r = await fetch(path, o);
  if (r.status === 401 && isWriteMethod(o.method)) {
    // 存量口令失效（比如刚在设置里换过 token）：清掉再问一次，只重试一次，不循环
    localStorage.removeItem('hub.term.token');
    const again = termToken();
    if (again) {
      o.headers = Object.assign({}, o.headers, { 'x-hub-token': again });
      r = await fetch(path, o);
    }
  }
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
  return el;   // v0.13.6：返回节点，供「链路恢复后收掉同一条提示」用（老调用方忽略返回值，行为不变）
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── 浮层唯一性（2026-09-23 事故：窄屏「设置」抽屉盖掉 92% 画面且没人关）────────
   三个浮层（侧栏抽屉 / detailDrawer / settingsDrawer）此前各开各的：
   开设置不关侧栏、导航不收抽屉、遮罩只管侧栏 —— 窄屏抽屉宽 min(400px,92vw)，
   一旦残留就把整页压成"白板 + 点不动"。规则钉死三条：
   ① 同一时刻最多一个抽屉是 on（开新的必先清旧的）；
   ② 导航 = 清抽屉（go 里做，不留给调用方自觉）；
   ③ 遮罩只有一个计算出口（06 的 syncOverlayMask），且点它一定关干净 ——
      手机上没有 ESC 键，点空白是唯一逃生路径。 */
const OVERLAY_IDS = ['detailDrawer', 'settingsDrawer'];
const overlayOpen = id => { const el = $(id); return !!(el && el.classList.contains('on')); };
window.overlayAnyOpen = () => OVERLAY_IDS.some(overlayOpen);
function closeDrawers() {
  let changed = false;
  OVERLAY_IDS.forEach(id => {
    const el = $(id);
    if (el && el.classList.contains('on')) { el.classList.remove('on'); changed = true; }
  });
  if (changed && window.syncOverlayMask) syncOverlayMask();
  return changed;
}
function closeOverlay(id) {
  const el = $(id);
  if (el && el.classList.contains('on')) el.classList.remove('on');
  if (window.syncOverlayMask) syncOverlayMask();
}
function openOverlay(id) {
  closeDrawers();                      // ① 只允许一个抽屉在开
  const el = $(id);
  if (el) el.classList.add('on');
  if (window.collapseSidebar) collapseSidebar();   // 窄屏别让侧栏抽屉和它叠着
  if (window.syncOverlayMask) syncOverlayMask();
}

function go(page) {
  /* 持久化状态必须校验后回退（2026-09-23 事故第二幕）：启动时直接吃
     `localStorage.getItem('hub.page')`，而这个值可能是**跨版本已改名的页名**（09-20
     「界面统一」改过一批）。下面原本只有 `toggle('on', s.id === 'page-' + page)` ⇒
     一旦认不出来就**把所有页面一起关掉** ⇒ 正文整块空白、页内零个可点元素。
     而 localStorage 是按 origin 隔离的 ⇒ 同一份代码在局域网那个源正常、
     在 Tailscale 那个源空白。认不出就退回总览，并留下可取证的 warn。 */
  if (!document.getElementById('page-' + page)) {
    console.warn('go(): 未知页面 "' + page + '"，退回 classroom');
    page = 'classroom';
  }
  curPage = page;
  // ② 导航即清浮层（只在窄屏强制：桌面上抽屉是右侧常驻面板，收掉反而影响操作）
  if (window.isNarrow && window.closeDrawers && isNarrow() && overlayAnyOpen()) closeDrawers();
  document.querySelectorAll('.sidebar button[data-page], .sidebar .side-item[data-sys]').forEach(b => {
    const on = (b.dataset.page || b.dataset.sys) === page;   // 常驻顶栏项用 data-sys，取值要看两个属性
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
    /* v0.10.2：「在线」换成「可用」——装了 ≠ 能用（fcc-* 入口壳、qoder 额度耗尽都是实例）。
       假卡已在服务端被 vitals 拦掉，这里的分母已经是真 Agent 数。 */
    const usableN = ag.filter(a => a.attested === true).length;
    const untryN = ag.filter(a => a.usable === true && a.attested === false).length;
    const pendN = ag.filter(a => a.usable == null).length;
    $('hAgents').textContent = 'Agents: ' + ag.length + '（可用 ' + usableN +
      (untryN ? ' · 未实测 ' + untryN : '') + (pendN ? ' · 待检 ' + pendN : '') + '）';
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
  const running = ag.filter(a => a.attested === true).length;   /* 「可用」只统计有实测凭据的 */
  const errs = AGENTS.filter(a => a.status === 'error').length;
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set('cntAgent', ag.length);
  set('cntInfra', infra.length);
  set('hsAgent', ag.length);
  set('hsRunning', running);   /* 标签在模板里已改「可用」：取 vitals 结论，无结论时退为在线 */
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


/* 模型层情报（rt_state）→ 短标。它只影响副标文案，永远不影响「可用」。 */
const RT_LABELS = {
  answered: '已应答', blocked_by_account: '额度/登录', rate_limited: '上游限流',
  model_unsupported: '模型标识', timeout: '应答超时', probe_rejected: '探针缺陷',
  no_output: '无输出', skipped: '未实测'
};
function seatStateOf(a) {
  /* 生死只看 verdict（能否打开窗口 + 能否自检）。额度/限流/超时是账号与上游条件，
     不配把菱形打成「异常」—— 那正是上一版误伤 claude/jcode/grok 的地方。 */
  if (a.kind === 'agent' && a.verdict === 'broken')
    return 'error';
  return a.status === 'running' ? 'running' : (a.status === 'installed' ? 'installed' : (a.status === 'error' ? 'error' : 'stopped'));
}
const SEAT_LABELS = { running: '在线', installed: '可启动', stopped: '离线', error: '异常' };
/* 判定结果给人看的短标：宁可短句，详情进 tooltip 与详情抽屉 */
const VERDICT_LABELS = { usable: '可用', blocked_by_account: '账号受限', broken: '启动异常',
                         stopped: '未运行', not_installed: '未安装', pending: '待体检',
                         unknown: '未判定' };
function seatLabelOf(a) {
  const st = seatStateOf(a);
  // 只有真跑通过应答（或自有端点在应声）才叫「可用」；仅自检正常 → 「未见异常」
  if (a.kind === 'agent' && a.verdict === 'usable') {
    const base = a.attested === false ? '未见异常' : '可用';
    // 模型层有情况就在标签里带一句，但底色仍是正常态（不是异常）
    const r = RT_LABELS[a.rt_state];
    return (a.rt_state && a.rt_state !== 'answered' && r) ? base + '·' + r : base;
  }
  if (a.kind === 'agent' && a.verdict && a.verdict !== 'stopped' && VERDICT_LABELS[a.verdict])
    return VERDICT_LABELS[a.verdict];
  return SEAT_LABELS[st];
}




function showDetail(id) {
  const a = AGENTS.find(x => x.id === id);
  if (!a) return;
  $('detailTitle').textContent = a.name + '（' + (a.type || a.kind || '-') + ' · ' + a.status + '）';
  const rows = [['endpoint', a.endpoint], ['config_path', a.config_path], ['working_dir', a.working_dir], ['description', a.description]];
  let html = rows.filter(([, v]) => v).map(([k, v]) =>
    '<div style="margin:6px 0"><div class="hint">' + k + '</div>' +
    '<div style="font-family:var(--font-mono);font-size:var(--fs-sm);word-break:break-all;color:var(--text-1)">' + escapeHtml(v) + '</div></div>').join('');
  const es = a.entries || [];
  /* vitals 台账：判定是什么、凭什么判的、什么时候判的 —— 可审计，不只是个颜色 */  if (a.kind === 'agent' && a.verdict) {
    html += '<div class="hint" style="margin:10px 0 4px">可用性判定</div>' +
      '<div style="font-size:var(--fs-sm);color:var(--text-1)">' +
      escapeHtml(VERDICT_LABELS[a.verdict] || a.verdict) +
      ' （来源 ' + escapeHtml(a.verdict_source || '-') +
      (a.verdict_confidence != null ? '，置信 ' + a.verdict_confidence.toFixed(2) : '') + '）</div>' +
      '<div class="hint" style="margin-top:2px">' + escapeHtml(a.verdict_reason || '') + '</div>' +
      '<div class="hint" style="margin-top:2px">模型层：' +
      escapeHtml(RT_LABELS[a.rt_state] || a.rt_state || '未实测') +
      (a.rt_model ? '（模型 ' + escapeHtml(a.rt_model) + '）' : '') +
      (a.rt_note ? '<div style="font-family:var(--font-mono);font-size:11px;color:var(--text-3);word-break:break-all;margin-top:2px">' + escapeHtml(a.rt_note) + '</div>' : '') +
      '</div>' +
      '<div class="hint" style="margin-top:2px">' +
      '<button class="act-btn" style="display:inline-block;padding:2px 8px" ' +
      'onclick="verifyAgent(\'' + escapeHtml(a.id) + '\')" ' +
      'title="跑一次真实请求（耗 token，只出模型层情报，不改可用性结论）">实测应答</button>' +
      (a.verdict_at ? ' 上次 ' + escapeHtml(String(a.verdict_at).slice(0, 19).replace('T', ' ')) + 'Z' : '') +
      '</div>';
  }
  html += '<div class="hint" style="margin:10px 0 4px">entries</div>' +
    (es.length ? es.map(e => '<span class="tag agent" style="display:inline-block;margin:2px 4px 2px 0;max-width:100%;overflow-wrap:anywhere">' +
      escapeHtml(e.type) + (e.url ? ': ' + escapeHtml(e.url) : '') + '</span>').join('') : '<span class="hint">无</span>');
  $('detailBody').innerHTML = html || '<span class="hint">无附加信息</span>';
  openOverlay('detailDrawer');
}
function closeDetail() { closeOverlay('detailDrawer'); }

/* L4 体检：让 hub 现场跑一次真实一次性请求（耗 token、冷启动可近 60s），完事刷列表 */
async function verifyAgent(id) {
  toast('体检 ' + id + '：正在跑真实请求（模型层实测，最长 40s）…');
  try {
    const d = await api('/api/agents/' + encodeURIComponent(id) + '/verify', { method: 'POST' });
    const ev = d.evidence || {};
    const rts = (d.rt_state || (d.evidence || {}).rt_state || 'skipped');
    toast(id + ' → ' + (VERDICT_LABELS[d.verdict] || d.verdict) +
      '｜模型层 ' + (RT_LABELS[rts] || rts),
      d.verdict === 'usable' ? 'ok' : 'err');
    await loadAgents();
    showDetail(id);
  } catch (e) { toast('体检失败：' + e.message, 'err'); }
}

/* ── 设置（口令保护的 TERM_TOKEN 查看/应用）── */
function openSettings() {
  openOverlay('settingsDrawer');
}
function closeSettings() { closeOverlay('settingsDrawer'); }
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

/* ── 统一对话（三模式：embed 原生UI / term pty终端 / chat 对话框）── */

let chatPick = localStorage.getItem('hub.chat.pick') || 'claude';
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
    const icon = hasEmbed ? ico('monitor', 'xs') : '';
    return '<div class="item' + (a.id === chatPick ? ' on' : '') + '" onclick="pickChatEntity(\'' + a.id + '\')">' +
      '<span>' + escapeHtml(a.name) + '</span>' + (icon ? '<span class="kindtag">' + icon + '</span>' : '') + '<span class="s-badge ' + badge + '" style="position:static"></span></div>';
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
  const en = $('chatEntName');
  if (en) en.textContent = a ? a.name : '';
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
    $('embedTitle').textContent = a.name;   // 用户 09-20：删掉「原生界面」后缀（宽屏要把地址并进同一行，标题越短越好）
    // 地址行：#embedUrlHint 现为 <button><span>URL</span><svg/></button>，直接写 textContent 会把图标抹掉
    const hintEl = $('embedUrlHint');
    const hintTxt = hintEl ? hintEl.querySelector('span') : null;
    if (hintTxt) hintTxt.textContent = url; else if (hintEl) hintEl.textContent = url;
    const f = $('embedFrame');
    if (f.dataset.src !== url) { f.src = url; f.dataset.src = url; }
    probeEmbed(url);
  } else if (chatMode === 'term') {
    $('termTitle').textContent = (a.name || chatPick);   // 用户 09-20：行首只留实体名，不加“· 终端会话”后缀，给芯片腾位
    ensureTerm();
    // ★ 修复核心：切换实体时解绑异主会话，画面不再残留上一个 Agent
    if (termSid && termSidAgent && termSidAgent !== chatPick) termDetach();
    termRefreshList().then(list => termAutoAttach(list));   // 清单直接接力给 autoAttach，一次 GET 就够
  } else {
    openChatSession();
    // 对话工具栏三联动：模型 / 工作目录 / 会话列表
    loadChatModels(false);
    chatCwdLoad();
    chatSessLoad();
  }
}
/* 模式切换栏 v0.7：右侧顶栏仅显示实体名，无分割线 */
function renderModeBar(a) {
  const bar = $('chatModeBar'), tabs = $('opTabs'), crumb = $('crumb');
  if (bar) bar.style.display = 'none';
  // 聊天页顶栏本来就是空的（模式 tab 已停用，实体名走 #chatEntName）：
  // 旧代码只在 !a 时清空，导致从其他页切进来时面包屑残留上一页标题（实测残留「总览」）。
  if (tabs) tabs.innerHTML = '';
  if (crumb) crumb.innerHTML = '';
  syncOpBar();
}
function switchMode(m) {
  chatMode = m;
  localStorage.setItem('hub.chatmode.' + chatPick, m);  // T9：按实体记忆模式
  applyChatMode();
}

/* 嵌入存活探测：no-cors fetch 失败=目标端口无响应 → 覆盖层引导切换 */
async function probeEmbed(url) {
  const pane = $('embedPane');
  // 存活信号只保留"死时"的那一份：失败会有整屏覆盖层（含切模式/重试按钮），
  // 活着时行内再挂一句「可达」是纯噪声 —— 用户 09-20 要求删掉该字样。
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
    dead.innerHTML = '<div style="font-size:var(--fs-base);color:var(--danger-text)">目标界面未响应（' + escapeHtml(url) + '）</div>' +
      '<div style="display:flex;gap:8px"><button class="btn sm" onclick="switchMode(\'chat\')">改用对话模式</button>' +
      '<button class="btn sm ghost" onclick="switchMode(\'term\')">改用终端</button>' +
      '<button class="btn sm ghost" onclick="embedRefresh()">重试嵌入</button></div>';
    if (getComputedStyle(pane).position === 'static') pane.style.position = 'relative';
    pane.appendChild(dead);
  }
}

async function embedRefresh() { const f = $('embedFrame'); f.src = f.src; probeEmbed(f.dataset.src || ''); }
function embedNewTab() { const a = entityById(chatPick); const e = (a.entries || []).find(x => x.type === 'embed'); if (e) window.open(lanUrl(e.url), '_blank'); }
/* 地址行点击复制（非安全上下文无 navigator.clipboard，降级用 execCommand）*/
function embedCopyUrl() {
  const raw = $('embedFrame').dataset.src || $('embedUrlHint').textContent || '';
  const uu = raw.trim();
  if (!uu) return;
  const ok = () => toast('已复制：' + uu);
  const fb = () => {
    const ta = document.createElement('textarea');
    ta.value = uu; ta.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.appendChild(ta); ta.select();
    let done = false;
    try { done = document.execCommand('copy'); } catch (err) { done = false; }
    ta.remove();
    if (done) { ok(); return; }
    // 降级失败就不假装成功：把地址显式选中，提示手动复制
    const el = $('embedUrlHint');
    try { const rg = document.createRange(); rg.selectNodeContents(el);
          const sl = getSelection(); sl.removeAllRanges(); sl.addRange(rg); } catch (err) {}
    toast('未能自动复制，地址已选中，请手动复制', 'err');
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(uu).then(ok, fb); else fb();
}

/* ── pty 终端（xterm.js + WebSocket）── 修复：会话按实体隔离，切换即换绑 ── */

let term = null, termFit = null, termWs = null, termSid = null, termSidAgent = null;

/* ── 终端链路自愈（v0.13.6 P1-1）：应用层心跳 + 退避重连 + 显式失败 ──────────────
   实测缺陷（09-23 取证，非推断）：termConnect() 只有一个 new WebSocket，全仓零重连、
   零心跳、零用户可见失败——手机切网络 / hub 重启后 socket 以 1006 静默死透，画面冻在
   最后一帧，唯一恢复手段是手动点行1 芯片。这就是本仓库反复被烧的「静默不可用」。
   规矩：任何带重试的组件必须 心跳 + 超时 + 显式失败，三者齐了才算自愈。

   协议（服务端 term.py 已实现，09-23 工作树）：
     客户端 → {"type":"hb"}   服务端 → {"type":"hb","t":<epoch float>}
     任何 hb 回执都重置看门狗；回执本身不进画面（display 一律忽略）。
   关闭码分诊：4404/4410 = 会话在服务端已不存在 ⇒ 永不重连（重连只会复活用户已经离开的会话）；
     4401 = 未鉴权 ⇒ 停手并给出「重新应用口令」的路径（拿同一个错口令重连只会永远 4401）；
     1005/1006/1011/1012 等传输码 = 链路断了但 pty 多半还活着 ⇒ 自动重连，
     服务端会保留会话到 idle TTL 并回放 ring（最近 64KB），所以重连后画面自己就回来了。
   退避 1/2/4/8/16/30s 封顶后每 30s 继续试，绝不静默放弃；只有 hb 真往返成功才回到 1s 档。 */
const TERM_HB_SEND_MS = 15000;    // 每 15s 发一帧 {"type":"hb"}
const TERM_HB_DEAD_MS = 30000;    // 距上一次 hb 回执 ≥30s ⇒ 判定半开，主动断开进重连
const TERM_RC_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000];   // 封顶 30s，之后一直 30s
let termHbTimer = null;           // 全仓唯一「发帧」interval（同一时刻最多 1 个）
let termHbDeadline = null;        // 全仓唯一「判死」一次性定时器（每次回执重新武装）
let termHbLastReply = 0, termHbLastSend = 0, termHbBaseline = 0;
let termRcTimer = null;           // 待触发的重连（同一时刻最多 1 个）
let termRcAttempt = 0;            // 退避档位；仅 hb 往返成功后清零
let termInputWarned = false;      // 「输入没送达」只提示一次，不按 keystroke 刷屏
let termToastEl = null;           // 本模块自己那条 toast，链路恢复时收掉

/* 链路提示走既有 toast()（不改 index.html/CSS 的前提下唯一可见出口）；
   同一时刻只留最新一条，重连成功即清除，不留「正在重连」的僵尸提示。 */
function termToast(msg, cls) {
  termToastClear();
  const el = toast(msg, cls);
  if (el && el.nodeType) termToastEl = el;
}
function termToastClear() {
  if (termToastEl) { try { termToastEl.remove(); } catch (e) {} termToastEl = null; }
}
/* 缓冲区里也留一行：toast 4.2s 自动消失，画面历史必须能事后追责 */
function termNotice(s) { if (term) term.write('\r\n\x1b[90m' + s + '\x1b[0m'); }

/* ── 鼠标上报闸门（v0.8.1）───────────────────────────────────────────────
   症状：挂上嵌入式终端后，鼠标在终端里划过就在屏幕上刷出一串 35;29;1m35;26;3m35;22;4m… 的乱码。
   根因（实测，非推断）：WS 建立瞬间服务端会 send_bytes(sess.ring) 回放最近 64KB 输出
   （term.py:238「回放最近输出（重连不白屏）」）。这段历史里若含某个 TUI 的 \x1b[?1003h
   （any-event 鼠标跟踪），重挂后的新 xterm 实例会把它当成"当前状态"重新进入鼠标跟踪——
   可那个 TUI 早退出了，pty 那头现在是 bash，于是每动一下鼠标就生成一条 SGR 上报
   \x1b[<35;x;yM 灌进 pty，被回显/被 readline 打散成字面量，屏幕立刻刷成乱码。
   实测证据：CDP 派发 5 次真实 mousemove → 该 WS 出站 12 帧，其中
     {"data":"\u001b[<35;66;10M"} {"data":"\u001b[<35;67;11M"} {"data":"\u001b[<0;71;11m"}
   （Cb=35 即"无按键按下时的移动"，正是 1003 模式的产物。）
   对策：只认「实时输出」里的鼠标开关指令，回放帧一律不算；未开启时鼠标上报不发给 pty。
   正在跑的 TUI 会自己重新发 \x1b[?1003h（连上时我们已发过 resize，它会重画）→ 届时照常放行。 */
let termMouseLive = false;
const TERM_MOUSE_MODES = new Set(['9', '1000', '1001', '1002', '1003', '1005', '1006', '1007', '1015', '1016']);
const TERM_DECSET_RE = /\x1b\[\?([0-9;]+)([hl])/g;
/* 三种鼠标编码：SGR(\x1b[<b;x;yM|m) / X10(\x1b[M + 3 字节) / 1015(\x1b[b;x;yM|m) */
const TERM_MOUSE_REPORT_RE = /\x1b\[(?:<[0-9]+;[0-9]+;[0-9]+[Mm]|M[\s\S]{3}|[0-9]+;[0-9]+;[0-9]+[Mm])/g;
/* 回放结束后就地复位鼠标跟踪：xterm 处于跟踪态时会吞掉拖拽选中，界面像是"选不中文字" */
const TERM_MOUSE_OFF = '\x1b[?9l\x1b[?1000l\x1b[?1001l\x1b[?1002l\x1b[?1003l'
                     + '\x1b[?1005l\x1b[?1006l\x1b[?1015l\x1b[?1016l';

/* ── 回放查询闸门（v0.12.4）───────────────────────────────────────────────
   ring 里除了画面字节，还夹着上一个 TUI 开机时发过的终端查询：\x1b[c（设备属性）、
   \x1b[6n / \x1b[5n（光标位置 / 状态）、\x1b]10;? 之类（配色）。xterm 会替终端**自动作答**，
   答案顺着 onData 灌进 pty，shell 不认这些字节就原样回显成
   `?1;2c` `1;1R` `0n` `]10;rgb:2424/2727/2b2b` —— 正是用户报的「白色遮挡时还有一串数字字母乱码」。
   实测（CDP 直查 xterm 5.5）：DA1 / DSR6 / DSR5 / OSC 10 / OSC 11 / OSC 4 六类全部会作答。
   对策：只在回放那一帧的解析期间把这几类注册成「吞掉不答」，写完 dispose 交还默认实现
   ⇒ 正在跑的 TUI 现场提问照样能得到答案，只有历史里的过期提问被静音。 */
const TERM_QUERY_OSC = [4, 10, 11, 12, 52];
function termWriteReplay(raw) {
  const gate = [];
  try {
    /* CSI 的私有前缀走独立派发表（注册 id 用 prefix 字段，写成 params 不生效——实测踩过）：
       \x1b[c \x1b[>c \x1b[?c（DA1/DA2/**DA3**）、\x1b[6n \x1b[5n \x1b[?6n（DSR）。
       实测漏掉 `?6n` 就会往 pty 吐一串 `?26;118R`。
       DA3（\x1b[?c）顺手也注册了，但要说清：grep 本仓 vendor 的 xterm 5.5，
       final:"c" 只注册了 primary 与 secondary 两个（{prefix:\"?\",final:\"c\"} **不存在**），
       所以在这个版本上 DA3 查询不会被作答 —— 这一条是**版本升级保险**，
       不是本次乱码的根因（原计划把它当根因，实测证伪）。 */
    for (const id of [{ final: 'c' }, { prefix: '>', final: 'c' }, { prefix: '?', final: 'c' },
                      { final: 'n' }, { prefix: '?', final: 'n' }])
      gate.push(term.parser.registerCsiHandler(id, () => true));
    /* DCS 这一类是本地实测补上的：原先整个闸门只管 CSI/OSC，漏了 DECRQPS。
       `ESC P $ q "q ESC \`（保护属性）与 `ESC P $ qr ESC \`（滚动区）都会被作答。
       实测（tests/verify_replay_gate.py，真代码抽取 + chromium，xterm 5.5 本仓 vendor）：
         不设防 ⇒ onData 收到 "\x1bP1$r0\"q" 与 "\x1bP1$r1;10r" 两个包
         注册后 ⇒ 两包消失，而对照组 \x1b[?6n 照常作答（不是把解析器整个闷掉）
       这些应答灌进 pty 后回显成 `P1$r0"q` / `P1$r1;10r`，与用户报的
       「白屏时夹带一串数字字母乱码」同形 —— 是 v0.12.4 那道闸门没覆盖完的分支。
       线序陷阱：中间码只有 `$`。多一个空格 intermediates 就变成 "$ "，压根匹配不上
       处理器，会得出"DECRQPS 不会作答"的假结论（本探针第一版就是这么错的）。 */
    gate.push(term.parser.registerDcsHandler({ intermediates: '$', final: 'q' }, () => true));
    // OSC 只在「是查询」时吞（带 ? 才是问，带颜色值是设色，不能误杀）
    for (const code of TERM_QUERY_OSC)
      gate.push(term.parser.registerOscHandler(code, s => String(s).includes('?')));
  } catch (e) { /* 注册失败就退回普通写入：宁可多几行乱码，也不能不显示画面 */ }
  term.write(raw, () => { while (gate.length) { try { gate.pop().dispose(); } catch (e) {} } });
}

/* 每帧 new TextDecoder() 是**正确性**问题，不只是浪费：
   UTF-8 多字节字符（中文、box-drawing、emoji）常被 WS 帧从中间劈开，
   独立解码器无法携带"半个字符"的状态 ⇒ 断点处固定吐出 U+FFFD。
   共用一个实例 + {stream:true} 才能把尾巴留到下一帧。
   每条新连接重置一次：上一连接残留的半个字符不该污染新会话的画面。 */
let termDecoder = new TextDecoder();
let termScanTail = '';
function termDecodeReset() { termDecoder = new TextDecoder(); termScanTail = ''; }
function termDecodeFrame(raw) {
  if (typeof raw === 'string') return raw;
  return termDecoder.decode(raw, { stream: true });
}

/* DECSET 扫描的两个便宜招：
   ① 预筛 —— 绝大多数帧里没有 `\x1b[?`，不必动那条全局正则；
   ② 带尾巴 —— `\x1b[?1003h` 恰好跨帧时（旧实现两帧都匹配不上，
      结果实时 TUI 开了鼠标跟踪却没被记下来，滚轮上报被闸门吃掉），
      把上一帧尾部 24 字符接在当前帧前面一起扫；同一条被扫到两次只是
      重复赋同一个值，幂等。 */
function termScanMouseFrame(text) {
  const chunk = termScanTail + text;
  termScanTail = text.slice(-24);
  if (chunk.indexOf('\x1b[?') < 0) return;
  termScanMouseMode(chunk);
}

function termScanMouseMode(text) {
  let m;
  TERM_DECSET_RE.lastIndex = 0;
  while ((m = TERM_DECSET_RE.exec(text))) {
    if (m[1].split(';').some(n => TERM_MOUSE_MODES.has(n))) termMouseLive = (m[2] === 'h');
  }
}

/* 输入不许静默丢弃：现在的做法是「socket 没开就把 keystroke 吃掉」，用户对着冻屏打字
   而毫无反馈——正是静默不可用的另一半。改成显式失败 + 立刻确认重连在排队。
   不跨重连排队回放：半途敲下的一行被打进另一个上下文（比如重连后已经是别的程序）比丢更糟。 */
function termInputLost() {
  /* 输入进不去通常意味着 onclose 已排过重连；这里只补漏（连接在飞时不打扰它） */
  if (termSid && !termRcTimer && termWs && termWs.readyState !== 0) termScheduleReconnect(termWs);
  if (termInputWarned) return;
  termInputWarned = true;
  termToast('终端未连接，输入不会被送达（不排队重放，避免打进错误上下文）', 'err');
}

function termSend(data) {
  if (!termWs || termWs.readyState !== 1) { termInputLost(); return; }
  let payload = data;
  if (!termMouseLive) {
    payload = data.replace(TERM_MOUSE_REPORT_RE, '');
    if (!payload) return;   // 整帧都是鼠标上报 → 丢弃，别污染 pty
  }
  termWs.send(JSON.stringify({ data: payload }));
}

/* ── 重新可见 / 尺寸变化的统一出口 ─────────────────────────────────────────
   两条实测出来的坑：
   ① 容器被 display:none 藏起来时宽高为 0，此时 fit 会算出 1 行的怪尺寸 ⇒ 一律先判可见；
   ② pane 从 none→flex 重新显示后，xterm 的 DOM 渲染层不会自己补画 ⇒ 屏幕留白块
      （用户报的「切页面 / 换会话标签后有白色遮挡」）。重新可见时把全部行 refresh 一遍。
   ResizeObserver 在 0→实际尺寸那一刻自动进来；浏览器标签页切回来走 visibilitychange。 */
let termPaintedAt = '';
function termVisible() {
  const el = $('termEl');
  return !!(el && el.clientWidth && el.clientHeight);
}
function termRepaint(force) {
  if (!term || !termFit) return;
  if (!termVisible()) { termPaintedAt = ''; return; }
  try { termFit.fit(); } catch (e) {}
  const size = term.cols + 'x' + term.rows;
  // pty 那边可能被别的客户端改过尺寸，重连时（force）无条件报一次当前行列
  if ((size !== termPaintedAt || force) && termWs && termWs.readyState === 1)
    termWs.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
  termPaintedAt = size;
  try { term.refresh(0, term.rows - 1); } catch (e) {}
  termHealNow();   // 隐藏期间攒下的「回放只剩半屏」在这里补做
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) termRepaint(); });

/* ── 心跳 / 看门狗（每个 socket 最多一条心跳定时器）────────────────────────────
   半开连接（手机切网络的典型形态）表现为 TCP 还在、WS readyState 仍是 1、但永不回数据：
   只有应用层往返能识破它，所以判据是「回执账本」而不是 readyState。 */
function termSockOpen(ws) { return !!ws && ws === termWs && ws.readyState === 1; }
function termIsHb(text) {
  if (!text || text.charCodeAt(0) !== 123) return false;   // 不以 '{' 开头的帧不去解析，省掉每帧 try
  try { return JSON.parse(text).type === 'hb'; } catch (e) { return false; }
}
function termHbStop() {
  if (termHbTimer) { clearInterval(termHbTimer); termHbTimer = null; }
  if (termHbDeadline) { clearTimeout(termHbDeadline); termHbDeadline = null; }
  termHbLastReply = 0; termHbLastSend = 0; termHbBaseline = 0;
}
function termHbSend(ws) {
  termHbLastSend = Date.now();
  try { ws.send('{"type":"hb"}'); } catch (e) { /* 正在关：交给 onclose */ }
}
/* 两个定时器各司其职，而不是一个轮询 tick：发帧按 15s 节流，判死按「回执 +30s」精确定点。
   后台标签页会被浏览器节流，所以判死不能只靠这个定时器：另有一条按账本补算的唤醒路（termLinkWake）。 */
function termHbArm() {
  if (termHbDeadline) clearTimeout(termHbDeadline);
  termHbDeadline = setTimeout(termHbTimeout, TERM_HB_DEAD_MS);
}
function termHbStart(ws) {
  termHbStop();                       // 先收上一条线的，绝不留下第二个定时器
  termHbBaseline = Date.now();        // 还没有回执时，以「本连接建立时刻」为账本基准
  termHbSend(ws);                     // 连上立刻首发：一次往返即确认链路，也尽早把画面判活
  termHbArm();
  termHbTimer = setInterval(termHbSendTick, TERM_HB_SEND_MS);
}
function termHbSendTick() {
  const ws = termWs;
  if (!ws) { termHbStop(); return; }                     // 已解绑：心跳自己收口，不等 close 事件
  if (ws.readyState !== 1) return;                       // closing/closed：由 onclose 进重连
  termHbSend(ws);
}
function termHbTimeout() {
  termHbDeadline = null;
  const ws = termWs;
  if (!ws) { return; }
  if (ws.readyState !== 1) return;                       // 已经断了：close 事件在负责重连
  if (Date.now() - (termHbLastReply || termHbBaseline) < TERM_HB_DEAD_MS) { termHbArm(); return; }  // 回执刚来过：重新武装，不误杀
  termHbFail(ws);
}
function termHbReply(ws) {
  if (!termSockOpen(ws)) return;
  termHbLastReply = Date.now();
  termRcAttempt = 0;        // 退避只在这里清零：真往返过 = 链路确实通了
  termInputWarned = false;
  termToastClear();         // 「正在重连」那条提示到此结案
  termHbArm();              // 每一次回执都把 30s 看门狗拨回原点
}
function termHbFail(ws) {
  termHbStop();
  termNotice('[心跳超时：' + Math.round(TERM_HB_DEAD_MS / 1000) + 's 无回执，判定链路半开——主动断开重连]');
  try { ws.close(); } catch (e) {}
  /* 半开时浏览器可能迟迟不派发 onclose，重连不能干等它；termRcTimer 有则不重复排 */
  termScheduleReconnect(ws);
}

/* ── 退避重连（全仓唯一待触发重连；任何新连接/解绑都先作废它）─────────────────── */
function termRcCancel() { if (termRcTimer) { clearTimeout(termRcTimer); termRcTimer = null; } }

function termScheduleReconnect(ws) {
  if (ws && termWs !== ws) return;        // 迟到的旧事件不驱动重连
  if (termRcTimer) return;                // 同一条线只许排一次（防重入：visibility/online/close 同时进来）
  if (!termSid) return;                   // 已解绑 / 会话已结束 ⇒ 不复活用户已经离开的会话
  const delay = TERM_RC_DELAYS[Math.min(termRcAttempt, TERM_RC_DELAYS.length - 1)];
  termRcAttempt++;
  termNotice('[连接中断——自动重连中，第 ' + termRcAttempt + ' 次（' + Math.round(delay / 1000) + 's 后）]');
  termToast('终端连接中断，正在重连（第 ' + termRcAttempt + ' 次）', 'err');
  termRcTimer = setTimeout(termRcFire, delay);
}

function termRcFire() {
  termRcTimer = null;
  if (!termSid) return;                                   // 等待期间用户已解绑/切实体
  const ws = termWs;
  if (ws && ws.readyState === 1 && termHbTimer) return;   // 期间已被别的入口接活
  /* 重连前先向服务端对一次账。必须对账的实测理由：4404（会话不存在）是服务端在 accept
     之前 close 的，浏览器拿不到那个业务码，只能看到握手被拒→1006（与“链路断了”同签名）。
     不对账就会对着一个已经不存在的 sid 无限重连——正是「陈旧的重试环复活用户已经离开的会话」。
     拿不到清单（live=null，接口错）时不下结论，照旧重连；只有服务端明确说「清单里没它了」才停。 */
  termRefreshList().then(live => {
    if (!termSid) {                     // 对账结果：会话已不在清单（termRefreshList 顺手解了绑）
      termToast('终端会话已结束，请重新打开', 'err');   // 那条路径原本只往缓冲区写一行，补上可见失败
      return;
    }
    termConnect(termSid, termSidAgent, { reconnect: true });
  });
}

/* 回前台 / 网络恢复：iOS 与安卓后台会冻掉定时器，切回前台那一刻按账本补算一次。
   只走唯一的重连出口，且靠 termRcTimer 去重 ⇒ 不会开出第二个 socket。 */
function termLinkWake() {
  if (!termSid) return;
  const ws = termWs;
  if (!ws || ws.readyState === 0) return;                 // 无绑定 / 连接在飞：交给它自己的事件
  if (ws.readyState !== 1) { termScheduleReconnect(ws); return; }
  if (termHbTimer && Date.now() - (termHbLastReply || termHbBaseline) >= TERM_HB_DEAD_MS) termHbFail(ws);
}
window.addEventListener('online', termLinkWake);
document.addEventListener('visibilitychange', () => { if (!document.hidden) termLinkWake(); });

/* 连接中反馈：终端区顶部一条 2px 走马灯。不往缓冲区写字，回放到了自然被盖掉。 */
function termConnecting(on, ws) {
  if (ws && termWs !== ws) return;   // 迟到的旧连接事件不算数
  const pane = $('termPane');
  if (pane) pane.classList.toggle('connecting', !!on);
}

/* ring 是「最近 64KB 原始输出」，对整屏 TUI 往往只剩最后几帧增量：回放完屏幕大半是空的，
   而同尺寸 resize 不会让内核发 SIGWINCH ⇒ TUI 永不重画，白块就一直挂着
   （CDP 实测：换标签 / 离开页面回来后 26 行里 21 行空白，挪一次尺寸立刻满屏）。
   这里在回放之后确认画面确实空了，才把 pty 尺寸挪一行再挪回来逼它重画整屏。
   回放落在隐藏态时先挂着（termHealPending），等 termRepaint() 在重新可见时补做。 */
let termHealPending = false;
function termHealBlank() { termHealPending = true; setTimeout(termHealNow, 250); }
/* 数「视口内」的空行 —— 必须从 viewportY 起算，不能从缓冲区第 0 行起算。
   buffer.active.getLine(0) 是**绝对坐标**，即 scrollback 的最老一行；
   一旦屏上有历史（输出超过一屏、或用户滚动过），0..rows-1 读到的是早滚出屏幕的旧行，
   与用户此刻看到的画面无关。旧写法在这里空耗两个后果：
     · 画面明明全白，绝对行却有内容 ⇒ blank 偏低 ⇒ 自愈被误抑制（P2-8 的实际症状）
     · 反之画面有内容但顶部历史是空的 ⇒ 会对着不白屏的 pty 乱发 resize 打扰它
   抽成函数是为了能在真浏览器里取证（tests/verify_term_heal_viewport.py 会
   同时算新旧两种口径，红绿放在同一份产物里对比）。 */
function termViewportBlankRows(t) {
  const b = t.buffer.active;
  const rows = t.rows;
  const top = (typeof b.viewportY === 'number' && b.viewportY >= 0) ? b.viewportY : 0;
  let blank = 0;
  for (let i = 0; i < rows; i++) {
    const l = b.getLine(top + i);
    if (!l || !l.translateToString(true).trim()) blank++;
  }
  return blank;
}

function termHealNow() {
  if (!termHealPending || !term || !termWs || termWs.readyState !== 1 || !termVisible()) return;
  termHealPending = false;
  const blank = termViewportBlankRows(term);
  if (blank * 3 < term.rows * 2) return;   // 画面有内容就别去打扰 pty
  const c = term.cols, r = term.rows;
  termWs.send(JSON.stringify({ type: 'resize', cols: c, rows: Math.max(1, r - 1) }));
  setTimeout(() => {
    if (termWs && termWs.readyState === 1) termWs.send(JSON.stringify({ type: 'resize', cols: c, rows: r }));
  }, 120);
}

function ensureTerm() {
  if (term) return;
  // 字号 / 字族 / 配色全部取自 index.html 的 --term-* token（唯一真值源）。
  // 改前是 fontSize:13 + 'Menlo,Consolas,monospace' + 全灰 ANSI：字号不落在站点音阶内、
  // 缺 CJK 等宽导致中文掉字体、16 色全是灰阶导致 ls/git diff 的着色输出完全看不出区别。
  const T = (k, fb) => cssToken('--term-' + k, fb);
  term = new window.Terminal({
    fontSize: cssNum('--term-fs', 14),
    lineHeight: cssNum('--term-lh', 1.5),
    fontFamily: T('font', 'monospace'),
    cursorStyle: 'bar', cursorBlink: true, scrollback: 5000,
    theme: {
      /* 兜底值与 token 真值同步为深色（黑底白字），token 缺失时也不回浅色 */
      background: T('bg', '#000000'), foreground: T('fg', '#ffffff'),
      cursor: T('cursor', '#ffffff'), cursorAccent: T('bg', '#000000'),
      selectionBackground: T('sel', '#b0d0ff40'),
      black: T('black', '#7f7f7f'), red: T('red', '#cd3131'), green: T('green', '#0dbc79'), yellow: T('yellow', '#e5e510'),
      blue: T('blue', '#2472c8'), magenta: T('magenta', '#bc3fbc'), cyan: T('cyan', '#3b8ea6'), white: T('white', '#e5e5e5'),
      brightBlack: T('bblack', '#666666'), brightRed: T('bred', '#f14c4c'), brightGreen: T('bgreen', '#23d18b'), brightYellow: T('byellow', '#f1f14c'),
      brightBlue: T('bblue', '#3c85cc'), brightMagenta: T('bmagenta', '#d73fd7'), brightCyan: T('bcyan', '#49c2d6'), brightWhite: T('bwhite', '#ffffff')
    }
  });
  termFit = new window.FitAddon.FitAddon();
  term.loadAddon(termFit);
  term.open($('termEl'));
  term.onData(termSend);
  /* 回调一律包一层：termRepaint(force) 的形参不能接 addEventListener/ResizeObserver 的事件对象 */
  window.addEventListener('resize', () => termRepaint());
  new ResizeObserver(() => termRepaint()).observe($('termEl'));
  requestAnimationFrame(() => termRepaint());
  setTimeout(() => termRepaint(), 150);
}

function termDetach() {
  termRcCancel();          // 用户显式离开 ⇒ 任何在排的重连一律作废，不许把会话拖回来
  termHbStop();
  termRcAttempt = 0;
  termInputWarned = false;
  termToastClear();
  if (termWs) { try { termWs.close(); } catch (e) {} termWs = null; }
  termSid = null; termSidAgent = null;
  termConnecting(false);   // 解绑后 close 事件会被 termWs!==ws 守卫吃掉，连接中状态在这儿自己收
  termHealPending = false;
}

/* TERM_TOKEN 鉴权（后端强制校验）：首次用终端时 prompt 一次存 localStorage，之后 header+query 双带 */
function termToken() {
  let t = localStorage.getItem('hub.term.token');
  if (!t) {
    t = prompt('请输入终端鉴权 TERM_TOKEN（也可在右上角「设置」查看后一键应用）') || '';
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

/* 是否该抢焦点：只有用户主动动作（新会话 / 点芯片 / 续聊历史）才聚焦。
   自动挂载与退避重连一律不抢 —— 手机上那两条路径每次都无条件弹出软键盘
   （用户描述为「手机一进来键盘就顶着脸」），桌面端则表现为离开页面回来被硬抢焦点。 */
function termFocusWanted(opts) { return !!(opts && opts.user); }

function termConnect(sid, agent, opts) {
  /* opts.reconnect：由 termRcFire 起的自动重连。目前与普通连接同路（都清屏 + 靠 ring 回放补画面），
     留着这个入参是为了「重连场景」与「用户点芯片」在后续分诊时不必再改调用方签名。 */
  const o = opts || {};
  termRcCancel();          // 新连接开始 ⇒ 作废旧的一切实重连计划（防第二个 socket / 防漏定时器）
  termHbStop();            // 心跳定时器全仓唯一，换绑即回收
  termInputWarned = false;
  const oldAlive = !!termWs && termWs.readyState === 1;   // 关旧线之前先记下它还活着
  if (termWs) { try { termWs.close(); } catch (e) {} termWs = null; }
  /* 只有「回到同一条会话且旧 socket 还活着」才保留画面。旧 socket 已死时画面里挂着
     [连接断开] 那行提示，必须清掉——清屏后的空屏由 termHealBlank 逼 pty 重画补回来。 */
  const keepScreen = (sid === termSid) && oldAlive;
  termSid = sid;
  termSidAgent = agent || termSidAgent;
  /* 行1 芯片的「当前」标记跟着走：点芯片回看另一路时 termConnect 不重绘列表，
     不手动改 class 的话 .cur 会停在旧芯片上（实测缺陷：点 first 后 cur 仍在 second）。 */
  const row = $('termSessList');
  if (row) row.querySelectorAll('.sess-item').forEach(x => x.classList.toggle('cur', x.dataset.sid === sid));
  termMouseLive = false;   // 新连接：鼠标开关从零判定，别继承上一会话的状态
  termHealPending = false; // 上一条会话攒下的补画请求作废，新连接的回放自己会再挂
  if (!o.reconnect) termToastClear();   // 用户主动接的线：收掉「正在重连」提示；自动重连则留到 hb 往返成功才结案
  /* 保留画面时别清屏：清屏 = 先给用户一屏白底，而服务端只回放 ring 里最近 64KB
     （整屏帧早被增量帧挤出去）⇒ 补不满就一直白着，就是用户报的现象。 */
  if (!keepScreen) term.clear();
  termConnecting(true);
  termDecodeReset();   // 上一连接可能残留半个 UTF-8 字符，别带进新会话
  const ws = new WebSocket(wsUrl('/ws/term/' + sid));
  ws.binaryType = 'arraybuffer';
  /* 连接后的第一帧 = 服务端的历史回放（term.py 里 ring 是整块 send_bytes 出去的，一帧到底）：
     只回显、不复位也不参与「当前是否需要鼠标」的判定——历史里的 TUI 开关是过期状态。
     见文件上方「鼠标上报闸门」与「回放查询闸门」注释。 */
  let replayFrame = true;
  ws.onmessage = ev => {
    if (termWs !== ws) return;
    const raw = typeof ev.data === 'string' ? ev.data : new Uint8Array(ev.data);
    /* 心跳回执只喂看门狗，不进画面、也不占「首帧=回放」那次判定 */
    if (typeof raw === 'string' && termIsHb(raw)) { termHbReply(ws); return; }
    if (replayFrame) {
      replayFrame = false;
      termWriteReplay(raw);   // 回放走闸门：历史里的终端查询不许替它作答
      term.write(TERM_MOUSE_OFF);
      termHealBlank();   // 回放可能只是 64KB 尾巴里的半屏，见函数注释
      return;
    }
    term.write(raw);
    termScanMouseFrame(termDecodeFrame(raw));
  };
  /* 重连成功后必须跑的仍是原来那三件事（收连接中灯 + 聚焦 + force 重绘报行列），
     之后额外挂上这条线自己的心跳。 */
  ws.onopen = () => { termConnecting(false, ws);
    if (termFocusWanted(opts)) term.focus();   // 自动挂载/重连不抢焦点、不弹软键盘
    termRepaint(true); termHbStart(ws); };
  ws.onclose = ev => {
    if (termWs !== ws) return;  // 旧连接的 close 不污染新会话画面
    termConnecting(false, ws);
    termHbStop();               // 本线心跳随本线收尸；重连成功后由新 socket 重新起一条
    const code = ev.code;
    if (code === 4404 || code === 4410) {   // 已退出/不存在 → 明确提示并刷新列表，绝不重连
      termNotice('[该会话已结束或不存在——点行1 芯片重连，或按「新会话」]');
      termToast('终端会话已结束，请重新打开', 'err');
      termDetach(); termRefreshList();
      return;
    }
    if (code === 4401) {                     // 未鉴权：同一个错口令重连只会一直被拒，停手指路
      termNotice('[鉴权失败（4401）——在「设置」里重新应用 TERM_TOKEN 后再打开终端]');
      termToast('终端鉴权失败（TERM_TOKEN 不符），已停止重连', 'err');
      termDetach();
      return;
    }
    // 1005/1006/1011/1012…：链路断了但 pty 多半还在服务端（ring 会回放）⇒ 退避自动重连
    term.write('\r\n\x1b[90m' + '[连接断开——点行1 芯片重连或新建]' + '\x1b[0m');
    termScheduleReconnect(ws);
  };
  termWs = ws;
}

async function termNew() {
  try {
    const d = await api('/api/term/sessions', { method: 'POST', headers: termHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ agent_id: chatPick }) });
    toast('已拉起 ' + chatPick + ' 终端会话', 'ok');
    termConnect(d.session.id, chatPick, { user: true });
    // 即时可见：不等服务端回写，先把新芯片本地插进去（termConnect 已设 termSid，所以自带 .cur）
    const el = $('termSessList');
    if (el && !el.querySelector('.sess-item[data-sid="' + d.session.id + '"]'))
      el.insertAdjacentHTML('beforeend', termChipHtml(Object.assign({ alive: true, title: '' }, d.session)));
    termRefreshList();   // 再拿服务端清单覆写，DOM 不骗人
  } catch (e) { toast(e.message, 'err'); }
}

/* 芯片模板：行1 内联会话项。v0.13.0 起标签＝历史会话的问题原文（中文），
   取不到标题（刚新建、agent 还没落摘要 / 无 pid 登记表）退显「新会话 MM-DD」。
   字母 sid 只留在 data-sid 里作 DOM 键，用户可见处一律不再出现。 */
/* 芯片点击的具名入口：内联 onclick 只能走全局作用域，所以在这里统一带上 user:true（点击=用户主动，该聚焦） */
function termOpenChip(sid, agent) { termConnect(sid, agent, { user: true }); }

function termChipHtml(s) {
  const label = (s.title && s.title.trim()) ? s.title.trim() : ('新会话 ' + hhTime(Math.floor(s.created)));
  return '<span class="sess-item' + (s.id === termSid ? ' cur' : '') + '" data-sid="' + s.id + '">' +
         '<a href="javascript:void(0)" title="' + escapeHtml(label) + '"' +
         ' onclick="termOpenChip(\'' + s.id + '\',\'' + s.agent_id + '\')"><span class="s-t">' +
         escapeHtml(label) + '</span></a>' +
         '<button class="sess-x" title="关闭此会话" aria-label="关闭此会话" ' +
         'onclick="termKillOne(\'' + s.id + '\')">' + ico('x', 'xs') + '</button></span>';
}

/* P1-7：清单类 GET 的 401 只能报一次 —— termRefreshList 是轮询调用，不去重就会
   反复弹同款错误把界面活埋。拿到过清单就重置，允许下次再错时重新报。 */
let termAuthWarned = false;
function termAuthWarn(e) {
  const m = String((e && e.message) || e || '');
  if (!/401|token/i.test(m)) return;      // 网络错/5xx 不归因到「口令」
  if (termAuthWarned) return;
  termAuthWarned = true;
  toast('终端清单需要 TERM_TOKEN：在「设置」里应用口令后重试', 'err');
}
function termAuthOk() { termAuthWarned = false; }

async function termRefreshList() {
  try {
    const d = await api('/api/term/sessions', { headers: termHeaders() });
    // 只显示当前选中 agent 的会话，避免显示其他 agent 的终端
    const live = (d.sessions || []).filter(s => s.alive && s.agent_id === chatPick);
    termAuthOk();   // 拿到清单 = 口令可用，重置去重位
    const el = $('termSessList');
    if (!el) return live;
    // 空清单就什么都不画（用户 09-20：拿掉「暂无活会话」占位字）
    el.innerHTML = live.map(termChipHtml).join('');
    // 当前正看的会话已被服务端回收（进程退出/超时）→ 清绑并提示，避免对着幽灵 sid 重连
    if (termSid && !live.some(s => s.id === termSid)) {
      termDetach();
      if (term) term.write('\r\n\x1b[90m[当前会话已结束——点「新会话」重新开始]\x1b[0m');
    }
    return live;
  } catch (e) {
    // 改前 GET 不鉴权，走不到这条路；P1-7 之后这里是 401 的唯一出口。
    // 全吐成 null 就是「静默不可用」—— 这是用户自己能修的一种失败，必须上屏。
    termAuthWarn(e);
    return null;
  }
  // 出错回 null（= 没拿到清单），与「清单为空」分开：空清单才清屏给提示，
  // 取不到清单不能把好好一块画面抹掉
}

function termAutoAttach(live) {
  // 只接本实体的活会话；没有就清屏给提示（确保不残留上一实体画面）
  // live = termRefreshList() 已经拿到的清单，别再为同一件事发第二个 GET
  const got = live ? Promise.resolve(live)
    : api('/api/term/sessions', { headers: termHeaders() })
        .then(d => (d.sessions || []).filter(s => s.agent_id === chatPick && s.alive));
  got.then(mine => {
    if (mine.length) {
      const last = mine[mine.length - 1].id;
      /* 已经接在这条会话上就别拆线重连：重连 = 清屏 + 只回放 ring 尾巴，
         用户看到的「离开当前页再回来就白屏」正是这么来的。补一次重绘即可。 */
      if (termSid === last && termWs && termWs.readyState === 1) { termRepaint(); return; }
      termConnect(last, chatPick);   // 自动挂载：刻意不带 user:true，否则手机一进页面就弹键盘
      return;
    }
    // 确保 detached：切换实体时清除旧绑定，避免显示错误会话
    if (termSid && termSidAgent !== chatPick) termDetach();
    term.clear();
    const a = entityById(chatPick);
    term.write('\x1b[90m' + chatPick + ' · ' + (a?.name || chatPick) + ' 终端\x1b[0m\r\n');
    term.write('\x1b[90m提示：点「新会话」拉起 ' + (a?.name || chatPick) + ' 的原生终端\x1b[0m\r\n');
  }).catch(e => { termAuthWarn(e); });
  // 这条 .catch 不是装饰：termAutoAttach 会为拿不到清单而自己补发一次 GET，
  // 无 catch 就只剩控制台里一条没人认领的 rejection，用户侧表现为「什么都没发生」
}

/* termKill()（「销毁当前」整块按钮）已随 v0.10.1 外框统一拿掉；销毁只保留芯片上的 × = termKillOne。 */
async function termKillOne(sid) {
  // 乐观移除：先摘 DOM 芯片，用户即时看到"删掉了"，再与后端对账
  const el = $('termSessList');
  const chip = el && el.querySelector('.sess-item[data-sid="' + sid + '"]');
  if (chip) chip.remove();
  try {
    await api('/api/term/sessions/' + sid, { method: 'DELETE', headers: termHeaders() });
    toast('会话已销毁', 'ok');
    if (sid === termSid) termDetach();
    termRefreshList().then(list => termAutoAttach(list));   // 一次 GET：重绘芯片并接上剩下的会话
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
  const el = document.createElement('div');
  el.className = 'msg ' + (m.role === 'user' ? 'user' : m.role === 'error' ? 'err' : 'assistant');
  el.textContent = m.content || '';
  return el;
}

async function chatSend() {
  const input = $('chatInput');
  const model = $('chatModel')?.value || '';
  const cwd = $('chatCwd')?.value || '';
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
  busy.className = 'msg assistant'; busy.textContent = '回复中…';
  box.appendChild(busy);
  const body = { message: msg, session_id: sid, model: model, cwd: cwd || null };
  // 统一走 /api/agents/{id}/chat
  try {
    const d = await api('/api/agents/' + encodeURIComponent(chatPick) + '/chat',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    busy.className = 'msg ' + (d.success ? 'assistant' : 'err');
    let txt = (d.response || d.error || JSON.stringify(d.hint || d));
    if (d.model) txt += '\n· model: ' + d.model;
    if (d.usage) txt += '\n· tokens: in ' + (d.usage.prompt_tokens || d.usage.input_tokens || '-') + ' / out ' + (d.usage.completion_tokens || d.usage.output_tokens || '-');
    busy.textContent = txt;
    chatSessLoad();  // 刷新会话列表（title 等信息更新）
  } catch (e) { busy.className = 'msg err'; busy.textContent = '[错误] ' + e.message; }
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
  // 1) agent 画像默认 cwd（claude/jcode/hermes/bash）
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
    opts.push('<option value="__new__">新会话</option>');
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
      '<button class="btn sm danger" onclick="delMemory(' + m.id + ')">' + ico('x') + '</button></div>').join('') ||
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
    '<tr><td>' + r.proto + '</td><td><b>' + r.port + '</b></td><td style="font-family:var(--font-mono);font-size:var(--fs-sm)">' + escapeHtml(r.address) + '</td>' +
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
        '</td><td>' + (p.success_rate >= 99 ? ico('check-circle', 'xs') : p.success_rate >= 80 ? ico('circle', 'xs') : ico('alert', 'xs')) + ' ' + p.success_rate + '%</td>' +
        '<td>' + p.avg_ms + 'ms</td><td>' + (p.max_ms || '-') + 'ms</td></tr>').join('') + '</tbody></table>'
      : '<div class="hint">暂无画像数据（对话/指挥官/DAG/定时执行后自动生成）</div>';
    const e = await api('/telemetry/events?limit=20');
    $('eventTable').innerHTML = '<table><thead><tr><th>时间</th><th>来源</th><th>会话</th><th>事件</th></tr></thead><tbody>' +
      (e.events || []).map(x => '<tr><td>' + (x.created_at || '').slice(5, 16).replace('T', ' ') + '</td><td>' + x.source + '</td><td style="font-family:var(--font-mono);font-size:var(--fs-sm)">' + escapeHtml((x.session_id || '').slice(0, 14)) + '</td><td>' + x.event + '</td></tr>').join('') +
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
  const btn = $('btnDecompose'); btn.disabled = true; btn.textContent = '拆解中…';
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
      '<tr><td style="font-family:var(--font-mono)">' + r.run_id + '</td>' +
      '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(r.goal || '') + '">' + escapeHtml((r.goal || '').slice(0, 40)) + '</td>' +
      '<td>' + ({ running: ico('play', 'xs') + '执行中', success: ico('check-circle', 'xs') + '成功', failed: ico('alert', 'xs') + '失败', partial: ico('circle', 'xs') + '部分', pending: ico('clock', 'xs') + '待跑' }[r.state] || r.state) + '</td>' +
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

// 色值必须是具体十六进制：下面 fill = 描边色 + '22' 透明度派生，拿到 "var(--x)" 会变成非法色值
const TASK_COLORS = {
  pending:  cssToken('--muted', '#6b6b6b'),
  running:  cssToken('--busy', '#2c5f8a'),
  success:  cssToken('--ok', '#2f7a4f'),
  failed:   cssToken('--danger', '#a3342c'),
  blocked:  cssToken('--warn', '#8a5a00')
};

function renderTaskTable(tasks) {
  $('taskBody').innerHTML = tasks.map(t =>
    '<tr><td><b>' + t.task_id + '</b></td><td>' + (t.agent_id || '-') + '</td>' +
    '<td>' + (t.deps || []).join(',') + '</td><td>' + t.status + '</td>' +
    '<td>' + (t.duration_ms != null ? t.duration_ms + 'ms' : '-') + '</td>' +
    '<td><details><summary class="hint">' + escapeHtml((t.output || t.error || '').slice(0, 50)) + '</summary><pre style="white-space:pre-wrap;font-size:var(--fs-sm);max-height:220px;overflow:auto">' + escapeHtml(t.output || t.error || '') + '</pre></details></td></tr>').join('');
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
  const DAG_LINE = cssToken('--muted', '#6b6b6b');
  const DAG_ST = n => cssNum(n, 13);
  svg += '<defs><marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="' + DAG_LINE + '"/></marker></defs>';
  tasks.forEach(t => (t.deps || []).forEach(dp => {
    const a = pos[dp], b = pos[t.task_id];
    if (a && b) svg += '<line x1="' + (a.x + NW) + '" y1="' + (a.y + NH / 2) + '" x2="' + b.x + '" y2="' + (b.y + NH / 2) + '" stroke="' + DAG_LINE + '" stroke-width="1.5" marker-end="url(#arw)"/>';
  }));
  tasks.forEach(t => {
    const p = pos[t.task_id];
    svg += '<g><rect x="' + p.x + '" y="' + p.y + '" width="' + NW + '" height="' + NH + '" rx="10" fill="' + (TASK_COLORS[t.status] || cssToken('--border')) + '22" stroke="' + (TASK_COLORS[t.status] || cssToken('--border')) + '" stroke-width="1.5"/>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 18) + '" fill="' + cssToken('--text-1') + '" font-size="' + DAG_ST('--fs-xs') + '" font-weight="bold">' + t.task_id + ' · ' + (t.agent_id || '') + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 34) + '" fill="' + cssToken('--text-2') + '" font-size="' + DAG_ST('--fs-xs') + '">' + escapeHtml((t.prompt || '').slice(0, 18)) + '</text>' +
      '<text x="' + (p.x + 8) + '" y="' + (p.y + 48) + '" fill="' + cssToken('--info') + '" font-size="' + DAG_ST('--fs-xs') + '">' + t.status + (t.duration_ms != null ? ' · ' + Math.round(t.duration_ms / 100) / 10 + 's' : '') + '</text></g>';
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
      '<td style="font-family:var(--font-mono)">' + j.cron + '</td><td>' + j.kind + '</td>' +
      '<td><button class="btn sm ' + (j.enabled ? '' : 'ghost') + '" onclick="toggleJob(\'' + j.id + '\',' + (j.enabled ? 0 : 1) + ')">' + (j.enabled ? '✔ 启用' : '‖ 停用') + '</button></td>' +
      '<td class="hint">' + (j.last_run || '').slice(5, 16).replace('T', ' ') + '</td>' +
      '<td>' + (j.last_status === 'success' ? ico('check-circle', 'xs') : j.last_status === 'fail' ? ico('alert', 'xs') : '-') + '</td>' +
      '<td style="max-width:220px"><details><summary class="hint">' + escapeHtml((j.last_result || '').slice(0, 30)) + '</summary><pre style="font-size:var(--fs-sm);white-space:pre-wrap">' + escapeHtml(j.last_result || '') + '</pre></details></td>' +
      '<td style="white-space:nowrap"><button class="btn sm ghost" onclick="runJobNow(\'' + j.id + '\')">' + ico('play', 'xs') + '立即</button> ' +
      '<button class="btn sm danger" onclick="delJob(\'' + j.id + '\')">' + ico('x') + '</button></td></tr>').join('') ||
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
  $('mcProbe').style.display = 'block'; $('mcProbe').textContent = '连接探测…';
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
        '<td style="font-family:var(--font-mono);font-size:var(--fs-sm);max-width:200px;overflow:hidden;text-overflow:ellipsis">' + escapeHtml(x.transport === 'stdio' ? (x.command || '') + ' ' + (x.args || []).join(' ') : x.url || '') + '</td>' +
        '<td><button class="btn sm danger" onclick="delServer(\'' + x.id + '\')">' + ico('x') + '</button></td></tr>').join('') +
      '</tbody></table>';
    $('mcTools').innerHTML = '聚合工具中（stdio 会临时拉起进程）…';
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
      '<button class="btn sm danger" aria-label="删除规则 ' + r.id + '" onclick="delAcl(' + r.id + ')">' + ico('x') + '</button></div>').join('') ||
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
  $('mcCallOut').style.display = 'block'; $('mcCallOut').textContent = '调用中…';
  try {
    const d = await api('/mcp/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server: mcpToolSel.server, tool: mcpToolSel.tool, args: args, agent_id: 'manager' }) });
    $('mcCallOut').textContent = JSON.stringify(d, null, 1);
  } catch (e) { $('mcCallOut').textContent = '失败: ' + e.message; }
}

/* ── T8 命令面板（⌘K/Ctrl+K）：搜实体直达工作台 ── */
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
  const ql = q.toLowerCase();
  const items = AGENTS.filter(a => !ql || a.id.toLowerCase().includes(ql) || (a.name || '').toLowerCase().includes(ql)).slice(0, 12);
  box.innerHTML = items.map(a =>
    '<div class="cmd-item" onclick="cmdGo(\'' + a.id + '\')"><span>' + escapeHtml(a.name) + '</span><span class="hint">' + escapeHtml(a.id) + '</span></div>').join('') ||
    '<div class="hint" style="padding:8px">无匹配实体</div>';
}
function cmdGo(id) { closeCmd(); gotoChat(id); }

/* ── 启动 ─────────────────────────────────────────── */

/* ── v0.7 左侧手风琴导航：单开模式 + 搜索 + 展开态持久化 ──
   20 个实体全部收拢进左栏（AGENTS 8 / 基础设施 12），系统功能仍走 go(page) ── */
const NAV_GROUPS = { agents: 'AGENTS', infra: '基础设施', system: '系统' };
const NAV_ICONS = { agents: 'cpu', infra: 'server', system: 'sliders' };   // 收起成图标条时仍可辨认（sprite id）
const NAV_ORDER = ['agents', 'infra', 'system'];
const NAV_SUB_KINDS = [['gateway', '网关'], ['service', '服务'], ['tool', '工具'], ['memory', '记忆']];
const SYS_PAGES = [['ports', '端口', 'share'], ['telemetry', '遥测', 'activity'], ['memory', '记忆中心', 'database'],
                   ['mcp', '工具', 'wrench'], ['jobs', '定时', 'clock'], ['tasks', '协同', 'flow']];
const MODE_LABEL = { embed: '嵌入', term: '终端', chat: '对话', detail: '详情', open: '新窗口' };
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
  const tip = a.name + ' · ' + seatLabelOf(a) + (a.port ? ' · :' + a.port : '') +
    (a.verdict_reason ? '\n' + a.verdict_reason : '') +
    (a.description ? '\n' + a.description : '');
  // 搜索高亮：名称用 hlMatch，ID 和端口保持原样
  const nameHtml = hlMatch(a.name || a.id, ($('navSearch') ? $('navSearch').value.trim() : ''));
  // 操作按钮：按 entries 类型渲染（最多3个）
  const es = a.entries || [];
  const actBtns = [];
  if (es.some(e => e.type === 'embed')) actBtns.push('<span class="act-btn" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'embed\')" title="嵌入会话">' + ico('monitor') + '</span>');
  if (es.some(e => e.type === 'term')) actBtns.push('<span class="act-btn" onclick="event.stopPropagation();gotoChat(\'' + a.id + '\',\'term\')" title="终端">' + ico('terminal') + '</span>');
  /* v0.10.1：删掉「对话」图标。智管对话（hub 自带会话页）已在 v0.10 移除，profile 里残留的
     chat entry 实测为死入口：claude / jcode 两个实体 POST /api/agents/{id}/chat 均 HTTP 500。
     留着它 = 坏入口（比没入口更糟），且让无 Web UI 的 Agent 行多出第三个图标。 */
  // 启动按钮：installed/stopped 状态的 agent
  if (a.kind === 'agent' && (st === 'installed' || st === 'stopped') && es.some(e => e.type === 'term')) {
    actBtns.push('<span class="act-btn start-btn" onclick="event.stopPropagation();startAgent(\'' + a.id + '\')" title="启动">' + ico('play') + '</span>');
  }
  const actsHtml = actBtns.length ? '<span class="nav-acts">' + actBtns.join('') + '</span>' : '';
  return '<button class="nav-item' + on + '" data-entity="' + escapeHtml(a.id) + '"' +
    ' title="' + escapeHtml(tip) + '" aria-label="' + escapeHtml(a.name + ' ' + seatLabelOf(a)) + '">' +
    '<span class="s-badge ' + st + '"></span>' +
    '<span class="lbl">' + nameHtml + '</span>' +
    (a.port ? '<span class="nav-port">:' + a.port + '</span>' : '') +
    actsHtml + '</button>';
}
/* v0.12.3：行内状态文字（.nav-st）与其横向滚动窗（rollNavStatus / st-roll）已按用户要求整体删除。
   行内只留 .s-badge 方块表达在线/离线；完整状态串在上面的 tip（hover）与工作台卡片里给。 */
// 启动 agent：创建新的终端会话
async function startAgent(id) {
  try {
    const d = await api('/api/term/sessions', { method: 'POST', headers: termHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ agent_id: id }) });
    toast('已拉起 ' + id + ' 终端', 'ok');
    gotoChat(id, 'term');
    setTimeout(() => termConnect(d.session.id, id, { user: true }), 100);
  } catch (e) { toast(e.message, 'err'); }
}
/* ── v0.13.0 左侧历史下拉：同一时刻只展开一个 agent（与 navOpen 手风琴同构，D3）。
   histOpen 进 localStorage；数据缓存在 HIST —— 30s loadAgents 重绘时不闪空白。 ── */
const HIST_LIMIT = 5;                                   // D6 修订（09-22）：每 agent 5 条，跳目录按时间取最近
const TERM_HIST_AGENTS = ['grok', 'claude', 'jcode', 'hermes', 'codex', 'qoder'];   // 与后端 SESSION_STORES 同集合
let histOpen = localStorage.getItem('hub.hist') || '';
const HIST = {};                                        // agent_id -> {items,note,loading,err}

function hhTime(ts) {                                   // 绝对时间：相对时间每轮变化会破 DOM diff
  if (!ts) return '';
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0');
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

/* 跳目录之后，同名会话可能来自不同工程：只在「不属于画像目录」时补一个目录尾名，
   画像目录本身不标（多数条目都是它，标了反而吵）。 */
function hhDir(cwd, home) {
  if (!cwd || !home || cwd === home) return '';
  const seg = String(cwd).replace(/\/+$/, '').split('/').filter(Boolean);
  return seg.length ? seg[seg.length - 1] : '';
}

function histHtml(aid) {
  const h = HIST[aid];
  if (!h || h.loading) return '<div class="nav-hist"><div class="hh-note">读取历史…</div></div>';
  if (h.err) return '<div class="nav-hist"><div class="hh-note">历史读取失败：' + escapeHtml(h.err) + '</div></div>';
  const rows = (h.items || []).map(it =>
    '<div class="hh-row" data-agent="' + escapeHtml(aid) + '" data-sid="' + escapeHtml(it.id) + '"' +
    ' title="' + escapeHtml(it.title) + (it.cwd ? ' ｜ ' + escapeHtml(it.cwd) : '') + '">' +
    '<span class="hh-t">' + escapeHtml(it.title) + '</span>' +
    '<span class="hh-cw">' + hhDir(it.cwd, h.home) + '</span>' +
    '<span class="hh-ts">' + hhTime(it.ts) + '</span></div>').join('');
  const hd = '<div class="hh-hd">历史会话' + (rows ? '（' + h.items.length + '）' : '') + '</div>';
  // note 在“已经有行”时也要显：截断/降级被吞掉的话，残缺结果看着就像完整清单
  // （09-22 jcode 只显 1 条那次，正是“扫描窗口用尽”的 note 没人看见）。
  const tail = (rows && h.note) ? '<div class="hh-note">' + escapeHtml(h.note) + '</div>' : '';
  return '<div class="nav-hist">' + hd +
         (rows || '<div class="hh-note">' + escapeHtml(h.note || '该目录暂无可续会话') + '</div>') + tail + '</div>';
}

function histLoad(aid) {
  HIST[aid] = { items: [], note: '', loading: true, err: '' };
  renderNav();
  api('/api/term/history/' + encodeURIComponent(aid) + '?limit=' + HIST_LIMIT, { headers: termHeaders() })
    .then(d => { HIST[aid] = { items: d.items || [], note: d.note || '', home: d.cwd || '', loading: false, err: '' }; })
    .catch(e => { const m = String((e && e.message) || e);
      // 新前端 + 未重启的旧后端＝路由不存在（FastAPI 回 Not Found）。说人话，别抛生涩 404。
      HIST[aid] = { items: [], note: '', loading: false,
                    err: /not\s*found|404/i.test(m) ? '后端未更新：需重启 agent-hub.service 后生效' : m }; })
    .then(() => renderNav());                            // 无 finally 依赖：老 Safari 也走得到
}

/* 刷新后 histOpen 会从 localStorage 复原，但 HIST 缓存是空的——不补一次拉取，下拉就
   永远停在「读取历史…」（实测 reload 必现）。没存过 token 时干脆收起：留个展开空壳更误导，
   而且 termToken() 会在每次刷新都弹一次口令框。 */
(function histBootstrap() {
  if (!histOpen) return;
  if (!TERM_HIST_AGENTS.includes(histOpen) || !localStorage.getItem('hub.term.token')) {
    histOpen = '';
    localStorage.removeItem('hub.hist');
    return;
  }
  histLoad(histOpen);
})();

async function termResume(agentId, sid) {
  try {
    const d = await api('/api/term/sessions', { method: 'POST',
      headers: termHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ agent_id: agentId, session_id: sid }) });
    toast('已在终端里续聊该历史会话', 'ok');
    gotoChat(agentId, 'term');
    termConnect(d.session.id, agentId, { user: true });
    termRefreshList();
  } catch (e) { toast('续聊失败：' + e.message, 'err'); }
}

/* 菜单行 = 实体行本体 + （命中展开项时）历史块。renderNav 的三分支 map 统一走这里。 */
function navRow(a) { return navItemHtml(a) + (a.id === histOpen ? histHtml(a.id) : ''); }

let _navHtml = '';   // 上一次渲染的菜单 HTML，用于跳过无变化的重写
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
  const html = NAV_ORDER.map(g => {
    const list = lists[g];
    if (q && !list.length) return '';
    const open = q ? true : navOpen === g;
    let body = '';
    if (g === 'infra') {
      NAV_SUB_KINDS.forEach(([k, label]) => {
        const sub = list.filter(a => a.kind === k);
        if (sub.length) body += '<div class="nav-sub">' + label + '</div>' + sub.map(navRow).join('');
      });
      const rest = list.filter(a => !NAV_SUB_KINDS.some(([k]) => k === a.kind));
      if (rest.length) body += '<div class="nav-sub">其他</div>' + rest.map(navRow).join('');
    } else if (g === 'system') {
      body = list.map(([p, label, ic]) =>
        '<button class="nav-item' + (curPage === p ? ' on' : '') + '" data-sys="' + p + '">' +
        ico(ic) + '<span class="lbl">' + label + '</span></button>').join('');
    } else {
      body = list.map(navRow).join('');
    }
    if (!body) body = '<div class="nav-empty">' + (AGENTS.length ? '无匹配' : '加载中…') + '</div>';
    return '<div class="nav-acc' + (open ? ' open' : '') + '">' +
      '<button class="nav-acc-head" data-group="' + g + '" aria-expanded="' + (open ? 'true' : 'false') + '">' +
      ico(open ? 'chevron-down' : 'chevron-right', null, 'caret') +
      ico(NAV_ICONS[g], 'md') +
      '<span class="lbl">' + NAV_GROUPS[g] + '</span>' +
      '<span class="badge">' + list.length + '</span></button>' +
      '<div class="nav-acc-body">' + body + '</div></div>';
  }).join('');
  /* 内容没变就不重写 DOM —— loadAgents 每 30s 刷一次，重写会把 #navTree 的滚动位置弹回顶部 */
  if (html !== _navHtml) { box.innerHTML = html; _navHtml = html; }
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
  syncOpBar();
}
/* v0.10.1：#opBar 无标题也无 tab 时整条收起，窄屏省 37px，宽屏也不留空带 */
function syncOpBar() {
  const ob = $('opBar'), c = $('crumb'), t = $('opTabs');
  if (!ob) return;
  const empty = !((c && c.textContent.trim()) || (t && t.textContent.trim()));
  ob.classList.toggle('void', empty);
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
/* ── v0.13.7 侧栏抽屉：偏好按视口档位分存 ─────────────────────────────
   事故（2026-09-23）：旧实现用一个**与宽度无关**的全局键 hub.sidebar 存折叠态，
   并在**加载时**就写盘。于是一次宽屏访问就把 "展开(0)" 存成全局偏好；手机再打开时
   stored==='0' ⇒ 不收起 ⇒ CSS @media(max-width:767px) 的 .sidebar:not(.collapsed)
   是 position:fixed / width:236px / z-index:46 的白色覆盖层 —— 390px 屏上盖掉 61%
   视口（实测 overlays: [{id:sidebar,bg:rgb(255,255,255),z:46,pct:61}]），用户看到
   就是「整页被白板糊住」。且 resize 只重画终端，抽屉态永不重算 ⇒ 刷新也不会好。

   三条不变量（本文件里全部可被 tests/test_sidebar_breakpoint.py 打到）：
     1 一档一键，宽屏的偏好永不污染窄屏；
     2 加载不写盘，只有真点过才算偏好（污染路径从根上断掉）；
     3 断点只有一个定义（matchMedia 767px，与 CSS 同值），跨断点必重算。 */
const mqNarrow = window.matchMedia('(max-width: 767px)');
const sidebarPrefKey = () => mqNarrow.matches ? 'hub.sidebar.narrow' : 'hub.sidebar.wide';

/* 纯判定，单列成顶层函数是为了让探针能原样抽出**真代码**跑（手抄即假绿）。
   stored：本档已存偏好；legacy：旧的全局键 hub.sidebar。
   legacy 只在宽屏当一次性迁移用 —— 它几乎必然由宽屏写入；窄屏一律回到默认收起，
   这样存量已被污染的手机（值='0'）首屏即自愈，不需用户清缓存。 */
function sidebarWantCollapsed(narrow, stored, legacy) {
  if (stored !== null) return stored === '1';
  if (!narrow && legacy !== null) return legacy === '1';
  return narrow;
}

function initSidebar() {
  const sb = document.getElementById('sidebar'), btn = document.getElementById('btnSideToggle');
  if (!sb || !btn) return;
  const narrow = () => mqNarrow.matches;
  const apply = (c, persist) => {
    sb.classList.toggle('collapsed', c);
    btn.innerHTML = c ? ico('panel-expand', 'xs') : ico('panel-collapse', 'xs') + '<span class="lbl">收起</span>';
    if (persist !== false) localStorage.setItem(sidebarPrefKey(), c ? '1' : '0');
    if (window.syncOverlayMask) syncOverlayMask();   // 遮罩不在这里算，统一走下面那个出口
  };
  /* ③ 遮罩的唯一计算出口（浮层唯一性）：窄屏 且（侧栏抽屉展开 或 任一抽屉浮层在开）
     才存遮罩。以前这里是个与 apply() 平行的写入点，开设置/导航都不过它 —— 所以
     设置抽屉盖住 92% 画面时遮罩还是没开，用户点哪儿都落在抽屉上。 */
  function syncOverlayMask() {
    const m = document.getElementById('sideMask');
    if (!m) return;
    m.classList.toggle('on', mqNarrow.matches &&
      (!sb.classList.contains('collapsed') || (window.overlayAnyOpen ? overlayAnyOpen() : false)));
  }
  // 用命名函数而不是 `window.x = () => {}`：后者 tests/_hub_extract 抽不到，闸门会变成假绿
  window.syncOverlayMask = syncOverlayMask;
  window.collapseSidebar = () => { if (mqNarrow.matches && !sb.classList.contains('collapsed')) apply(true); };
  window.isNarrow = () => mqNarrow.matches;   // 断点单一真源：外面只准问这个，不准再写 767
  // 首屏解析档位偏好：persist=false ⇒ 加载本身不再写盘（老代码正是在这一步把宽屏的"展开"存成全局值）
  const resolve = () => apply(sidebarWantCollapsed(narrow(), localStorage.getItem(sidebarPrefKey()),
                                                   localStorage.getItem('hub.sidebar')), false);
  resolve();
  btn.onclick = () => apply(!sb.classList.contains('collapsed'));
  // 跨断点（转屏/窗口拖窄/桌面缩放）重新解析本档偏好；老代码只重画终端，抽屉状态永远停在加载那一刻
  const onBreak = () => resolve();   // apply() 自己会带遮罩，不再加一个平行的掩码同步路径
  if (mqNarrow.addEventListener) mqNarrow.addEventListener('change', onBreak);
  else if (mqNarrow.addListener) mqNarrow.addListener(onBreak);   // Safari < 14
  // 事件委托：静态常驻项 + 手风琴动态项（含系统页）统一走这里
  sb.addEventListener('click', e => {
    let el = e.target.closest('button[data-page]');
    if (el) { go(el.dataset.page); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-settings]');
    if (el) { openSettings(); return; }   // 「设置」以前走 inline onclick 旁路委托 ⇒ 抽屉永远不收
    el = e.target.closest('button[data-sys]');
    if (el) { go(el.dataset.sys); if (narrow()) apply(true); return; }
    el = e.target.closest('.hh-row');                     // 历史条目：续聊，窄屏顺手收抽屉
    if (el) { termResume(el.dataset.agent, el.dataset.sid); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-entity]');
    if (el) {
      const aid = el.dataset.entity;
      if (TERM_HIST_AGENTS.includes(aid)) {
        if (histOpen === aid) histOpen = '';              // 再点当前行 = 只收起，不离开页面
        else { histOpen = aid; histLoad(aid); }           // 展开新的（自动收起上一个）
        localStorage.setItem('hub.hist', histOpen);
        renderNav();
      }
      openEntity(aid);                                    // 进工作台照旧（A：两件事一次点击）
      if (narrow() && !histOpen) apply(true);
      return;
    }
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
  // 点遮罩 = 一次关干净（抽屉 + 侧栏）：手机上没 ESC 键，这是唯一逃生路径
  if (mask) mask.addEventListener('click', () => {
    if (window.closeDrawers) closeDrawers();
    apply(true);
    if (window.syncOverlayMask) syncOverlayMask();
  });
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
    const first = $('cmdList').querySelector('.cmd-item');
    if (first && !q) closeCmd(); else if (first) first.click();
  } else if (e.key === 'Escape') closeCmd();
});
/* 快捷键不许在"正在输入"的地方劫持按键（改前实测的两种破坏）：
   · 终端里敲 `/`（路径分隔符，一天几百次）→ preventDefault + 焦点跳搜索框，
     之后所有输入都进了搜索框，画面看起来像"打字没反应"；
   · bash 的 Ctrl+K（kill-line）→ 被拿去开命令面板；
   · vim 的 Esc → 归 pty 的用，不能顺手去关我的浮层。
   规则：输入位（含 xterm 自己的 textarea）里只放行"搜索框的 Esc 清空"这一条，
   其余一律 return；非输入位维持原有行为。 */
function keyTargetIsEditing(e) {
  const t = e && e.target;
  if (!t || !t.closest) return false;
  if (t.closest('.xterm')) return true;
  const tag = t.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable === true;
}
document.addEventListener('keydown', e => {
  const editing = keyTargetIsEditing(e);
  if (e.key === 'Escape') {
    // 终端里的 Esc 原样给 pty；只有搜索框自己认领"Esc 清空"
    if (editing && !(e.target && e.target.id === 'navSearch')) return;
    closeDetail();
    closeSettings();
    const si = $('navSearch');
    if (si && si.value) { si.value = ''; renderNav(); return; }
    return;
  }
  if (editing) return;
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    $('cmdMask').classList.contains('on') ? closeCmd() : openCmd();
  } else if (e.key === '/') {
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
  // 只看 termPane 的 on 不够：整块 page-chat 被 display:none 藏起来时它仍是 on，白轮询
  if (document.getElementById('page-chat').classList.contains('on')
      && document.getElementById('termPane').classList.contains('on')) termRefreshList();
}, 6000);
loadAgents();
go(localStorage.getItem('hub.page') || 'classroom');  // T9：默认落点 = 上次所在页（chatPick/chatMode 已在声明处恢复）
