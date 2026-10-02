# 终端移动端 + 窄屏改造实施方案（CloudCLI 三零件落地）

- 日期：2026-10-01
- 目标：把 CloudCLI(claudecodeui) 终端里「本机确实没有、且值得要」的 3 个零件，以**需求规格**而非代码来源的方式，按本仓现有架构重写；宽屏窄屏一并优化
- 上游取证：`/tmp/ccui-ref-1790867882`（claudecodeui @ dc7cb6c，2026-09-28，AGPL-3.0-or-later）
- 状态：**授权执行**（用户 2026-10-01）

---

## 0. 结论先行

**不做「完整移植融合」，只做 3 个独立重写零件。**

三条依据（均为实测，非推断）：

1. **许可证**：上游 AGPL-3.0-or-later，`NOTICE` 明写网络服务触发 §13。本机 `deploy/agent-hub.service`
   是 `--host 0.0.0.0:3102` + 公网 IPv6 域名，属 §13 场景 ⇒ 完整拷贝会要求公开 hub 源码。
   **规避方式：只读上游作为「行为规格」，不复制任何代码/表达式/常量。**
2. **本机已在多数维度反超**（详见 §2）⇒ 移植会引入退化。
3. **安全边界不可让渡**：上游 `isPlainShell` + `initialCommand` 是任意命令字符串直连 `bash -c`
   （`shell-websocket.service.ts:187-189`）。移植 = 拆掉本机「API 只收 agent_id、命令出自画像白名单」
   这条唯一硬防线（`src/term.py:404-405` 模块 docstring 明确写了这套安全模型）。

---

## 1. 本机缺口（这才是要修的）

全仓 grep 取证，**零命中**即为缺失：

| # | 缺口 | 上游出处 | 用户可感知症状 |
|---|---|---|---|
| **P1** | 移动端触摸层 | `src/modules/shell/utils/mobileTerminalSelection.ts`(265行) | 手机上能连终端但**选不中文本、滚不动**。上游注释直说 xterm `scrolls the viewport 1:1 with the finger and never coasts` ⇒ 它自建了惯性滑动 |
| **P2** | auth_url 旁路消息 | `shell-websocket.service.ts:454-471` | 手机跑 `claude setup-token` 只能**肉眼抄 URL** |
| **P3** | ANSI 剥离去重 + URL 跨行拼接 | 同上 `:56-86` `extractUrlsFromText` | P1/P2 的前置（终端宽度会把 URL 折行） |

**关键取舍**：P2/P3 必须做在**后端**（要扫的是 pty 输出字节流，前端拿不到未剥 ANSI 的原文）；
P1 纯前端。这决定了下面的落点切分。

---

## 2. 为什么不能整体移植（能力对照）

| 维度 | 本机 agent-hub | CloudCLI | 判定 |
|---|---|---|---|
| 多观看者 | 每观看者独立队列 (`term.py:747`) | `session.ws` 单字段 (`shell-websocket.service.ts:31`) | **本机强**（修过"桌面+手机互偷字节"） |
| 输出背压 | coalescer 5ms + 丢弃字节明说 (`term.py:269`) | 无合并，直发 | **本机强** |
| 重连 | 自动退避重连 + 64KB ring 回放 (`term.py:152`) | `onclose` 直接 `clearTerminalScreen()` (`:183`) | **本机强** |
| resize | claim/update 所有权 + 100ms debounce | 无条件 resize，后台可压扁前台 vim | **本机强** |
| 进程治理 | 进程组灭杀 + EIO 三次确认 + 退出归因 | `pty.kill()`，只报 code | **本机强** |
| 安全边界 | 画像白名单，命令进不去 | 任意命令串 | **本机强** |
| xterm | **6.0.0** + 6 addon | 5.5.0 + 4 addon | **本机强** |
| 触摸层 | 无 | 有（长按选/惯性滚/双指缩放） | **上游强 ← P1** |
| auth_url | 无 | 有 | **上游强 ← P2** |

