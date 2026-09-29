# Paseo → Agent Hub 终端融合借鉴方案

- 日期：2026-09-29
- 上游：`https://github.com/getpaseo/paseo`（v0.10.0，clone 于 `/tmp/paseo-src`）
- 目标：把 Paseo 终端子系统值得学的设计，融进本机 `/home/gztxt/agent-hub`（v0.13.x，FastAPI + 原生 JS + xterm.js）
- 状态：**B1 / B2 / B4 已落地并实证**（2026-09-29 下午），B3 与 P2 未做。见 §9 执行记录。

---

## 0. 结论先行

**值得借鉴，但只在"流性能工程"与"多端一致性"两个方向；不要整体移植 Paseo 的终端架构。**

理由：两者定位不同。Paseo 是**通用终端编排器**（任意命令、跨平台、跨设备，要忍 Windows conpty / RN WebView 一堆平台坑）；Agent Hub 是**多 Agent 管理中心**（命令只出自画像白名单、跑 claude/codex 这类会 fork 子进程的 CLI）。Agent Hub 在**安全边界、进程治理、退出归因、Agent 可用性探活**四项上已经**强于** Paseo，这些绝不能为了"对齐 Paseo"而改。

真正的差距集中在三处，按性价比排序：

| 优先级 | 借鉴项 | 解决什么真问题 | 难度 | 收益 |
|---|---|---|---|---|
| **P0-1** | 输出合并 Coalescer（5ms 前后沿节流） | 构建/Agent 大段输出时 WS 帧洪水 | 低 | 高 |
| **P0-2** | 终端尺寸所有权（claim / update） | 手机后台偷偷改尺寸，把你桌面的 vim 压扁 | 低 | 高 |
| **P0-3** | resize 客户端 debounce（100ms） | 拖窗口打出上百次 `TIOCSWINSZ` | 低 | 中 |
| P1-1 | 带外消息保序（flush before snapshot/exit） | 偶发屏幕错乱、重复输出（难复现） | 低 | 中 |
| P1-2 | 每观看者独立合并缓冲 + 背压门 | 一个慢客户端拖慢/拖垮其他人 | 中 | 中 |
| P1-3 | xterm addon 补齐（Search / Unicode11 / WebLinks / Clipboard） | 无终端内搜索、CJK 宽字符错位、粘贴多行被逐行执行 | 低 | 中 |
| P2-1 | 服务端无头终端真值（pyte） | 重连只能重放裸字节，TUI 回放半屏空白 | 高 | 高（但前置依赖重） |
| P2-2 | 终端 tab 活动指示器 | 不知道哪个 Agent 跑完了、哪个在等授权 | 高 | 中 |

**不建议移植**：Paseo 的 worker 进程架构、无进程组灭杀、WebView/native-grid 渲染器、pane 分屏系统。理由见 §5。

---

## 1. Paseo 实证画像

（本节均为实测确认，来自 clone 后读源码，非推断）

### 1.1 定位

> "One interface for Claude Code, Codex, Copilot, OpenCode, and Pi agents." — `README.md:37`
> "Paseo doesn't have any telemetry, tracking, or forced log-ins." — `README.md:50`

跨平台多 Agent 编排：desktop / mobile / web / CLI 四端连一个本地 daemon。

### 1.2 规模

| 层 | 位置 | 终端相关代码量 |
|---|---|---|
| 服务端 | `packages/server/src/terminal/` | **10,894 行**（含测试） |
| 协议 | `packages/protocol/src/terminal-*` + `binary-frames/` | ~456 行 |
| 前端 | `packages/app/src/` 终端组件 + native-renderer | **10,496 行** |
| 文档 | `docs/terminal-performance.md`, `docs/terminal-activity.md` | 设计意图的一手材料 |

核心依赖：`node-pty` + `@xterm/headless`（`packages/server/package.json:119,130`）。

### 1.3 官方给出的管道（理解全部设计的钥匙）

`docs/terminal-performance.md:5-16`：

```
pty (node-pty, forked worker process)
  → headless xterm parse (worker, snapshot fidelity)
  → TerminalOutputCoalescer (worker, ≤1 IPC message per 5ms per terminal)
  → process.send IPC → daemon main process
  → TerminalOutputCoalescer (per client stream)
  → binary ws frame (2-byte header + raw bytes)
  → client decode → stream router → emulator runtime → xterm.write
```

三个结构决策：
1. PTY + 无头 xterm 解析器放**独立 worker 进程**；
2. **两级 coalescer**（worker 级 + 每客户端级）；
3. **服务端持有屏幕真值**（cell grid），而非只转发裸字节。

### 1.4 许可证（合规前置）

`LICENSE:1-8`：Apache License 2.0（第三方组件保留各自许可）。

