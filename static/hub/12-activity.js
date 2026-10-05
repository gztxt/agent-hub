/* ── P2-B（2026-09-30）：跨 Agent 活动指示 ──────────────────────────────────────
   兑现「多 Agent 统一管理中心」最核心的那条承诺：**现在要看谁在跑必须逐个点开**。
   本批走计划里的低成本路径 —— 复用既有 /api/term/sessions（不依赖服务端 pyte，
   融合计划的 P2-1 真值方案成本高风险大，本批不做，另登记）。

   两处上屏：
     ① 侧栏每个实体行尾一个忙碌点：有 alive 终端会话 ⇒ 亮；无 ⇒ 不占位
        （用 :empty 语义，零会话时侧栏与改动前逐像素一致）。
     ② 顶栏「在跑 N」：**在跑 = 有活着的终端会话的 Agent 数**，与「可用数」是两个
        概念，刻意分开 —— Agent 服务在跑（usable）不等于此刻有人在用它。

   口径纪律（与 P1-20 同一个教训）：判据只认 /api/term/sessions 的 alive，
   不用 last_seen / 进程表去猜「有没有人在用」—— 那些是「服务活着」不是「人在用」。
   拉取失败一律**保持上一次的值**并 console.warn，不清零：指示消失比指示滞后更糟。 */
var ACTIVITY = {};          // agent_id → { n, latest, active_s }
var activityLoaded = false;
var activityFailed = false;

/* 空闲多久算「在忙」：会话还活着但没动静超过 2 分钟 ⇒ 只亮常点不闪。
   取 2 分钟是照终端心跳（TERM_HB）量级定的：人 thinking 一轮通常 <2min。 */
const ACTIVITY_BUSY_S = 120;

function activityMap(sessions) {
  const m = {};
  (sessions || []).forEach(s => {
    if (!s || !s.alive || !s.agent_id) return;   // 已退出的会话不算「有人在用」
    const cur = m[s.agent_id];
    /* 一个 Agent 可能开着多个会话，取最近活跃的那个做代表 */
    if (!cur || (s.activity_s || 0) > (cur.active_s || 0)) {
      m[s.agent_id] = { n: (cur ? cur.n : 0) + 1, active_s: s.activity_s || 0, sid: s.id };
    } else {
      cur.n += 1;
    }
  });
  return m;
}

async function loadActivity() {
  /* 2026-10-05：页面隐藏时早退（别在后台标签里白拉）。
   * ⚠ 守卫放在**函数体内**而不是 `setInterval` 注册处 ——
   * tests/test_activity_indicator.py:114 断言的字面量是
   * `setInterval(loadActivity,\s*\d+)`，动注册会撞红。
   * 语义无副作用：停表期间不更新 ACTIVITY，而「拉不到就保持旧值」本来就是
   * 本函数的既定失败语义（见下面 catch），所以早退不改变可见行为。 */
  if (document.hidden) return;
  try {
    /* 只读**已存档**的 token，绝不走 termHeaders()。
       改前踩的坑（真渲染探针抓的，renderer 直接挂死）：
       termHeaders() → termToken() 在没有存档 token 时会 prompt() 弹原生口令框，
       而原生弹窗会挂住 renderer ⇒ 任何 CDP 请求都超时、页面看着"卡死"。
       产品侧更严重：活动指示是**被动展示**，若在加载时调 termHeaders()，
       每个没配过终端口令的访问者一进页面就被弹一次口令框 —— 为了看一个
       忙碌点逼人交密码。口令缺失时安静跳过（activityFailed），用户点进终端
       自己会走既有那条 prompt 流程。 */
    const t = lsGet('hub.term.token');
    if (!t) { activityFailed = true; renderActivity(); return; }
    const d = await api('/api/term/sessions', { headers: { 'X-TERM-TOKEN': t } });
    ACTIVITY = activityMap(d && d.sessions);
    activityLoaded = true;
    activityFailed = false;
    renderActivity();
  } catch (e) {
    /* 拉不到就保持旧值：宁可指示滞后，也不要让「在跑 N」闪成 0 再闪回来。
       首次就失败则标记出来，让顶栏不假装自己知道。 */
    activityFailed = true;
    if (!activityLoaded) ACTIVITY = {};
    console.warn('[activity] 拉取终端会话失败，沿用上次值：', e && e.message);
    renderActivity();
  }
}

/** 侧栏行尾忙碌点（只亮不闪，hover 给具体会话数）。 */
function activityDotHtml(a) {
  const st = ACTIVITY[a.id];
  if (!st) return '';
  const busy = (st.active_s || 0) <= ACTIVITY_BUSY_S;
  return '<span class="nav-busy' + (busy ? ' busy' : '') + '" title="' +
    escapeHtml(st.n > 1 ? st.n + ' 个终端会话在跑' : '终端会话在跑') +
    '" aria-label="' + (st.n > 1 ? st.n + ' 个终端会话在跑' : '终端会话在跑') + '"></span>';
}

/** 顶栏「在跑 N」+ 侧栏点。只在有会话时出现，零会话时不占位。 */
function renderActivity() {
  const ids = Object.keys(ACTIVITY);
  const n = ids.length;
  const el = $('hBusy');
  if (el) {
    if (activityFailed && !activityLoaded) {
      el.innerHTML = '';
    } else if (n) {
      const sess = ids.reduce((s, id) => s + (ACTIVITY[id].n || 0), 0);
      el.innerHTML = '<span class="hdot b" title="有活着的终端会话的 Agent"></span>在跑 ' + n +
        (sess > n ? '（' + sess + ' 会话）' : '');
    } else {
      el.innerHTML = '';
    }
  }
  /* 侧栏点：renderNav 有「内容没变就不重写 DOM」的省写逻辑，直接重渲即可，
     但为免打断滚动位置，这里只在状态确实变了时才让 nav 重绘。 */
  const sig = ids.map(id => id + ':' + ACTIVITY[id].n).sort().join(',');
  if (sig !== renderActivity._sig) {
    renderActivity._sig = sig;
    if (typeof renderNav === 'function') renderNav();
  }
}

/* 轮询节奏：活动变化比实体状态快得多（人开终端是秒级，实体启动是分钟级），
   故独立 8s 一次，比 loadAgents 的 30s 密。终端在跑时 xterm 已有自己的心跳，
   这里只读不写，不与 TERM_HB 抢。 */
loadActivity();
setInterval(loadActivity, 8000);
/* 回到可见时**立刻补一次**。否则笔记本合盖唤醒后，指示会停在休眠前的快照
   直到下一个 8s 周期 —— 用户第一眼看到的是过期数据。
   （这一条闸门没有约束，是我按「隐藏守卫必须有唤醒路径」补的；
    闸门真正约束的只有上面那条 setInterval 字面量。） */
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadActivity(); });
