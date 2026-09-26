/* Agent Hub v0.3.0 教室视图 — 复刻 Agent_Manager(Dashboard/Manager/Memory/Ports) 交互语义
   v0.10 chat: 动态模型选择器 + 工作目录 + 会话管理（服务 claude/jcode）*/
/* ── 断点唯一真源（不变量 3）─────────────────────────────────────
   全站只允许这一处 `matchMedia('(max-width: 767px)')`。放在 01 是因为 05 的顶层
   语句也要读它，而 `window.isNarrow` 要等 initSidebar 跑起来才被赋值。 */
const HUB_NARROW_MQ = window.matchMedia('(max-width: 767px)');
const hubNarrow = () => HUB_NARROW_MQ.matches;

'use strict';

/* ── localStorage 守卫（v0.13.13，2026-09-24）──────────────────────────────────
   为什么要这一层：全站原有 45 处**裸**读写 localStorage，任何一处抛异常都会打断启动链。
   本项已有过两次“顶层语句抛异常 ⇒ 整段 hub.js 当场死亡 ⇒ 端侧看起来随机坏”的事故
   （09-23 TDZ、09-23 响应体被截断）。会抛的三种真实场景：
     ① 隐私模式 / WebView 禁 DOM Storage —— 连取 window.localStorage 本身都抛；
     ② 配额满（QuotaExceededError / NS_ERROR_DOM_QUOTA_REACHED）—— setItem 抛；
     ③ 跨源 iframe 被策略拦下 —— 读写都抛。
   口径：**只加兜底，不改语义**。取不到给 fallback（默认 null，与浏览器原生「键不存在」
   返回值一致）；写失败返回 false，**不假装写进去了**；异常一律计数 + 留最后一条摘要，
   经 window.__lsDiag 上 ?diag=1 面板 ⇒ 端侧能自证“是不是存储被禁了”。
   位置必须在 01 最前面：02/05/06 都有顶层语句直接读存储（let chatPick = …  /
   const navOpenStored = …  /  go(lsGet('hub.page'))），顺序由 tests/test_ls_guard.py 钉住。
   两个计数助手自带初始化：这层存在的理由就是「不许抛」，它自己更不能抛。 */
window.__lsDiag = { fails: 0, ok: 0, lastErr: '' };
function lsDiagStore() {
  return window.__lsDiag || (window.__lsDiag = { fails: 0, ok: 0, lastErr: '' });
}
function lsDiagHit() { lsDiagStore().ok++; }
function lsDiagFail(op, key, e) {
  const d = lsDiagStore();
  d.fails++;
  d.lastErr = op + ' ' + key + ': ' + String((e && (e.name || e.message)) || e).slice(0, 90);
}
function lsGet(key, fallback) {
  try {
    const v = window.localStorage.getItem(key);
    lsDiagHit();
    return v === null ? (fallback === undefined ? null : fallback) : v;
  } catch (e) {
    lsDiagFail('get', key, e);
    return fallback === undefined ? null : fallback;
  }
}
function lsSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
    lsDiagHit();
    return true;
  } catch (e) {
    lsDiagFail('set', key, e);
    return false;      // ★调用方可以选择察觉；写失败不静默假装成功
  }
}
function lsRemove(key) {
  try {
    window.localStorage.removeItem(key);
    lsDiagHit();
    return true;
  } catch (e) {
    lsDiagFail('rm', key, e);
    return false;
  }
}

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

let AGENTS = [], PORTS = [], portsLoaded = false, memLoaded = false, skillsLoaded = false, kbLoaded = false;
/* v0.13.30 本机项目页懒加载标志：声明在 01（拼接序最先）而赋值/读取在 09 分片。
   lpLoaded 的 var 声明在 09 里会提升到 go() 可见——两处同名 var 是刻意冗余，
   保证 06 分片顶层 go() 调用（在 09 声明之前执行）读到的是 undefined 而非 TDZ。 */
var lpLoaded = false;

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
    lsRemove('hub.term.token');
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
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    /* v0.13.27 additive：把 HTTP 状态码与响应体挂到错误对象上（既有调用方只读
       .message 不受影响）。runlog 页靠 http===401/503 区分「鉴权态」与「故障态」；
       技能正文 409 靠 detail.candidates 渲染路候选——不再靠正则猜文案。 */
    err.http = r.status;
    err.payload = data;
    throw err;
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

/* ── v0.13.27 三态加载助手（B 统一加载）──────────────────────────────
 * 三中心的 loader 统一走 busy→数据/失败 三态（此前失败只 toast，列表区停旧内容
 * ——用户分不清「没数据」与「还没加载」与「加载挂了」）。遥测页「加载中…」
 * 占位先例（index.html:988-991）从页内文案升格为全站函数。
 * 主内容区 inline onclick 合法（侧栏禁令不涉这里——浮层三条配套红线原文口径）。 */
function boxBusy(id, msg) {
  const el = $(id);
  if (el) el.innerHTML = '<div class="hint">' + escapeHtml(msg || '加载中…') + '</div>';
  return !!el;
}
function boxFail(id, err, retryFn) {
  const el = $(id);
  if (!el) return;
  const retry = retryFn ? ' <button class="btn sm" onclick="' + retryFn + '()">重试</button>' : '';
  el.innerHTML = '<div class="hint" style="color:var(--st-error,var(--danger))">加载失败：' +
    escapeHtml(String(err && err.message || err || '')) + retry + '</div>';
}

/* 三中心健康（供侧栏系统项挂 s-badge 点；loader 完成时写入）。
 * var 声明防 TDZ：renderNav（05 分片，拼接序在前）会读它。 */
var CENTER_HEALTH = { memory: '', skills: '', kb: '' };

/* ── 浮层唯一性（2026-09-23 事故：窄屏「设置」抽屉盖掉 92% 画面且没人关）────────
   三个浮层（侧栏抽屉 / detailDrawer / settingsDrawer）此前各开各的：
   开设置不关侧栏、导航不收抽屉、遮罩只管侧栏 —— 窄屏抽屉宽 min(400px,92vw)，
   一旦残留就把整页压成"白板 + 点不动"。规则钉死三条：
   ① 同一时刻最多一个抽屉是 on（开新的必先清旧的）；
   ② 导航 = 清抽屉（go 里做，不留给调用方自觉）；
   ③ 遮罩只有一个计算出口（06 的 syncOverlayMask），且点它一定关干净 ——
      手机上没有 ESC 键，点空白是唯一逃生路径。 */
const OVERLAY_IDS = ['detailDrawer', 'settingsDrawer', 'skillDocDrawer'];
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
  lsSet('hub.page', page);  // T9：记忆上次所在页，刷新后回落
  renderNav();            // v0.7：同步左侧手风琴（实体/系统项的选中态）
  renderPageCrumb(page);  // v0.7：系统页面包屑（实体页由 renderModeBar 接管）
  if (page === 'memory' && !memLoaded) { memLoaded = true; loadMemories(); loadDoc('l2'); loadDoc('l3'); }
  if (page === 'skills' && !skillsLoaded) { skillsLoaded = true; loadSkills(); loadSkillBudget(); }
  if (page === 'kb' && !kbLoaded) { kbLoaded = true; loadKbStatus(); kbBrowse(); }
  if (page === 'localprojects' && !lpLoaded) { lpLoaded = true; loadLocalProjects(); }   // v0.13.30 本机项目页（09 分片）
  if (page === 'ports' && !portsLoaded) { portsLoaded = true; loadPorts(); }
  if (page === 'telemetry') loadTelemetry();
  if (page === 'runlog') loadRunlog(true);   // v0.13.27：每次进页刷新（与 telemetry 同口径，不设 loaded 位）
  if (page === 'chat') renderChatSide();
  if (page === 'tasks') { fillAgentSelect($('taskAgent'), true); loadRuns(); }
  if (page === 'jobs') { fillAgentSelect($('jobAgent'), false); loadJobs(); }
  if (page === 'mcp') { loadMcp(); loadAcl(); }
  if (page === 'assets') loadAssets();   // P4 资产面板（只读门面聚合，07-asset-panel.js）
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
  /* v0.13.29：claude 实体的详情抽屉追加「CloudCLI 项目」面板——用户核心诉求
     「加载本机所有项目、精确显示名称、点击快速开始」。列表直读 auth.db 与
     cloudcli 服务活死解耦；点「开始会话」走 cloudcliStart（04 分片）。 */
  if (id === 'claude' && typeof loadCloudcliProjects === 'function') loadCloudcliProjects();
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
function settingsPasscode() { return lsGet('hub.passcode') || ''; }

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
    lsSet('hub.passcode', pc);  // 验证通过才缓存
    const box = $('settingsTokenBox');
    box.style.display = 'block';
    $('settingsTokenVal').dataset.token = d.term_token || '';
    $('settingsTokenVal').textContent = d.set ? (d.term_token || '（空）') : '服务端未配置 TERM_TOKEN（终端鉴权走启动时自动生成的临时 token）';
    $('settingsToggleShow').textContent = '隐藏';
    if (!d.set) toast('服务端未显式配置 TERM_TOKEN', 'err');
  } catch (e) {
    lsRemove('hub.passcode');  // 口令错/失效则不缓存
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
  lsSet('hub.term.token', t);
  toast('已应用到终端（本浏览器后续自动携带）', 'ok');
}

