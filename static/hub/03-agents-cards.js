/* 尺寸意图（B2）：claim = 「我是主人，按我的尺寸来」；update = 「我只是几何变了」。
   服务端对非所有者的 update 一律静默忽略 ⇒ 后台手机端偷不走 PTY 尺寸。
   force（连接建立 / 重连 / 点芯片 / 自愈）一律 claim：那是用户主动动作，
   也保证至少有一端能拿到所有权（否则谁都只发 update，尺寸会永远卡在 80×24）。 */
function termSizeIntent(force) {
  if (force) return 'claim';
  if (document.hidden) return 'update';   // 后台标签/切走的手机：不夺权
  return termSizeClaimed ? 'claim' : 'update';
}
function termSendSize(cols, rows, force) {
  if (!termWs || termWs.readyState !== 1) return;
  const intent = termSizeIntent(force);
  try {
    termWs.send(JSON.stringify({ type: 'resize', intent: intent, cols: cols, rows: rows }));
    if (intent === 'claim') termSizeClaimed = true;
  } catch (e) { /* 正在关：交给 onclose */ }
}

/* 几何变化走 debounce，force 走立刻。
   debounce 只压几何变化：连拖窗口时中间那些尺寸一个都不必真的下发，
   ioctl + 整屏 refresh 才是最贵的那部分。 */