Apache-2.0 允许借鉴设计、改写、自用。Agent Hub 属自用不分发，无 NOTICE 义务；但**若直接复制源码片段**，需在文件头保留版权声明并标注修改——本方案 P0/P1 各项**均为按设计重写**（Python/JS 各自实现），不复制代码，故无此负担。P2-1 若引入 `pyte` 属第三方包（MIT），需单独记入依赖清单。

---

## 2. 双端能力对照总表

| 能力 | Paseo | Agent Hub | 判定 |
|---|---|---|---|
| PTY 实现 | node-pty，独立 worker 进程 | `pty.fork()` + `asyncio` reader（单进程） | Hub 更简，够用 |
| **进程组灭杀** | ❌ 只杀 shell pid，靠 SIGHUP 隐式回收 | ✅ `os.killpg()` 组灭 + TERM→KILL 升级（`term.py:147-181`） | **Hub 强，别改** |
| **命令来源安全** | ❌ 任意命令（用户 profile 自定义） | ✅ 仅画像白名单，API 只收 `agent_id`（`term.py:1-8`） | **Hub 强，别改** |
| **退出原因归因** | 退出码 + 信号 + 最后 12 行 | 退出码 + 信号 + **JSC 内存耗尽专项判定**（`term.py:44-54`） | **Hub 强，别改** |
| **Agent 可用性探活** | 靠往用户 agent 配置装 hook 上报 | ✅ `vitals.py` L1/L2/L4/EP 四层取证 | **Hub 强，别改** |
| cwd 校验 | — | ✅ 绝对路径 + 实盘存在 + 字符栅栏（`term.py:217-231`） | Hub 强 |
| **输出合并** | ✅ 5ms 前后沿节流，两级部署 | ❌ 读一块发一块 | **补** |
| **尺寸所有权** | ✅ claim/update + WeakRef | ❌ 任何连接都能 resize（`term.py:500-504`） | **补** |
| **resize debounce** | ✅ 客户端 100ms | ❌ ResizeObserver 直发（`03-agents-cards.js:1-13`） | **补** |
| **多观看者隔离** | ✅ 每流独立 coalescer + slot + 背压 | ⚠️ 每观看者独立队列（`term.py:131`），但共享发送节奏 | 增强 |
| **重连回放** | ✅ 服务端 cell grid 快照，ANSI 重绘 | ⚠️ 64KB 裸字节 ring（`term.py:389-390`） | P2 增强 |
| **半开连接识别** | 靠 socket bufferedAmount | ✅ 心跳账本 + 退避重连（`02-nav-and-poll.js:197-199`） | **Hub 强** |
| **回放副作用治理** | 协议查询抑制 | ✅ 鼠标模式复位 + OSC/CSI gate（`03-agents-cards.js` + 常量在 `02:234-250`） | **Hub 强** |
| 白块自愈 | claim 状态机（根治尺寸） | ⚠️ "挪一行再挪回来"启发式（`03-agents-cards.js:153-165`） | 可升级 |
| 终端内搜索 | ✅ SearchAddon + 视口锚定 | ❌ 无 | 补 |
| CJK/宽字符 | ✅ Unicode11Addon | ❌ 只装了 FitAddon | 补 |
| 粘贴多行 | ✅ bracketed paste | ❌ 无 | 补 |
| 二进制帧 | ✅ 2 字节头 + 裸 payload | ❌ JSON 文本帧 | 暂不补（见 §5） |

---

## 3. 明确"别动"清单（Agent Hub 已有的强项）

这几项是 Agent Hub 比 Paseo 强的地方，方案执行时**禁止**为了对齐 Paseo 而改：

1. **进程组灭杀**（`term.py:147-181`）：Paseo 的 `terminal.ts:1467-1473` 只杀 shell pid，靠 PTY master 关闭时内核发 SIGHUP 隐式回收子进程——对 claude/codex 这类会 fork MCP server 的 CLI 是不可靠的。Hub 的 `killpg` + TERM→2s→KILL 升级是**更正确**的做法。
2. **命令白名单**（`term.py:1-8` 模块头）：Paseo 允许用户 profile 定义任意命令。Hub 只接受 `agent_id`，命令从画像出。这是安全边界，不能松动。
3. **退出归因**（`term.py:44-54`）：Hub 针对 `SIGILL/SIGSEGV/SIGBUS/SIGABRT/SIGKILL` 专门识别 JSC 内存耗尽，这是从本机 opencode 秒退事故里实测出来的，Paseo 没有。
4. **心跳半开识别**（`02-nav-and-poll.js:197-199`）：Paseo 靠 socket 层 `bufferedAmount`；Hub 的应用层往返账本对手机切网络更可靠。
5. **回放副作用治理**（鼠标模式复位、CSI/OSC 查询 gate）：Paseo 的协议查询抑制在**服务端**做（因为它有服务端 xterm）；Hub 在**客户端**做，同样有效且更轻。

---