本机 35 次终端提交 / 242 次总提交、27 个终端测试 —— 这是被反复打磨的，不是半成品。

---

## 3. 落点（精确到文件与函数）

### P1 移动端触摸层（纯前端）

**新建 `static/hub/13-term-touch.js`**（独立 part，不塞进已 686 行的 03）

| 函数 | 职责 | 上游对应（仅作规格参考） |
|---|---|---|
| `termTouchBind()` | 入口，在 `termInit` 末尾调 | mobileTerminalSelection.ts 整体 |
| 长按选区 | 600ms 长按 → 出选择手柄 → 拖动选中文本 → 复制 | `LONG_PRESS_MS` + `DragHandle` |
| 惯性滚动 | 手指拖动 1:1 跟手 + 松手后按速度惯性衰减 | 注释明说 xterm 无 coast，故自建 |
| 双指缩放 | 改字号（8–48），clamp + 节流 | `ZOOM_THROTTLE_MS` / `MIN/MAX_FONT_SIZE` |

**接线点**：`static/hub/03-agents-cards.js` 的 `termInit` 末尾（`termPasteBind();` 之后，`termFindBind();` 之前）

**硬约束**：
- 只在 `matchMedia('(pointer: coarse)')` 或 `ontouchstart` 时绑定，桌面零开销
- **不得给 `#termEl` 或 `.term-body` 加任何 padding/inset**（`templates/index.html:720-724` 的 FitAddon
  高度口径铁律：父层 inset 会让算出的行数多一行、底部被 `overflow:hidden` 裁掉）
- 所有浮层必须 `position:absolute` 且挂在 `.term-body`（同 `term-find` 既有做法），不进 flex 流
- 字号缩放后必须 `termRepaint(true)` 重算行列，否则 PTY 与前端尺寸不一致 ⇒ 下一次输入错位

**配套 CSS**（`templates/index.html` 终端块内，`@media (max-width:767px)` 档）：
浮层样式 + `touch-action` 声明。**新增 `@media` 会被既有测试盯**，故一律挂在既有的 767px 断点上
（该文件 496 行注释明写「不新增 @media：窄屏档只挂在既有的 1100px 断点上」为既有约定）。

### P2 + P3 auth_url（后端）

**改 `src/term.py`**，`Session.onData` 路径内新增旁路扫描：

| 新增 | 职责 |
|---|---|
| `TERM_URL_SCAN_MAX` | 扫描缓冲上限（对齐上游 `SHELL_URL_PARSE_BUFFER_LIMIT=32768` 的量级，取 16KB） |
| `sess.url_buf` | 滚动缓冲，只存**剥 ANSI 后**的文本 |
| `_strip_ansi(text)` | P3：剥 ANSI/VT 序列 |
| `_scan_urls(buf)` | P3：提 URL，含**跨行拼接**（终端宽度会把 URL 折断） |
| `_emit_auth_url(sess, url, auto)` | P2：带外发 JSON 帧 `{"type":"auth_url","url":...,"auto":...}`，**每 URL 去重一次** |

**关键差异（不能照抄上游）**：上游单 `session.ws` ⇒ 只能发给一个观看者。
本机多观看者 ⇒ **必须逐观看者发**，否则回到"多观看者互偷字节"的老坑（`term.py:747` 的 P1-5）。

**去重状态放哪**：`sess` 级 `announced_urls: set`，跨观看者共享 —— 同一 URL 只播一次，不因换端重播。

**开销护栏**：扫描只在缓冲有变化时做；正则只在 16KB 窗口内跑；**绝不把旁路帧塞进输出队列**
（旁路走独立 `send_text`，不进 coalescer）。

### 前端消费 auth_url

**改 `static/hub/03-agents-cards.js`** `ws.onmessage`：
- 现逻辑只认「字符串 + `termIsHb`」，其余一律当字节写进终端
- **改动**：先 `JSON.parse` 尝试 → 命中 `{type:'auth_url'}` 则弹 toast + 可选 `window.open`
  （`auto=true` 时自动开；否则给按钮让用户点，手机上自动开新页会被拦）

