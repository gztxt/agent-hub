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
/* v0.13.31 GitHub 页同型（双 var 冗余纪律见上）；10 分片里 var ghLoaded 提升
   保证 06 顶层 go() 先行调用读到 undefined 而非 TDZ。 */
var ghLoaded = false;
/* v0.13.43 设置三页懒加载标志：go() 在 06 分片顶层就会跑（可能早于 01 里
   settingsModelsLoad 之外的一些初始化），故同样用 var 提升规避 TDZ —— 与
   lpLoaded / ghLoaded 同一条纪律；真值在 go() 里首次进页时才置。 */
var setModelPageLoaded = false;
var setGithubPageLoaded = false;
var setLogsPageLoaded = false;   // v0.13.46：日志页（进页才拉，日志量级大不能每次导航都拉）
/* v0.13.32 TDZ 补丁（真事故驱动的修复）：06 顶层 go(lsGet('hub.page')) 在
   09/10 分片顶层初始化**之前**就能调到 loadLocalProjects()/loadGithubRepos()
   （函数声明提升），而 LP/GH/lpStars… 的 `var X = …` 初始化还没跑 ⇒ 函数里
   读 LP.length 直接 TypeError ⇒ promise reject ⇒ 列表永远停在「加载中…」。
   修法与 lpLoaded 同型：状态声明+初始化前置到 01（拼接序最先），09/10 里的
   同名 var 是刻意冗余（提升后赋值不再能覆盖函数已经写入的数据——赋值均为
   「首次空态」形状，函数体只在数据就位后才写非空值，语义不变）。 */
var LP = [], GH = [];
var lpStars = new Set(), lpHiddenSet = new Set();
var ghStars = new Set(), ghHiddenSet = new Set();

/* ── 基础 ─────────────────────────────────────────── */

/* v0.13.6 P1-7：写端点服务端强制凭据。**只有写操作才索取 token** ——
   GET 也 prompt 的话，用户一打开页面就被口令框糊脸（读路径本来不需要）。 */