let termRepaintTimer = null;
function termRepaint(force) {
  if (!term || !termFit) return;
  if (!termVisible()) { termPaintedAt = ''; return; }
  if (force) {
    if (termRepaintTimer) { clearTimeout(termRepaintTimer); termRepaintTimer = null; }
    termRepaintNow(true);
    return;
  }
  if (termRepaintTimer) clearTimeout(termRepaintTimer);
  termRepaintTimer = setTimeout(() => { termRepaintTimer = null; termRepaintNow(false); },
                                TERM_RESIZE_DEBOUNCE_MS);
}
function termRepaintNow(force) {
  try { termFit.fit(); } catch (e) {}
  const size = term.cols + 'x' + term.rows;
  // pty 那边可能被别的客户端改过尺寸，重连时（force）无条件报一次当前行列
  if ((size !== termPaintedAt || force)) termSendSize(term.cols, term.rows, force);
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

/* ── v0.13.64 auth_url 旁路消费（P2）──────────────────────────────────────
   场景：手机上跑 `claude setup-token` / 任何 OAuth 登录，登录 URL 只出现在
   pty 输出里。窄屏上那串 URL 要靠肉眼抄 —— 又长又断行，抄错一个字符就白跑。
   这里把 URL 提成**可点按钮**：一眼可按，按完直接开浏览器。
   为什么默认不自动开：现代浏览器只允许「用户手势内」window.open，程序性调用
   会被拦成弹窗 ⇒ 用户看到的是"点了没反应"，比不给按钮更糟。
   服务端 auto=true（输出里明说 "press enter to open" 之类）才自动开。 */
function termAuthUrl(url, auto) {
  const safe = String(url || '');
  if (!/^https?:\/\//i.test(safe)) return;     // 双保险：只放行 http/https
  /* 画面上也留一行可复制的纯文本：按钮被拦、或用户想手动拷时仍有出路。
     刻意用 OSC 8 之外的方式（普通可见文本）—— 它要"看得见"，不是隐藏超链接。 */
  const note = '\r\n\x1b[95m[登录链接] ' + safe + '\x1b[0m\r\n';
  try { if (term) term.write(note); } catch (e) {}
  const open = () => { try { window.open(safe, '_blank', 'noopener'); } catch (e) { toast('请手动复制上面的链接', 'err'); } };
  /* toast() 的签名是 (msg, cls)，**没有** onClick 参数（01-core-boot.js:157）——
     早先这里多传了个 open 当第三参，函数会静默忽略 ⇒ 按钮点不动。这里显式
     把 toast 节点改成可点，而不是给 toast() 硬加参数（那会波及其余 40+ 调用方）。 */
  try {
    const el = toast('检测到登录链接，点此打开 ↗', 'info');
    if (el) {
      el.style.cursor = 'pointer';
      el.style.textDecoration = 'underline';
      el.addEventListener('click', open);
      el.title = safe;
    }
  } catch (e) { open(); }
  if (auto) open();
}
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
  /* 自愈是**主动干预**（不是被动的几何变化），两帧都必须 claim：
     一是它得能真的改到尺寸，二是顺手把所有权收回本端——白块往往正是被别的端
     改小尺寸压出来的。它有自己的 120ms 定时器，不经过 resize debounce。 */
  termSendSize(c, Math.max(1, r - 1), true);
  setTimeout(() => termSendSize(c, r, true), 120);
}

/* ── 渲染器选择 ────────────────────────────────────────────────────────────────
   实测（@xterm/xterm 6.0.0，grep vendor/xterm.js）：核心只内置 DomRenderer，
   `_createRenderer()` 直接 `createInstance(DomRenderer, ...)` —— 每个字符格子是一个
   DOM <span>，80×24 就 1920 个节点，scrollback 5000 行上限下最多约 40 万节点在 DOM 里。
   原生终端是 GPU 画的位图，这是「跟系统终端差很远」的首要技术原因。
   WebGL 渲染器是**独立包**，必须自己挂：挂不上就留 DOM（慢，但不白屏）。
   必须在 term.open() **之后**挂：渲染器要拿真实 DOM 容器。 */

/* 渲染器偏好：URL `?term=webgl|dom` 优先（并记进 localStorage），其次 localStorage，
   `?term=auto` 清除记忆回到默认。留这个后门是因为「哪个渲染器能用」取决于客户端字体与
   GPU，服务端看不见也测不到 —— 出事时用户能自己一键切，不用等我。
   `canvas` 曾是中间档，xterm 6.0 已移除该 addon（peerDependencies 仍锁 `^5.0.0`，
   取证见 vendor/README.md）⇒ 老链接里的 `?term=canvas` 现在**显式降级 dom 并告警**，
   不静默改写成别的档：留着旧 URL 的人要能看见"这条后门没了"，否则他只会当成
   "改了参数没生效"再来报一次。 */
function termRendererPref() {
  try {
    const q = new URLSearchParams(location.search).get('term');
    if (q === 'auto') { lsRemove('hubTermRenderer'); return ''; }
    if (q === 'canvas') {
      console.warn('[term] ?term=canvas 在 xterm 6.0 已不存在（canvas addon 停止维护且不兼容 6.0）→ 按 dom 处理');
      lsSet('hubTermRenderer', 'dom');
      return 'dom';
    }
    if (/^(webgl|dom)$/.test(q || '')) {
      lsSet('hubTermRenderer', q);
      return q;
    }
    // 走 01 分片的 lsGet 守卫（隐私模式/配额满时不抛，见 tests/test_ls_guard.py）
    const s = lsGet('hubTermRenderer', '');
    if (s === 'canvas') {
      console.warn('[term] 本地记忆的渲染器偏好 canvas 已失效（xterm 6.0 移除该 addon）→ 按 dom 处理');
      lsSet('hubTermRenderer', 'dom');
      return 'dom';
    }
    return /^(webgl|dom)$/.test(s || '') ? s : '';
  } catch (e) { return ''; }
}

/* CJK 字形可用性探测 —— 决定敢不敢用 GPU/Canvas 渲染器。
   为什么需要这道闸：GPU/Canvas 渲染器把每个字形光栅化进一张纹理图集，对「不是来自字体栈
   里点名的那几个字体、而是靠 generic monospace 兜回来的 CJK」处理很差 —— 汉字被画成白色
   方块/空白，同一行的拉丁字母却完全正常（siteboon/claudecodeui#822 同款）。
   DOM 渲染器走浏览器原生文本渲染，字体回退链是完整的，永远不会出这个问题（代价是慢）。
   探测法：用**同一个字体栈**在 canvas 2d 上画「中」，再画一个私用区码点（正常字体必然缺
   该字形，会画成缺字方块）；两者墨迹量接近 ⇒ 「中」画出来的也是缺字方块而不是汉字。
   探测本身失败时返回 true（不阻断，维持原行为）—— 闸门只该在确证有问题时落下。 */
function termCjkUsable() {
  try {
    const cv = document.createElement('canvas');
    cv.width = 48; cv.height = 48;
    const cx = cv.getContext('2d');
    if (!cx) return true;
    // 字体栈里带换行缩进，先压成单行空白再交给 canvas font 解析
    const stack = cssToken('--term-font', 'monospace').replace(/\s+/g, ' ');
    const ink = ch => {
      cx.clearRect(0, 0, 48, 48);
      cx.font = '28px ' + stack;
      cx.fillStyle = '#ffffff';
      cx.fillText(ch, 4, 34);
      const d = cx.getImageData(0, 0, 48, 48).data;
      let n = 0;
      for (let i = 3; i < d.length; i += 4) if (d[i] > 40) n++;
      return n;
    };
    const zh = ink('\u4e2d');       // 中
    if (zh <= 0) return false;       // 汉字一个像素都没画出来
    const tofu = ink('\ue000');      // 私用区：正常字体必然缺字形
    return Math.abs(zh - tofu) > 10;
  } catch (e) { return true; }
}

/* 诊断出口：控制台里 `hubTermDiag()` 可查渲染器/CJK/字体栈，排障不用猜。 */
window.hubTermDiag = function () {
  return {
    renderer: termRendererName,
    pref: termRendererPref(),
    cjkUsable: termCjkUsable(),
    font: cssToken('--term-font', '')
  };
};

/* WebGL 图集显存止血（v0.13.60）：webgl 渲染器把每个用到的字形光栅化进一张纹理
   图集，上游只按 LRU 换页、**从不主动清空**；终端会跑数小时（长会话 / tail -f / 编译进度），
   字形集单调增长 ⇒ 图集页用满后换页开销上升，长期挂着会出现显存占用偏高、GPU 进程吃紧。
   上游 5.5.0 与 6.0.0 都提供公开的 `clearTextureAtlas()`，本项目从未调用过 ⇒ 这是未修态。
   这里定时调它：字形表是**惰性重建**的（清掉后用到哪个字形重新光栅化，多一次几十微秒的工作），
   换来显存不单调涨。放在终端不可见时跳过，不打扰后台标签页。 */
const TERM_ATLAS_SWEEP_MS = 120000;
function termAtlasSweep(addon) {
  const tick = () => {
    if (!termVisible() || document.hidden) return;
    try { addon.clearTextureAtlas(); } catch (e) {
      /* 上游若改签名就安静停掉这条止血线：宁可显存涨，也不能因为清理失败把终端带崩 */
      console.warn('[term] 图集清理失败，已停用定时清理：' + ((e && e.message) || e));
      clearInterval(id);
    }
  };
  const id = setInterval(tick, TERM_ATLAS_SWEEP_MS);
  /* term 是全局单例、整个页面生命周期不 dispose，所以定时器不用随 term 清理 */
  return id;
}

function termLoadRenderer() {
  const pref = termRendererPref();
  if (pref === 'dom') {
    termRendererName = 'dom';
    console.info('[term] 渲染器：dom（按 ?term=dom / localStorage 指定）');
    return;
  }
  if (!termCjkUsable()) {
    /* 中文是硬需求，性能是软需求：宁可慢，不能看不见字。 */
    termRendererName = 'dom';
    console.warn('[term] 字体栈取不到 CJK 字形（汉字会画成方块）→ 放弃 GPU，改走 DOM 渲染');
    return;
  }
  /* 只有 GPU 一档，失败即 DOM：canvas addon 的 peerDependencies 仍锁 @xterm/xterm ^5.0.0，
     与 6.0 不兼容（取证见 vendor/README.md），挂着它只会得到一个白屏的终端。 */
  let tries = [
    ['webgl', window.WebglAddon && window.WebglAddon.WebglAddon]
  ];
  if (pref === 'webgl') tries = tries.filter(t => t[0] === pref);
  for (let i = 0; i < tries.length; i++) {
    const name = tries[i][0], Ctor = tries[i][1];
    if (typeof Ctor !== 'function') continue;
    try {
      const addon = new Ctor();
      term.loadAddon(addon);
      /* WebGL 上下文会被系统回收（GPU 进程崩溃 / 驱动重置 / 标签页后台久了被丢弃）。
         xterm 会自己摘掉渲染器退回 DOM，这里补一条可见日志 + 强制重画把画面补回来。 */
      if (name === 'webgl') {
        if (addon.onContextLoss) addon.onContextLoss(() => {
          termRendererName = 'dom';
          console.warn('[term] WebGL 上下文丢失，已回落 DOM 渲染');
          try { term.refresh(0, term.rows - 1); } catch (e) {}
        });
        if (typeof addon.clearTextureAtlas === 'function') termAtlasSweep(addon);
      }
      termRendererName = name;
      console.info('[term] 渲染器：' + name);
      return;
    } catch (e) {
      console.warn('[term] ' + name + ' 渲染器不可用：' + ((e && e.message) || e));
    }
  }
  termRendererName = 'dom';
  console.warn('[term] 未挂上 WebGL 渲染器，停留在 DOM 渲染（可用但会卡）');
}

function ensureTerm() {
  if (term) return;
  // 字号 / 字族 / 配色全部取自 index.html 的 --term-* token（唯一真值源）。
  // 改前是 fontSize:13 + 'Menlo,Consolas,monospace' + 全灰 ANSI：字号不落在站点音阶内、
  // 缺 CJK 等宽导致中文掉字体、16 色全是灰阶导致 ls/git diff 的着色输出完全看不出区别。
  const T = (k, fb) => cssToken('--term-' + k, fb);
  term = new window.Terminal({
    /* allowProposedApi 必须开：term.unicode（Unicode11 宽字符）是 proposed API，
       不开的话写 `term.unicode.activeVersion='11'` 会抛
       "You must set the allowProposedApi option to true" —— 实测踩到（09-29 CDP 取证）。
       它只解锁 proposed 接口访问，不改变已有行为。 */
    allowProposedApi: true,
    fontSize: cssNum('--term-fs', 14),
    lineHeight: cssNum('--term-lh', 1.5),
    fontFamily: T('font', 'monospace'),
    cursorStyle: 'bar', cursorBlink: true, scrollback: 5000,
    /* v0.13.63 滚轮灵敏度：xterm 6.0 新增 consumeWheelEvent 里有
       `if (|deltaY| < 50) r *= 0.3` 再 `Math.floor` 取整 —— 默认 scrollSensitivity=1
       时，一格标准滚轮(deltaY=120，行高 24px) 只走 120/24*0.3 = 1.5 → 取整 1~2 行。
       实测（CDP 真派发，2000 行 scrollback、24 行视口）：
         sens=1  → 2.1 行/格   sens=3 → 6.2   sens=5 → 10.5   sens=10 → 20.8（严格线性）
       也就是说默认配置下要从底部滚到顶得摇约 940 格，体感就是「无法上翻」。
       5.5.0 没有这段逻辑（A/B 实测两版行为一致），但 5.5 是按 deltaY/行高 走的，
       同样幅度的滚动本来就该是 5 行/格 ⇒ 6.0 的 0.3 折相当于把滚动体验砍到 1/5。
       取 5：与「不按 6.0 打折时的自然值」(5 行/格) 对齐，滚到顶约 390 格，
       且按住 Alt/Ctrl/Shift 仍走 fastScrollSensitivity(=5) 走得更远，不丢快速滚动能力。 */
    scrollSensitivity: 5,
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
  /* Unicode11 必须在 open **之前**挂并激活：CJK / emoji 的 cell 宽度判定在渲染器初始化时
     就固化进 cell 尺寸表，之后再改 activeVersion 不会重算已布局的行 ⇒ 中文整体错位。 */
  try {
    if (window.Unicode11Addon && typeof window.Unicode11Addon.Unicode11Addon === 'function') {
      term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
      term.unicode.activeVersion = '11';
    }
  } catch (e) { console.warn('[term] Unicode11 不可用：' + ((e && e.message) || e)); }

  termFit = new window.FitAddon.FitAddon();
  term.loadAddon(termFit);
  term.open($('termEl'));
  /* 渲染器必须紧跟 open()：它要拿真实容器量 cell 尺寸。挂晚了会先以 DOM 渲染一阵子再切换。 */
  termLoadRenderer();

  /* 其余插件统一在这里挂，单个失败不影响别的（vendor 文件缺失 / 版本错都不会拖垮终端）。 */
  try {
    if (window.SearchAddon && typeof window.SearchAddon.SearchAddon === 'function') {
      termSearch = new window.SearchAddon.SearchAddon();
      term.loadAddon(termSearch);
    }
  } catch (e) { console.warn('[term] SearchAddon 挂载失败：' + ((e && e.message) || e)); }
  try {
    if (window.ClipboardAddon && typeof window.ClipboardAddon.ClipboardAddon === 'function')
      term.loadAddon(new window.ClipboardAddon.ClipboardAddon());
  } catch (e) { console.warn('[term] ClipboardAddon 挂载失败：' + ((e && e.message) || e)); }
  try {
    if (window.WebLinksAddon && typeof window.WebLinksAddon.WebLinksAddon === 'function')
      term.loadAddon(new window.WebLinksAddon.WebLinksAddon());
  } catch (e) { console.warn('[term] WebLinksAddon 挂载失败：' + ((e && e.message) || e)); }

  term.onData(termSend);
  /* 用户亲手点到终端 / 焦点落进来 ⇒ 本端主张尺寸所有权（见 termSizeIntent 注释）。
     这是「谁在用谁说了算」：正在操作的那一端永远能拿回尺寸，后台那一端拿不走。 */
  try { term.onFocus(() => { termSizeClaimed = true; }); } catch (e) {}
  const tEl = $('termEl');
  if (tEl && !tEl.dataset.claimBound) {
    tEl.dataset.claimBound = '1';
    tEl.addEventListener('pointerdown', () => { termSizeClaimed = true; });
  }
  /* 回调一律包一层：termRepaint(force) 的形参不能接 addEventListener/ResizeObserver 的事件对象 */
  window.addEventListener('resize', () => termRepaint());
  new ResizeObserver(() => termRepaint()).observe($('termEl'));
  requestAnimationFrame(() => termRepaint());
  setTimeout(() => termRepaint(), 150);
  termFindBind();
  termPasteBind();
  /* 触摸层最后挂：它要读 term.options（字号/行高）做手势换算，构造完才有意义。
     内部自带 touch 判定，桌面端这行是空操作。 */
  if (typeof termTouchBind === 'function') termTouchBind();
  termMouseResetBind();
}

/* ── 鼠标跟踪看门狗（v0.13.81）───────────────────────────────────────────────
   用户报障（2026-10-06）：agent-hub 嵌入式终端「向上浏览有时不行、无法复制、
   codex 终端会抢鼠标焦点」；cloudcli 终端（普通 shell→TTY 场景）踩中少所以
   「正常」。实测根因（真 chromium + CDP，探针 /tmp/probe_*）：
   TUI 程序（claude/codex 的交互界面）开启 xterm 鼠标跟踪（DECSET ?1002h/?1003h）
   后，滚轮与拖拽选中会被 xterm 原样吞掉转成 SGR 上报发给 pty 程序 —— xterm
   既把它当「程序内的滚动/点击」重画界面（codex 表现为输入框跟着动、焦点被抢），
   浏览器侧也不再滚 scrollback。TUI 异常退出时没发关闭序列（?1003l），xterm
   内部 mouseTrackingMode 就卡死在 any，只能靠重连（termConnect 写 TERM_MOUSE_OFF）
   复位 —— 这就是「时好时坏」和「打开 cloudcli 后再回来就能滑了」的机制。
   为什么用 capture 阶段监听 + 同步协议复位（试错试出来的最优解）：
     ① xterm 6.0 attachCustomWheelEventHandler 返回 false 不够 —— 事件已被
        preventDefault，浏览器默认滚动被禁，滚轮照样不动（实测 a/b/c）。
     ② passive wheel 监听里只写 term.write(1003l) 是异步的，首格滚轮被吞
        （onProtocolChange 要等下一帧才把 handleMouseWheel 翻回来）。
     ③ 唯一「首事件即恢复」的做法：capture 阶段同步把 coreMouseService
        .activeProtocol 切回 'NONE' —— setter 同步触发 onProtocolChange，
        把 _scrollableElement 的 handleMouseWheel 立刻翻回 true，当次 wheel
        事件就走默认滚动；再异步写 TERM_MOUSE_OFF 到 pty 让对端也退出跟踪态。
   实测数据（probe_final_watchdog.py，1003 跟踪态下）：
     首滚一格 viewportY 50→40（恢复滚动）｜首拖选 sel=12 选中文本（恢复复制）
     纯点击 mode any→none（点击回到浏览器，不再被 TUI 抢焦点）
   设计约束（改这几行前先读）：
     - 只在 mouseTrackingMode !== 'none' 时动手：正常态零开销、不碰任何行为。
     - mousedown 也复位：拖选第一帧（mousedown）就把协议切回 NONE，
       xterm 自带选择才能在这帧启动（实测只挂 wheel 时 sel 选不中）。
       代价：跟踪态下的纯点击也会回到浏览器行为 —— 这正是用户要的「点终端
       不再被 TUI 抢焦点」；TUI 的鼠标交互需要它自己重新发开启序列，它仍在
       DRAW 循环里时会即刻重新开起来。
     - termMouseLive 同步置 false：与 hub 的鼠标上报闸门口径一致，
       否则 termSend 还会把 SGR 上报当有效数据发给 pty。
     - 只绑一次（dataset 标记），#termEl 是整页生命周期同一个节点。 */
let termMouseResetBound = false;
function termMouseResetNow() {
  /* 同步切协议：让当次 wheel/mousedown 事件立刻回到浏览器默认路径 */
  try {
    if (term && term._core && term._core.coreMouseService
        && term._core.coreMouseService.activeProtocol !== 'NONE')
      term._core.coreMouseService.activeProtocol = 'NONE';
  } catch (e) { /* 内部结构升级就退回纯异步复位，不抛 */ }
  if (term && term.modes.mouseTrackingMode !== 'none') {
    termMouseLive = false;              // 与上报闸门口径对齐
    try { term.write(TERM_MOUSE_OFF); } catch (e) {}   // 异步补：让对端也退出
  }
}
function termMouseResetBind() {
  const el = term && term.element;
  if (!el || el.dataset.mouseResetBound) return;
  el.dataset.mouseResetBound = '1';
  /* capture 阶段 + passive：抢在 xterm 任何内部处理器之前，且不吞事件。
     mousedown 走捕获是为了纯点击也能复位（用户诉求「不抢焦点」）。 */
  el.addEventListener('wheel', termMouseResetNow, { capture: true, passive: true });
  el.addEventListener('mousedown', termMouseResetNow, { capture: true });
}

/* ── 粘贴（bracketed paste 安全包装）──────────────────────────────────────────
   接 xterm 自己的 textarea paste（用户 Ctrl/Cmd+V、右键粘贴都走这条路），但**抢在它前面**：
   捕获阶段接下 → 自己做安全处理 → 再交给 term.paste()（term.paste 会按 2004 状态包装）。
   两件 xterm 不做、我们必须做的事：
     ① 内嵌终止序列降级：粘贴内容里若含 ESC[201~，原样包进 bracketed 段会让对端**提前**
        结束粘贴模式，其后字节降级为普通按键被逐条执行（这就是注入面）。
        paseo 同款处理见 terminal-paste.ts:27（replaceAll(BRACKETED_PASTE_END, "[201~")）。
     ② 换行归一：\r\n / \n 统一成 \r（终端的"回车"语义），否则 readline 会把 CRLF 里的
        LF 再解释一次，多出一个空行。
   termBracketed 为 false（对端没声明支持）时**不自己发明包装**：发出去的 ESC[200~ 会被
   当成字面量糊在屏幕上，比不包更糟。此时只做换行归一，行为与改前一致。 */
function termPasteText(txt) {
  if (!term || !txt) return;
  let s = String(txt).replace(/\r\n/g, '\r').replace(/\n/g, '\r');
  if (termBracketed && s.indexOf(TERM_PASTE_END) >= 0) s = s.split(TERM_PASTE_END).join('[201~');
  try { term.paste(s); } catch (e) { /* paste 不可用就退回 input，至少别把内容丢了 */
    try { term.input(s, true); } catch (e2) {}
  }
}
function termPasteBind() {
  const ta = term && term.textarea;
  if (!ta || ta.dataset.pasteBound) return;
  ta.dataset.pasteBound = '1';
  ta.addEventListener('paste', e => {
    const cd = e.clipboardData || window.clipboardData;
    const txt = cd ? cd.getData('text') : '';
    if (!txt) return;
    e.preventDefault();
    e.stopPropagation();   // 别让 xterm 的原生 paste 再处理一遍（那遍不转义内嵌终止序列）
    termPasteText(txt);
  }, true);
}

/* ── 终端内查找（Ctrl/Cmd + F）─────────────────────────────────────────────────
   SearchAddon 挂上之后必须给它一个入口，否则只是「插件挂了但用户够不着」。
   keydown 走**捕获阶段**：xterm 会吞掉大部分按键，只有捕获阶段能抢在它前面拦下。
   搜索条是 absolute 浮层，不占 flex 空间 ⇒ 不影响 #termEl 的内容盒（FitAddon 算行数靠它）。 */
let termFindTimer = null;
function termFindNote(s) { const el = $('termFindNote'); if (el) el.textContent = s || ''; }
function termFindOpen() {
  if (!term) return;
  const box = $('termFind');
  if (!box) return;
  box.classList.add('on');
  const inp = $('termFindInput');
  if (inp) { try { inp.focus(); inp.select(); } catch (e) {} }
  termFindRun(1);
}
function termFindClose() {
  const box = $('termFind');
  if (box) box.classList.remove('on');
  try { if (termSearch) termSearch.clearDecorations(); } catch (e) {}
  termFindNote('');
  /* 回焦必须走同一谓词（L0 护栏 test_only_guarded_term_focus 盯的就是这一条）：
     关闭查找框是**用户**动作（Ctrl+F 是他自己按的），不是自动挂载/重连那类会偷偷弹软键盘
     的路径 ⇒ 传 user:true 如实表达「这是用户主动」，而不是绕过守卫写裸 focus。 */
  if (term && termFocusWanted({ user: true })) term.focus();
}
function termFindRun(dir) {
  if (!term || !termSearch) { termFindNote('查找不可用'); return; }
  const inp = $('termFindInput');
  const q = inp ? inp.value : '';
  if (!q) { try { termSearch.clearDecorations(); } catch (e) {} termFindNote(''); return; }
  /* incremental:true —— 边输边跳到当前匹配，不等回车；否则用户看不到自己打到哪了 */
  const opt = { caseSensitive: false, wholeWord: false, regex: false, incremental: true };
  try {
    const hit = dir < 0 ? termSearch.findPrevious(q, opt) : termSearch.findNext(q, opt);
    termFindNote(hit ? '有匹配' : '无匹配');
  } catch (e) { termFindNote('查找失败'); }
}
function termFindBind() {
  const inp = $('termFindInput');
  if (inp && !inp.dataset.bound) {
    inp.dataset.bound = '1';
    inp.addEventListener('input', () => {
      if (termFindTimer) clearTimeout(termFindTimer);
      termFindTimer = setTimeout(() => termFindRun(1), 120);   // 节流：别每敲一键扫一遍全部缓冲区
    });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); termFindRun(e.shiftKey ? -1 : 1); }
      else if (e.key === 'Escape') { e.preventDefault(); termFindClose(); }
      e.stopPropagation();      // 查找框里的按键绝不能漏进 pty
    });
  }
  const bind = (id, fn) => {
    const el = $(id);
    if (el && !el.dataset.bound) { el.dataset.bound = '1'; el.addEventListener('click', fn); }
  };
  bind('termFindPrev', () => termFindRun(-1));
  bind('termFindNext', () => termFindRun(1));
  bind('termFindClose', termFindClose);
}
/* 终端没在显示时不抢 Ctrl+F —— 那时浏览器自己的页内查找才是用户想要的 */
document.addEventListener('keydown', e => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  if (e.key !== 'f' && e.key !== 'F') return;
  if (!termVisible()) return;
  e.preventDefault(); e.stopPropagation();
  termFindBind();
  termFindOpen();
}, true);

function termDetach() {
  termRcCancel();          // 用户显式离开 ⇒ 任何在排的重连一律作废，不许把会话拖回来
  termTouchReset();        // 清掉残留手势态（否则新会话第一次滑动就"自己动了"）
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
  termBracketed = false;   // 粘贴模式同理：新会话的 2004 要等它自己实时发来才算数
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
    /* v0.13.64 auth_url 旁路（P2）：服务端把登录 URL 以 JSON 文本帧带外送来。
       必须**在写进画面之前**拦掉 —— 否则 {"type":"auth_url",...} 会被 xterm 当正文
       画到屏幕上（这正是旁路通道存在的理由：绝不污染输出流）。
       同理不能占用 replayFrame 那次判定：它是画面帧，不是控制帧。 */
    if (typeof raw === 'string' && raw.charCodeAt(0) === 123 /* { */) {
      let m = null;
      try { m = JSON.parse(raw); } catch (e) { m = null; }
      if (m && m.type === 'auth_url' && typeof m.url === 'string') {
        termAuthUrl(m.url, !!m.auto);
        return;
      }
    }
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