---

## 4. 宽屏 / 窄屏一并优化（用户明确要求）

| 档 | 优化项 | 落点 |
|---|---|---|
| **窄屏(≤767px)** | 触摸层全量启用 | P1 |
| | 终端顶栏 `.ebar-menu` 已有压缩规则，本批**只补**浮层不碰它 | 模板 767px 档 |
| | `term-find` 查找条已有 `max-width:45vw`，不改 | — |
| **宽屏** | 双指缩放不触发（pointer:fine 不绑）⇒ 零行为变化 | — |
| | 新增功能对宽屏**完全无感**，避免"改宽屏改出新 bug" | — |

**设计取向**：宽屏不引入任何新交互。风险面收窄到窄屏触控这一条路径。

---

## 5. 验收标准（每条可证伪，不接受「看起来对了」）

沿用仓内既有铁律（`tests/verify_term_scroll.py` 的口径）：判据必须是可断言的量，**不能以截图交差**。

### L1 静态闸门（pytest，新文件）
| 编号 | 判据 |
|---|---|
| A1 | `13-term-touch.js` 被 `build_hubjs.sh` 收进 `hub.js`（拼接 == 产物） |
| A2 | `test_hubjs_split.py` 仍绿（拼接逐字节等价未被破坏） |
| A3 | `#termEl` / `.term-body` 的 padding 仍为 0（FitAddon 口径未被破坏） |
| A4 | `_scan_urls` 对折行 URL 能拼回完整串；对尾随标点 `)`/`.` 能正确剥离 |
| A5 | auth_url 去重：同 URL 两次只播一次 |

### L2 真机闸门（CDP 实派发，新文件）
| 编号 | 判据 |
|---|---|
| B1 | 移动视口(390×844)下派发真实 touch 序列 ⇒ 惯性滚动确有位移（**松手后再滑 ≥3 行**，2026-10-02 由“>0 行”收紧；见 §9 补记） |
| B2 | 长按 600ms ⇒ 出现选择浮层；拖动后 `term.getSelection()` 非空 |
| B3 | 双指捏合 ⇒ 字号改变且 clamp 在 [8,48]；改完 `term.cols` 与服务端一致（不发错位帧） |
| B4 | 后端旁路帧到达 ⇒ 前端 toast 出现，且**输出流未被污染**（终端里不出现 JSON 原文） |
| B5 | 桌面视口(1440×900)下 B1–B3 不触发，终端行为与改前一致（零回归） |
| B6 | 触摸层反复开关（10 次）后无监听器泄漏（`getEventListeners` 计数不增长） |

---

## 6. 回滚策略

**分批独立，每批可单独回滚**：

| 批 | 内容 | 回滚方式 |
|---|---|---|
| **B1** | `13-term-touch.js` + 接线 + CSS | `rm 13-term-touch.js` + 删接线行 + `git checkout -- templates/index.html` + 重建 hub.js |
| **B2** | `term.py` auth_url | `git checkout -- src/term.py`（后端独立于前端，可单独回） |
| **B3** | 前端 auth_url 消费 | 删 `ws.onmessage` 新增分支 |

**回滚触发条件**：任一 L1 红，或 B1–B6 任一红且 30 分钟内定位不到根因。

**备份铁律**：改任何已有文件前先 `cp <file> <file>.bak-$(date +%Y%m%d_%H%M%S)-<说明>`。
涉及：`src/term.py`、`templates/index.html`、`static/hub/03-agents-cards.js`、`static/hub.js`（产物）。

**产物纪律**：`static/hub.js` 是**构建产物**，不可直接编辑 ⇒ 改 part 后必须走
`scripts/build_hubjs.sh`（原子写 + `node --check` + `?v=` 提手按内容 md5 自动同步）。

---

## 7. 执行顺序