## 4. 借鉴清单（逐项）

> 每项含：Paseo 证据 → Hub 现状 → 真实痛点 → 改法 → 难度/收益/风险。

### P0-1 输出合并 Coalescer（5ms 前后沿节流）

**Paseo 证据** — `packages/server/src/terminal/terminal-output-coalescer.ts:19,39-64`：

```ts
const DEFAULT_FLUSH_DELAY_MS = 5;
...
    // Leading edge: if nothing is pending and no flush happened within the last
    // flushDelayMs, flush immediately so interactive echo isn't delayed a full
    // window. Otherwise accumulate and let the trailing timer drain the burst.
    if (!this.flushTimer) {
      const elapsed = this.lastFlushAt === null ? Number.POSITIVE_INFINITY : this.now() - this.lastFlushAt;
      if (elapsed >= this.flushDelayMs) { this.flush(); return; }
      this.flushTimer = this.timers.setTimeout(() => { this.flushTimer = null; this.flush(); }, this.flushDelayMs);
    }
```

关键在**前沿立即刷**：`docs/terminal-performance.md:22` 明写"Reverting to trailing-only adds a full window (~5ms) to every keystroke echo"。纯 debounce 会给每次按键回显平白加 5ms——这是它比朴素 debounce 高级的地方。

部署位置也有讲究（`docs/terminal-performance.md:23`）：**合并要在离 PTY 最近的一层做**，否则主循环照样被打爆，只是换了个挨打的地方。

**Hub 现状** — `term.py:380-411`（`_attach_reader.on_readable`）：

```python
data = os.read(sess.fd, 65536)
if data:
    ...
    for vid, q in list(sess.viewers.items()):
        q.put_nowait(data)      # 读一块 → 每个观看者各塞一块
```

`pump()`（`term.py:454-491`）随即 `await ws.send_bytes(data)`。即：**PTY 每吐一块就发一帧**。PTY 的典型 chunk 是几十~几百字节，`npm run build` / agent 输出大 diff 时会持续高频吐。

**真实痛点**：WS 帧洪水。Starlette 每帧都有协议开销；手机端解析不过来时队列（maxsize=2000，`term.py:445`）打满 → 触发 Hub 现有的"丢弃字节"路径（`term.py:394-396`），用户看到 `[hub: 输出过快，已丢弃 N 字节]`——这是**丢内容**，不是降帧率。

**改法**：在 `term.py` 加一个 ~40 行的 `_OutputCoalescer`：

```python
class _OutputCoalescer:
    """前后沿节流：空闲后第一块立刻发（保按键回显），突发攒 5ms 尾巴一起发。"""
    def __init__(self, on_flush, delay_ms=5.0):
        self._on_flush, self._delay = on_flush, delay_ms / 1000.0
        self._buf, self._timer, self._last = bytearray(), None, None
    def handle(self, data: bytes):
        if not data: return
        self._buf += data
        if self._timer is None:
            now = time.monotonic()
            if self._last is None or now - self._last >= self._delay:
                self.flush(); return
            self._timer = asyncio.get_event_loop().call_later(self._delay, self.flush)
    def flush(self):
        if self._timer is not None: self._timer.cancel(); self._timer = None
        if not self._buf: return
        payload = bytes(self._buf); self._buf.clear()
        self._last = time.monotonic()
        self._on_flush(payload)
```

接入点在 `on_readable` 与 `pump` 之间：**每个观看者一个 coalescer**（顺带完成 P1-2 的一半），`on_flush` 里做 `send_bytes`。

- 难度：**低**（半天）
- 收益：**高**（直接消除帧洪水；按键回显延迟不升反降）
- 风险：低。唯一要守的是 P1-1（带外消息必须 flush 后再发）。

---

### P0-2 终端尺寸所有权（claim / update）

**Paseo 证据** — `packages/server/src/terminal/terminal-size-ownership.ts`（全文 38 行）：

```ts
const terminalSizeOwners = new WeakMap<TerminalSession, WeakRef<object>>();

export function applyTerminalSize(terminal, owner, request): boolean {
  const intent = resolveTerminalSizeIntent(request.intent);
  if (intent === "update" && terminalSizeOwners.get(terminal)?.deref() !== owner) return false;
  if (intent === "claim") terminalSizeOwners.set(terminal, new WeakRef(owner));
  if (currentSize.rows !== request.rows || currentSize.cols !== request.cols) {
    terminal.send({ type: "resize", rows: request.rows, cols: request.cols });
  }
  return true;
}
```

设计意图（`docs/terminal-performance.md:31`）：*"This lets an owning pane follow splits and keyboard insets without allowing an idle phone or browser to steal the PTY size."*

一个反直觉但重要的细节：**claim 即使尺寸相同也转移所有权**（测试 `terminal-size-ownership.test.ts:48-59`）。

**Hub 现状** — `term.py:500-504`：