function settingsClearPasscode() {
  lsRemove('hub.passcode');
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

let chatPick = lsGet('hub.chat.pick') || 'claude';
/* v0.13.21 换键 hub.chatmode. → hub.chatmode2.：旧键里躺着的是**默认推导**被无差别落盘形成的
   假偏好（gotoChat 每次点击都写）。不换键，就算改了默认形态，已被旧值钉在嵌入页的浏览器仍进不去
   终端 —— 与 09-23「交互结果不得依赖存量」同源。新键只在用户**点名**形态时写。 */
const MODE_KEY = 'hub.chatmode2.';
// 上次**显式**选过的形态；空串=没选过，由 applyChatMode 走 defaultModeOf（唯一真源）
let chatMode = lsGet(MODE_KEY + chatPick) || '';
function sessKey(id) { return 'hub.sess.' + id; }

function entityById(id) { return AGENTS.find(a => a.id === id); }
/* 工作台默认形态 —— **唯一真源**（openEntity 也走这里；两处各写一份优先级必然漂移）。
   有原生终端的 Agent 先给终端：它的独立 Web 宿主（claude←cloudcli :3010）自带一套登录，
   嵌进 hub 就是一张要重新登录的白页，而终端页里的 TUI 与本机命令行完全一致。
   宿主界面保留为可切换的第二形态（终端页头部「原生界面」按钮）。 */
function defaultModeOf(a) {
  const es = (a && a.entries) || [];
  const has = t => es.some(e => e.type === t);
  if (a && a.kind === 'agent' && has('term')) return 'term';
  if (has('embed')) return 'embed';
  if (has('term')) return 'term';
  return 'chat';
}
function gotoChat(id, mode) {
  chatPick = id;
  lsSet('hub.chat.pick', id);  // T9：记忆上次实体
  const a = entityById(id);
  chatMode = mode || lsGet(MODE_KEY + id) || defaultModeOf(a) || 'chat';
  if (mode) lsSet(MODE_KEY + id, mode);   // 只有点名了形态才算偏好；推导出来的不写盘
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
  lsSet('hub.chat.pick', id);
  // T9：模式记忆优先——只认用户**显式**选过的形态（新键），没选过就走默认
  chatMode = lsGet(MODE_KEY + id) || defaultModeOf(entityById(id));
  renderChatSide();
}

function applyChatMode() {
  const a = entityById(chatPick);
  renderModeBar(a);
  const en = $('chatEntName');
  if (en) en.textContent = a ? a.name : '';
  if (!a) return;   // 实体已被删除（localStorage 里留着旧 pick）：保持默认面板，不再往下猜模式
  const es = a.entries || [];
  // 形态对该实体不可用（没选过、或 entry 被删/改）：回落默认形态。
  // 这是**推导**，绝不写盘 —— 一写就把默认固化成偏好，改默认也救不回来。
  if (!es.some(e => e.type === chatMode)) chatMode = defaultModeOf(a) || 'chat';
  // 面板头互切按钮：菜单行内的动作图标自 v0.12.3 起 display:none、模式 tab 也已停用，
  // 站内不留这条出口，embed 与 term 就互相锁死（点进哪个就再也切不到另一个）。
  const toTerm = $('embedToTerm'), toEmbed = $('termToEmbed');
  if (toTerm) toTerm.style.display = es.some(e => e.type === 'term') ? '' : 'none';
  if (toEmbed) toEmbed.style.display = es.some(e => e.type === 'embed') ? '' : 'none';
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
  lsSet(MODE_KEY + chatPick, m);  // T9：按实体记忆模式（这里是用户点名切换 ⇒ 算真偏好）
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
  let t = lsGet('hub.term.token');
  if (!t) {
    t = prompt('请输入终端鉴权 TERM_TOKEN（也可在右上角「设置」查看后一键应用）') || '';
    if (t) lsSet('hub.term.token', t);
  }
  return t;
}
function termHeaders(extra) {
  return Object.assign({ 'X-TERM-TOKEN': termToken() }, extra || {});
}

function wsUrl(path) {
  const t = lsGet('hub.term.token');
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
  const sid = lsGet(sessKey(chatPick));
  if (!sid) { box.innerHTML = '<div class="hint" style="margin:auto">开始新会话（' + escapeHtml(chatPick) + '）</div>'; return; }
  try {
    const d = await api('/api/sessions/' + encodeURIComponent(sid) + '/messages');
    for (const m of d.messages || []) {
      box.appendChild(renderChatMsg(m));
    }
  } catch (e) { lsRemove(sessKey(chatPick)); }
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
  let sid = lsGet(sessKey(chatPick));
  if (!sid) { sid = Math.random().toString(36).slice(2, 14); lsSet(sessKey(chatPick), sid); }
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
  const saved = lsGet('hub.model.' + chatPick) || '';
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
  lsSet('hub.model.' + chatPick, e.target.value);
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
  const saved = lsGet('hub.cwd.' + chatPick) || '';
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
    lsSet('hub.cwd.' + chatPick, v.trim());
    toast('已设 cwd: ' + v.trim(), 'ok');
  }
}
$('chatCwd')?.addEventListener?.('change', e => {
  lsSet('hub.cwd.' + chatPick, e.target.value);
});