1. 建 part `13-term-touch.js`（P1）+ 接线 + CSS
2. 重建 `hub.js`，跑 `test_hubjs_split.py`
3. 写 L1 静态闸门，跑红→改绿
4. 改 `term.py`（P2+P3）
5. 改 `ws.onmessage` 消费 auth_url
6. 写 L2 真机闸门（CDP）
7. 逐条验收，出证据

---

## 8. 一句话总结

**不移植，重写 3 个零件**：移动端触摸层（前端新 part）+ auth_url 旁路与 URL 识别（后端 term.py）。
宽屏零行为变化，窄屏补上"能选、能滚、能登录"三件事。AGPL 风险为零（本批不含任何上游代码）。

---

## 9. 执行记录（2026-10-01，全部落地并实证）

**结果：L1 静态闸门 14/14 通过；L2 真机闸门 13/13 通过；全仓 975 passed / 0 failed。**

### 9.1 补记（2026-10-02）：B1/T2b 从“>0 行”收紧为“≥3 行”，并修掉惯性尾巴被逐帧取整吞掉

**触发**：用户 10-02 要求“继续完成本任务”，本会话先复跑闸门取新证据（不采信本节自述数字）——
首跑 T2b **红**（`viewportY 371 →(松手)365 →(停)365`，松手后 0 行），复跑 **13/13 绿**
（同参数 `371→363→361`，2 行）⇒ **判据在边界上掷硬币，不是稳定红**。

**取证（先量数字再改代码）**：往页面里装 capture 阶段 touchend 探针，读**产品自己**用来判甩动的
`ttState.idle`/`ttState.v`；同时量 CDP 往返延迟以排除“探针慢导致 idle 超阈值”。

- CDP 往返 **0.4ms 中位** ⇒ “探针派发延迟把 idle 顶过 150ms 阈值”假设**被证伪**；
- 红的那次实测 `idle=23ms v=-1010px/s` ⇒ 甩动**确实起了**（门槛 `idle<150`、`|v|>40`）
  ⇒ “惯性没起”假设也被证伪；
- 于是只剩第三个假设：**位移在下游被吞**。定位到 `ttScrollByPx()` 每帧
  `Math.round(px/cell)` 且**无跨帧余量**：`cell≈24px` ⇒ 一行要满 24px、半行 12px；
  轻甩（v₀=-1010px/s）起初每帧 16px 能凑出 1 行，但 v 按 0.94/帧 衰减到 **<750px/s** 后
  每帧不足半行 ⇒ 小数当场被抹平、且**没有下一次来补** ⇒ 衰减段（占全程约 7 成）整段消失。
  按 `0.27·v₀` 推算全程应滑 ≈11 行，**实测只滑 2 行**，对得上。

**改法（产品）**：`ttScrollByPx()` 引入跨帧余量 `ttResidPx`（新手势在 touchstart 清零、
撞顶/撞底时作废）；单次大位移（跟手拖动）行为与改前**逐位一致**（余量从 0 起算）。
**改法（闸门）**：T2b 判据由“松手后再动 >0 行”收紧为“**≥3 行**”，并在红时打印产品侧
`idle/v` 与实测行数 ⇒ 日后红是真红，不必再猜。收紧理由：坏值 0~2 行、好值 ≈8~11 行，
取 3 行既有区分度又留 4 倍余量；**不是为了让红变绿而挪门**——同一派发（`v=-1010, idle=23`）
在改产品前是 2 行（红）、改产品后是 8 行（绿），唯一变量是产品逻辑。

**红→绿证据**：改判据后未改产品先跑 → `FAIL … 松手后再滑 2 行（要求≥3）｜idle=23ms v=-1010px/s`；
改产品并重建 `hub.js`（md5 `61dd9775`）→ `PASS … 松手后再滑 8 行（要求≥3）｜idle=23ms v=-1010px/s`，
全闸门 13/13。同批全量回归：L0 914 + L1 47 = **961 例 0 失败 / 0 错误 / 0 跳过**（exit=0）。

### 落点（与 §3 一致，无偏移）