```python
if j.get("type") == "resize":
    sess.cols, sess.rows = int(j["cols"]), int(j["rows"])
    fcntl.ioctl(sess.fd, termios.TIOCSWINSZ, struct.pack("HHHH", sess.rows, sess.cols, 0, 0))
```

**任何 WS 连接都能无条件改尺寸**，无归属概念。

**真实痛点**：Hub 有 PWA（`static/manifest.json`）且明确服务手机端（`term.py` 注释多处提及"手机断网/切网络"）。场景——桌面正开着 vim 编辑（120×40），手机端页面在后台被 ResizeObserver 或 visibilitychange 触发一次 80×24 的 resize → 桌面 vim 被压扁。这类 bug 表现为"我什么都没做，终端自己乱了"，极难归因。

**改法**：`term.py` 加约 30 行：

```python
_size_owner: Dict[str, str] = {}   # sid -> 持有尺寸所有权的 vid

def _apply_size(sess, vid, rows, cols, intent):
    if intent == "update" and _size_owner.get(sess.id) != vid:
        return False                      # 非所有者：静默忽略
    if intent == "claim":
        _size_owner[sess.id] = vid        # claim 无条件夺权（含同尺寸）
    if sess.rows != rows or sess.cols != cols:
        sess.rows, sess.cols = rows, cols
        fcntl.ioctl(sess.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    return True
```

WS 断连时在 `finally`（`term.py:525-528`）清掉 `_size_owner.pop(sid)`（若持有者是自己），避免悬挂。用 `vid` 而非对象引用，规避 WeakRef 语义差异。

前端配合：`termRepaint()`（`03-agents-cards.js:1-13`）改为——**用户交互/聚焦**发 `{"type":"resize","intent":"claim",...}`，**纯几何变化**（ResizeObserver / visibilitychange）发 `intent:"update"`。`termHealNow`（`:153-165`）的白块自愈属主动干预，发 `claim`。

- 难度：**低**
- 收益：**高**（消除多端互相踩；顺带根治白块——见下）
- 风险：低。需保证老客户端（无 intent 字段）兼容 → **缺省视为 `claim`**（Paseo 同样处理，`terminal-size-ownership.ts:32-38`）。

> 附带收益：这条落地后，`termHealNow` 那条"挪一行再挪回来逼重画"的启发式（`03-agents-cards.js:153-165`）可以从"猜"升级为"确定性 claim 重发"。Paseo 用 claim 状态机根治了同类问题（其 e2e `terminal-stuck-size.spec.ts:8-22` 描述的就是"窗口失焦时创建的终端，PTY 永远停在 80×24"）。

---

### P0-3 resize 客户端 debounce（100ms）

**Paseo 证据** — `packages/app/src/components/terminal-pane.tsx:95`：`TERMINAL_RESIZE_DEBOUNCE_MS = 100`；且 debouncer 对 `shouldClaim`/`forceClaim` 走**累积语义**（`terminal-resize-debouncer.ts:27-43`）——任一请求要求 claim 就不能被后续普通测量吞掉。

**Hub 现状** — `03-agents-cards.js`（构建产物 `hub.js:1687-1688`）：

```js
window.addEventListener('resize', () => termRepaint());
new ResizeObserver(() => termRepaint()).observe($('termEl'));
```

直发，无 debounce。

**真实痛点**：拖窗口 / 分屏动画 / 手机键盘弹起会产生几十上百次事件，每次都打一次 `TIOCSWINSZ` ioctl + xterm 整体重排（`term.refresh(0, term.rows-1)`，`hub.js:1505`）。

**改法**：在 `termRepaint` 外套一个 debouncer（~20 行），`delayMs=100`，并保留 `force` 语义（重连/首次挂载必须立即发）。同时保留 Hub 已有的尺寸去重（`hub.js:1500-1504` 的 `termPaintedAt`）——这与 Paseo 服务端短路（尺寸未变不下发）等价。

- 难度：**低**
- 收益：中
- 风险：低。注意 debounce 只作用于**几何变化**，不能延迟 `termHealNow` 的自愈（它有自己的 120ms 定时器）。

---

### P1-1 带外消息保序（flush before 非输出帧）

**Paseo 证据** — `terminal-worker-process.ts:140-147`：

```ts
      // Non-output messages (snapshot/snapshotReady/titleChange) must not jump
      // ahead of buffered output: flush the coalescer first, then forward.
      outputCoalescer.flush();
      sendToParent({ type: "terminalMessage", terminalId: session.id, message });
```

以及 `terminal-output-coalescer.ts:83-89` 的 `markFlushed()`：让带外帧参与节流节奏，避免紧接着的输出被前沿立即刷成"背靠背两帧"。

**Hub 现状**：目前无 coalescer，故无此问题；但**一旦上了 P0-1 就必须同时落地**，否则会引入新 bug。

