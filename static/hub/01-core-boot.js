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