| 文件 | 变更 | 行数 |
|---|---|---|
| `static/hub/13-term-touch.js` | **新增**：触摸层（惯性滚 / 长按选区 / 捏合缩放） | 新文件 ~230 行 |
| `static/hub/03-agents-cards.js` | 接线 `termTouchBind()` + `termTouchReset()` + `termAuthUrl()` 消费 + onmessage 拦截 | +46 |
| `static/hub.js` | 构建产物（`build_hubjs.sh` 重建） | +303 |
| `src/term.py` | `strip_ansi` / `_scan_urls` / `_normalize_url` + `_scan_auth_urls` 逐观看者旁路 | +112 |
| `templates/index.html` | 767px 档 `touch-action` + `tterm-pinch`（**唯一**允许碰 #termEl 的属性） | +10 |
| `tests/test_ls_guard.py` | 登记新分片（两份清单 + SHARDS_AFTER_RED） | +7 |
| `tests/test_term_touch_authurl.py` | **新增** L1 闸门 14 条 | 新文件 |
| `tests/verify_term_mobile_touch.py` | **新增** L2 真机闸门 13 条 | 新文件 |

### 五个只有真机才抓得到的坑（静态断言全绿时功能其实是坏的）

1. **`scrollLines` 在两端静默失效** —— xterm 6.0 的 `scrollLines(amount)` 内部落到
   `this._viewport.scrollLines(e)` → DOM `setScrollPosition`，而视口在最底/最顶时被 clamp
   ⇒ 不报错、也不动。实测：`viewportY=376`（底部）时 `scrollLines(±n)` 全部纹丝不动，
   滚到 200 中段才正常。而"手指上滑看新内容"**总从底部起步** ⇒ 用户 100% 感觉失效。
   **改法**：一律换算绝对行号调 `scrollToLine(target)`（不经 DOM clamp，三态一致有效）。
2. **`scrollLines` 单参签名** —— 沿用旧版双参写成 `scrollLines(0, n)`，第二个参数是废参、
   实际传入 0 ⇒ 滚动量恒为 0。已在 vendor/xterm.js 里交叉取证签名。
3. **长按选区被 xterm 异步抹掉** —— xterm 自己在 document 上注册了 touchstart/touchend
   （非 passive，`TouchGestureSource.onTouchStart`）。实测时序：长按 1s 时 `getSelection()='L15'`，
   touchend 后 0.05s 起变 `''`；我的补刀 `setTimeout(0)` **确实执行了**（selectLines 被调到 `14-14`）
   但仍为空 —— xterm 的清理排在更晚一拍。**改法**：补刀延到 60ms。
4. **`select(col,row,len)` 的第三参是长度不是终点** —— 长按用 `select(0,row)` 选中恒为空
   （且行号是 buffer 绝对坐标）。**改法**：用 `selectLines(row,row)` 整行语义。
5. **`toast()` 没有 onClick 参数** —— 首版传了第三个参数当回调，函数静默忽略 ⇒ 按钮点不动。
   **改法**：取 toast 返回的 DOM 节点自行挂 click（不硬改 `toast()` 签名，那会波及 40+ 调用方）。

另有一个**闸门抓到的真 TDZ**：`13-term-touch.js` 拼接序在末位，但 `01/02` 的顶层语句
（`initSidebar()` / `go()`）会同步调到本文件函数 ⇒ `let/const` 声明会读到未初始化绑定。
`test_tdz_order.py` 判红。**改法**：按 09-local-projects.js 的既有做法改 `var`（提升 + 顶层立即赋值）。

### 未做（留待以后，含理由）

- **多标签终端**：上游 `cloudcli-plugin-terminal` 有（2.9k 行 TS + 插件宿主体系）。
  本机是"芯片切会话"单 pane 形态，引入多标签要连带做 tab 生命周期与后端 key 维度，
  收益不如先把窄屏可用性做扎实。
- **二进制帧协议**：Paseo 融合文档 §5 已判过（P0-1 合并已解决 80%），维持不改。
- **`tterm-pinch` 之外的移动端浮层**（快捷键面板等）：宽屏无需求，暂不做。