**需要 flush 的带外帧**（对应 Hub 现有两处）：
- `on_readable` 的 EIO 退出分支：`[会话结束]` + 退出原因（`term.py:404-409`）
- `pump()` 收尾的 `[process exited]`（`term.py:482-487`）

**改法**：这两处发送前先 `coalescer.flush()`，再发文本。

- 难度：**低**
- 收益：中（防的是"偶发屏幕错乱/重复输出"这类极难复现的问题）
- 风险：不做则埋雷。

---

### P1-2 每观看者独立合并缓冲 + 背压门

**Paseo 证据** — `terminal-session-controller.ts:59-75`（`ActiveTerminalStream` 含独立 `outputCoalescer` / `bufferedOutputs` / `outputBytesSinceSnapshot`）；背压门**双条件 AND**（`:889-913`）：

```ts
          if (!activeStream.exiting &&
              activeStream.outputBytesSinceSnapshot > MAX_TERMINAL_OUTPUT_FRAME_BYTES &&
              (clientBufferedAmount === null || clientBufferedAmount > MAX_CLIENT_BUFFERED_BYTES)) {
            activeStream.snapshotOutput = payload;
            activeStream.needsSnapshot = true;
            void this.trySendSnapshot(activeStream);
            return;              // 落后太多 → 改发快照追赶，而不是继续堆帧
          }
```

阈值（`terminal-restore.ts:10,17`）：产出 256KB **且** socket 缓冲 > 4MB。双条件是关键——文档记录了单条件的教训（`docs/terminal-performance.md:26`）：*"Before this gate existed, every 256KB of build output dropped a frame and forced a full JSON cell-grid snapshot (~200k objects across IPC) — the historical source of spiky lag and GC hitches."*

**Hub 现状**：每观看者已有一条独立队列（`term.py:131`），这点**已经做对了**（注释里记录了旧版"共用队列导致桌面+手机互相偷字节"的实测事故）。缺的是**背压策略**：满了就丢字节（`term.py:394-396`）。

**改法**：P0-1 落地后每个观看者自带 coalescer，在此基础上加背压统计——累计"已提交但未确认"的字节数（pump 队列长度 × 估算），超过阈值时**暂停 coalescer 刷出**而非丢弃。Python 侧拿不到 `bufferedAmount`，用"队列积压"近似。

- 难度：中
- 收益：中
- 风险：中（阈值调不好会误伤）。建议**先做单条件 + 保守阈值**（如队列积压 > 512 帧才降速），观察后再谈双条件。

---

### P1-3 xterm addon 补齐（Search / Unicode11 / WebLinks / Clipboard）

**Paseo 证据** — `packages/app/src/terminal/runtime/terminal-emulator-runtime.ts:441-459` 拉满全家桶：`FitAddon / SearchAddon / Unicode11Addon / WebLinksAddon / ClipboardAddon / WebglAddon / ImageAddon / LigaturesAddon`。

**Hub 现状** — `static/vendor/` 只有 `xterm.js` + `fit.js` + `xterm.css`。四项能力缺失：

| 缺失 | 后果 |
|---|---|
| Unicode11Addon | CJK 宽字符 / emoji 占位错位（Hub 中文环境，会踩） |
| SearchAddon | 无终端内搜索 |
| ClipboardAddon + bracketed paste | 粘贴多行脚本被 shell 逐行执行（`terminal-paste.ts:19-28`） |
| WebLinksAddon | 输出里的 URL 不可点击 |

**改法**：按需下载 addon 到 `static/vendor/`（注意离线环境，需落盘不走 CDN），在 `ensureTerm()`（`03-agents-cards.js:165`）里 `loadAddon`。**优先级：Unicode11 > Clipboard/bracketed paste > Search > WebLinks**。

- 难度：**低**（但需新增 vendor 文件，注意 `build_hubjs.sh` 的 `?v=` 哈希与 `staticguard.py` 的服务白名单）
- 收益：中
- 风险：低。`templates/index.html:1923` 引入 `xterm.js?v=046fc128`，新增 vendor 需同步加 script 标签。

> 其中 **bracketed paste 是正确性修复而非锦上添花**：粘贴多行命令时 shell 会把第一行当命令立刻执行、其余行当垃圾报错——这是真实会丢数据的行为。

---

### P2-1 服务端无头终端真值（pyte）

**Paseo 做法**：每个 PTY 配一个服务端 `@xterm/headless` 实例（`terminal.ts:931-936`，`scrollback: 1000`），所有输出先写进 xterm 再广播。收益：重连可发**屏幕快照**（而非裸字节）、resize 可重排、Agent 可读屏、退出可带最后 12 行。

