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