const WRITE_METHODS = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };
function isWriteMethod(m) { return !!WRITE_METHODS[String(m || 'GET').toUpperCase()]; }
async function api(path, opt) {
  const o = opt || {};
  /* v0.13.44：noToken 的写端点（设置页几个只认 HUB_PASSCODE 的端点，已登记在
     writeauth.EXEMPT_PREFIXES）不再向 termToken() 索 TERM_TOKEN —— 那会先弹一个
     "请输入终端鉴权 TERM_TOKEN" 的窗，跟保存模型毫无关系，用户只会以为保存卡住了。 */
  if (isWriteMethod(o.method) && !o.noToken) {
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
   三个浮层（侧栏抽屉 / detailDrawer / settingsDrawer）此前各开各的
   （v0.13.43 起设置抽屉已拆成左侧手风琴 + 正文页，剩 detailDrawer / skillDocDrawer 两个）：
   开设置不关侧栏、导航不收抽屉、遮罩只管侧栏 —— 窄屏抽屉宽 min(400px,92vw)，
   一旦残留就把整页压成"白板 + 点不动"。规则钉死三条：
   ① 同一时刻最多一个抽屉是 on（开新的必先清旧的）；
   ② 导航 = 清抽屉（go 里做，不留给调用方自觉）；
   ③ 遮罩只有一个计算出口（06 的 syncOverlayMask），且点它一定关干净 ——
      手机上没有 ESC 键，点空白是唯一逃生路径。 */
const OVERLAY_IDS = ['detailDrawer', 'skillDocDrawer'];   // v0.13.43：设置抽屉已拆，改走正文页
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
  if (page === 'github' && !ghLoaded) { ghLoaded = true; loadGithubRepos(); }   // v0.13.31 GitHub 项目页（10 分片）
  // v0.13.43 设置三页：与系统页同口径，进页才拉（模型清单 / GitHub 现值）
  if (page === 'settings-model' && !setModelPageLoaded) { setModelPageLoaded = true; settingsModelsLoad(); }
  if (page === 'settings-github' && !setGithubPageLoaded) { setGithubPageLoaded = true; settingsGithubLoad(); }
  if (page === 'settings-logs' && !setLogsPageLoaded) { setLogsPageLoaded = true; settingsLogsLoad(); }
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

/* ── 设置（口令保护的 TERM_TOKEN 查看/应用）──
   v0.13.43：设置不再是抽屉。三个子页（模型 / GitHub / 终端口令）是 main 里的
   section.page，由左侧「设置」手风琴组切换 —— 入口从 btnSettings 的 inline onclick
   变成与系统页同口径的 data-sys ⇒ 委托 ⇒ go(page)，顺带继承"窄屏点完自动收侧栏"。 */
/* 口令三源：页内输入框 → 本机缓存 → prompt（最后一源保留但不依赖它）。
   页内框是 v0.13.44 加的：APP 内嵌 WebView 与部分手机浏览器会直接吞掉 prompt()
   （返回 null 且不弹窗），那种环境里"点保存毫无反应"就是这么来的 —— 凡是靠
   prompt 收口令的路径，在端侧都等于静默失败。 */
function passcodeInput() {
  for (const id of ['setPasscode', 'ghPasscode']) {
    const el = $(id);
    if (el && el.value.trim()) return el.value.trim();
  }
  return '';
}
function clearPasscodeInput() {
  ['setPasscode', 'ghPasscode'].forEach(id => { const el = $(id); if (el) el.value = ''; });
}
function settingsPasscode() { return passcodeInput() || lsGet('hub.passcode') || ''; }

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

/* ── 设置 → 模型子菜单（v0.13.41）──────────────────────────────────────
   交互两步：先选 agent，再选该 agent 要用的 CCR 模型；保存前给 diff 预览，
   保存时要口令（HUB_PASSCODE，与「终端口令」子页共用一个本机缓存的口令）。
   三条纪律：
     ① **不新增浮层**：v0.13.43 起三个子页是 main 里的 section.page（抽屉已拆），
        切换由左侧手风琴 + go(page) 完成 —— 比"抽屉内切 section"更彻底：压根没有浮层；
     ② **不用 inline onclick**：一切走三个设置页上的同一委托 settingsDelegates()
        （inline 会旁路委托，把"导航即清浮层"那类收场逻辑整段跳过）；
     ③ **档位判定读当前值、不读存档**：进哪个子页由点击决定，不写 localStorage
        （跨 origin 分叉那条不变量的同款要求）。 */
let SET_AGENTS = [];          // /api/settings/models 的 agents 段
let SET_AGENT = '';           // 当前选中的 agent（不落盘）
let SET_MODELS = null;        // /api/models 缓存（60s）
let SET_DIFF = null;          // 最近一次预览结果（保存时复用同一个 model 值）
/* 三个设置页的 id —— 委托与 go() 懒加载共用这一份清单，别再各处写字面量 */
const SET_PAGE_IDS = ['page-settings-model', 'page-settings-github', 'page-settings-token',
                      'page-settings-logs'];

async function settingsModelsLoad(force) {
  const box = $('setAgentList');
  if (!box) return;
  if (!SET_AGENTS.length || force) {
    try {
      const d = await api('/api/settings/models');
      SET_AGENTS = d.agents || [];
      settingsRouterRender(d.ccr || {});
    } catch (e) {
      box.innerHTML = '<span class="hint">agent 清单加载失败：' + escapeHtml(e.message) + '</span>';
      return;
    }
  }
  box.innerHTML = SET_AGENTS.map(a => {
    const off = !a.writable;
    const why = a.reason || a.note || '不可设置';
    const tip = off ? why : ((a.name || a.id) + ' · 当前 ' + (a.current || '未读到') +
      (a.provider ? ' · provider ' + a.provider : '') + (a.files || []).join(' / '));
    return '<button class="btn sm' + (a.id === SET_AGENT ? ' on' : '') + '" type="button"' +
      ' data-settings-agent="' + escapeHtml(a.id) + '"' +
      (off ? ' aria-disabled="true"' : '') +
      ' title="' + escapeHtml(tip) + '">' + escapeHtml(a.name || a.id) + '</button>';
  }).join('') || '<span class="hint">无可用 agent</span>';
}

function settingsRouterRender(ccr) {
  const box = $('setCcrRouter');
  if (!box) return;
  const r = ccr.router || {};
  const keys = Object.keys(r);
  if (!keys.length) { box.textContent = ccr.note || '未读到 CCR 配置'; return; }
  box.innerHTML = '<div class="set-router">' + keys.map(k =>
    '<span class="k">' + escapeHtml(k) + '</span><span class="v">' + escapeHtml(r[k]) + '</span>').join('') +
    '</div><div class="hint" style="margin-top:6px">只读：CCR 属三方互斥保护面，且运行中写 config.json 会被运行态覆盖。</div>';
}

function settingsPickAgent(id) {
  const a = SET_AGENTS.find(x => x.id === id);
  if (!a || !a.writable) return;
  SET_AGENT = id;
  SET_DIFF = null;
  settingsModelsLoad();                       // 重渲染选中态
  $('setModelStep').style.display = 'block';
  $('setDiffBox').style.display = 'none';
  /* v0.13.45：不再在这里把保存按钮置灰 —— 置灰的按钮不派发 click，用户点它
     得不到任何反馈（日志里连请求都没有），正是第二轮报障的形态。 */
  $('setAgentMeta').textContent = '当前：' + (a.current || '未读到') +
    '｜hub 侧：' + (a.hub_model || '未设置') + '｜配置文件：' + (a.files || []).join(' / ');
  settingsModelOptions(a);
}

async function settingsModelOptions(a) {
  const sel = $('setModelSel');
  if (!sel) return;
  const fresh = !SET_MODELS || (Date.now() - (SET_MODELS._ts || 0) > 60000);
  if (fresh) {
    sel.innerHTML = '<option value="">模型清单加载中…</option>';
    try {
      const d = await api('/api/models');
      SET_MODELS = { models: d.models || [], groups: d.groups || {}, _ts: Date.now() };
    } catch (e) {
      sel.innerHTML = '<option value="">模型清单加载失败</option>';
      return;
    }
  }
  // 未设 hub 侧时以 agent 现值预选；两项都没有则「默认（网关路由）」= 空值
  const cur = a.hub_model || a.current || '';
  const g = SET_MODELS.groups || {};
  let html = '<option value="">默认（网关路由）</option>';
  Object.keys(g).forEach(gk => {
    html += '<optgroup label="' + escapeHtml(gk) + '">' + g[gk].map(mid => {
      const m = (SET_MODELS.models || []).find(x => x.id === mid);
      const label = (m && m.name && m.name !== mid) ? (mid + ' — ' + m.name) : mid;
      return '<option value="' + escapeHtml(mid) + '"' + (mid === cur ? ' selected' : '') + '>' +
        escapeHtml(label) + '</option>';
    }).join('') + '</optgroup>';
  });
  const grouped = new Set(Object.keys(g).flatMap(k => g[k]));
  const rest = (SET_MODELS.models || []).filter(m => !grouped.has(m.id));
  if (rest.length) {
    html += '<optgroup label="其他">' + rest.map(m =>
      '<option value="' + escapeHtml(m.id) + '"' + (m.id === cur ? ' selected' : '') + '>' +
      escapeHtml(m.name || m.id) + '</option>').join('') + '</optgroup>';
  }
  sel.innerHTML = html;
}

function setSelectedModel() { return $('setModelSel') ? $('setModelSel').value : ''; }

async function settingsPreviewModel() {
  if (!SET_AGENT) return toast('先选一个 agent', 'err');
  const model = setSelectedModel();
  if (!model) return toast('先选一个模型', 'err');
  const box = $('setDiffBox');
  try {
    SET_DIFF = await api('/api/settings/model/preview?agent_id=' + encodeURIComponent(SET_AGENT) +
      '&model=' + encodeURIComponent(model));
  } catch (e) {
    SET_DIFF = null; box.style.display = 'none';
    settingsRenderError('预览失败', e);
    return toast('预览失败：' + e.message, 'err');
  }
  const files = SET_DIFF.files || [];
  box.innerHTML = '<div class="set-diff">' +
    '<div class="set-diff-hd">将写入 ' + files.length + ' 个文件（保存时自动时间戳备份）</div>' +
    files.map(f => '<div class="set-diff-row"><div class="k">' + escapeHtml(f.file) + '</div>' +
      (f.changes || []).map(c => '<div class="v">' + escapeHtml(c.where) + '：' +
        escapeHtml(c.from || '（空）') + ' → ' + escapeHtml(c.to) + '</div>').join('') +
      /* v0.13.44：预览就把「这个文件现在写不动」摆在明面上（chattr +i / 只读挂载），
         别等保存时才用一句 4 秒就消失的 toast 告诉用户。 */
      (f.writable === false ? '<div class="v">注意：该文件当前不可写，直接保存会被拒' +
        '（常见是被设了不可变属性，需先解除）</div>' : '') + '</div>').join('') +
    '</div>' + (SET_DIFF.argv && SET_DIFF.argv.length
      ? '<div class="hint" style="margin-top:6px">hub 拉起终端时追加：' + escapeHtml(SET_DIFF.argv.join(' ')) + '</div>'
      : '');
  box.style.display = 'block';
  $('setApplyBtn').disabled = false;
}

/* 保存结果必须**常驻在页内**：09-27 报障「保存并不能生效」的真身是——保存其实成功
   了，但页面只闪一条 4 秒的 toast，状态行与下拉全都停在保存前的样子，用户据此判定
   没生效。所以成功要把落盘清单 + 备份路径写出来，失败要把 HTTP 状态与原因写出来，
   两样都不许只靠 toast。 */
function settingsRenderApplied(d) {
  const box = $('setDiffBox');
  if (!box) return;
  const applied = d.applied || [];
  box.innerHTML = '<div class="set-diff"><div class="set-diff-hd">已生效：' +
    escapeHtml(d.agent_id) + ' → ' + escapeHtml(d.model) +
    (d.argv && d.argv.length ? '（新开终端追加 ' + escapeHtml(d.argv.join(' ')) + '）' : '') +
    '</div>' +
    applied.map(a => '<div class="set-diff-row"><div class="k">' + escapeHtml(a.file) + '</div>' +
      (a.changes || []).map(c => '<div class="v">' + escapeHtml(c.where) + '：' +
        escapeHtml(c.from || '（空）') + ' → ' + escapeHtml(c.to) + '</div>').join('') +
      '<div class="v">备份：' + escapeHtml(a.backup) + '</div></div>').join('') +
    '<div class="v">已开着的会话不受影响，新开终端才带这个模型。</div></div>';
  box.style.display = 'block';
}

function settingsRenderError(prefix, e) {
  const box = $('setDiffBox');
  if (!box) return;
  box.innerHTML = '<div class="set-diff"><div class="set-diff-hd">' + escapeHtml(prefix) + '</div>' +
    '<div class="set-diff-row"><div class="v">' + escapeHtml(e.message || String(e)) + '</div>' +
    (e.http ? '<div class="v">HTTP ' + escapeHtml(String(e.http)) + '</div>' : '') +
    '</div></div>';
  box.style.display = 'block';
}

async function settingsApplyModel() {
  /* v0.13.45：保存按钮不再有"未预览就禁用"的状态。09-27 第二轮报障（手机
     192.168.5.99 来访）日志里只有 GET /api/settings/models 与 /api/models，
     **连一条 preview/apply 请求都没有** ⇒ 用户是选完模型直接点保存，而 disabled
     按钮不派发 click ⇒ 委托收不到、toast 也不弹，全程零反馈，"保存不了"就此成立。
     所以：保存自己负责补齐预览（一次点击走完），按钮只在**保存进行中**才禁用。 */
  if (!SET_AGENT) {
    settingsRenderError('保存未执行', new Error('先在上面的列表里选一个 agent'));
    return toast('先选一个 agent', 'err');
  }
  const model = setSelectedModel();
  if (!model) {
    settingsRenderError('保存未执行', new Error('先在「选择模型」下拉里选一个 CCR 模型'));
    return toast('先选一个模型', 'err');
  }
  if (!SET_DIFF || SET_DIFF.model !== model) {
    await settingsPreviewModel();              // 自动补预览；失败时里面已渲染 + toast
    if (!SET_DIFF || SET_DIFF.model !== model) return;
  }
  let pc = settingsPasscode();
  if (!pc) {
    /* 不再用 prompt 收口令：APP 内嵌 WebView 与部分手机浏览器会直接吞掉它
       （不弹窗、返回 null），那种环境里这一步等于静默失败。改成指路口令框。 */
    const el = $('setPasscode');
    if (el) el.focus();
    settingsRenderError('保存未执行', new Error(
      '请先在「设置口令」框里填入 HUB_PASSCODE（不再弹窗收集：手机/APP 会吞弹窗）'));
    return toast('请先填设置口令', 'err');
  }
  const btn = $('setApplyBtn');
  if (btn) btn.disabled = true;                // 只防连点，保存完立刻恢复
  try {
    const d = await api('/api/settings/model/apply', {
      method: 'POST',
      noToken: true,                          // 该端点只认 HUB_PASSCODE，别再索 TERM_TOKEN
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: SET_AGENT, model: model, passcode: pc })
    });
    lsSet('hub.passcode', pc);                // 通过了才缓存（与口令页同口径）
    clearPasscodeInput();
    toast(SET_AGENT + ' 模型已设为 ' + model + '（备份 ' + (d.applied || []).length + ' 份）', 'ok');
    SET_AGENTS = [];
    SET_DIFF = null;
    await settingsModelsLoad(true);
    settingsRefreshMeta(model);               // 用重载后的真值刷新状态行（不是拿入参糊一个）
    settingsRenderApplied(d);
  } catch (e) {
    if (/401|口令/.test(e.message)) lsRemove('hub.passcode');
    clearPasscodeInput();
    settingsRenderError('保存失败', e);
    toast('保存失败：' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;            // 保存完必须恢复可点，否则第二次就"点不动"
  }
}

function settingsRefreshMeta(model) {
  const a = SET_AGENTS.find(x => x.id === SET_AGENT);
  const el = $('setAgentMeta');
  if (!el) return;
  if (!a) { el.textContent = '已保存：' + model; return; }
  el.textContent = '当前：' + (a.current || '（未读到）') + '｜hub 侧：' + (a.hub_model || '未设置') +
    '｜配置文件：' + (a.files || []).join(' / ');
}

/* ── 设置 → GitHub 子菜单（v0.13.42）──────────────────────────────────────
   把原来写死在 githubprojects.py 里的三样东西（API 基址 / token 文件 / 克隆落点）
   变成运行时可配。三条纪律与模型页同源：
     ① 不新增浮层（第三个设置页，与模型页并列在 main 里）；
     ② 按钮一律 data-settings-act 走同一委托，无 inline onclick；
     ③ key 是**只写**字段：页面只显示掩码与来源，输入框留空 = 不改动（清除走按钮）。
   读取顺序 Hub 设置 → 环境变量 → 内置默认，改完即时生效（服务端每次现读）。 */
let GH_VIEW = null;                 // GET /api/settings/github 的现值

async function settingsGithubLoad(force) {
  try {
    GH_VIEW = await api('/api/settings/github');
  } catch (e) {
    const st = $('ghStatus');
    if (st) { st.style.display = ''; st.textContent = 'GitHub 设置加载失败：' + e.message; }
    return;
  }
  const v = GH_VIEW || {};
  const put = (id, val) => { const el = $(id); if (el && (force || !el.value)) el.value = val || ''; };
  put('ghApiBase', v.api_base);
  put('ghGitHost', v.git_host);
  put('ghOwner', v.owner);
  put('ghCloneBase', v.clone_base);
  const src = v.sources || {};
  const srcLabel = { db: '本页设置', env: '环境变量', default: '内置默认' };
  const stamp = (id, key) => { const el = $(id); if (el) el.textContent = '（' + (srcLabel[src[key]] || '内置默认') + '）'; };
  stamp('ghApiBaseSrc', 'api_base');
  stamp('ghGitHostSrc', 'git_host');
  stamp('ghCloneBaseSrc', 'clone_base');
  const t = v.token || {};
  const ts = $('ghTokenState');
  if (ts) ts.textContent = t.set ? ('已设置 ' + (t.mask || '') + ' · ' + (t.type || '') + ' · 来源 ' +
    (srcLabel[t.source] || t.source || '?')) : '未设置';
  settingsGithubStatus();
}

function settingsGithubStatus(note) {
  const box = $('ghStatus');
  if (!box || !GH_VIEW) return;
  const v = GH_VIEW;
  const L = v.list || {};
  const rows = [
    ['远程地址', v.api_base],
    ['Git 主机', v.git_host || '（按地址派生）'],
    ['归属', v.owner || '（当前账号全部）'],
    ['克隆落点', v.clone_base + (v.clone_base_exists ? '' : '（尚不存在，首次克隆时创建）')],
    ['key', (v.token && v.token.set) ? ((v.token.mask || '') + ' · ' + (v.token.type || '') +
      ' · 来源 ' + (v.token.source || '?')) : '未设置'],
    ['远端清单', L.count + ' 个仓库' + (L.cached ? '（缓存 ' + L.age_s + 's，点刷新重拉）' : '（未拉取）')],
    ['口令门', v.writable ? '已启用（HUB_PASSCODE 已配）' : '未启用：服务端没配 HUB_PASSCODE，保存会被拒']
  ];
  box.innerHTML = rows.map(r => '<div><span class="k">' + escapeHtml(r[0]) + '：</span>' +
    '<span class="v">' + escapeHtml(r[1]) + '</span></div>').join('') +
    (note ? '<div style="margin-top:6px">' + escapeHtml(note) + '</div>' : '');
  box.style.display = '';
}

function ghPayload(extra) {
  const v = (id) => { const el = $(id); return el ? el.value.trim() : ''; };
  const p = { api_base: v('ghApiBase'), git_host: v('ghGitHost'), owner: v('ghOwner'),
    clone_base: v('ghCloneBase') };
  const tok = v('ghToken');
  if (tok) p.token = tok;                     // 留空 = 不改动
  if ($('ghWriteFiles') && $('ghWriteFiles').checked) p.write_files = true;
  return Object.assign(p, extra || {});
}

/* 与模型页同口径（v0.13.45）：不向 prompt 要口令 —— 手机/APP WebView 会吞弹窗，
   那种环境里"点了没反应"就是这么来的。缺口令就指回页面上的口令框并说明原因。 */
function ghRenderError(prefix, e) {
  const box = $('ghStatus');
  if (!box) return;
  box.innerHTML = '<div class="v">' + escapeHtml(prefix + '：' + (e.message || String(e))) +
    (e.http ? '（HTTP ' + escapeHtml(String(e.http)) + '）' : '') + '</div>';
  box.style.display = '';
}

async function ghAskPasscode(what) {
  let pc = settingsPasscode();
  if (!pc) {
    const el = $('ghPasscode');
    if (el) el.focus();
    ghRenderError(what + '未执行', new Error(
      '请先在「设置口令」框里填入 HUB_PASSCODE（不再弹窗收集：手机/APP 会吞弹窗）'));
    return '';
  }
  return pc;
}

function ghPost(path, body) {
  /* noToken：GitHub 四个写端点与模型保存同口径，只认 HUB_PASSCODE（已登记在
     writeauth.EXEMPT_PREFIXES），索 TERM_TOKEN 只会多一个无关弹窗。 */
  return api(path, { method: 'POST', noToken: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
}

async function settingsGithubTest() {
  let pc = await ghAskPasscode('测试连接');
  if (!pc) return;
  const p = ghPayload({ passcode: pc, api_base: $('ghApiBase').value.trim() || undefined });
  try {
    const d = await ghPost('/api/settings/github/test', p);
    lsSet('hub.passcode', pc);
    if (!d.ok) return settingsGithubStatus('试连失败：' + (d.error || '未知'));
    const rate = d.rate || {};
    settingsGithubStatus('试连成功：' + (d.login || '?') + ' · key ' + (d.token_type || '') +
      ' ' + (d.token_mask || '') + ' · 配额 ' + (rate.remaining === undefined ? '?' :
        rate.remaining + '/' + rate.limit) + ' · 列仓库 ' + (d.repo_probe && d.repo_probe.ok ? '可用' : '不可用') +
      ' · ' + (d.took_ms || 0) + 'ms');
  } catch (e) {
    if (/401|口令/.test(e.message)) lsRemove('hub.passcode');
    toast('试连失败：' + e.message, 'err');
  }
}

async function settingsGithubApply() {
  let pc = await ghAskPasscode('保存 GitHub 设置');
  if (!pc) return;
  const p = ghPayload({ passcode: pc });
  try {
    const d = await ghPost('/api/settings/github/apply', p);
    lsSet('hub.passcode', pc);
    const box = $('ghDiff');
    if (box) {
      const ch = d.changes || [], fw = d.files_written || [];
      box.innerHTML = '<div class="set-diff"><div class="set-diff-hd">已保存 ' + ch.length +
        ' 项' + (fw.length ? '（回写 ' + fw.length + ' 个文件）' : '（仅 Hub 侧，未落文件）') + '</div>' +
        ch.map(c => '<div class="set-diff-row"><div class="k">' + escapeHtml(c.key) + '</div>' +
          '<div class="v">' + escapeHtml(c.from) + ' → ' + escapeHtml(c.to) + '</div></div>').join('') +
        (d.backups || []).map(b => '<div class="set-diff-row"><div class="v">备份 ' +
          escapeHtml(b) + '</div></div>').join('') + '</div>';
      box.style.display = '';
    }
    $('ghToken').value = '';
    await settingsGithubLoad(true);
    toast('GitHub 设置已保存（' + (d.applied || []).join('、') + '）', 'ok');
  } catch (e) {
    if (/401|口令/.test(e.message)) lsRemove('hub.passcode');
    toast('保存失败：' + e.message, 'err');
  }
}

async function settingsGithubClear() {
  if (!confirm('清除 Hub 侧的 GitHub 设置（地址/key/归属/落点），回落到环境变量与内置默认？')) return;
  let pc = await ghAskPasscode('清除 GitHub 设置');
  if (!pc) return;
  try {
    const d = await ghPost('/api/settings/github/clear', { passcode: pc });
    lsSet('hub.passcode', pc);
    await settingsGithubLoad(true);
    toast('已清除：' + ((d.cleared || []).join('、') || '（本来就是空的）'), 'ok');
  } catch (e) {
    if (/401|口令/.test(e.message)) lsRemove('hub.passcode');
    toast('清除失败：' + e.message, 'err');
  }
}

async function settingsGithubRefresh() {
  let pc = await ghAskPasscode('刷新远端清单');
  if (!pc) return;
  try {
    const d = await ghPost('/api/settings/github/refresh', { passcode: pc });
    lsSet('hub.passcode', pc);
    await settingsGithubLoad(true);
    if (d.errors && d.errors.length) toast('重拉完成但有告警：' + d.errors[0], 'err');
    else toast('已重拉远端清单：' + d.count + ' 个仓库', 'ok');
  } catch (e) {
    if (/401|口令/.test(e.message)) lsRemove('hub.passcode');
    toast('刷新失败：' + e.message, 'err');
  }
}

/* ── 设置 → 日志子菜单（v0.13.46）────────────────────────────────────────
   数据源 GET /api/hublog：journald 服务日志 + profile_events 操作事件，合并按时间倒序。
   三条纪律与另三个设置页同源：① 无浮层（正文出页，筛选控件全在页内）；
   ② 按钮一律 data-settings-act 走同一委托，无 inline onclick；
   ③ **鉴权失败不弹 prompt**（手机/APP WebView 吞弹窗 ⇒ 点了没反应）：原因写进
      页内 #logStats 并 focus 口令框，与模型/GitHub 页 v0.13.45 的口径一致。
   后端按写方法鉴权（GET 也要口令），故 token 由本页自己带（api() 只给写方法带）。
   var 声明而非 let：go()（06 分片顶层就会跑）可能经 settingsLogsLoad 读到它，
   与 RL_FIRST/setModelPageLoaded 同一条 TDZ 纪律。 */
var LOG_LAST = null;      // 最近一次结果：复制/导出复用，不再为同一次查看打两次后端

function settingsLogsParams() {
  const p = new URLSearchParams();
  const v = id => { const el = $(id); return el && el.value ? el.value : ''; };
  if (v('logSource')) p.set('source', v('logSource'));
  if (v('logLevel')) p.set('level', v('logLevel'));
  if (v('logWindow')) p.set('window', v('logWindow'));
  if (v('logQ')) p.set('q', v('logQ'));
  p.set('limit', '200');
  return p;
}

async function settingsLogsLoad() {
  const body = $('logBody');
  if (!body) return;
  boxBusy('logBody', '加载中…');
  const pc = ($('logPasscode') && $('logPasscode').value.trim()) || settingsPasscode();
  const opt = pc ? { headers: { 'x-hub-token': pc } } : {};
  try {
    const d = await api('/api/hublog?' + settingsLogsParams().toString(), opt);
    LOG_LAST = d;
    settingsLogsRender(d);
  } catch (e) {
    LOG_LAST = null;
    settingsLogsError(e);
  }
}

function settingsLogsRender(d) {
  const body = $('logBody');
  if (!body) return;
  const es = (d && d.entries) || [];
  if (!es.length) {
    body.innerHTML = '<div class="hint">该筛选下没有条目 —— 可放宽时间窗、换成「全部来源」，' +
      '或确认单元名（服务端未走 systemd 时 journald 一路为空）。</div>';
  } else {
    /* 复用 .set-diff/.set-diff-row 既有骨架（不新增 CSS 类与色 token）；
       级别只在行头的 .k 上着色，error=--danger / warn=--warn（:root 既有）。 */
    body.innerHTML = '<div class="set-diff">' + es.map(e => {
      const lv = e.level === 'error' ? 'ERR' : e.level === 'warn' ? 'WARN' : 'INFO';
      const col = e.level === 'error' ? 'var(--danger)' : e.level === 'warn' ? 'var(--warn)' : 'var(--muted)';
      return '<div class="set-diff-row"><div class="k" style="color:' + col + '">' +
        escapeHtml((e.ts || '').slice(0, 19) + '  ' + lv + '  ' + e.src + ':' + (e.tag || '')) +
        '</div><div class="v">' + escapeHtml(e.msg || '') + '</div></div>';
    }).join('') + '</div>';
  }
  settingsLogsStats(d);
}

function settingsLogsStats(d) {
  const box = $('logStats');
  if (!box) return;
  const s = (d && d.stats) || {};
  const rows = [
    ['条数', (d && d.count != null ? d.count : 0) + (d && d.truncated ? '（已到上限，可缩小时间窗或加关键字）' : '')],
    ['错误 / 警告', (s.error || 0) + ' / ' + (s.warn || 0)],
    ['来源', ((d && d.sources) || []).map(x => x.label + ' ' + x.count +
      (x.ok ? '' : '（不可用：' + (x.note || '') + ')')).join('；') || '—'],
  ];
  box.innerHTML = rows.map(r => '<div><span class="k">' + escapeHtml(r[0]) + '：</span>' +
    '<span class="v">' + escapeHtml(String(r[1])) + '</span></div>').join('');
  box.style.display = '';
}

function settingsLogsError(e) {
  const box = $('logStats');
  const body = $('logBody');
  const msg = (e && e.message) || String(e);
  if (/401|口令|缺少凭据/.test(msg)) lsRemove('hub.passcode');   // 存量口令失效：清掉再问，不循环
  if (body) body.innerHTML = '';
  if (box) {
    box.innerHTML = '<div class="v" style="color:var(--danger)">读取失败：' + escapeHtml(msg) +
      (e && e.http ? '（HTTP ' + escapeHtml(String(e.http)) + '）' : '') + '</div>' +
      '<div class="hint">在「设置口令」框填 HUB_PASSCODE 再点刷新（不会弹窗）。' +
      'HTTP 503 表示服务端没配 HUB_PASSCODE，填什么都没用。</div>';
    box.style.display = '';
  }
  const el = $('logPasscode');
  if (el) el.focus();
  toast('日志读取失败：' + msg, 'err');
}

function settingsLogsText() {
  if (!LOG_LAST || !LOG_LAST.entries || !LOG_LAST.entries.length) return '';
  return LOG_LAST.entries.map(e => (e.ts || '') + ' [' + String(e.level || '').toUpperCase() + '] ' +
    e.src + ':' + (e.tag || '') + ' ' + (e.msg || '')).join('\n');
}

/* 复制的两级兜底（09-27 实测）：navigator.clipboard 只在**安全上下文**可用，
   而手机走的是 http://192.168.x.x:3102（非 https ⇒ 不是安全上下文）⇒ 直接抛
   "Write permission denied"。所以先 clipboard、再 execCommand，两级都失败才
   明确告诉用户改用「导出 .log」，而不是一句"复制失败"让人以为是页面坏了。
   临时 textarea 挂在视口外 + pointer-events:none，不遮挡、不截点击。 */
async function settingsLogsCopyText(t) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(t);
      return true;
    }
  } catch (e) { /* 落到 execCommand 兜底 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = t;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return !!ok;
  } catch (e) {
    return false;
  }
}

async function settingsLogsCopy() {
  const t = settingsLogsText();
  if (!t) return toast('先刷新出日志再复制', 'err');
  if (await settingsLogsCopyText(t)) {
    toast('已复制 ' + LOG_LAST.entries.length + ' 条', 'ok');
  } else {
    toast('复制失败：浏览器不给剪贴板权限（局域网 http 地址常见）——请改用「导出 .log」', 'err');
  }
}

function settingsLogsExport() {
  const t = settingsLogsText();
  if (!t) return toast('先刷新出日志再导出', 'err');
  /* 走 Blob 而不是 format=text 端点：已在页里的结果不必再打一次后端，
     也省得把口令再塞进 URL（?token= 会被 uvicorn 记进访问行）。 */
  const blob = new Blob([t], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'agent-hub-' + ((LOG_LAST.query && LOG_LAST.query.source) || 'all') + '-' +
    new Date().toISOString().slice(0, 19).replace(/[:T]/g, '') + '.log';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast('已导出 ' + LOG_LAST.entries.length + ' 条', 'ok');
}

/* 三个设置正文页的**唯一**委托出口：选 agent / 预览 / 保存 / GitHub / 口令 / 日志 */
function settingsDelegates() {
  SET_PAGE_IDS.forEach(id => {
    const page = document.getElementById(id);
    if (!page) return;
    page.addEventListener('click', e => {
    const ag = e.target.closest('[data-settings-agent]');
    if (ag) {
      if (ag.getAttribute('aria-disabled') === 'true') {
        return toast(ag.getAttribute('title') || '该 agent 不支持统一设置模型', 'err');
      }
      return settingsPickAgent(ag.dataset.settingsAgent);
    }
    const act = e.target.closest('[data-settings-act]');
    if (act && act.dataset.settingsAct === 'preview') return settingsPreviewModel();
    if (act && act.dataset.settingsAct === 'apply') return settingsApplyModel();
    if (act) {
      const a = act.dataset.settingsAct;
      if (a === 'gh-test') return settingsGithubTest();
      if (a === 'gh-apply') return settingsGithubApply();
      if (a === 'gh-clear') return settingsGithubClear();
      if (a === 'gh-refresh') return settingsGithubRefresh();
      /* 终端口令页：原先是 inline onclick，随抽屉一起改走委托（同一个出口） */
      if (a === 'token-view') return settingsViewToken();
      if (a === 'token-clear') return settingsClearPasscode();
      if (a === 'token-copy') return settingsCopyToken();
      if (a === 'token-apply') return settingsApplyToken();
      if (a === 'token-toggle') return settingsToggleShow();
      /* 日志页（v0.13.46）：刷新 / 复制 / 导出 */
      if (a === 'log-refresh') return settingsLogsLoad();
      if (a === 'log-copy') return settingsLogsCopy();
      if (a === 'log-export') return settingsLogsExport();
    }
    });
    /* 筛选下拉变了就重拉（关键字框要等敲完 ⇒ 走「刷新」按钮，不逐字符打后端）。
       change 也挂在同一个委托出口里，不另起监听点。 */
    page.addEventListener('change', e => {
      const id = e.target && e.target.id;
      if (id === 'logSource' || id === 'logLevel' || id === 'logWindow') return settingsLogsLoad();
    });
  });
}
/* 注意：settingsDelegates() 的**调用**放在启动尾（06 分片），不在这里立即执行 ——
   它会经 api() 一路读到 04 分片声明的 `let term`，而 TDZ 闸门（tests/test_tdz_order.py）
   判的是"调用点早于声明点"：在 01 分片里直接调用必然踩线。注册动作本身没有时序要求
   （只是挂监听），放在启动尾零成本。 */
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