**Hub 现状**：64KB 裸字节 ring（`term.py:389-390`）。注释里已坦承局限（`03-agents-cards.js` / `hub.js:1621-1624`）：*"ring 是最近 64KB 原始输出，对整屏 TUI 往往只剩最后几帧增量：回放完屏幕大半是空的"*——现有解法是"挪一行再挪回来"的启发式自愈。

**改法**：引入 `pyte`（纯 Python VT100 模拟器，MIT）做服务端屏幕真值；重连时把 cell grid 反向渲染成 ANSI 重放（对应 Paseo 的 `terminal-restore.ts:66-75`）。

- 难度：**高**（一周量级；pyte 性能弱于 xterm.js，需限制解析量；要处理颜色/属性取舍）
- 收益：**高**（根治重连白屏/半屏，解锁"退出带最后 N 行"、Agent 读屏）
- 风险：**中高**。建议**单独立项、单独批次**，不与 P0 混做。

---

### P2-2 终端 tab 活动指示器

**Paseo 做法**（`docs/terminal-activity.md:9`）：*"Activity production lives outside terminal stream parsing: agent hook commands report coarse activity to the daemon's local `/api/terminal-activity` endpoint."* 三态 `idle/working/attention` + 两种原因 `finished/needs_input`。

**但 Hub 已有更好的基础**：`src/vitals.py` 的四层取证（L1 身份 / L2 自述 / L4 应答 / EP 端点）判定 `usable|blocked|broken|...`。Paseo 需要往用户 agent 配置里装 hook 才能拿到粗粒度状态；Hub 已有独立的可用性判定体系。

- 难度：**高**（若照搬 Paseo 的 hook 注入，要改用户 agent 配置，侵入性强）
- 收益：中
- 建议：**不照搬 Paseo**。若要"哪个 Agent 跑完了"的提示，应基于 Hub 已有的 vitals / profiles 状态做轻量呈现，而非引入 hook 注入机制。

---

## 5. 明确不移植的四项

| 项 | Paseo 做法 | 不移植的理由 |
|---|---|---|
| **worker 进程架构** | `fork` 独立进程跑 PTY + xterm 解析（`worker-terminal-manager.ts:142-148`） | 收益主要来自 node-pty 的 Windows conpty 缺陷（异步 spawn 失败会以**未捕获异常**炸worker，`terminal-worker-process.ts:26-37`）与 xterm.js 的 GC 压力。Python `pty` 没有这些行为。成本差一个数量级。 |
| **无进程组灭杀** | 只杀 shell pid（`terminal.ts:1467-1473`） | 见 §3.1，Hub 的做法更正确。 |
| **移动端渲染器** | WebView bridge（`terminal-emulator-webview.native.tsx`）/ 自研 RN 网格（`terminal-emulator-native-grid.native.tsx`，1052 行） | 纯 React Native 产物。Hub 是浏览器 + xterm.js，零收益。 |
| **pane 分屏系统** | workspace/tab/pane 三层架构 | 与 Paseo 整体架构深度耦合，单体移植成本极高。Hub 的"芯片切换 + 单 pane"形态够用。 |

**另：二进制帧协议暂不做。** Paseo 用 2 字节头 + 裸 payload（`binary-frames/terminal.ts:65-91`）规避 JSON 转义开销与非法 UTF-8 损坏。但 Hub 当前瓶颈是**帧频率**而非帧编码，P0-1 的合并已能解决 80%。二进制化要同时改前后端协议，留作"实测证明编码仍是瓶颈"之后再做。

---

## 6. 落地批次与回滚

### 前置：改动落点须知（重要）

**`static/hub.js` 是构建产物，不可直接编辑。** 构建链路（`scripts/build_hubjs.sh`）：

```
static/hub/[0-9][0-9]-*.js  →(按文件名排序拼接, node --check)→  static/hub.js
    + 自动同步 templates/*.html 里的 ?v= 内容哈希（md5 前 8 位）
```

终端代码目前**分散在三个分片**（这是现状，非本方案引入）：

| 分片 | 承载内容 | 关键行 |
|---|---|---|
| `02-nav-and-poll.js` | 常量：HB/RC/鼠标模式/查询抑制 | `:197-250` |
| `03-agents-cards.js` | **核心**：repaint / heal / connect / ensureTerm / detach | `:1-13`, `:132-165`, `:198`, `:234` |
| `04-terminal-ws.js` | 会话清单 / 自动挂载 | `:3`, `:29` |

> 备注：`03-agents-cards.js` 名为"agents-cards"却承载了最核心的终端逻辑，这是历史演进结果。本方案**不提议**现在做分片拆分（收益低、风险高），但后续若终端逻辑继续膨胀，建议单拆 `08-terminal-core.js`。

**每次改完分片必须跑** `bash scripts/build_hubjs.sh`（会自动 `node --check` + 原子替换 + 同步 `?v=`）。

### 批次划分