/* ── 会话管理 ── */
let CHAT_SESSIONS = [];
async function chatSessLoad() {
  const sel = $('chatSessList');
  if (!sel) return;
  const cur = lsGet(sessKey(chatPick)) || '';
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
  lsRemove(sessKey(chatPick));
  openChatSession();
  toast('已开新会话', 'ok');
}
async function chatSessRename() {
  const sid = lsGet(sessKey(chatPick));
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
  lsSet(sessKey(chatPick), v);
  openChatSession();
});
// T7：删除当前会话（后端 DELETE /api/sessions/{id} 连同消息一并清理）
async function chatSessDel() {
  const sid = lsGet(sessKey(chatPick));
  if (!sid) return toast('当前无会话', 'err');
  if (!confirm('删除当前会话 ' + sid.slice(0, 8) + ' 及其全部消息？不可恢复。')) return;
  try {
    await api('/api/sessions/' + encodeURIComponent(sid), { method: 'DELETE' });
    lsRemove(sessKey(chatPick));
    openChatSession();
    toast('会话已删除', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* applyChatMode 已内置：切换到 chat 模式时三联动刷新 */

/* ── 记忆中心 ─────────────────────────────────────── */

/* ── CloudCLI 项目直达（v0.13.29）──────────────────────────────────────
 * 用户诉求：「cloudcli 项目检索要完善、无法加载本机所有项目、精确显示项目名称、
 * 点击对应项目快速开始」。
 * 列表：GET /api/cloudcli/projects（直读 auth.db，与 cloudcli 服务活死解耦）；
 * 启动：POST /api/cloudcli/start {path} → {sessionId, url} → iframe 直达
 * /session/{id}（先 gotoChat('claude') 进 embed 模式再覆写 src——dataset 同步
 * 防 applyChatMode 重置；embed 顶栏地址行同步）。
 * 嵌入 iframe 的鉴权态由 cloudcli 自己的 localStorage 管（跨源但同浏览器持久，
 * 知识文档 50 号已证）；hub 不传 token 不越权。 */
async function loadCloudcliProjects() {
  const body = $('detailBody');
  if (!body) return;
  const holder = document.createElement('div');
  holder.id = 'ccProjects';
  holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目（本机全部）</div><div class="hint">加载中…</div>';
  body.appendChild(holder);
  try {
    const d = await api('/api/cloudcli/projects');
    if (!d.ok) {
      holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目</div>' +
        '<div class="hint" style="color:var(--st-error,var(--danger))">加载失败：' + escapeHtml(d.error || '?') + '</div>';
      return;
    }
    const rows = (d.projects || []).map(p =>
      '<div class="mem-item" style="gap:6px">' +
      '<p style="min-width:0"><b>' + escapeHtml(p.name) + '</b>' + (p.starred ? ' ★' : '') +
      (p.sessions ? ' <span class="hint">' + p.sessions + ' 会话</span>' : '') +
      '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
      escapeHtml(String(p.path).slice(0, 48)) +
      (p.last_activity ? ' · ' + String(p.last_activity).slice(5, 16).replace('T', ' ') : '') + '</span></p>' +
      '<button class="btn sm" style="align-self:center" onclick="cloudcliStart(\'' +
      escapeHtml(String(p.path).replace(/'/g, "\\'")) + '\')" title="在 CloudCLI 开始此项目的新会话">▶ 开始会话</button></div>').join('');
    holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目（' + (d.count || 0) + ' 个 · 点「开始会话」直达）</div>' +
      '<div class="tscroll" style="max-height:300px;overflow-y:auto">' + (rows || '<div class="hint">无项目</div>') + '</div>';
  } catch (e) {
    holder.innerHTML = '<div class="hint" style="margin:10px 0 4px">CloudCLI 项目</div>' +
      '<div class="hint" style="color:var(--st-error,var(--danger))">加载失败：' + escapeHtml(e.message || '') + '</div>';
  }
}

async function cloudcliStart(path) {
  toast('CloudCLI 创建会话中…');
  try {
    const d = await api('/api/cloudcli/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    });
    const full = 'http://127.0.0.1:3010' + (d.url || '');
    /* 先进 embed 模式（gotoChat 会触发 applyChatMode 重置 iframe src），
       再覆写 src 到 /session/{id}——dataset 必须同步，否则下次 applyChatMode
       会把它重置回实体 ui.url。地址行（embedUrlHint 的 span）同步显示。 */
    if (typeof gotoChat === 'function') gotoChat('claude');
    const f = $('embedFrame');
    if (f) {
      const url = lanUrl(full);
      f.dataset.src = url;
      f.src = url;
      const hintTxt = $('embedUrlHint') ? $('embedUrlHint').querySelector('span') : null;
      if (hintTxt) hintTxt.textContent = url;
    }
    closeOverlay('detailDrawer');
    toast('会话已创建：' + (d.session_name || d.sessionId), 'ok');
  } catch (e) { toast('创建会话失败：' + e.message, 'err'); }
}

/* ── v0.13.28 源标识与逐路徽标（记忆页/kb 页共用渲染器）────────────────
 * SRC_ABBR：源名缩写（结果卡 tag 位宽恒定，不因长源名换行）。
 * srcTag()：三变体 .tag.src-doc/.src-session/.src-index（明度区分，不彩虹——
 *   v0.9 设计系统「色相只出现在语义上」的约束；裸拼 `tag turbovec` 无 CSS 定义
 *   静默灰底的根因修复）。kb 路名短别名也入表（tdai/workspace/archived）。 */
const SRC_ABBR = {
  claude_mem: 'CM', claude_projects: 'CP', pi_sessions: 'PI', codex_sessions: 'CX',
  grok_memory: 'GK', hermes_memory: 'HM', workbuddy_memory: 'WB',
  workspace_files: 'WS', archived_sessions: 'AR',
  turbovec: 'TV', tdai_l1: 'TD', tdai: 'TD', local: 'L1'
};
function srcTag(srcName) {
  const key = String(srcName || '');
  const abbr = SRC_ABBR[key] || (key.slice(0, 2).toUpperCase() || '??');
  const cls = (key === 'turbovec' || key === 'local') ? 'src-index'
    : (key.indexOf('session') >= 0 || key === 'claude_mem') ? 'src-session'
    : 'src-doc';
  return '<span class="tag src-' + cls + '" title="' + escapeHtml(key) + '" style="align-self:flex-start">' +
    escapeHtml(abbr) + '</span>';
}
/* fedBadges：逐路健康徽标行（绿=ok 有命中 / 黄=ok 零命中 / 红=挂了点名+错误 tooltip）。
 * 记忆页与 kb 页两处同构消费——从 memFedSearch 内联抽出（DRY，两页口径归一）。 */
function fedBadges(d) {
  return (d.backends || []).map(b => {
    const col = b.ok ? (b.count ? 'var(--st-running)' : 'var(--st-installed)') : 'var(--st-error)';
    const nm = b.ok ? b.name : b.name + ' 挂了';
    return '<span style="display:inline-flex;align-items:center;gap:3px;margin-right:10px">' +
      '<span class="hdot" style="background:' + col + '"></span>' +
      '<span class="hint">' + escapeHtml(nm) + (b.ok && b.count != null ? ' ' + b.count : '') +
      (b.error ? ' <span title="' + escapeHtml(String(b.error).slice(0, 100)) + '">?</span>' : '') + '</span></span>';
  }).join('');
}

/* ── 联邦检索（v0.13.27 E 缺口）：6 路 RRF，逐路 backends 徽标（四态口径
 *   与资产面板一致：ok/空/降级/端点挂，不把「挂了」渲染成「没有」）。
 *   按需触发，进页不自动跑。 ─────────────────────────────────────────── */
async function memFedSearch() {
  const q = ($('memFedQ') ? $('memFedQ').value : '').trim();
  if (!q) { toast('输入检索词', 'err'); return; }
  const sources = Array.from($('memFedSrcs').selectedOptions).map(o => o.value).join(',');
  const hint = $('memFedHint');
  if (hint) hint.textContent = '检索中…';
  boxBusy('memFedResults', '联邦检索中…');
  const badges = $('memFedBadges');
  try {
    const d = await api('/api/memory/search?q=' + encodeURIComponent(q) + '&sources=' + encodeURIComponent(sources || 'local,tdai'));
    if (hint) hint.textContent = (d.count || 0) + ' 条 · ' + (d.took_ms || '?') + 'ms · ' + (d.engine || 'none');
    /* 逐路徽标：绿=ok 有命中 / 黄=ok 零命中 / 红=挂了（点名，不糊成绿） */
    if (badges) badges.innerHTML = fedBadges(d);
    renderQueryBar('memFedQueryBar', q, d.count || 0, 'memFedClear');
    $('memFedResults').innerHTML = (d.memories || []).map(m =>
      '<div class="mem-item"><span class="tag ' + (m.category || m.type || 'fact') + '" style="align-self:flex-start">' +
      escapeHtml(m.category || m.type || m.layer || '?') + '</span>' +
      '<p>' + escapeHtml(String(m.content || '').slice(0, 300)) +
      '<br><span class="hint">' + escapeHtml(m.source || m.id || '') + '</span></p></div>').join('') ||
      '<div class="hint">零命中。' + ((d.degraded || []).length ? '降级路：' + d.degraded.join(',') + '（清单不完整，不代表没资产）' : '全部路可用且零命中（真没有）') + '</div>';
  } catch (e) {
    if (hint) hint.textContent = '加载失败';
    boxFail('memFedResults', e, 'memFedSearch');
  }
}

/* v0.13.28 批3：检索回退。用户痛点「搜索完成后无法回退到记忆主界面」——
 * 检索结果覆写结果区后没有清空出口，只能刷新页面。设计三条：
 * ① QueryBar（当前检索 q · N 条 · 清空↺）由检索成功尾部渲染，失败态也挂（可撤销失败展示）；
 * ② Clear 还原初始文案+清 hint/badges/输入框——结果区回到「输入关键词开始检索」；
 * ③ **不碰 CENTER_HEALTH**：侧栏健康点是三中心体检态（上次加载成败的记录），
 *   与「用户清空本次检索结果」语义无关，清空不许把 ok 洗成未知。 */
function renderQueryBar(id, q, n, clearFn) {
  const bar = $(id);
  if (!bar) return;
  bar.style.display = '';
  bar.innerHTML = '<span class="hint">当前检索「<b>' + escapeHtml(q) + '</b>」· ' + n + ' 条</span>' +
    '<button class="btn ghost sm" onclick="' + clearFn + '()">清空 ↺</button>';
}
function memFedClear() {
  const bar = $('memFedQueryBar');
  if (bar) { bar.style.display = 'none'; bar.innerHTML = ''; }
  const results = $('memFedResults');
  if (results) results.innerHTML = '输入关键词开始联邦检索';
  const badges = $('memFedBadges');
  if (badges) badges.innerHTML = '';
  const hint = $('memFedHint');
  if (hint) hint.textContent = '';
  const input = $('memFedQ');
  if (input) input.value = '';
  /* 注意：CENTER_HEALTH 故意不清（见函数头注释③） */
}

async function loadMemories() {
  const p = new URLSearchParams();
  if ($('memQ').value.trim()) p.set('q', $('memQ').value.trim());
  if ($('memCat').value) p.set('category', $('memCat').value);
  boxBusy('memList');
  try {
    const d = await api('/api/memory/l1?' + p.toString());
    CENTER_HEALTH.memory = 'ok';
    $('memList').innerHTML = (d.memories || []).map(m =>
      '<div class="mem-item"><span class="tag ' + m.category + '" style="align-self:flex-start">' + m.category + '</span>' +
      '<p>' + escapeHtml(m.content) + '<br><span class="hint">' + escapeHtml(m.source || '') + ' · ' + (m.created_at || '').slice(0, 10) + '</span></p>' +
      '<button class="btn sm danger" onclick="delMemory(' + m.id + ')">' + ico('x') + '</button></div>').join('') ||
      '<div class="hint">空空如也。可手动添加，或由指挥官/外部 Hook 写入。</div>';
  } catch (e) {
    CENTER_HEALTH.memory = 'err';
    boxFail('memList', e, 'loadMemories');   // 失败上屏（不再只 toast——列表区停旧内容=分不清挂没挂）
  }
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


/* ── 技能中心（v0.13.26 批4）────────────────────────── */

let SKILLS = [], SKILL_ROUTES = [];

async function loadSkills() {
  boxBusy('skillList');
  try {
    const d = await api('/api/skill/list');
    CENTER_HEALTH.skills = (d.degraded || []).length ? 'warn' : 'ok';
    SKILLS = d.items || d || [];
    // 发现点下拉（过滤器 + 安装选择器共用）
    SKILL_ROUTES = (d.backends ? d.backends.filter(b => b.name !== 'tdai').map(b => b.name) : []) || [];
    const opt = SKILL_ROUTES.map(r => '<option value="' + r + '">' + r + '</option>').join('');
    $('skillRoute').innerHTML = '<option value="">全部发现点</option>' + opt;
    $('instFrom').innerHTML = opt;
    $('instTo').innerHTML = opt;
    $('instName').innerHTML = SKILLS.map(s => '<option value="' + escapeHtml(s.name) + '">' + escapeHtml(s.name) + '</option>').join('');
    $('skillHint').textContent = SKILLS.length + ' 个技能 · ' + SKILL_ROUTES.length + ' 路发现点';
    if ((d.degraded || []).length) $('skillHint').textContent += ' · 降级路：' + d.degraded.join(',');
    renderSkillList();
  } catch (e) {
    CENTER_HEALTH.skills = 'err';
    boxFail('skillList', e, 'loadSkills');
  }
}

function renderSkillList() {
  const q = ($('skillQ').value || '').toLowerCase();
  const route = $('skillRoute').value;
  const rows = SKILLS.filter(s =>
    (!q || String(s.name || '').toLowerCase().includes(q) || String(s.description || '').toLowerCase().includes(q)) &&
    (!route || (s.routes || []).includes(route) || s.route === route));
  $('skillList').innerHTML = rows.map(s => {
    const rt = (s.routes && s.routes.length) ? s.routes[0] : s.route;   // 首路作为查看默认 route
    return '<div class="mem-item"><span class="tag agent" style="align-self:flex-start">' + escapeHtml(s.name) + '</span>' +
    '<p>' + escapeHtml(String(s.description || '').slice(0, 160)) +
    '<br><span class="hint">' + escapeHtml((s.routes || [s.route]).join(', ')) + '</span></p>' +
    '<button class="btn sm" title="读全文（脱敏）" onclick="skillRead(\'' + escapeHtml(String(s.name || '')).replace(/'/g, "\\'") + '\',\'' + escapeHtml(String(rt || '')) + '\')">查看</button></div>';
  }).join('') ||
    '<div class="hint">没有匹配的技能（' + SKILLS.length + ' 总数）</div>';
}

/* 技能正文查看（C 缺口）：调 /api/skill/read 弹 skillDocDrawer 抽屉。
 * 409 多路冲突时读 err.payload.detail.candidates 渲染候选按钮（api() 已挂 payload）。
 * 抽屉只经 openOverlay 开（浮层三条红线：禁止裸 classList.add('on')）。 */
async function skillRead(name, route) {
  const title = $('skillDocTitle'), body = $('skillDocBody');
  if (title) title.textContent = name + (route ? ' · ' + route : '');
  if (body) body.textContent = '读取中…';
  openOverlay('skillDocDrawer');
  try {
    const d = await api('/api/skill/read?name=' + encodeURIComponent(name) + (route ? '&route=' + encodeURIComponent(route) : ''));
    let txt = d.content || '';
    if (d.truncated) {
      txt += '\n\n[正文 ' + (d.bytes_total || '?') + 'B 超过读取上限已截断；需要全文请直接读磁盘路径 ' + (d.realpath || '?') + ']';
    }
    if (body) body.textContent = txt;
  } catch (e) {
    const cands = (e.payload && e.payload.detail && e.payload.detail.candidates) || [];
    if (e.http === 409 && cands.length && body) {
      body.innerHTML = '<div class="hint">同名多路且指向不同文件，请选一路：</div>' +
        cands.map(c => '<div class="mem-item" style="cursor:pointer" onclick="skillRead(\'' +
          escapeHtml(String(name)).replace(/'/g, "\\'") + '\',\'' + escapeHtml(String(c.route || '')) + '\')">' +
          '<b>' + escapeHtml(String(c.route || '')) + '</b> <span class="hint">' +
          escapeHtml(String(c.path || '')) + (c.via_symlink ? '（软链）' : '') + '</span></div>').join('');
    } else if (body) {
      body.textContent = '读取失败：' + (e.message || e);
    }
  }
}
function closeSkillDoc() { closeOverlay('skillDocDrawer'); }

async function skillInstall() {
  const name = $('instName').value;
  const from = $('instFrom').value;
  const to = Array.from($('instTo').selectedOptions).map(o => o.value).filter(t => t !== from);
  if (!name || !from || !to.length) { toast('选择技能、源与至少一个目标', 'err'); return; }
  try {
    const d = await api('/api/skill/install', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, from_route: from, targets: to }) });
    toast('已软链：' + (d.created || []).join(', ') + (d.existed && d.existed.length ? '（幂等跳过 ' + d.existed.join(', ') + '）' : ''), 'ok');
    loadSkills();
  } catch (e) { toast(e.message, 'err'); }
}

async function loadSkillBudget() {
  const tok = parseInt($('skillTok').value, 10) || 800;
  boxBusy('skillBudget');
  try {
    const d = await api('/api/skill/budget?max_tokens=' + tok);
    const full = (d.full || []).map(f => escapeHtml(f.name) + (f.description ? ' — ' + escapeHtml(f.description.slice(0, 80)) : ''));
    const names = (d.name_only || []).map(n => escapeHtml(n));
    $('skillBudget').innerHTML =
      '预算 ' + d.budget + ' tok · 实际约 ' + d.used_est + ' · 全条目 ' + full.length + ' / 仅名 ' + names.length +
      (d.truncated ? ' / <b>被裁 ' + d.truncated + '</b>' : '') + '（共 ' + d.total + '）<br>' +
      full.concat(names).map(s => '· ' + s).join('<br>');
  } catch (e) { boxFail('skillBudget', e, 'loadSkillBudget'); }
}

/* ── 知识库中心（v0.13.26 批4）──────────────────── */

async function kbSearch() {
  const q = $('kbQ').value.trim();
  if (!q) { toast('输入检索词', 'err'); return; }
  const routes = Array.from($('kbRoutes').selectedOptions).map(o => o.value).join(',');
  $('kbHint').textContent = '检索中…';
  boxBusy('kbResults');
  try {
    const d = await api('/api/kb/search?q=' + encodeURIComponent(q) + '&routes=' + encodeURIComponent(routes) + '&k=12');
    CENTER_HEALTH.kb = 'ok';
    $('kbHint').textContent = d.count + ' 条 · ' + d.took_ms + 'ms · ' + (d.engine || '');
    /* v0.13.28 批3/批4：逐路徽标（与记忆页 fedBadges 同构——替换只 toast 的半吊子表态） */
    const badges = $('kbBadges');
    if (badges) badges.innerHTML = fedBadges(d);
    renderQueryBar('kbQueryBar', q, d.count || 0, 'kbClear');
    $('kbResults').innerHTML = (d.results || []).map(r => {
      /* v0.13.28 批4：源徽标走 SRC_ABBR + src-* 三变体（替换裸拼 `tag turbovec`——
         CSS 无定义静默灰底的根因）；路径行可溯源，workspace/archived 命中且首段
         是 KB 四根之一 ⇒ 下钻按钮（复用 kbBrowse 单层能力，零后端改动）。 */
      const srcName = String(r.source || r.from || '');
      const path = String(r.path || (r.id || '').replace(/:\d+$/, '') || '');
      const seg = path.split('/').filter(Boolean)[0] || '';
      const KB_ROOTS = ['MEMORY.md', 'memory', 'agent-knowledge', 'digest'];
      const drill = (srcName === 'workspace_files' || srcName === 'archived_sessions') &&
                    KB_ROOTS.indexOf(seg) >= 0
        ? ' <button class="btn ghost sm" onclick="kbBrowse(\'' + escapeHtml(seg) + '\')" title="在文档树中查看">↖ ' + escapeHtml(seg) + '</button>' : '';
      return '<div class="mem-item">' + srcTag(srcName) +
      '<p>' + escapeHtml(String(r.title || r.path || r.id || '').slice(0, 100)) +
      '<br><span class="hint">' + escapeHtml(String(r.content || r.preview || '').slice(0, 200)) + '</span>' +
      (path ? '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' + escapeHtml(path.slice(0, 120)) + '</span>' + drill : '') +
      '</p></div>';
    }).join('') ||
      '<div class="hint">零命中。' + (d.note || '') + '</div>';
    // 逐路表态：哪路挂了要说出来（不许全绿假装没挂）——徽标行已表达，toast 保留作主动提醒
    const bad = (d.backends || []).filter(b => !b.ok);
    if (bad.length) {
      CENTER_HEALTH.kb = 'warn';
      toast('降级路：' + bad.map(b => b.name).join(', '), 'err');
    }
  } catch (e) {
    CENTER_HEALTH.kb = 'err';
    boxFail('kbResults', e, 'kbSearch');
  }
}
function kbClear() {
  const bar = $('kbQueryBar');
  if (bar) { bar.style.display = 'none'; bar.innerHTML = ''; }
  const results = $('kbResults');
  if (results) results.innerHTML = '输入关键词开始检索';
  const badges = $('kbBadges');
  if (badges) badges.innerHTML = '';
  const hint = $('kbHint');
  if (hint) hint.textContent = '';
  const input = $('kbQ');
  if (input) input.value = '';
  /* CENTER_HEALTH 故意不清（与 memFedClear 同口径） */
}

async function loadKbStatus() {
  boxBusy('kbStatus');
  try {
    const d = await api('/api/kb/status');
    const seg = (name, v) => '<div><b>' + name + '</b>：' +
      (v.available ? '✅ 可用' : '❌ 不可用') +
      (v.count != null ? ' · ' + v.count : '') +
      (v.chunks ? ' chunks' : '') +
      (v.index_mtime_age_days != null ? ' · 索引 ' + v.index_mtime_age_days + ' 天前' : '') +
      (v.ms != null ? ' · ' + v.ms + 'ms' : '') +
      (v.error ? ' · <span style="color:var(--red)">' + escapeHtml(String(v.error).slice(0, 120)) + '</span>' : '') + '</div>';
    /* local_memory 用 state（fresh/stale/empty）而非 available——旧行硬套
       available 三元判 undefined ⇒ 永远渲染 ❌ 不可用，把「陈旧便签」演成「挂了」
       （2026-09-26 用户误报记忆模块不可用的直接观感来源）。如实三态：
       fresh=✅ / stale=⚠ 陈旧（不参与决策，非故障） / empty=⭕ 空。 */
    const segState = (name, v) => {
      const st = v.state || 'unknown';
      const icon = st === 'fresh' ? '✅' : st === 'stale' ? '⚠️' : '⭕';
      const tag = st === 'stale' ? ' 陈旧便签（非故障，主源在 TDAI）' : st === 'empty' ? ' 空' : '';
      return '<div><b>' + name + '</b>：' + icon + st + tag +
        (v.rows != null ? ' · ' + v.rows + ' 行' : '') +
        (v.authoritative_source ? ' · 主源 ' + v.authoritative_source : '') +
        (v.reason ? ' · <span class="hint">' + escapeHtml(String(v.reason).slice(0, 100)) + '</span>' : '') + '</div>';
    };
    $('kbStatus').innerHTML =
      seg('TDAI L1', d.tdai || {}) +
      seg('turbovec 语义', d.turbovec || {}) +
      seg('workspace 全文', d.workspace || {}) +
      seg('archived 会话备份', d.archived || {}) +
      segState('local 便签', d.local_memory || {});
  } catch (e) { boxFail('kbStatus', e, 'loadKbStatus'); }
}

/* 文档树浏览：sub=根名单层下钻（后端 kb.py 白名单校验，只支持一层——
 * 根内子目录如实渲染为不可点+hint，不扩后端）。面包屑 KB_SUB 记当前层。
 * var 防外层引用时的 TDZ 静态序风险（test_tdz_order 钉着）。 */
var KB_SUB = '';
async function kbBrowse(sub) {
  KB_SUB = sub || '';
  boxBusy('kbBrowse');
  try {
    const d = await api('/api/kb/browse' + (KB_SUB ? '?sub=' + encodeURIComponent(KB_SUB) : ''));
    const crumb = $('kbCrumb');
    if (crumb) crumb.innerHTML = KB_SUB
      ? '<button class="btn ghost sm" onclick="kbBrowse()">知识库根</button> ▸ ' + escapeHtml(KB_SUB)
      : '<span class="hint">知识库根（点目录名下钻一层）</span>';
    $('kbBrowse').innerHTML = (d.entries || []).map(e => {
      const meta = e.files != null ? e.files + ' 文件' : Math.round((e.size || 0) / 1024) + 'KB';
      // 顶层（无 sub）目录条目可点下钻；进了 sub 之后，后端只支持单层——如实说
      const clickable = !KB_SUB && e.dir;
      return clickable
        ? '<div class="mem-item" style="cursor:pointer" onclick="kbBrowse(\'' + escapeHtml(e.name) + '\')" title="下钻 ' + escapeHtml(e.name) + '">' +
          '📁 <b>' + escapeHtml(e.name) + '</b> <span class="hint">' + meta + '</span></div>'
        : '<div class="mem-item">' + (e.dir ? '📁' : '📄') + ' ' + escapeHtml(e.name) +
          ' <span class="hint">' + meta + (KB_SUB && e.dir ? '（暂只支持下钻一层）' : '') + '</span></div>';
    }).join('') || '空目录';
    if (d.errors && d.errors.length) {
      $('kbBrowse').innerHTML += '<div class="hint" style="color:var(--st-error,var(--danger))">' +
        d.errors.map(escapeHtml).join('<br>') + '</div>';
    }
  } catch (e) { boxFail('kbBrowse', e, 'kbBrowse'); }
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

/* ── 会话导出（件 1 / spec §3）──────────────────────────────────────────
   三个实测出来的坑，决定了这里为什么长这样：
   ① 不能走 api()：01-core-boot.js 的 isWriteMethod() 只给 POST/PUT/PATCH/DELETE 带 token，
      而 /api/sessions/export 是 **GET 却要写级鉴权**（后端显式 writeauth.decide("POST",…)）⇒ 必 401。
   ② 不能用 api() 取体：它会 JSON.parse 成对象，CSV/JSON **文件字节**就毁了 ⇒ 必须 blob。
   ③ token 不走 ?token=：那会进服务端访问日志与浏览器历史（本工作区三次凭据外流前例）⇒ 只走头。
   文案四态互斥（照抄 07-asset-panel.js 的红向口径）：**被拒绝不许说成"没有会话"**。
   窄屏口径：只加 1 个图标按钮，不做 format/redact 的一排开关（chrome 单行化优先级更高）；
   CSV 与 redact=0 走 API 参数，不在本轮做 UI（YAGNI，且给原文出口做 UI 需单独裁定）。 */
function exportStateOf(status, count, detail) {
  if (status === 503) return 'misconfig';
  if (status === 401) return /缺少凭据/.test(String(detail || '')) ? 'need-token' : 'bad-token';
  if (status !== 200) return 'error';
  return (count > 0) ? 'ok' : 'empty';
}

function exportStateText(state, count, extra) {
  const n = (count == null || count < 0) ? '?' : String(count);
  const code = (extra && extra.status) ? String(extra.status) : '?';
  const hits = (extra && typeof extra.hits === 'number') ? extra.hits : 0;
  if (state === 'ok') {
    return '已导出 ' + n + ' 条会话（默认脱敏，命中 ' + hits + ' 处' +
           (hits > 0 ? '，正文已打码）' : '）');
  }
  if (state === 'empty') return '导出成功，但这个范围内确实是 0 条会话（文件是空的，不是被拒）';
  if (state === 'need-token') return '导出被拒：未提供凭据 —— 不是没有会话。请在「设置」里应用 TERM_TOKEN 后重试';
  if (state === 'bad-token') return '导出被拒：凭据不匹配 —— 不是没有会话。存量口令可能已换过，已清除，请重新输入';
  if (state === 'misconfig') return '导出被拒：服务端未配置口令（fail-closed）—— 不是没有会话';
  // 'error' 必须是**显式分支**而不是掉进兜底：闸门按状态名逐个点名（缺一个即红），
  // 而兜底留给"将来新增状态忘了配文案"这种情况 —— 那时也要说清不是没有会话。
  if (state === 'error') return '导出失败（HTTP ' + code + '）—— 不是没有会话';
  return '导出失败（未知状态 ' + state + '）—— 不是没有会话';
}

async function chatSessExport() {
  const tk = termToken();
  if (!tk) { toast(exportStateText('need-token', -1, {}), 'err'); return; }
  const p = new URLSearchParams({ format: 'json', limit: '1000',
                                  with_messages: '1', redact: '1' });
  if (chatPick) p.set('agent_id', chatPick);
  let r;
  try {
    r = await fetch('/api/sessions/export?' + p.toString(),
                    { headers: { 'X-TERM-TOKEN': tk } });   // 头，不是 URL
  } catch (e) {
    toast('导出失败：网络不可达（' + e.message + '）—— 不是没有会话', 'err');
    return;
  }
  const cnt = parseInt(r.headers.get('X-Export-Count') || '-1', 10);
  const hits = parseInt(r.headers.get('X-Export-Redacted-Hits') || '0', 10);
  if (r.status !== 200) {
    let detail = '';
    try { const d = await r.json(); detail = (d && d.detail) || ''; } catch (e) { detail = ''; }
    const st = exportStateOf(r.status, cnt, detail);
    if (st === 'bad-token') lsRemove('hub.term.token');   // 存量失效口令：清掉，下次重新问
    toast(exportStateText(st, cnt, { status: r.status, hits: hits }), 'err');
    return;
  }
  let body;
  try { body = await r.blob(); } catch (e) {
    toast('导出失败：读不到响应体 —— 不是没有会话', 'err'); return;
  }
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="?([^";]+)"?/);
  const name = (m && m[1]) || 'agent-hub-sessions.json';   // 后端保证纯 ASCII 文件名
  const obj = URL.createObjectURL(body);
  const a = document.createElement('a');
  a.href = obj;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(obj); a.remove(); }, 0);
  toast(exportStateText(exportStateOf(200, cnt, ''), cnt, { hits: hits }), 'ok');
}

/* data-export 委托：新按钮一律走委托，不用 inline onclick（AGENTS.md 浮层配套红线，口径取最严）。
   挂在 document 上而不是某个面板里 ⇒ 工具条重渲染后不必重新绑定。 */
document.addEventListener('click', function (e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-export]') : null;
  if (!b) return;
  if (b.getAttribute('data-export') === 'sessions') chatSessExport();
});
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
      '<div class="hint">暂无规则——<b>零规则行时</b>所有 agent 默认放行（首次接入零摩擦）；'
      + '但只要存在任何一条适用规则（<b>含 <code>*</code> 通配行</b>），即进入白名单语义：未覆盖即拒，且 deny 优先。</div>';
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
                   ['skills', '技能中心', 'zap'], ['kb', '知识库', 'book'],
                   ['mcp', '工具', 'wrench'], ['jobs', '定时', 'clock'], ['tasks', '协同', 'flow'],
                   ['assets', '资产', 'layers'], ['runlog', '运行日志', 'radar']];
const MODE_LABEL = { embed: '嵌入', term: '终端', chat: '对话', detail: '详情', open: '新窗口' };
const PAGE_LABELS = { classroom: '总览', chat: '统一对话', tasks: '协同', jobs: '定时',
                      memory: '记忆中心', skills: '技能中心', kb: '知识库', mcp: '工具', ports: '端口', telemetry: '遥测',
                      assets: '资产', runlog: '运行日志', localprojects: '本机项目' };
const navOpenStored = lsGet('hub.nav.open');
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
/* ⚠ 声明位置是硬约束，不许往下挪：下面 histBootstrap() 是**顶层 IIFE**，会同步走
histLoad() → renderNav() → 读本变量。09-23 自研 APP 事故就是顺序被破坏：手机那份
localStorage 有 hub.term.token ⇒ 早期路径被激活，而 let 声明在 renderNav 之后 ⇒
抛 ReferenceError: Cannot access '_navHtml' before initialization（hub.js:1907），
hub.js 当场死亡 ⇒ 菜单空白 + initSidebar 从未执行 + 抽屉停在展开态遮住正文。
浏览器那份没有 hub.term.token，走不到这条路 ⇒ 这就是「局域网正常/Tailscale 异常」
的真判据（不是缓存、不是网络、不是 origin 的 IP 段）。
闸门：tests/test_tdz_order.py（静态扫同类顺序违规；红基线取修复前的 git 版本）。 */
let _navHtml = '';   // 上一次渲染的菜单 HTML，用于跳过无变化的重写
const TERM_HIST_AGENTS = ['grok', 'claude', 'jcode', 'hermes', 'codex', 'qoder', 'opencode'];   // 与后端 SESSION_STORES 同集合
// ★窄屏首屏**不恢复**上次的历史展开项。`hub.hist` 是按 origin 隔离的存量，一旦参与
// 首屏判定，同一个动作在不同入口（局域网 IP / Tailscale IP）就会走出不同结果：
// 09-23 23:3x 四格实测 —— hist 空 ⇒ 点 agent 名称只展开列表、侧栏不收起；
// hist='claude' ⇒ 点名称即收起侧栏。两个 origin 各自一致、彼此不同 ⇒ 差异纯属存量，
// 与网络/Tailscale 无关。闸门：tests/verify_collapse_symmetry.py。
let histOpen = hubNarrow() ? '' : (lsGet('hub.hist') || '');
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
  if (!TERM_HIST_AGENTS.includes(histOpen) || !lsGet('hub.term.token')) {
    histOpen = '';
    lsRemove('hub.hist');
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
      /* v0.13.27：三中心（memory/skills/kb）行尾挂健康点（CENTER_HEALTH，
         loader 完成时写入；空=未加载不显示，ok/warn/err 对应 s-badge 色族）。 */
      const hlth = p => (typeof CENTER_HEALTH !== 'undefined' && CENTER_HEALTH[p]) ?
        '<span class="s-badge ' + (CENTER_HEALTH[p] === 'ok' ? 'running' : CENTER_HEALTH[p] === 'warn' ? 'installed' : 'error') + '"></span>' : '';
      const HLTH_PAGE = { memory: 1, skills: 1, kb: 1 };
      body = list.map(([p, label, ic]) =>
        '<button class="nav-item' + (curPage === p ? ' on' : '') + '" data-sys="' + p + '">' +
        ico(ic) + '<span class="lbl">' + label + '</span>' + (HLTH_PAGE[p] ? hlth(p) : '') + '</button>').join('');
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
  lsSet('hub.nav.open', navOpen);
  renderNav();
}
/* 点左侧实体 → 右侧加载该实体工作台（复用既有 gotoChat / showDetail，不另起炉灶）
   形态一律交给 defaultModeOf()：它自己会读「上次显式选过的形态」再回落默认。
   这里以前另写了一份 embed > term > chat 优先级 —— 那份就是 09-25 故障的本体：
   Claude Code 是唯一同时有 embed（cloudcli 宿主端口活 ⇒ discovery 注入）与 term 的 Agent，
   于是点行必进那张要独立登录的 iframe，而站内没有任何按钮能切回终端。 */
function openEntity(id) {
  const a = entityById(id);
  if (!a) return;
  const es = a.entries || [];
  const has = t => es.some(e => e.type === t);
  if (has('embed') || has('term') || has('chat')) return gotoChat(id);
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
const mqNarrow = HUB_NARROW_MQ;   // 断点唯一真源在 01（不变量 3）
const sidebarPrefKey = () => mqNarrow.matches ? 'hub.sidebar.narrow' : 'hub.sidebar.wide';

/* 纯判定，单列成顶层函数是为了让探针能原样抽出**真代码**跑（手抄即假绿）。
   stored：本档已存偏好；legacy：旧的全局键 hub.sidebar。
   legacy 只在宽屏当一次性迁移用 —— 它几乎必然由宽屏写入；窄屏一律回到默认收起，
   这样存量已被污染的手机（值='0'）首屏即自愈，不需用户清缓存。 */
function sidebarWantCollapsed(narrow, stored, legacy) {
  if (stored !== null) return stored === '1';
  if (!narrow && legacy !== null) return legacy === '1';
  /* 窄档无存档时的默认值 = 收起（48px 图标条）——用户 2026-09-24 裁定。
     本次没改行为：实测（冷 profile + 清 localStorage，390x768）本来就是
     collapsed=true / offsetWidth=48，故只把这行**钉成断言**：
     tests/verify_narrow_default_iconbar.py（附两格灵敏度对照：本档存过 '0' → 必须展开；
     掐 hub.js → collapsed 必须 false，证明闸门能判红、不是 stuck-true 假绿）。
     谁要把这行改成 false，先去看那两格为什么红。 */
  return narrow;
}

function initSidebar() {
  const sb = document.getElementById('sidebar'), btn = document.getElementById('btnSideToggle');
  if (!sb || !btn) return;
  const narrow = () => mqNarrow.matches;
  const apply = (c, persist) => {
    sb.classList.toggle('collapsed', c);
    btn.innerHTML = c ? ico('panel-expand', 'xs') : ico('panel-collapse', 'xs') + '<span class="lbl">收起</span>';
    if (persist !== false) lsSet(sidebarPrefKey(), c ? '1' : '0');
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
  const resolve = () => apply(sidebarWantCollapsed(narrow(), lsGet(sidebarPrefKey()),
                                                   lsGet('hub.sidebar')), false);
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
        lsSet('hub.hist', histOpen);
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
        lsSet('hub.nav.open', navOpen);
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
/* 终端页不许把按键丢在地上（2026-09-25 用户报障「Grok 会话窗口空格不能用，一按就出现重复的文字内容」）。
   实测：焦点一旦落到终端外（手机上点过标题/会话芯片、桌面上点过页面任意处），敲进去的字符
   既不进 pty 也没有任何提示 —— 影子实例里把 pty 设成 `-echo -icanon` 交给 cat 逐字对账，
   焦点=BODY 时敲 'c'+空格+'d'，pty 实收 0 份。用户的下一步必然是点一下终端再敲，而 Grok TUI
   在主屏缓冲区里反复重画整屏（首帧无 ?1049h 备用屏）⇒ 同一份文字在 xterm 里出现两遍，
   于是现象被报成"空格一按就重复"。
   规则：终端页可见 + 焦点不在任何输入位（含 xterm 自己的 helper textarea，那条路本来通）
   ⇒ 把可打印字符（空格算一个）交给终端，并 preventDefault 挡掉浏览器把空格当翻页。
   只接力单字符：Ctrl/Cmd/Alt 组合键与 Enter/Backspace 等非可打印键一律放行 —— 那是浏览器
   和终端各自的语义，这里不发明新行为（P2-11「快捷键不得劫持输入位」的口径原样保留）。 */
document.addEventListener('keydown', e => {
  if (e.defaultPrevented || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (keyTargetIsEditing(e)) return;
  if (typeof e.key !== 'string' || e.key.length !== 1) return;
  const pg = $('page-chat'), tp = $('termPane');
  if (!pg || !tp || !pg.classList.contains('on') || !tp.classList.contains('on')) return;
  if (!term || !termWs || termWs.readyState !== 1) return;   // 没接上线就别假装送达（termSend 那条路会自己报警）
  e.preventDefault();
  const opts = { user: true };   // 用户亲手敲的键＝主动意图，走同一条焦点策略（P2-10：只跟"用户主动"）
  if (termFocusWanted(opts)) term.focus();
  term.input(e.key);
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
go(lsGet('hub.page') || 'classroom');  // T9：默认落点 = 上次所在页（chatPick/chatMode 已在声明处恢复）
/* ══════════════════ P4 资产面板（只读门面聚合）══════════════════
   契约源：src/memory.py（/api/memory/*）、src/kb.py（/api/kb/*）、src/skill.py（/api/skill/*）。
   口径（09-24 架构裁定）：hub 只做**门面 + ACL + 审计 + 前端**，本页零写操作、零"第二权威副本"。

   ★ 为什么这个文件里最讲究的是"四态可区分"而不是好看：
     ok / empty / degraded / down 四态里，只要把后两种渲染成"暂无数据"，
     用户就会以为本机资产是空的 —— 这正是 ccpocket-bridge「active、端口在听、/health ok
     而会话起不来，静默 21 天」那一族故障在前端层的形态。闸门
     tests/test_asset_panel.py 用**抽出来的真函数**喂 degraded 与 404 两种载荷钉死这条。

   ★ 施工约束（09-23 分档五不变量 + 浮层三条）：
     - 不新增 matchMedia（断点唯一真相在 01 的 HUB_NARROW_MQ）；
     - 不裸用 localStorage（一律 lsGet/lsSet）；
     - 侧栏/菜单内禁 inline onclick ⇒ 本页交互全走 data-asset 委托，且入口函数
       initAssetPanel() 由 go('assets') 懒调用（不用顶层 IIFE：v0.13.11 的 TDZ 教训）。 */

/* 顶层状态位故意用 `var`：本分片在 hub.js 里排在 01 之后，而 01 的 go() 会调到
   本文件的函数。`let/const` 在语句执行到之前是 TDZ（v0.13.11 的 `_navHtml` 白屏
   就是这一型），而 `function` 声明整体提升 ⇒ 下面的常量表一律做成函数。 */
var assetsBound = false;   // 事件委托只绑一次（go() 会反复进本页）

function ASSET_SOURCES() {
  return [
  {
    key: 'memory', label: '记忆', box: 'assetMemory', needsQ: true,
    path: q => '/api/memory/search?q=' + encodeURIComponent(q) + '&limit=6',
    items: d => d.memories || [],
    itemText: it => it.content || it.summary || it.text || '',
    itemMeta: it => [it.category || it.type, it.agent || it.source, it.created_at ? String(it.created_at).slice(0, 10) : ''].filter(Boolean).join(' · '),
    status: d => (d.engines && d.engines.memory) || d.engine || '',
  },
  {
    key: 'kb', label: '技术文档', box: 'assetKb', needsQ: true,
    path: q => '/api/kb/search?q=' + encodeURIComponent(q) + '&k=6',
    items: d => d.results || [],
    itemText: it => it.snippet || it.text || it.title || '',
    itemMeta: it => [it.path || it.url, it.chunk ? '#' + it.chunk : ''].filter(Boolean).join(''),
    status: d => d.engine || '',
  },
  {
    key: 'skill', label: '技能', box: 'assetSkill', needsQ: false,
    path: q => '/api/skill/list?limit=6' + (q ? ('&q=' + encodeURIComponent(q)) : ''),
    items: d => d.items || [],
    itemText: it => it.name + (it.description ? (' — ' + it.description) : ''),
    itemMeta: it => [it.route, it.dir ? (it.dir.split('/').slice(-2).join('/')) : ''].filter(Boolean).join(' · '),
    status: d => (d.engine || 'disk'),
  },
  ];
}

/* 第五态 idle：某些门面端点按契约 **缺 q 就回 422**（memory/kb 检索）。
   空查询时发一个注定 422 的请求、再把后端校验错报成「部分后端不可用」，
   技术上诚实但语义误导（用户什么都没输）。idle 单独一态，不发请求。 */
function assetIdleHtml(src) {
  return '<div class="hint">「' + escapeHtml(src.label) + '」这一路需要检索词（端点缺 q 按契约回 422）。'
    + '在上方输入关键词后点「检索」。</div>';
}

/* 四态之一：ok / empty / degraded / down。判定顺序即优先级——坏消息不许被"空"掩盖。 */
function assetStateOf(d, err) {
  if (err) return /HTTP 404/.test(String(err.message)) ? 'down' : 'degraded';
  if (!d) return 'down';
  // 后端自己报的 degraded 与逐路 backends[].ok 取并集（有一路不算账就算降级）
  const bad = (d.backends || []).some(b => !b.ok) || (d.degraded || []).length > 0;
  if (bad) return 'degraded';
  return (d.count || 0) ? 'ok' : 'empty';
}

/* 状态文案与颜色同样做成函数（见上方 TDZ 注释）。
   只用主题里**真存在**的 token（--ok / --muted / --warn / --danger-text）：
   不存在的 var() 会静默退化成继承色 ⇒ 警告条变普通文字，又是新的静默不可用。 */
function assetStateText(state) {
  return ({ ok: '有结果', empty: '确实零命中', idle: '待输入检索词',
            unwired: '按设计未接入',
            degraded: '部分后端不可用', down: '端点不可用' })[state] || state;
}

function assetStateColor(state) {
  return ({ ok: 'var(--ok)', empty: 'var(--muted)', idle: 'var(--muted)',
            unwired: 'var(--muted)',
            degraded: 'var(--warn)', down: 'var(--danger-text)' })[state] || 'var(--muted)';
}

function assetBadge(src, state, note) {
  return '<div class="chip" style="min-width:0">' +
    '<b style="color:' + assetStateColor(state) + '">' + escapeHtml(src.label) +
    '：' + escapeHtml(assetStateText(state)) + '</b>' +
    (note ? '<span class="hint">' + escapeHtml(note) + '</span>' : '') + '</div>';
}

function assetResultsHtml(src, d, state, err) {
  if (state === 'down') {
    return '<span style="color:var(--danger-text)">该门面端点取不到（' +
      escapeHtml(err ? String(err.message).slice(0, 120) : 'HTTP 404') +
      '）。<b>这不是"没有资产"</b>——多半是 hub 代码比进程新（未重启）或后端未启用。</span>';
  }
  if (state === 'degraded') {
    /* ★ 红向修正：降级态以前会落到 empty 分支里说「确实没有命中」，
       那恰好把「查不了」糊成「没有」——就是 09-22 那族静默不可用。 */
    return '<div class="hint" style="color:var(--warn)"><b>部分后端不可用</b>：'
      + '下面的清单<b>不完整</b>，不能当作「没有资产」。</div>'
      + (d && d.note ? '<div class="hint" style="color:var(--warn)">' + escapeHtml(d.note) + '</div>' : '')
      + (d ? backendLedger(d) : '')
      + (d && src.items(d).length ? itemListHtml(src, src.items(d))
                                  : '<div class="hint">可用后端本次未返回条目。</div>');
  }
  if (state === 'empty') {
    return (d && d.note ? '<div class="hint" style="color:var(--warn)">' + escapeHtml(d.note) + '</div>' : '')
      + backendLedger(d) +
      '<div class="hint">后端全部在线，这条改写查询确实没有命中 —— 换个说法再试。</div>';
  }
  const items = d ? src.items(d) : [];
  return (d && d.note ? '<div class="hint" style="color:var(--warn)">' + escapeHtml(d.note) + '</div>' : '')
    + backendLedger(d) + itemListHtml(src, items);
}

/* 逐路 backends[] 台账：路名 + 成败 + 条数 + 耗时 + 错因（错因截 60 字防溦屏） */
function backendLedger(d) {
  const backs = ((d && d.backends) || []).map(b =>
    '<span class="hint" style="margin-right:8px">' + escapeHtml(b.name) +
    (b.ok ? '✓' : '✗') + ' ' + (b.count == null ? '-' : b.count) + '条 ' + (b.ms == null ? '' : b.ms + 'ms') +
    (b.ok ? '' : (' <span style="color:var(--danger-text)">' + escapeHtml(String(b.error || '')).slice(0, 60) + '</span>')) +
    '</span>').join('');
  return backs ? '<div style="margin-bottom:6px">' + backs + '</div>' : '';
}

function itemListHtml(src, items) {
  if (!items || !items.length) return '<div class="hint">本次无条目。</div>';
  return '<div class="mem-list">' + items.map(it =>
    '<div class="mem-item"><p>' + escapeHtml(String(src.itemText(it)).slice(0, 300)) +
    (src.itemMeta(it) ? '<br><span class="hint">' + escapeHtml(src.itemMeta(it)) + '</span>' : '') +
    '</p></div>').join('') + '</div>';
}

async function loadAssetStatus() {
  const box = $('assetStatus');
  if (!box) return;
  const probes = [
    { label: '技能', path: '/api/skill/status', kind: 'status' },
    { label: '知识', path: '/api/kb/status', kind: 'status' },
    { label: '记忆', path: '/health', kind: 'health' },
  ];
  const settled = await Promise.allSettled(probes.map(p => api(p.path)));
  const chips = settled.map((r, i) => {
    const p = probes[i];
    if (r.status === 'rejected') {
      const st = /HTTP 404/.test(String(r.reason.message)) ? 'down' : 'degraded';
      return assetBadge({ label: p.label }, st, String(r.reason.message).slice(0, 60));
    }
    const d = r.value || {};
    if (p.kind === 'health') {
      const mb = d.memory_backend || {};
      const st = mb.state === 'ok' ? 'ok' : (mb.state ? 'degraded' : 'down');
      return assetBadge({ label: '记忆' }, st,
        mb.state ? (mb.state + ' L1=' + mb.l1_total + ' L0=' + mb.l0_total + (mb.stale ? ' stale' : '')) : '字段缺失（进程旧于代码）');
    }
    /* ★ 关键区分：`available:false` 开 **带 why** 的是设汁裁定（如 kb 的 wigolo、skill 的 TDAI 注册表），
       不是故障；带 error 的才是真挂了。把前者涂成黄色告警，首屏就是一屏假故障——
       那会把真故障埋掉（09-22 “静默不可用”的反面：吼叫式假告警）。 */
    const off = Object.values(d).filter(v => v && typeof v === 'object' && v.available === false);
    const unwired = off.filter(v => v.why && !v.error);
    const broken = off.filter(v => !(v.why && !v.error));
    const st = broken.length ? 'degraded' : (unwired.length ? 'unwired' : 'ok');
    const why = (broken.length ? broken.map(v => v.error || '未说明') : unwired.map(v => v.why))
      .join('；').slice(0, 90);
    return assetBadge({ label: p.label }, st, off.length ? ((broken.length ? '不可用：' : '原因：') + why) : '');
  });
  box.innerHTML = '<div style="display:flex;flex-wrap:wrap;gap:8px">' + chips.join('') + '</div>';
}

async function runAssetSearch() {
  const q = (($('assetQ') || {}).value || '').trim();
  const hint = $('assetHint');
  if (hint) { hint.textContent = '检索中…'; }
  const settled = await Promise.allSettled(ASSET_SOURCES().map(s => {
    // 空查询时，需要 q 的那两路**根本不发请求**（而是置 idle）
    if (!q && s.needsQ) return Promise.resolve({ idle: true });
    return api(s.path(q));
  }));
  let degraded = 0;
  settled.forEach((r, i) => {
    const src = ASSET_SOURCES()[i];
    const box = $(src.box);
    if (!box) return;
    let state, d = null, err = null;
    if (r.status === 'fulfilled' && r.value && r.value.idle) {
      state = 'idle'; box.innerHTML = assetIdleHtml(src);
    } else if (r.status === 'rejected') {
      err = r.reason; state = assetStateOf(null, err);
      box.innerHTML = assetResultsHtml(src, null, state, err);
    } else {
      d = r.value; state = assetStateOf(d, null);
      box.innerHTML = assetResultsHtml(src, d, state, null);
    }
    if (state === 'degraded' || state === 'down') degraded++;
  });
  if (hint) {
    if (!q) {
      hint.textContent = '未带检索词：记忆/技术文档两路置为待输入（它们缺 q 会回 422），技能路已列前 6 条';
    } else {
      hint.textContent = degraded
        ? ('完成：' + degraded + '/3 路有降级，见对应列的红/黄字')
        : '完成：三路均正常';
    }
  }
}

function initAssetPanel() {
  if (assetsBound) return;
  assetsBound = true;
  const root = $('page-assets');
  if (!root) return;
  // data-asset 委托：唯一出口，侧栏/菜单内零 inline onclick（浮层三条之配套红线）
  root.addEventListener('click', e => {
    const b = e.target.closest('[data-asset]');
    if (!b) return;
    if (b.dataset.asset === 'search') runAssetSearch();
    else if (b.dataset.asset === 'refresh') loadAssets();
  });
  const inp = $('assetQ');
  if (inp) inp.addEventListener('keydown', e => { if (e.key === 'Enter') runAssetSearch(); });
}

async function loadAssets() {
  initAssetPanel();
  const sb = $('assetStatus');
  if (sb) sb.innerHTML = '<span class="hint">门面健康自检中…</span>';
  await loadAssetStatus();
  runAssetSearch();
}
/* ── 运行日志页（v0.13.27 批2）────────────────────────────────────────
 * 分片头注释（军规：分片源，不是 build 产物；产物在 static/hub.js 由
 * scripts/build_hubjs.sh 按字典序拼接，直接改产物会被 test_hubjs_split 判红）。
 *
 * 数据源：GET /api/runlog（后端按**写方法**鉴权——运行日志含查询词可反推意图，
 * 与 /api/audit/list 同口径）。api() 只给写方法自动带 token，本页是 GET ⇒
 * token 由本页自己带。鉴权 UX 三态（互斥）：
 *   ① 有 token → 直接带，正常三态（busy/数据/空窗）
 *   ② 401/503  → 容器渲染「需终端口令」+ 按钮（点了才 termToken() 弹框——
 *                别在 loadRunlog 里直接弹，05:297 的教训：刷新一次弹一次）
 *   ③ 空窗     →「该窗口内无事件」
 * 游标翻页：before_id = 上一页末行 id（append-only 表 id<? 恒定代价，不用 OFFSET）。
 * go() 里每次进页都 loadRunlog(true)（与 telemetry 同口径，不设 loaded 位）。
 */

/* 三态渲染（busy 数据 空窗 互斥；鉴权态由 catch 401/503 分支接管） */
function runlogRowHtml(e) {
  let d = {};
  try { d = JSON.parse(e.detail || '{}') || {}; } catch (x) { /* 容错：detail 形状漂移就当无附加信息 */ }
  const st = e.status === 'success' ? 'g' : 'r';
  const bad = (d.degraded || []).length;
  const q = d.q || '';
  return '<tr><td>' + (e.created_at || '').slice(5, 16).replace('T', ' ') + '</td>' +
    '<td>' + escapeHtml(e.source || '') + '</td>' +
    '<td><b>' + escapeHtml(e.subject || '') + '</b></td>' +
    '<td><span class="hdot ' + st + '"></span>' + escapeHtml(e.status || '') + '</td>' +
    '<td>' + (e.duration_ms == null ? '-' : e.duration_ms + 'ms') + '</td>' +
    '<td>' + escapeHtml(d.channel || '-') + '</td>' +
    '<td>' + (bad ? '<span style="color:var(--st-error,var(--danger,#c00))">' + bad + ' 路</span>' : '-') + '</td>' +
    '<td class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
    escapeHtml(String(q).slice(0, 60)) + '</td></tr>';
}

function renderRunlog(events) {
  const body = $('runlogBody');
  if (!body) return;
  body.innerHTML = events.length ? events.map(runlogRowHtml).join('')
    : '<tr><td colspan="8" class="hint">该窗口内无事件（进三中心检索一次即有留痕）</td></tr>';
}

/* 鉴权失败态：容器出「输入口令并重试」按钮，点击才弹 termToken()（不自动弹） */
function runlogAuthGate() {
  const body = $('runlogBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="8" class="hint">运行日志需终端口令。' +
    '<button class="btn sm" style="margin-left:8px" onclick="runlogTokenRetry()">输入口令并重试</button></td></tr>';
}

function runlogTokenRetry() {
  termToken();                       // 弹一次，存 localStorage 后不再弹
  loadRunlog(true);
}

/* 首次带 token 的 GET（api() 不会给 GET 带 token，本页自己带；无 token 也发——
 * 让后端 401 说话，别在前端先猜「没 token 必失败」而渲染成挂了） */
function runlogFetch(params) {
  const opt = {};
  const tk = lsGet('hub.term.token');
  if (tk) opt.headers = { 'x-hub-token': tk };
  return api('/api/runlog' + (params || ''), opt);
}

/* 游标用 var 不用 let：go()（01 分片，拼接序在前）会经 loadRunlog 读到它们，
   let 的 TDZ 静态序风险会被 test_tdz_order 判红——07-asset-panel 同教训。 */
var RL_FIRST = null;    // 本页当前首页末行游标（翻页链表头）
var RL_CUR = null;      // 下一页的 before_id

async function loadRunlog(reset) {
  const hint = $('rlHint');
  const body = $('runlogBody');
  if (!body) return;
  if (reset) { RL_FIRST = null; RL_CUR = null; }
  if (hint) hint.textContent = '加载中…';
  /* 筛选控件的首个选项初始化只做一次（source/subject 清单后端随包返回） */
  const p = new URLSearchParams();
  const v = id => { const el = $(id); return el && el.value ? el.value : ''; };
  if (v('rlSource')) p.set('source', v('rlSource'));
  if (v('rlSubject')) p.set('subject', v('rlSubject'));
  if (v('rlStatus')) p.set('status', v('rlStatus'));
  if (v('rlWindow')) p.set('window', v('rlWindow'));
  p.set('limit', '50');
  try {
    const d = await runlogFetch('?' + p.toString());
    renderRunlog(d.events || []);
    if (hint) hint.textContent = (d.count || 0) + ' 条';
    RL_FIRST = (d.events || []).length ? d.events[d.events.length - 1].id : null;
    RL_CUR = d.next_before_id || null;
    const btn = $('rlMoreBtn');
    if (btn) btn.style.display = RL_CUR ? '' : 'none';
    /* source/subject 下拉：后端给的是全量清单，只填一次不覆盖用户选择 */
    if ($('rlSource').options.length <= 1 && (d.sources || []).length) {
      $('rlSource').innerHTML = '<option value="">全部 source</option>' +
        d.sources.map(s => '<option>' + escapeHtml(s) + '</option>').join('');
    }
    if ($('rlSubject').options.length <= 1 && (d.subjects || []).length) {
      $('rlSubject').innerHTML = '<option value="">全部 subject</option>' +
        d.subjects.map(s => '<option>' + escapeHtml(s) + '</option>').join('');
    }
  } catch (e) {
    /* 401/503 = 鉴权态（出按钮）；其他错误 = 加载失败态（错误文案上屏，不 toast 装看不见） */
    const gate = e.http === 401 || e.http === 503;
    if (gate) {
      if (hint) hint.textContent = '';
      runlogAuthGate();
    } else {
      if (hint) hint.textContent = '加载失败';
      body.innerHTML = '<tr><td colspan="8" class="hint">加载失败：' + escapeHtml(e.message || '') +
        ' <button class="btn sm" onclick="loadRunlog(true)">重试</button></td></tr>';
    }
    const btn = $('rlMoreBtn');
    if (btn) btn.style.display = 'none';
  }
}

async function loadRunlogMore() {
  if (!RL_CUR) return;
  const hint = $('rlHint');
  if (hint) hint.textContent = '加载中…';
  const p = new URLSearchParams();
  const v = id => { const el = $(id); return el && el.value ? el.value : ''; };
  if (v('rlSource')) p.set('source', v('rlSource'));
  if (v('rlSubject')) p.set('subject', v('rlSubject'));
  if (v('rlStatus')) p.set('status', v('rlStatus'));
  if (v('rlWindow')) p.set('window', v('rlWindow'));
  p.set('limit', '50');
  p.set('before_id', String(RL_CUR));
  try {
    const d = await runlogFetch('?' + p.toString());
    const body = $('runlogBody');
    /* 追加不是覆盖（首页数据还在 DOM 里，直接 innerHTML += 会重排——量级 50 行无所谓） */
    body.insertAdjacentHTML('beforeend', (d.events || []).map(runlogRowHtml).join(''));
    RL_CUR = d.next_before_id || null;
    const btn = $('rlMoreBtn');
    if (btn) btn.style.display = RL_CUR ? '' : 'none';
    if (hint) hint.textContent = '又 ' + (d.count || 0) + ' 条';
  } catch (e) {
    if (hint) hint.textContent = '翻页失败：' + (e.message || '');
  }
}
/* ── 本机项目页（v0.13.30）────────────────────────────────────────
 * 分片头注释（军规：分片源，不是 build 产物；产物在 static/hub.js 由
 * scripts/build_hubjs.sh 按字典序拼接，直接改产物会被 test_hubjs_split 判红）。
 *
 * 数据源：GET /api/localprojects（后端多根 git 扫描 + cloudcli auth.db 合并，
 * 只读不鉴权）。列表三态（busy/数据/失败含重试）；errors 里点名的降级源
 * 用 hint 展示——「查不了」不能糊成「没有」。
 * 新建会话：复用 startAgent 的链路（POST /api/term/sessions），仅多传 cwd；
 * 后端 _cwd_or_none 校验后只进 os.chdir，命令仍出自画像白名单。
 * 状态变量用 var：go()（01 分片，拼接序在前）会经顶层 go(lsGet('hub.page'))
 * 同步走到本分片函数体，let 的 TDZ 静态序风险会被 test_tdz_order 判红——
 * 08-runlog 同教训（RL_FIRST/RL_CUR）。
 */

var LP = [];         // 全量项目（过滤前的缓存）
var lpSel = '';      // 当前选中项目 path（'' = 未选）
var lpSelName = '';
var lpLoaded = false;   // go() 懒加载标志（与 memLoaded/skillsLoaded 同型；go() 分支在 01）

function lpTime(la) {
  if (!la) return '';
  return String(la).slice(5, 16).replace('T', ' ');
}

function lpRowHtml(p, i) {
  const on = p.path === lpSel ? ' on' : '';
  const src = (p.git ? '<span class="tag agent">git</span>' : '') +
              (p.cloudcli ? '<span class="tag">cc</span>' : '');
  return '<div class="mem-item' + on + '" data-i="' + i + '" style="gap:6px;cursor:pointer"' +
    ' onclick="lpSelect(' + i + ')"' +
    ' title="' + escapeHtml(p.path) + '">' +
    '<p style="min-width:0"><b>' + escapeHtml(p.name) + '</b>' + src +
    (p.sessions ? ' <span class="hint">' + p.sessions + ' 会话</span>' : '') +
    (p.last_activity ? ' <span class="hint">' + escapeHtml(lpTime(p.last_activity)) + '</span>' : '') +
    '<br><span class="hint" style="font-family:var(--font-mono);font-size:var(--fs-xs)">' +
    escapeHtml(String(p.path).slice(0, 72)) + '</span></p></div>';
}

function lpSelect(i) {
  const p = LP[i];
  if (!p) return;
  lpSel = p.path;
  lpSelName = p.name;
  const nameEl = $('lpSelName');
  if (nameEl) { nameEl.textContent = p.name; nameEl.title = p.path; }
  const btn = $('lpStartBtn');
  if (btn) btn.disabled = false;
  lpRenderList();   // 重画选中态（.on）
}

function lpRenderList() {
  const box = $('lpList');
  if (!box) return;
  const q = ($('lpQ') ? $('lpQ').value.trim() : '').toLowerCase();
  const idx = q ? LP.map((p, i) => [p, i]).filter(([p]) =>
    String(p.name || '').toLowerCase().includes(q) || String(p.path || '').toLowerCase().includes(q))
    : LP.map((p, i) => [p, i]);
  box.innerHTML = idx.map(([p, i]) => lpRowHtml(p, i)).join('') ||
    '<div class="hint" style="padding:10px">' + (q ? '无匹配项目' : '无项目') + '</div>';
}

function lpRenderAgents() {
  const sel = $('lpAgent');
  if (!sel) return;
  const cur = sel.value;
  const items = AGENTS.filter(a => a.kind === 'agent' && (a.entries || []).some(e => e.type === 'term'));
  sel.innerHTML = items.map(a =>
    '<option value="' + escapeHtml(a.id) + '">' + escapeHtml(a.name) + '</option>').join('');
  if (cur && items.some(a => a.id === cur)) sel.value = cur;   // 轮询重绘不覆盖用户选择
  else if (!cur && items.length) sel.value = items[0].id;
}

async function loadLocalProjects(force) {
  const box = $('lpList');
  const hint = $('lpHint');
  if (!box) return;
  if (force) LP = [];
  if (!LP.length) box.innerHTML = '<div class="hint" style="padding:10px">扫描本机项目中…</div>';
  if (hint) hint.textContent = '加载中…';
  try {
    const d = await api('/api/localprojects');
    LP = d.projects || [];
    lpRenderList();
    lpRenderAgents();
    if (hint) hint.textContent = (d.count || 0) + ' 个项目';
    const meta = $('lpMeta');
    if (meta) meta.textContent = (d.roots || []).join(' · ') +
      ((d.errors || []).length ? ' ｜ 降级源：' + d.errors.join('；') : '') +
      (d.took_ms != null ? ' ｜ ' + d.took_ms + 'ms' : '');
  } catch (e) {
    if (hint) hint.textContent = '加载失败';
    box.innerHTML = '<div class="hint" style="padding:10px">加载失败：' + escapeHtml(e.message || '') +
      ' <button class="btn sm" onclick="loadLocalProjects(true)">重试</button></div>';
  }
}

/* 核心：选中项目 + agent → 拉起 pty 会话 → 跳该 agent 终端工作台。
   逐字仿 startAgent（05 分片），仅多传 cwd。 */
async function lpStart() {
  const agent = $('lpAgent') ? $('lpAgent').value : '';
  if (!lpSel || !agent) { toast('先选项目与 Agent', 'err'); return; }
  try {
    const d = await api('/api/term/sessions', { method: 'POST',
      headers: termHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ agent_id: agent, cwd: lpSel }) });
    toast('已在 ' + lpSelName + ' 拉起 ' + agent + ' 终端会话', 'ok');
    gotoChat(agent, 'term');
    setTimeout(() => termConnect(d.session.id, agent, { user: true }), 100);
  } catch (e) { toast('新建会话失败：' + e.message, 'err'); }
}
