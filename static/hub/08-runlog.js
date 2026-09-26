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