| 批次 | 内容 | 涉及文件 | 预估 |
|---|---|---|---|
| **B1** | P0-1 coalescer + P1-1 保序 flush | `src/term.py` | 半天 |
| **B2** | P0-2 尺寸所有权 + P0-3 debounce（前后端） | `src/term.py` + `03-agents-cards.js` + `02-nav-and-poll.js`（常量） | 1 天 |
| **B3** | P1-2 背压（单条件保守阈值） | `src/term.py` | 半天 |
| **B4** | P1-3 addon 补齐（Unicode11 → Clipboard → Search） | `static/vendor/` + `templates/index.html` + `03-agents-cards.js` | 1 天 |
| B2' | 白块自愈从启发式升级为确定性 claim 重发 | `03-agents-cards.js` | 随 B2 |

B1 / B2 优先，两者合计一天半，覆盖绝大部分收益。

### 回滚策略（沿用工作区铁律）

1. 改前备份：`cp src/term.py src/term.py.bak-$(date +%Y%m%d_%H%M%S)-paseo-fusion-B1`（工作区已有大量 `.bak-<时间戳>-<原因>` 命名先例）。
2. 前端分片同理备份；`build_hubjs.sh` 自带 `index.html` 的 `.bak-<hash>-token` 留档。
3. 服务端热重载后先自测：`curl` 探 `/api/health`（`main.py:363` 已暴露 `term_sessions` / `term_idle_max_s`）。
4. 每批次独立提交，提交信息带时间戳与变更原因（符合本机工程习惯）。
5. Hub 当前有未提交改动（`git status`：hub.js / 11-resources.js / index.html 已改），**执行前须先确认这些改动是否要一并提交**，否则回滚点会混淆。

---

## 7. 验收标准（可实证，非"看起来没问题"）

| 项 | 验收方法 |
|---|---|
| P0-1 合并生效 | 跑一条持续高频输出命令（如 `find / -type f 2>/dev/null`），用浏览器 DevTools 或 CDP 统计 WS 帧数：合并前后帧数应显著下降，且**按键回显延迟不升** |
| P0-1 不丢内容 | 合并前后终端内容与 `script`-录制的原始输出逐字节比对（或对比 `ring` 内容） |
| P0-2 所有权生效 | 开两个客户端（桌面 + 手机 UA）看同一会话，后台客户端发 resize → 前台 PTY 尺寸**不变**（`stty size` 验证） |
| P0-2 不误伤 | 单客户端场景 resize 行为与改前完全一致 |
| P0-3 debounce | 连续拖动窗口，统计 resize 帧数应 ≤ 改动前 1/5 |
| P1-1 保序 | 退出场景下 `[process exited ...]` 提示必须出现在最后一段输出**之后** |
| 全局 | `bash scripts/build_hubjs.sh` 通过 `node --check`；`/api/health` 返回 200 且 `term_sessions` 正常 |

仓库已有 `tests/` 与 `scripts/run_tests.sh`，新增逻辑建议补对应测试（参照现有 `tests/test_hubjs_split.py` 的"逐字节比对"风格）。

---

## 8. 一句话总结

Paseo 值得学的是**它为了把终端做快、做稳而付出的工程细节**（5ms 前后沿合并、尺寸所有权 claim、resize debounce、带外消息保序、addon 全家桶）；不值得学的是**它的架构形态**（worker 进程、RN 渲染器、pane 分屏）与**它比 Hub 弱的部分**（进程治理、安全边界、退出归因、探活）。

建议先做 B1 + B2（一天半），拿到实证数据后再决定要不要上 B3/B4 与 P2-1。

---

## 9. 执行记录（2026-09-29 下午）

按 §6 的批次划分落地，逐批独立提交（提交信息带时间戳与变更原因）。

| 批次 | 提交 | 内容 | 实证结果 |
|---|---|---|---|
| — | `a397f69` | test: hublog 夹具时间改相对（既有红灯：写死日期随日历翻页变红） | L0 768 例全绿 |
| — | `78508b2` | fix: 结束进程时目标已不存在视为成功 | — |
| B4 | `53b4000` | xterm addon 补齐（webgl/canvas + Unicode11 + 查找 + bracketed paste） | CDP 实测：renderer=**webgl**、unicode=11；粘贴内嵌 `ESC[201~` → `[201~` |
| B1 | `cef4542` | 输出合并 coalescer + 带外消息保序 | 真 pty `seq 1 60000`：帧数 **2602 → 7**（0.27%），字节 408894 两遍一致 |
| B2 | `226b94a` | 尺寸所有权 claim/update + resize 100ms 去抖 | CDP（假 socket 收帧）：20 次几何事件 → **1 帧**；force=claim 立即发、后台端=update、持所有权端=claim |

### 执行中发现的坑（下次动手先看这里）

1. **`static/hub.js` 是构建产物**：改完分片必须 `bash scripts/build_hubjs.sh`（自带 `node --check` + 原子替换 + `?v=` 同步）。
   CDP 验证前若页面是构建前加载的，取到的会是旧代码（本次就因此白跑一轮：帧里没有 `intent` 字段）。
2. **护栏会假红**：新增终端内查找的 Ctrl+F 监听器后，`tests/test_term_focus_policy.py`
   「取第一个 `document.addEventListener('keydown')`」的定位命中错人 ⇒ 已改为按身份认
   （找调用 `keyTargetIsEditing` 的那个）。同理 `term.focus()` 的回焦改写
   `termFocusWanted({user:true})`，如实表达它是用户主动路径。
3. **活终端会拖死 CDP**：页面一旦真连上 pty，后续 `Runtime.evaluate` 与截图全部超时。
   验证终端前端逻辑时的做法：造假 socket（`termWs = {readyState:1, send:...}`）+ 覆写
   `termVisible`，只收帧不连 pty。
4. **测试夹具不要写死日期**：hublog 的 7 条用例因 `created_at` 写死 2026-09-27、
   窗口 24h 而集体假红——代码没动、日历翻页就红，最伤护栏可信度。

### 未做（留待以后，含理由）

- **B3（每观看者背压门）**：B1 已把帧数降两个数量级，队列打满的概率大幅下降；
  方案里也写了「先单条件保守阈值 + 观察」，需要真实慢客户端（手机弱网）数据再定阈值。
- **P2-1（服务端 pyte 无头终端真值）**：高难度、前置依赖重，方案明确要求单独立项。
  注意 B1 落地后重连回放仍是 64KB 裸字节 ring，白块自愈仍是启发式——这条没变。
- **P2-2（tab 活动指示器）**：不照搬 paseo 的 hook 注入，要做得基于 hub 已有的 vitals。

---

## 9. 执行记录（2026-09-29 下午）

按 §6 的批次划分落地，逐批独立提交（提交信息带时间戳与变更原因）。

| 批次 | 提交 | 内容 | 实证结果 |
|---|---|---|---|
| — | `a397f69` | test: hublog 夹具时间改相对（既有红灯：写死日期随日历翻页变红） | L0 768 例全绿 |
| — | `78508b2` | fix: 结束进程时目标已不存在视为成功 | — |
| B4 | `53b4000` | xterm addon 补齐（webgl/canvas + Unicode11 + 查找 + bracketed paste） | CDP 实测：renderer=**webgl**、unicode=11；粘贴内嵌 `ESC[201~` → `[201~` |
| B1 | `cef4542` | 输出合并 coalescer + 带外消息保序 | 真 pty `seq 1 60000`：帧数 **2602 → 7**（0.27%），字节 408894 两遍一致 |
| B2 | `226b94a` | 尺寸所有权 claim/update + resize 100ms 去抖 | CDP（假 socket 收帧）：20 次几何事件 → **1 帧**；force=claim 立即发、后台端=update、持所有权端=claim |

### 执行中发现的坑（下次动手先看这里）

1. **`static/hub.js` 是构建产物**：改完分片必须 `bash scripts/build_hubjs.sh`（自带 `node --check` + 原子替换 + `?v=` 同步）。
   CDP 验证前若页面是构建前加载的，取到的会是旧代码（本次就因此白跑一轮：帧里没有 `intent` 字段）。
2. **护栏会假红**：新增终端内查找的 Ctrl+F 监听器后，`tests/test_term_focus_policy.py`
   「取第一个 `document.addEventListener('keydown')`」的定位命中错人 ⇒ 已改为按身份认
   （找调用 `keyTargetIsEditing` 的那个）。同理 `term.focus()` 的回焦改写
   `termFocusWanted({user:true})`，如实表达它是用户主动路径。
3. **活终端会拖死 CDP**：页面一旦真连上 pty，后续 `Runtime.evaluate` 与截图全部超时。
   验证终端前端逻辑时的做法：造假 socket（`termWs = {readyState:1, send:...}`）+ 覆写
   `termVisible`，只收帧不连 pty。
4. **测试夹具不要写死日期**：hublog 的 7 条用例因 `created_at` 写死 2026-09-27、
   窗口 24h 而集体假红——代码没动、日历翻页就红，最伤护栏可信度。

### 未做（留待以后，含理由）

- **B3（每观看者背压门）**：B1 已把帧数降两个数量级，队列打满的概率大幅下降；
  方案里也写了「先单条件保守阈值 + 观察」，需要真实慢客户端（手机弱网）数据再定阈值。
- **P2-1（服务端 pyte 无头终端真值）**：高难度、前置依赖重，方案明确要求单独立项。
  注意 B1 落地后重连回放仍是 64KB 裸字节 ring，白块自愈仍是启发式——这条没变。
- **P2-2（tab 活动指示器）**：不照搬 paseo 的 hook 注入，要做得基于 hub 已有的 vitals。
