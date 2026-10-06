## v0.13.82 — 备用屏（?1049）跨会话污染：终端「不能向上滚动」的根治

> 2026-10-06，PT-20261006-01 续。用户报障：**跑过一次 codex 的嵌入式终端后，
> 所有 agent 的终端都不能向上滚动查看内容了**（cursor Agent 的窗口也被带坏）；
> 同一问题「已经修过几次」。对照：cloudcli 的终端一直正常。
> 详细判例见 agent-knowledge/85。

① **根因（pty 字节级实测，不是推断）**：codex 的 TUI 开机就发 `\x1b[?1049h` 进
「备用屏」——本机 codex 0.160.0 在 `TERM=xterm-256color`（与 hub 给 pty 的环境一致）下
用 `script` 捕获，默认命中 1 次；换 `codex --no-alt-screen` 后归零。而 **xterm.js 的
备用屏按设计没有 scrollback**（vendor 源码里备用屏是 `new Buffer(!1, …)`）⇒ 备用屏里
根本没有可上翻的历史。**为什么一坏坏一页**：整页只有一个 `#termEl` / 一个 xterm 实例
（`let term` 单例），切会话走的 `term.clear()` 只清**当前缓冲区的行**、**不退出备用屏**
——vendor 里只有 `BufferSet.reset()` 会把 `_activeBuffer` 切回 `_normal`。所以一次 codex
把整页拖进备用屏后，谁来都滚不动，直到整页刷新。cloudcli 正常正是因为它走普通
shell→TTY，从不发 `?1049h`。
② **为什么 v0.13.81 没治住**：那一版治的是**鼠标跟踪态**，判据取
`term.modes.mouseTrackingMode`，**从没量过 `term.buffer.active.type`**。鼠标态每次重连
都被 `TERM_MOUSE_OFF` 清掉（看着像「会话级」），备用屏从没被复位（其实是**页面级**、
会跨会话传染）⇒ 两次修的是两条不同的腿。
③ **修复（三层）**：
  - **前端跨会话复位**（`static/hub/02-nav-and-poll.js` 新增 `TERM_ALT_OFF`
    = `?1049l/?47l/?1047l` 与 `TERM_STATE_RESET`；`03-agents-cards.js` 的 `termConnect`
    在回放帧里按 `keepScreen` 择一写）：换会话写全量复位，同会话重连只清鼠标模式。
    **刻意不放进看门狗按滚轮触发**——实测 codex 的 `?1049h` **开机只发一次**、不随重画
    重发（688B 里 1049h 恰好 1 次，而 `?2026h/l` 15 对），TUI 活着时把它踢出备用屏会让
    画面停在不含 TUI 输出的缓冲区上、看起来像「冻住」且回不去。
  - **codex 启动带 `--no-alt-screen`**（`src/profiles.py` 新会话 + `src/sessions_store.py`
    续聊两条路）：官方 `--help` 原文 "Runs the TUI in inline mode, preserving terminal
    scrollback history."，让 codex 自己的输出落进普通屏 scrollback。
  - **端侧可观测 + 逃生口**（`templates/index.html` + `03-agents-cards.js`）：终端工具栏
    加缓冲区状态字（主屏 / 备用屏·不可上翻）与「退出备用屏」按钮，把「滚不动」变成
    可判据的观察，而不是「我感觉大概是」。
④ **验收（`tests/verify_term_altscreen.py`，L2 live，影子实例 + 真 chromium，10/10 PASS）**：
主屏 `baseY=278` 且能上翻 → 写 `?1049h` 后 `alternate`、`baseY=0`、上翻不可能 →
状态字报「备用屏·不可上翻」→ 点按钮回主屏 → **再造污染后切会话自动回主屏**
（本次核心）→ scrollback 与上翻能力真恢复。**闸门可重复**（两次连跑均 10/10，自带会话清理，
不会撞 `MAX_SESSIONS=8` 的 429）。
⑤ **已知残留（如实登记）**：上游 open（#14277/#10331/#20063/#23651）指出即便关掉备用屏，
codex 在普通屏做整屏重画（`\x1b[2J` 会把 viewportY 拽回底部）时**仍可能丢 scrollback**
⇒ 第 ② 层是「改善」不是「根治」，收口靠第 ①③ 层。若端侧仍见 codex 窗口滚不动，
下一步是在 PTY→terminal 边界过滤 DEC 2026 同步块内的 `2J`（上游 xterm.js#5801 未修）。

---

## v0.13.81 — 终端鼠标跟踪看门狗（滚轮/拖选/焦点在 TUI 会话下的自愈）

> 2026-10-06，PT-20261006-01。用户报障：agent-hub 嵌入式终端「向上浏览有时不行、
> 无法复制文字」；claude 会话时好时坏、codex 会话会「抢鼠标焦点 / 输入框跟着动」；
> cloudcli 终端一直正常。实测根因与修复见 agent-knowledge/83（本版不展开）。

① **根因（CDP 真浏览器 + WS 字节流实测）**：TUI 程序（claude/codex 交互界面）开启
xterm 鼠标跟踪（DECSET ?1002h/?1003h）后，滚轮与拖拽点击会被 xterm 转成 SGR 鼠标
上报发给 pty 程序 —— xterm 把它当「程序内滚动/点击」重画界面（codex 输入框跟着动、
焦点被抢），浏览器侧不再滚 scrollback、也无法拖选。TUI 异常退出不发 ?1003l 时
xterm 内部 mouseTrackingMode 卡在 any，只有重连（termConnect 写 TERM_MOUSE_OFF）
才复位 —— 这就是「时好时坏」与「打开 cloudcli 后再回来就好了」的机制
（cloudcli 是普通 shell→TTY 场景，TUI 少，踩中概率低）。
② **修复（static/hub/03-agents-cards.js）**：ensureTerm 末尾挂看门狗，capture 阶段
监听 wheel + mousedown；当 mouseTrackingMode !== 'none' 时**同步**把
coreMouseService.activeProtocol 切回 'NONE'（setter 同步触发 onProtocolChange，
当次事件即回浏览器默认路径），再异步写 TERM_MOUSE_OFF 到 pty 让对端也退出。
实测（probe_verify_watchdog.py，真实代码路径）：首滚一格恢复滚动（viewportY 25→14）、
首拖选即选中文本（sel=12）、纯点击 mode any→none（点击不再被 TUI 抢焦点）；
常规态零开销、不碰任何行为。TUI 的鼠标交互（选择器/对话框）仍在 DRAW 循环时会
自己重新发开启序列。
③ **为什么是同步协议复位而不是 attachCustomWheelEventHandler**：xterm 6.0 的
wheel handler 返回 false 不够（事件已被 preventDefault，浏览器默认滚动被禁）；
passive wheel 里只写 term.write(1003l) 是异步的，首格滚轮被吞。唯一「首事件即恢复」
的做法是 capture 阶段同步切 activeProtocol（见 03-agents-cards.js 注释）——
这一条是试错试出来的，改前必读。
④ 配套：tests/test_hubjs_split、test_term_scroll_sensitivity、test_term_xterm6 等
终端相关闸门全绿；L0 全量 1327 例仅 2 例预存失败（test_kb_federation/test_memfed，
主仓 HEAD 同失败，与本次无关）。

---

## v0.13.80 — CloudCLI 独立子菜单（Agents 菜单拆出原生界面+项目直达面板）

> 2026-10-05：cloudcli 从 claude 卡拆出为独立画像卡（kind=agent，systemd 检测 :3010）。
> claude 卡 ui=None（纯终端+对话），cloudcli 卡 embed 原生界面下方内嵌 CloudCLI 项目直达
> 面板（#embedBody 槽位 + renderCloudcliProjects，02-nav-and-poll.js）。
> 会话创建入口从 gotoChat('claude') 改为 gotoChat('cloudcli')。
> L0 1327 全绿；/api/agents 实测：claude=term+chat+detail，cloudcli=embed+open+detail。

---

## v0.13.79 — 八批优化收口：闸门可信度 + 性能地基 + XSS + 前端卫生 + 文档归位

> 本版是 2026-10-05 一轮系统性优化的收口。**起点是一个测试基础设施缺陷**：
> `FORCE_COLOR=3` 让 node 把 `console.log(数字)` 染成 ANSI，22 条用例假红 ——
> 而「全绿/全红」是本轮一切判据的前提。逐条见各批 commit。

① **闸门自己会说谎，所以先修它**：`FORCE_COLOR=3` 让 node 把 `console.log(数字)`
染成 ANSI ⇒ `int('\x1b[33m60\x1b[39m')` 直接 ValueError ⇒ 22 条用例**假红**。
而「全绿/全红」是本轮一切判据的前提 —— 判据本身不可信时，后面八批的
「已验证」全是自欺。`NO_COLOR` 在 `FORCE_COLOR` 存在时**无效**（node 自己
警告后忽略），唯一可靠解是从 `run_tier.py` 里摘掉变量。这类**假红比没有
闸门更坏**：它逼人改断言求绿。本仓的假红禁令见 `tests/tiers.py:107-114`。
② **性能主症是缓存缺失，不是算法慢**：`/api/skill/list` 174ms 每次全盘重扫、
`/api/agents` 57ms 里 `docker ps` 独占 16.5ms、`/api/kb/status` 冷启动 1.49s ——
而 `/status` 早有 60s TTL 范式，只是没被其余四条路由复用。**逐字搬运**式的
「抽取而非编写」也用在这里：历史根因搬进 CHANGELOG 是同一手法。
③ **闸门盲区下的漏网比缺陷本身更值得记**：`renderTaskTable` 四处裸拼 innerHTML
（LLM 返回的 `task_id` 无字符集校验直达 DOM ⇒ 存储型 XSS），**而同一函数
下一格有 `escapeHtml`** ⇒ 证明是漏网非有意。三处同型，所以新闸门的主判据是
**形状**（同行两种写法）而不是逐点列举 —— 后者修完就忘。
同类教训已在册 6 次（TDAI 透传三错、chat 端点每请求 500、WS 双消费者偷字节…）。
④ **只缓存成功路径**：模型清单那次我先写成「fetch 没抛异常 ⇒ 成功」，影子实测
证明错 —— `async with s.get()` 连接失败时**不抛异常**，只回空清单 ⇒ 缓存了失败
（用户看到「没有模型」，真因是上游连不上）。判据必须是 `payload["error"]` 为空。
── v0.13.70 及更早的逐版根因已于 2026-10-05 归档至 ──
── CHANGELOG.md（脚本 scripts/extract_changelog.py 逐字搬运）──
此处**只保留当前版本**的根因，避免两份会各自漂移的副本。
（上一版 v0.13.78 的根因随本批归档至 CHANGELOG.md §v0.13.78）原三条：
① **收起态图标条第 4、5 个形状完全一样**（用户 2026-10-04 报「分不清是什么」）。
真因是 `cpu` 一次被用了**三处**：静态「资源」、`NAV_ICONS.agents`、
`SET_PAGES` 的「模型」—— 收起态里前两处**并排可见**，直接造成误读。
一次清完三处：「资源」cpu → **`monitor`**（它监视 CPU+内存+进程，
cpu 这个隐喻本来就偏窄）；「AGENTS」cpu → **`hubmark`**（hub 中心 +
4 个 agent 节点 + 辐条，语义正对）；「模型」保留 cpu（AI 模型常见隐喻）。
两处**顺带查过** `monitor`/`hubmark` 是否已被导航占用 —— 只在
agent 卡片徽标与页眉 logo 用，不在图标条，避免「改了 A 又造出 B 的撞车」。
② **`/list?q=` 与 MCP 工具描述口径统一**：MCP `tools/list` 的
`description` 原走 `escapeHtml` ⇒ 星号原样显示。现与技能描述同走 `mdInline`。
③ **顺带修一处 XSS 洞（超出用户点名范围，必须报）**：MCP 工具行里
`<b>' + x.name + '</b>` 与 `onclick="pickTool('' + x.name + '')"`
**既没转义、也没过 `jsStr`** —— 同一个串同时进 **HTML 正文**与
**onclick 的 JS 字面量**两个语境，两处都不设防。工具名来自 MCP server
（外部注册）。本仓 `jsStr` 的注释早就写明「`escapeHtml` 只处理 HTML
上下文，HTML 实体转义**不足以**让任意串安全进 JS 字面量」，此处却没用。
现补：HTML 语境 `escapeHtml`、JS 字面量语境 `jsStr`。

## v0.13.78 — 侧栏图标撞车 + mdInline 口径统一 + MCP 工具名转义

① **收起态图标条第 4、5 个形状完全一样**（用户 2026-10-04 报「分不清是什么」）。
真因是 `cpu` 一次被用了**三处**：静态「资源」、`NAV_ICONS.agents`、
`SET_PAGES` 的「模型」—— 收起态里前两处**并排可见**，直接造成误读。
一次清完三处：「资源」cpu → **`monitor`**（它监视 CPU+内存+进程，
cpu 这个隐喻本来就偏窄）；「AGENTS」cpu → **`hubmark`**（hub 中心 +
4 个 agent 节点 + 辐条，语义正对）；「模型」保留 cpu（AI 模型常见隐喻）。
两处**顺带查过** `monitor`/`hubmark` 是否已被导航占用 —— 只在
agent 卡片徽标与页眉 logo 用，不在图标条，避免「改了 A 又造出 B 的撞车」。
② **`/list?q=` 与 MCP 工具描述口径统一**：MCP `tools/list` 的
`description` 原走 `escapeHtml` ⇒ 星号原样显示。现与技能描述同走 `mdInline`。
③ **顺带修一处 XSS 洞（超出用户点名范围，必须报）**：MCP 工具行里
`<b>' + x.name + '</b>` 与 `onclick="pickTool('' + x.name + '')"`
**既没转义、也没过 `jsStr`** —— 同一个串同时进 **HTML 正文**与
**onclick 的 JS 字面量**两个语境，两处都不设防。工具名来自 MCP server
（外部注册）。本仓 `jsStr` 的注释早就写明「`escapeHtml` 只处理 HTML
上下文，HTML 实体转义**不足以**让任意串安全进 JS 字面量」，此处却没用。
现补：HTML 语境 `escapeHtml`、JS 字面量语境 `jsStr`。

## v0.13.65 — 统一记忆注入通道（D1 接联邦 + D1.5 开默认快路口径）

> 方案 `docs/superpowers/specs/2026-10-02-unified-memory-injection-design.md`（未跟踪文件，本批一并入库）。
> **⚠️ 生产重启未执行**（沿用「禁重启生产」边界）⇒ 本批**尚未在生产生效**，`/health` 的
> `code_matches_head` 在重启前会判 stale。切换命令已写好待用户点头。

### 病根（实测，非推断）

`GET /api/memory/context` 收了 `sources` 里 9 路联邦源 ID、**过白名单校验不报 400**，
然后在函数体里被静默丢弃 —— 实测回包 `backends` 只有 `tdai_profile` 与 `local`，
`fed.sources=0`、正文 1585 字符、联邦段完全缺席，`took_ms` 6~21ms。
HTTP 200、无异常、无告警：本仓反复警告的「全指标绿而功能层已死」同族。
而 `/api/memory/search` 早已接 memfed ⇒ **「搜索能聚合、注入不能」是分裂的根源**。

### 本批

- **D1 能力** `src/memfed.py`：`search_fed` 加**可选** `wall_s`（默认 `None`＝既有 27 例语义逐字不变）；
  给定值走 `asyncio.wait(timeout=)`，超预算记 `error=skipped_budget` 并**不取消**
  （`asyncio.to_thread` 不可取消，只挂 done_callback 取走异常引用防 GC 刷 `exception was never retrieved`）。
- **D1 能力** `src/memory.py`：注入包接联邦，新增第 4 段「其他 Agent 记忆」。
  按**轮转**取而非先 RRF 再截断 —— 先 RRF 会让多命中的源独占名额；轮转保证每路都轮到、
  预算不够时「均匀地被砍」而不是「整路消失」。被预算掐掉/检索失败的逐路**点名写进段里**。
- **D1.5 默认**：`/api/memory/search` 与前端兜底由 `local,tdai` 升到 `memory.FED_FAST_SOURCES`
  （**单一真源**，工具层不抄第二份默认字符串）。取值来自 2026-10-02 实测并联墙钟：
  workbuddy 47 / claude_mem 148 / claude_projects 261ms。**慢三路不进默认**（见下）。
- **工具面** `src/hubmcp.py`：`hub_memory_context` 开 `sources` 口子（空串 ⇒ 取 `FED_FAST_SOURCES`）；
  `hub_memory_search` **不改**（显式召回工具，广度优先）。

### 为何慢三路不进默认（0.13.62 的教训在 0.13.65 复用）

`pi_sessions` 1894ms / `codex_sessions` 2028ms / `archived_sessions` 2506ms（要扫 5622 个文件）——
三路在源自身 `timeout_s=1.8` 下**必然超时**。把它们硬塞进默认行径 = 每次开局等 3s。
根因是 9 路里**只有 `claude_mem` 有真索引（FTS5）**，其余每次对整棵树跑 `rg` ⇒ O(文件数) 无索引。
根治方案 = A4 索引投影层（设计已落 `docs/superpowers/specs/2026-10-02-memindex-a4-design.md`，本批未开）。

### 实测（逐项 PASS，命令与输出见下）

| 项 | 结果 |
|---|---|
| `scripts/run_tests.sh hermetic` | **PASS** — ran=950 skipped=0 failures=0 errors=0 |
| `scripts/run_tests.sh host` | **PASS** — ran=47 skipped=0 failures=0 errors=0 |
| 收集器对账 | **PASS** — unittest=997 = pytest=997 |
| `tests.test_memfeed_inject`（G1~G8） | **PASS** — 16 例 |
| `static/hub.js` 与 `static/hub/*.js` 同步态 | **PASS** — 拼接结果逐字节相等 |
| `templates/index.html` 的 `?v=` | **PASS** — `59aa0e83` == md5(static/hub.js)[:8] |
| 影子树探针 `hub_memory_context()` | **PASS** — 17ms/1585 字符/`fed.sources=0` → 230ms/快路 5 源全 ok/含联邦段 |
| **生产生效** | **未执行**（禁重启生产，待授权） |

**禁把 SKIP 当 PASS**：G3 用真 `search_fed` + 真慢源（sleep 夹具），不是把 fake 的
`skipped_budget` 字符串塞回去自证；`run_tests.sh hermetic` 要求零跳过，本批实测 `skipped=0`。

### 回滚

```bash
cd /fs/1000/ftp/技术文档/agent-hub
git checkout a640c0b -- src/hubmcp.py src/memfed.py src/memory.py src/main.py \\
                        static/hub.js static/hub/04-terminal-ws.js templates/index.html
cp src/memory.py.bak-20261002_172241-fix-ver-label src/memory.py   # 若走备份路径
```

### 两条元教训

1. **YAGNI 砍掉的那一层恰好是唯一有收益的一层**：原稿以「不改 MCP 工具面」为理由砍掉它，
   理由是「diff 小」——但它不影响 D1 自身的测试结果，只影响 **claude/codex 能不能拿到
   联邦记忆**。2026-10-02 实测推翻并已实现（设计第七节已加横幅留档）。
2. **不动端点默认 ≠ 不翻默认行径**：D1 只接通 `/api/memory/context` 的**能力**，
   闸门 G4 明确钉住「端点默认不翻」；真正翻的是 `/api/memory/search` 与前端兜底。
   分步的意义是一次只动「能力」或「默认」一件事，出问题能二分定位。

---

## v0.13.77

① **技能描述按不可信输入处理，只渲染两个行内标记**：
① **技能描述按不可信输入处理，只渲染两个行内标记**。
描述取自 `SKILL.md` 的 frontmatter，而这些文件来自 **20+ 个发现点**，
含第三方仓（mattpocock-skills / hallmark / Agent-Reach / crawl4ai）
—— 它们是**外部内容**，不是本仓自己写的文案。hub 的 origin 里有
**终端**（能起 pty），所以「渲染 markdown」必须按处理不可信输入做：
能写进 frontmatter 的恶意描述一旦渲染成 `<img onerror=…>` 或
`<a href="javascript:…">`，就是**存储型 XSS** 且能直接摸到终端。
口径：只认 `**粗体**` 与 `` `行内代码` ``；链接/图片/标题/列表/
原始 HTML 一律**不渲染**（保持转义后的字面文本）。
**顺序不可颠倒**：先 `escapeHtml` 再替换 —— 串里不再有裸 `< > & "`，
此后插入的 `<strong>`/`<code>` 是**唯一**由我们放进去的标签。
**不做斜体**：`_italic_` 会把 `snake_case_name`、`*.py` 吃成斜体，
技能描述里标识符与路径很常见，是实打实的误伤。
② **收起态侧栏的「总览 10」徽标没隐藏**（用户 2026-10-04 报）：
收起态隐藏规则写的是 `.badge`，而侧栏徽标的**真实类名是 `.nav-badge`**
⇒ 选择器对不上，CSS **静默不生效**，48px 图标条上一直挤着个「10」。
与 09-24 那次方向相反：那次是**写出来的**类名不存在，这次是
**规则里的**类名不存在。两者都不报错，都只是安静地什么都不做。

## v0.13.76

① **抽屉永远打不开、而遮罩照亮 = 整页锁死**（用户 2026-10-04 报
「窄屏左侧菜单栏展开不正常」）。实为 v0.13.75 的回归：
`<head>` 里的 `narrow-rail` 是**首帧专用**标记（它让窄屏首屏
就是图标条，而不是 236px 白板盖住 60~74% 视口），
但我当初**打完就没再摘**，于是它变成永久标记。
`html.narrow-rail .sidebar:not(.collapsed)` 的**特异性高于**
`.sidebar:not(.collapsed)` ⇒ 用户点「展开」时 JS 确实移除了
`collapsed`，几何却仍被按回 52px 图标条。
实测：展开后 `class=sidebar` 但 `width=52px / position:relative`
（应为 236px/fixed），而遮罩 `on` ⇒ **点哪都点不到**。
修法：**JS 一接管就摘标记**，语义回到本意「JS 还没跑（或没跑起来）」；
失败模式也是对的 —— bundle 挂了则标记留下，窄屏仍是可读图标条，
而不是一块盖住大半屏的白板。
② **探针上一版为什么没抓到**：它只量**首帧**，从来没点过开。
首帧是对的、交互是坏的 —— 这正是「只验一个时刻」的盲区。
已给 `probe_narrow_layout.py` 加第五组量「点开后抽屉(宽/定位/遮罩)」，
验证它会咬：修前五档全红（52px/relative），修后 236px/fixed 全绿。
另加静态闸门 `test_marker_must_be_removed_when_js_takes_over`
（只管「打」不管「摘」的补丁等于永久补丁）。

## v0.13.75

① **技能中心在窄屏每个字一行**（2026-10-04 用户真机截图）：
窄屏 CSS 是 `.sidebar:not(.collapsed)` 那类**flex 挤压**的同族 ——
`.mem-item` 是 `display:flex` + **nowrap**，`<p>` 是 `flex:1`(1 1 0%)。
实测 390px：容器 296 = 名字 141 + 操作区 128 + gap 16 ⇒ 描述只剩 **3px**
（900px 时是 275px），于是「行)、小 / 字母描 / 客转文 / 字、」这样一行一字。
**本仓对同类结构修过一次**（`.sp-list > .mem-item > p{min-width:0}`，
第 618 行），但**技能中心列表不在 `.sp-list` 里** ⇒ 只修了一处。
这是「按选择器修 bug」的典型漏网：修的是那一个选择器，不是**那一类形态**。
窄屏口径：`flex-wrap:wrap` + 描述 `flex:1 1 100%;min-width:0` 独占整行
+ 操作区右对齐 + 空描述 `:empty{display:none}`。
实测 390px 描述 **3px → 292px**；assets 页无回归（两版 266px）。
② **真渲染探针入库** `tests/probe_narrow_layout.py`（CDP）：
量首帧几何 / 技能描述宽 / 逐页最窄描述 / 底部留白四组可断言的量。
为什么要它：静态闸门只判「机制写对了没」，**判不了像素**——
CSS 写错类名、flex 基准给错、首帧竞态，这三类**都不会让任何静态测试变红**。
已验证它会咬：修前 `EXIT=1`（320/360/390/412 四档红，描述 0~20px），
修后 `EXIT=0`（五档全 OK）。
探针自身也踩了两次坑（已修）：就绪判定把字符串 `'{}'` 当有效值（truthy）
⇒ 假绿；底部留白在切到 200 行技能列表**之后**才量，`.page.on` 仍是那页
⇒ 输出 -50811px 废话。
底部留白**只报不判**：上一轮试过把它均匀分布到各块之间，
截图一看**更丑**（空白跑到页面中段，把图例和操作按钮割开），已回退。
「短页面底部有留白」是正常形态，不是缺陷。

## v0.13.74

① **窄屏第一眼是一块白板**（09-23 事故形态复现，生产实测）：
① **窄屏第一眼是一块白板**（09-23 事故形态复现，生产实测）：
窄屏 CSS 是 `.sidebar:not(.collapsed){position:fixed;width:236px;z-index:46}`，
而 `<aside>` 初始**没有** `collapsed` 类 —— 那要等 bundle 末尾的
`initSidebar()` 才加上。实测用户第一眼看到 **236px 盖住
60.5%(390px)~73.8%(320px)** 的视口，正文被从中间切断。
既有闸门 `verify_narrow_default_iconbar.py` 断言的是**稳定后**
collapsed=true，所以这段一直没人管 —— 缺的不是某条规则的正确性，
是**「首帧」这个时刻压根没有判据**（新缺口，不在原五条不变量内）。
修法：`<head>` 里按当前视口给 `<html>` 挂 `narrow-rail`，
CSS 用 `html.narrow-rail .sidebar:not(.collapsed)` 把**首帧**那一格
从抽屉改成图标条。**为什么必须在 <head>**：第一版写在 `<aside>` 的
首个子节点，320px 生效但 **390px 仍闪** —— 浏览器可在解析到开标签后、
跑完该脚本前完成首次绘制（竞态，两档结果不一致）。移进 <head> 后
body 尚不存在，无物可绘 ⇒ 竞态从根上不存在。
真渲染复验：首帧 **236px/fixed → 52px/relative**，五档全 OK。
另修：顶栏状态字被视口右缘**切断**（无省略号）、窄屏字号/行长、
图标栏按钮**没有 title/aria-label**（收起态 .lbl 是 display:none，
手机又无 hover ⇒ 既无可见文字也无可访问名）、
以及那段描述「手风琴 + 顶部搜索框」的文案在窄屏白占 1/3 屏高
（窄屏根本没有手风琴）—— 改为 CSS 断点分两份文案。
② `/api/skill/list?q=`：匹配字段补 `realpath`（此前搜不到
「按来源仓名找技能」，`q=mattpocock` 命中 0，因 `path` 是软链那一侧），
并改为按分数排序（精确在前、近似在后，与前端一致）。
对账闸门同步加强为**比对分数**而非只比命中/不命中。
过程中抓到一处真漂移：JS 的分隔符类含 `，`/`、`/`，Python 早先没有
⇒ 少切词。已补齐，并把「夹具必须判别」也做成闸门
（第一版中文标点夹具走的是精确子串快路，压根没进切词逻辑，删掉漂移照样绿）。

## v0.13.73

① **同一台机器两套口径**：v0.13.72 把前端搜索框改成模糊后，`q` 仍是纯子串
① **同一台机器两套口径**：v0.13.72 把前端搜索框改成模糊后，`q` 仍是纯子串
⇒ UI 里搜得到，而走 `/api/skill/list?q=` 的两条链路
（**MCP 门面 `hubmcp.py`——pi/Claude 注入真正走的那条**、
资产面板三路检索 `07-asset-panel.js`）搜不到。
`q` 已改为：先精确子串，再**逐字段**算编辑距离，容忍度阶梯
（≤4 不容忍 / ≥5 容忍 1 / ≥8 容忍 2）与前端逐字一致。
刻意**不**把四字段拼成大串再算距离：拼接后分隔符会抵掉失配。
**只改「在不在结果里」，不改排序**——改排序会影响 MCP 门面既有
消费方，属另一个决定，不在本版悄悄带上。
② **两份实现靠闸门锁住，不靠自觉**：Python 与 JS 各写一份（语言不通，
无法共用），「口径同源」的可执行定义是「两侧对**同一批夹具**给出
同一个判定」。`tests/test_skill_list_q_parity.py` 把 12 条夹具分别喂给
node 与 Python 逐条比对，两侧一致**且**都要符合夹具声明的期望
（防「两侧一起错」也绿）。
已用注入回归验证它真会咬：把 Python 退回纯子串 ⇒ 6 红；
把容忍度阶梯改错一格 ⇒ 1 红；还原 ⇒ 全绿。

## v0.13.72

① **模糊匹配不再是「技能中心专属」**：搜 `crawl1ai`（数字 1）找不到
`crawl4ai` 这类手误，此前只有技能中心改了；本版把 `fuzzyMatch` 从
`04-terminal-ws` 搬进 `01-core-boot`（它被 5 个分片用，挂在 terminal
那一片会让下一个找它的人以为「只有终端用」），并接入全部搜索框：
侧栏搜索 / agents 命令面板 / 端口表 / 本地项目 / GitHub 项目 / 技能中心。
**口径不变**：精确子串恒 1000 分压倒近似，短查询(<5字)不容忍编辑距离，
近似行标 `≈近似` 徽标——面板必须能回答「为什么这条出现在这里」。
⚠ 连带改判上版说法：上版汇报称「还有 6 处纯 includes()」，实测只有
**5 处**——「终端历史」与「经理任务」**根本没有客户端搜索框**
（前者只有成员判断、后者无输入框），那个 6 是我数错的。
② **一个恒真的 L0 用例**：`test_skill_visibility_sync` 的干跑用例原先
直接 `os.listdir($HOME/.claude/skills)`（→ `hermetic-clean` 档报
`FileNotFoundError`，因为假 HOME 里那个目录本就不存在）。改成「不存在
就当空」虽然不报错，但**仍是恒真**：把 `apply_plan` 挪到 `--apply`
判断之前（制造「干跑却真写了」的回归）重跑，测试依然绿——
因为 `os.symlink` 在父目录不存在时抛 `FileNotFoundError`，被
`except OSError` 吞进 `failed`，那个环境下**想写也写不成**。
⇒ 改为「测试自建源与目标目录，且源里真有 2 条待链技能」，
并补一条**前提守卫**先证明真写确实写得进去。
已用注入回归验证：改坏时红、还原时绿（不验证就会把假绿当修好）。

## v0.13.71

① **归档根与发现点是两张表**：`_DEFAULT_DIRS` 答「各 CLI 自己会读哪」，
而 09-25 装的 hallmark / mattpocock-skills / Agent-Reach 与既有的
crawl4ai，其 `SKILL.md` 都在 `技术文档/<仓>/…`。`_inside()` 按 realpath
判 ⇒ 这四仓的软链被自己的防越界闸门拒读，**实测 62 条**（与
PT-20261002-13 记的数字逐条对上，现已清零）。口径依据归档军规
「源码唯一权威副本必须落在 `技术文档/<项目名>/`」。
刻意**不**把整个 `技术文档/` 当根：那会架空 `EXCLUDED_DIRS` 已定的
snapshots(664) / 全量备份(247) / `.orca-audit` / `Hermes-backup` 四条结论。
② **搜索框的纯 includes() 让手误等于不存在**：搜 `crawl1ai`（数字 1）
找不到 `crawl4ai`。改为「精确优先（恒 1000 分，压倒近似）+ 编辑距离
近似」，近似行标 `≈近似` 徽标 —— 面板必须能回答「为什么这条出现在这里」。
── 以下为 v0.13.70（D5/D6）时的根因，保留供追溯，非本版条目 ──
① **退出码会让 Claude 拒绝输入**：UserPromptSubmit 同步阻塞在用户
输入之前，hook 返回非零就是把「技能没检索到」升级成「用户发不出
消息」⇒ 脚本吞掉一切异常并 exit 0。实测 6 种形态（正常 / 不可达 /
404 旧后端 / 垃圾 stdin / 空 stdin / 斜杠命令）全部 exit=0 stderr=0；
对生产 v0.13.65 打过去是 404⇒静默⇒升级前不干扰 Claude。
注入链路此前只有 pi 一条，Claude 连检索出口都没有（skill.read 恒 0）。
与 D4 同参同源：都打 /api/skill/relevant、都 rerank=false
（rerank=true 实测 took_ms=1064.5ms，两条通道预算都小于它）。
改法：外科式文本插入 + 写前在内存里验「别人的每个键的值未变」——
该文件今早被 3 个别的会话写过（06:50/07:00/09:22）。
② **窄授权不得读成宽授权**：`pi` 路主动扣下。`~/.pi/agent/**` 是受
保护面，用户给的是单文件授权（hub-facade.ts）而非整棵树。
③ **禁改面拒绝**并说明原因（jcode/hermes/qwenpaw/codebuddy），
不是静默跳过。
④ **报冲突不覆盖**：已存在但指向别处的一律跳过——覆盖会毁掉别人
手工做的链接。只软链不复制、同 realpath 幂等、默认 dry-run。
opencode 由 B3 证伪（不扫 ~/.config/opencode/skill），不作目标；
它真正扫的 claude+agents 两路已在表里 ⇒ 顺带覆盖。
实测四路建成 66 条，幂等复跑 0 新增 / 72 已就位 / 0 冲突。
闸门 tests/test_skill_visibility_sync.py（L0 12 例，含「干跑不得改动
真实 Agent 目录」与「冲突不得被覆盖」两条负向用例）。

（以下为 0.13.69 D7 技能中心前端改版）
#   病根：页面里**存着第二个真相**——技能页长期写死「7 路发现点」，
而 D1 早已把路由改成 20 路。第二个真相比第一个危险，因为它
看起来永远正确、不会随后端变，只能靠人去发现它过期。
本版：① 删掉写死文案，路数/条数一律由 /api/skill/list 的
SKILL_ROUTES.length 与 items.length 现算；
② 新增发现点自检（/api/skill/status：四态 + 排除段 + 拒读数）；
③ 新增相关性实验室（/api/skill/relevant：命中词 + 耗时 + jev 状态）；
④ 注入预算卡补进度条与「被裁 = agent 看不到它」；
⑤ 列表加「N 天零调用」徽章与「只看零调用」筛选。
**置信度封顶必须在界面上写出来**：零调用榜的 medium 藏起来
就会被读成 high（d.direct_source=not-implemented 同理）。
**闸门 tests/test_skill_center_ui.py 改为 L0**（计划书原写 verify_*）：
仓内 README 的 verify_/probe_ 属 L2 live、需服务、手工单跑、
不被 discover -p "test_*.py" 收进来 ⇒ 按那个名字写的东西
**在提交时根本不会跑**，那不叫闸门叫摆设。
**闸门自己蒙对过一次，已修**：首版 rerank 断言拿注释里的
「默认 rerank=false」字样去过，而代码里是
`'&n=5&rerank=' + (useJev ? 'true' : 'false')`，并无该字面量。
「文字存在 ≠ 已生效」那一族（TDZ / vitals_loop / 跨档镜像声明 同族）——
**蒙对的闸门比没闸门更危险**。现改为剥注释后判代码，
并补「jev 开关出厂不得带 checked」一条；两条均已反向验证会红。
窄屏：未新增任何断点值，复用 01-core-boot.js 的 HUB_NARROW_MQ
（分档偏好不变量③）；新增 .sk-budget 预算条用独立类名，
不复用 .bar/.chip 以免改坏别处。

（以下为 0.13.68 D3 技能调用记账与零调用僵尸榜）
#   病根：本批只接了 hub 通道（profile_events 里 source='rest' 的
skill.read / skill.inject），各家 agent **直读自己技能目录的旁路
统计未实现**。设计书 §7 的口径是「两源皆零 → confidence=high」，
而第二源不存在时那条口径不成立——照抄会得到一个看着确定、实际是
仪表盘盲区自欺的榜。故本批**把封顶值做成可断言字段**：
每行 confidence 恒 medium + direct_source="not-implemented"，
顶层再回显 counted（账里有痕迹的技能数，0 = 压根没记账），
前端与测试据此区分「真的没人用」和「没有仪表盘」。
src/skill_usage.py  counts()/zombies()/snapshot()：
读不到 DB **绝不抛**（面板要能开），回落方向保守——多提醒不漏提醒；
零调用技能**绝不自动删**，只出 suggested_action，且单路可见优先
widen_visibility（它可能压根没机会被选中，不能先判它该退）。
闸门 tests/test_skill_usage.py（L0 29 例，用假 db 模块驱动 counts()
顺带断言 SQL 参数形状：真 API 是 db.query，计划书里的 db.fetchall
并不存在，照抄会直接 AttributeError）。
runlog.SUBJECTS 增 skill.relevant / skill.inject（D4/D5 注入链记账用）。
B3 侦察反证 D1 第 20 路 opencode 是**假发现点**（opencode 1.18.34
只扫 ~/.claude/skills、~/.agents/skills、项目 {skill,skills} 与配置键
skills.paths；opencode.json 无 skills 键 ⇒ 接线不存在），
顺带量出 62 条第三方技能对 agent-hub 不可见 ⇒ 已登记
**PT-20261002-13**，本批不夹带（它动生产常量与安全白名单）。
生产未重启（重启授权留到 D3 之后一次执行）。

（以下为 0.13.67 D2 技能相关性检索：GET /api/skill/relevant）   病根：/api/skill/list?q= 只是大小写不敏感**子串**过滤，
「说一段任务描述 → 找回对的技能」机制上不存在；而 hub-facade.ts
的 input→context 通道只能拿到记忆（skill.read=0），没有技能候选可注入。
src/skill_relevance.py  零新依赖 BM25F：ASCII 按词 + 连字符名额外拆子词，
CJK 只出相邻二字（单字查询走长度为 1 的回落）；W_NAME 2.5 / W_DESC 1.0。
src/jev_client.py  jev 异步精排：8s 超时、600s 缓存、连续 2 次失败熔断，
**不进必成功关键路径**。依据实测：choice 中文置信常 1.00 而 score
主观刻度掉到 ~0.3 ⇒ jev 只逐行回传分数与置信度，**不改写排序**，
排序权威仍是 BM25。
闸门 tests/test_skill_relevance.py（L0 37 例，含端点级 tmp 根 TestClient）。
**两处真实盘取证修正**（分词器的错不抛异常，只安静地少召回）：
① 同时出单字+二字时高频字（能/不/登）各带 IDF 累加成噪声，把
arkcli-auth/deploy 抬进“登录态加载不出但能新建”的前四名；
② _LATIN_RE 把 '-' 当词内字符 ⇒ agent-dispatch 整名不可分，
查询里写 dispatch 则**全部 365 条技能 name命中恒为空**。
旁证：codex/skill-creator 在真实结果里各出现两次 = PT-20261002-12
记的 realpath 去重缺陷，正在输出里显形。**生产重启未执行**（沿用禁重启边界）。
上一版 v0.13.66 技能中心统一列表（D1 补齐 20 路发现点 + 排除清单 + state 四态）：
病根：技能门面只扫 7 路 ⇒ 家长 10 家里 6 家约 300 条技能不在册，
「按任务自动发现技能」从机制上就漏（PT-20261002-11 的 R1 可见性缺口）。
D1  src/skill.py：_DEFAULT_DIRS 7→20 路；EXCLUDED_DIRS 15 条**带理由**
（marketplace 缓存/安装暂存/备份/快照/厂商同源副本），排除项进 /api/skill/status
不静默；_scan_one 加 reason 三态 + skill_state() 四态（ok/empty/missing/error）
——原 available 布尔把「这家没装」与「我们配错」混成一句话；
expand_roots/route_roots 支持 glob（qoder-alpha 扩展目录名是内容哈希，
写死必然升级即 missing），_scan_many 多根部分失败不判整路失败。
取证修正三处曾记错的实况：find 不带 -L 不跟随软链 ⇒ opencode 实为 2 条非 0；
qoderwake 的 runtime-generations/{A,B} 与 resources/builtin-skills 是
realpath 不同的同源副本（各 11 条）⇒ 列入排除否则 11 报成 44；
hermes 120 与 hermes-agent 58 实测**零重叠**（去重机制另在 L0 造重叠验证）。
闸门 tests/test_skill_routes.py（L0 23 例 + L1 4 例）。**生产重启未执行**（沿用禁重启边界）。
上一版 v0.13.65 统一记忆注入通道（D1 接联邦 + D1.5 开默认快路口径）：
病根实测：/api/memory/context 收了 9 路联邦源 ID、过白名单校验不报 400，
却在函数体里被静默丢弃 ⇒ 回包 backends 只有 tdai_profile/local，
fed.sources=0、正文 1585 字符、联邦段完全缺席。HTTP 200 无异常无告警
（本仓反复警告的「全指标绿而功能层已死」同族）。
D1  src/memory.py：注入包接联邦 + 第 4 段（预算内轮转取，未返回/失败的
逐路点名写进段里，不静默丢弃）；src/memfed.py：search_fed 加**可选**
wall_s 墙钟（默认 None＝既有 27 例语义逐字不变），超预算记 skipped_budget
且不取消（to_thread 不可取消）——坐在会话起始链路上，宁可少一路也不能卡死开局。
D1.5 /api/memory/search 与前端兜底默认由 local,tdai 升到快路联邦集
（memory.FED_FAST_SOURCES 单一真源）；慢三路 pi/codex/archived 实测
rg 1.9~2.5s 且必然超时 ⇒ 不进默认，等 A4 索引投影。
src/hubmcp.py：hub_memory_context 开 sources 口子（空串取唯一真源），
工具层不抄第二份默认字符串。
闸门 tests/test_memfeed_inject.py（L0 16 例 G1~G8，含 AST 零写与
「预算掐掉的源必须写在段里」）。**生产重启未执行**（沿用禁重启边界）。
上一版 v0.13.64 后端：终端移动端三零件（借鉴 cloudcli 行为规格，不复制其 AGPL 代码）：
P1 触摸层 static/hub/13-term-touch.js：惯性滚 / 长按选区 / 双指缩放，
宽屏不绑定；P2/P3 src/term.py：auth_url 逐观看者旁路 + ANSI 去重，
API 只收 agent_id、命令取画像白名单（不退化为 bash -c）。
10-02 复核补记：惯性尾巴曾被 ttScrollByPx 每帧 Math.round 逐帧取整吞掉
（v 衰减到 <750px/s 后每帧不足半行，衰减段≈7 成路程整段消失：理论 ≈11 行、
实测 2 行）⇒ 改为跨帧余量 ttResidPx；L2 判据 T2b 同步收紧为「松手再滑 ≥3 行」。
证据：docs/TERM-MOBILE-IMPROVE-PLAN-20261001.md §9.1；台账 PT-20261002-10。
上一版（v0.13.63）根因档案保留在下方，它被 L0 闸门 test_term_scroll_sensitivity
钉住（改这里之前先读那段注释）：
终端滚轮「无法上翻 / 到不了页顶」：xterm 6.0 的 scrollSensitivity 仍取默认 1
6.0 的 consumeWheelEvent 里有 `if (|deltaY| < 50) r *= 0.3` 再
Math.floor 取整 ⇒ 标准一格滚轮（deltaY=120、行高 24px）只走
120/24*0.3=1.5 → 1~2 行。CDP 真派发实测：sens=1 → 2.1 行/格、
3 → 6.2、5 → 10.5、10 → 20.8（严格线性，与 deltaY 不成比例）。
2000 行 scrollback 从底部滚到顶要 ~940 格 ⇒ 体感就是「滚不动」。
A/B 实测 5.5.0 与 6.0.0 行为一致 ⇒ 非升级引入；5.5 按 deltaY/行高
走（自然 5 行/格），6.0 的 0.3 折把体验砍到 1/5。
修法：Terminal 构造显式 scrollSensitivity: 5（对齐自然值），
端到端实测滚到顶 1979 行只需 190 格（-80%），Alt/Ctrl/Shift 仍走
fastScrollSensitivity(=5) 不丢快速滚动。
注：滚动条「看不见」是 6.0 Auto 档设计（hover 才显、离开 500ms 淡出），
真渲染量到 opacity=1 / pointer-events=auto，非缺陷，故不动样式。
↑ v0.13.62：历史「点得进去」——列表与续聊校验共用同一套 codex source 判据
↑ v0.13.62：v0.13.61 的半边修复收尾 ——
列表侧放了 vscode 会话，校验侧仍写死 source='cli' ⇒ 点「续聊」
必 404「session_id 不在实盘清单内」（列表能看见却点不动）。
抽 _codex_real_user_sql() 作唯一判据真源，两侧共用一份。
↑ v0.13.61：侧栏 agent 名下「最新会话」停在 09-28 的根因 ——
sessions_store._t_codex 写死 where source='cli'，而 09-29 起
用户在 IDE 扩展里开的会话 source 记为 'vscode' ⇒ 最新会话被整体
过滤。改成「排除噪音」（exec 探针 + subagent 子线程）而非白名单，
免得 codex 下次新增入口再次静默漏（详见 CHANGELOG v0.13.61）。
↑ v0.13.60：xterm 5.5.0 → 6.0.0 整组升级（core + 6 addon），
canvas addon 随 6.0 移除（peerDeps 仍锁 ^5.0.0，取证见
static/vendor/README.md），回落链收敛为 webgl → dom；
另补 WebGL 纹理图集定时清理（clearTextureAtlas，显存不再单调涨）。
↑ v0.13.59：P2-B 跨 Agent 活动指示 + P2-D 文档/密钥纵深批。
↑ v0.13.58：终端状态跨客户端连续（TTL 判据=无生命迹象）+ P0 止血批
↑ v0.13.56：尺寸所有权 claim/update + resize 100ms 去抖 —— 后台那一端
偷不走 PTY 尺寸（桌面开着 vim、手机端在后台唤醒的典型坑）。
↑ v0.13.55：输出合并 coalescer（5ms 前后沿），WS 帧数 2602→7。
↑ v0.13.54：xterm addon 补齐（webgl/canvas 渲染器、Unicode11、
终端内查找、bracketed paste 安全包装）。
↑ v0.13.51：① drift 体检从「只报」升级为「按持久化值写回」（落笔前
时间戳备份，HUB_MODEL_DRIFT_REPAIR=0 可退回只报）；因 CCR 与 hub
开机同秒启动，启动那一次会被 CCR 盖掉 ⇒ 再加 300s 定期巡检。
② 续聊会话（`claude --resume <id>`）也按白名单追加 --model：不带
flag 的启动读的是会被 CCR 改写的配置文件。
↑ v0.13.50：对话/协同子任务/定时任务以前从不读 hub 侧持久化模型
（adapter.default_model 是启动时常量 ⇒ 实测仍发 qwen3.8-flash），
现统一走 modelcfg.chat_model()；并加只读 drift 体检（CCR 重启会
把 ~/.claude/settings.json 的 env 三兄弟改回旧值）。
↑ v0.13.49：侧栏历史会话：条数 8 / 去标题行 / 时间只留日期 / 左边距对齐状态图标
三个子项与系统页同口径（data-sys ⇒ 委托 ⇒ go(page) ⇒ 正文出页），
设置抽屉整体拆除 ⇒ 09-23「手机上被浮层糊住」的形态不再存在。
↑ v0.13.42：设置→GitHub 子菜单（远程地址/key/归属/克隆落点不再硬编码）
↑ v0.13.41：设置→模型子菜单（选 agent → 选 CCR 模型 → 预览 → 口令落笔；
Hub 侧 per-agent 模型用于拉起终端注入 --model + 写该 agent 自己的
配置文件；CCR Router 五场景只读；写前预览+备份+口令三件套）
↑ v0.13.40：系统子菜单页：删页顶标题/分割线（renderPageCrumb 只清空）
+ 十页统一骨架重排（标题+分割线来自 #opBar：renderPageCrumb 是全站
唯一写 #crumb 的地方，它不写字 ⇒ syncOpBar 判 void ⇒ 整条 opBar 收起；
chat 早退交给 renderModeBar。排版统一到 .sp/.sp-card 骨架）
↑ v0.13.39：修「选 pi 起会话 ⇒ 终端一屏 JS 堆栈」：终端子进程 PATH 前置 nvm node bin
（pi 的 shebang 是 #!/usr/bin/env node，服务 PATH 无 nvm ⇒ 内核把系统
node v20.20.2 交给它，而 pi v0.85.1 的 bundle 用 node:fs 的 globSync
（Node 22+）⇒ SyntaxError 启动即崩。which() 的 nvm 兜底只管 hub 找
得到 pi，管不到 pi 自己再找解释器 —— 两层都得补）
↑ v0.13.38：本机项目/GitHub 项目页 agent 候选框补 pi 与 codebuddy：两张 Web 型卡补终端入口
（候选框口径=entries 含 term；pi 有 CLI v0.85.1、codebuddy CLI 用 WorkBuddy
包内绝对路径——裸名 which 落空，必须带目录分隔符；qwenpaw 无 CLI 故不在列）
↑ v0.13.37：Agents 菜单补 CodeBuddy Code 卡：`codebuddy --serve` 的原生遥控界面 :35431
（画像唯一改动：src/profiles.py；无 cli/terminal ⇒ vitals 按 web-service
形态以自有端口应答为存在证据。卡片入口=嵌入会话+新窗口+详情）
↑ v0.13.36：两项目页收藏/隐藏落服务端：app_prefs KV 表 + GET/PUT /api/prefs/{key}
（键白名单 projects.lp/gh，写走 write_gate+显式 decide 双保险）；
前端载入拉后端偏好为准、切换回写，localStorage 降级为离线兜底。
+ GitHub 项目页：GET /api/github/repos（远端清单+strict remote 本地匹配）+ POST /api/github/clone
（白名单 slug→服务端重构 URL→浅克隆到 GITHUB_CLONE_BASE，审计 action=create）
+ POST /start 铸 JWT 转调创建会话 → 详情抽屉项目列表 + iframe 直达 /session/{id}；
MCP +hub_cloudcli_projects
（--embed-line / --embed-bg / --font-display / --on-accent），fr 轨道一律
minmax(0,…) 防内容顶破容器，数字列 tabular-nums 兜字体回退；DESIGN.md 新增
「视觉系统」语义索引章（权威源仍是 templates/index.html 的 :root，不复制取值）。
取值与原字面量逐字相同 ⇒ 渲染零变化；取证见 agent-knowledge/57（真渲染四档 + gate 50）。
上一版 v0.13.25 后端：终端进程退出时把「为什么没了」说清楚。waitpid 的退出状态原先被
`_st` 直接丢弃（src/term.py 的 _cleanup / _force_kill）⇒ 崩溃原因永远上不了屏，
用户只看到一句「[会话结束]」。新增 describe_exit() 把信号/退出码解成人话：
SIGILL/SIGSEGV/SIGBUS/SIGABRT/SIGKILL 点名「疑似内存不足」；并用 hub_killed
区分「hub 自己发的 SIGTERM/SIGKILL」（点 × / 空闲 TTL / 服务退出）与内核
OOM-killer ⇒ 绝不把用户主动关会话报成内存不足。API 侧 to_dict() 透出 exit_reason。
起因：2026-09-25 排查「菜单点 OpenCode 秒退」，只能靠 dmesg(trap invalid opcode)
+ objdump(ud2) + ulimit -v 三步反推出 bun/JSC 的 MemoryExhaustion 主动 abort。
真因是整机 swap 耗尽（/vol1/.swap/swap2 那 4G 因开机顺序 + nofail 静默失效），
hub 代码本身无 bug —— 本次只补「可观测性」。详见 CHANGELOG。
上一版 v0.13.24 后端+前端：asset_audit 资产变更审计（append-only 表 + log_asset_event 写口径 + /api/audit/list）；
本地记忆便签 staleness 观测（memstats，**只报告不清理**）挂 /api/kb/status.local_memory；
chat 会话工具条加导出按钮（blob 下载、token 只走头、四态文案互斥）。
上一版 v0.13.23 后端：/health 补上游网关(CCR)连通性与模型注册清单 + 画像最近检测时间；
会话批量导出端点（JSON/CSV，默认脱敏，按写端点同等鉴权）；
MANAGER_LLM_BASE_URL 默认值由已退役的 FCC :8082 改回 CCR :3456。
版本号让位：本批原自命名 0.13.22，但 master 上 ff53581（终端页空格接力，纯前端）已占用该标签
⇒ 本批改 0.13.23，避免两批共用一个版本号（详见 CHANGELOG）。
v0.13.21/22 均为纯前端批次，按项目口径
「VERSION 与清 code_stale 随下次后端改动同批」⇒ 本次一并 bump。
上一版（v0.13.20 FCC 退役收尾 / v0.13.19 P3 工具注册表 + P4 资产面板）明细见 CHANGELOG.md。
: 常驻后台任务统一登记处。裸 create_task 不持引用 ⇒ 事件循环只持弱引用，
: GC 可能在任意时刻回收掉这些循环任务（表现为「跑着跑着某功能静默停摆」），
: 且 shutdown 时无法 cancel，进程退出要等事件循环超时。

## v0.13.64 — 终端移动端三零件（借鉴 cloudcli 行为规格）+ 惯性尾巴被逐帧取整吞掉的修复

> 方案 `docs/TERM-MOBILE-IMPROVE-PLAN-20261001.md`（授权执行），台账 PT-20261002-10。
> **cloudcli / claudecodeui 为 AGPL-3.0-or-later**：本批只复现其**行为规格**，
> 不复制代码、表达式或常量。

### 本批（10-01 落地）

- **P1 触摸层** `static/hub/13-term-touch.js`（新增）：惯性滚 / 长按选区 / 双指缩放；宽屏不绑定。
- **P2/P3** `src/term.py`：ANSI 去重、`auth_url` 逐观看者旁路；**API 只收 `agent_id`，
  命令取画像白名单**，不退化为任意 `bash -c`。
- **闸门**：`tests/test_term_touch_authurl.py`（L1，14 条）、`tests/verify_term_mobile_touch.py`（L2 真机，13 条）。

### 10-02 复核修补：惯性尾巴

T2b「松手后有惯性滑行」**首跑红（松手后 0 行）、复跑绿（2 行）** ⇒ 判据压在能力边界上，
不是稳定红。三个假设里**两个被实测证伪**：

| 假设 | 实测 | 结论 |
|---|---|---|
| 探针慢，`idle` 顶过 150ms | CDP 往返中位 **0.4ms** | 证伪 |
| 甩动没起 | 产品侧读到 `idle=23ms, v=-1010px/s` | 证伪 |
| 位移在下游被吞 | `ttScrollByPx()` 每帧 `Math.round(px/cell)` **无跨帧余量** | **成立** |

真因：v 衰减到 **<750px/s** 后每帧位移不足半行 ⇒ 小数被逐帧抹平且无下次来补，
**衰减段（≈7 成路程）整段消失**（理论 ≈11 行、实测 2 行）。修法：跨帧余量 `ttResidPx`
（新手势清零、撞顶/底作废），单次大位移（跟手拖动）行为**逐位不变**；
T2b 同步收紧为「松手再滑 **≥3 行**」并在红时打印产品侧 `idle/v`。

**红→绿（同一派发 `v=-1010, idle=23`，唯一变量是产品逻辑）**：
`FAIL …再滑 2 行（要求≥3）` → `PASS …再滑 8 行（要求≥3）`；L2 13/13。

### 测试口径更正（重要）

方案 §9 原记「全仓 975 passed」。实测两者口径不同：

    pytest tests/            → 975 collected   （含 pytest 风格的模块级函数）
    scripts/run_tests.sh all → 961（L0 914 + L1 47，unittest discover）

差额 **14** 恰为本批 `test_term_touch_authurl.py` 的 14 条**模块级 pytest 风格**用例
——`unittest discover` 收不进来 ⇒ **标准回归套件当时不跑这 14 条**（需另用 pytest 跑）。

**已于同日修复**（不改版本号，纯测试基础设施；两个方向都做了）：

1. `test_term_touch_authurl.py` 的 14 条搬进 `TestTermTouchAuthUrlGates` ——
   **只改归属与缩进，28 条 assert 逐字未变**（已用 `ast.unparse` 前后比对取证）；
   `node --check` 那条按仓内同族写法（`test_export_button.py` 等）补了缺 node 时的显式跳过。
2. `run_tests.sh` 兼跑 pytest：新增 `pytest` 模式；`hermetic`/`host`/`all` 跑完自动做
   **收集器对账**（`unittest` 收进 N 例 vs `pytest --collect-only` 收进 N 例，不等即红、
   退出码 2）。**只收不跑** —— 拿 pytest 跑用例会让同一批跑两遍、两个例数并排。

另建 `tests/test_tier_collector_parity.py` 三条仓级闸门（每条都带自证样本，防元闸门恒绿）：
① 标准层无 unittest 收不到的模块级裸函数 ② 每个 `test_*.py` 至少贡献 1 例
③ 两个收集器例数相等。

> 判据边界（取证后收窄）：① 曾连带管「非 TestCase 类里的 test_*」，**误报 216 条** ——
> 仓里常规写法是 `class TestTransport(TdaiBase)`，`TdaiBase` 是同模块另一 TestCase
> 子类的别名，unittest 照收（实测 17/10/22 例）。**假红的闸门比没有闸门更坏**（会逼人
> 改断言求绿），故类级差异交给 ③ 兜（运行期事实，无推断余地）。

现口径：`run_tests.sh all` = **L0 934 + L1 47 = 981**，与 `pytest tests/` 的 981 一致。

## v0.13.63 — 终端滚轮「无法上翻 / 滚动不到页顶」：xterm 6.0 的 scrollSensitivity 从没被显式设过

> 由 `wt/01a0f0c4` 施工（PT-20260930-07）。用户报障「agent 终端页面，桌面浏览器里
> 也是无法上翻或者滚动到页顶」。

### 根因（CDP 真派发滚轮量出来的，不是推断）

**不在 CSS、也不在浮层遮挡，而是一个从没显式设过的构造参数。**

xterm 6.0 的 `consumeWheelEvent` 里有这么一段：

    Math.abs(e.deltaY) < 50 && (r *= .3)          // 随后 Math.floor 取整

默认 `scrollSensitivity = 1`。于是一格标准滚轮（deltaY=120、行高 24px）只走
`120/24 * 0.3 = 1.5` → 取整 **1~2 行**。真渲染实测（2000 行 scrollback、24 行视口、
10 格滚动取平均）：

    scrollSensitivity =  1  ⇒  2.1 行/格
                     =  3  ⇒  6.2
                     =  5  ⇒ 10.5
                     = 10  ⇒ 20.8          严格线性

注意它**与 deltaY 的大小完全不成比例**：deltaY=-40 / -120 / -360 走的都是 2 行
（< 50 那档的 0.3 折把三档全拍平了）。从底部滚到顶 1979 行需要约 **940 格**，
体感上就是「滚不动」。API 侧一切正常（`scrollLines(-31)` 精确走 31 行、
`scrollToTop()` 真的到 viewportY=0），所以问题只在**滚轮输入**这一条路上。

**A/B 实测 5.5.0 与 6.0.0 默认配置行为一致（都 ~2 行/格）⇒ 这不是 6.0 升级引入的回归。**
5.5 是按 `deltaY/行高` 走的，自然值 5 行/格；6.0 的 0.3 折恰好把体验砍到 1/5。
原来只是"本来就偏慢"，升级后被放大成"滚不动"。

### 修法

`static/hub/03-agents-cards.js` 的 `new window.Terminal({…})` 显式加 `scrollSensitivity: 5`
—— 与「不按 6.0 打折时的自然值」对齐，不臆造新手感。`Alt`/`Ctrl`/`Shift` 仍走
`fastScrollSensitivity`（默认 5），快速滚动能力不受影响。

### 滚动条为什么"看不见"：是设计，不是缺陷（故不动样式）

6.0 用 VSCode 式自绘滚动条替代了 5.5 的原生 `.xterm-viewport` 滚动，档位默认
`Auto`（核心里 `vertical: 1` 硬编码，非构造参数可调）：`_onMouseOver` ⇒ 常态 `visible`，
`_onMouseLeave` 后 500ms 淡出。slider 背景色由 `theme.scrollbarSliderBackground` 驱动
（前景色 20% alpha），且样式是 `open()` 时**运行时注入**到 `#termEl` 里的 `<style>`。

真渲染量到的事实：

    加载后未动鼠标    cls="invisible scrollbar vertical fade"  opacity=0  pe=none
    鼠标移入终端      cls="visible scrollbar vertical"          opacity=1  pe=auto
    停留 1.4s 后     仍 visible（mouseIsOver 保持 ⇒ 不被 hide）
    移出终端 1.2s 后 回到 invisible（越过 500ms 隐藏超时）

⇒ 桌面浏览器里把鼠标放进终端，滚动条是**正常显示且可拖动**的。原计划里的
"加 CSS 强制常显"因此**撤销**：那会把 xterm 有意做的自动隐藏破坏掉，且因为注入
`<style>` 同特异性、注入点在 head 之后，静态 CSS 还得靠加载顺序硬压，脆而不值。
本闸门改断言"hover 后可见"（S7），若日后有人把样式改坏会红。

### 验收

- L0 静态 `tests/test_term_scroll_sensitivity.py` **6 例**：参数钉住且 ≥ 5、没顺手调小
  快速滚动、**vendor 里那段 0.3 折仍在**（换版本后逼人重量一遍）、分片与 `hub.js`
  产物对账（改分片忘重建 = 改了等于没改）、模板 `?v=` 提手 == 产物 md5、版本注释记着根因。
- L2 真渲染 `tests/verify_term_scroll.py` **7/7 PASS**：生效值 = 5、**10.4 行/格**
  （修前 2.1）、线性关系成立、**真滚轮从 1979 行到顶只花 190 格**（-80%，
  修前约 940）、视口首行是 banner 而非 `L1xxx`、hover 后滚动条 opacity=1/pe=auto。
- L0-hermetic **914 OK 零跳过**（908 + 新增 6）、L1-host 47 OK。
- 15 轮取证探针归入 `tests/probe/`（`probe_scroll2…15`），结论不留在聊天记录里。

### 顺带记一笔

`?v=` 提手由产物内容派生（`scripts/build_hubjs.sh`），所以"改了分片没重建"在 URL 上
**看不出来**——本次就先踩了一次：探针量到 `scrollSensitivity` 仍是 1。补了
`test_built_hubjs_matches_shard` 与 `test_template_token_matches_hubjs_md5` 两道对账。

## v0.13.62 — 历史「看得见、点不进去」：v0.13.61 半边修复的收尾

> 由 `wt/01a0f090` 施工。用户报障「点击历史会话记录还是无法拉起，显示续聊失败」。

### 根因（实测复现，非推断）

v0.13.61（PT-20260930-03）把 `sessions_store._t_codex` 的 `source='cli'` 白名单换成了
排除集，**只改了列表侧**；续聊前的存在性校验 `_exists_on_disk` 里那条
`where id=? and source='cli' and archived=0` 原封未动。

于是形成半边修复：列表放行了 IDE 扩展开的会话（`threads.source='vscode'`），
点下去却被校验判 404。实盘复现（`~/.codex/state_5.sqlite`，source 分布 cli 7 / vscode 10 /
exec 75 / subagent 5）：

    POST /api/term/sessions  {"agent_id":"codex","session_id":"01a0f084-…"}
    → {"detail":"session_id 不在实盘清单内"}          # 但这条正在侧栏历史里显示

journal 里 09-30 当天 4 次 404 与 200 相间，正是这个分叉（新建会话不带 session_id ⇒ 200）。

### 修法

- 校验侧改用与列表**同一套**排除集（`exec` 探针 + `{"subagent":…}` 子代理仍拒）。
- 抽 `_codex_real_user_sql()` 返回 `(where 片段, 绑定参数)` 作为**唯一判据真源**，
  列表与校验各调一次。两处各写一份 SQL 字面量，正是这次走偏的直接原因。
- 反向闸照旧：噪音来源仍 404（`test_codex_noise_source_still_rejected`）。

### 验收

- 新增 `test_listed_codex_session_is_resumable`：**拿列表自己的结果去问 `resume_argv`**，
  把「列表能列出来的必须点得进去」锁成不变量。
- 变异测试：精确复刻 09-30 现场（列表=排除集、校验=白名单）⇒ 5 例红；反向放宽成全收
  ⇒ 噪音闸红；还原后全绿。
- L0-hermetic 908 OK 零跳过、L1-host 47 OK。

# CHANGELOG

## v0.13.61 — codex 最新会话不显示：source 白名单漏掉 IDE 扩展入口

> 由 `wt/01a0f05e` 施工（PT-20260930-03）。用户报障「侧栏 agent 名称下面没有最新会话记录」，
> 修复后 codex 历史从停在 09-28 变为跟到当下。

### 根因（实测，不是推断）

`src/sessions_store.py::_t_codex` 写死了 `where source='cli'`。而 `~/.codex/state_5.sqlite`
的 `threads.source` 实测有四个取值（09-30 全库统计）：

| source | 条数 | 性质 |
|---|---|---|
| `exec` | 74 | **探针**（本仓 `codex exec` 能力/版本探测） |
| `cli` | 7 | 真实用户会话 |
| `vscode` | 7 | **真实用户会话** |
| `{"subagent":{...}}` | 6 | 子代理线程 |

用户 09-29 起实际改在 IDE 扩展里开会话，`source` 记的是 `vscode` ⇒ 白名单把**所有最新会话**
整体过滤掉。旁证：`cli` 里最新的停在 09-28 16:04，而全库最新是 09-30 11:35，差 2 天。

### 修法：白名单 → 排除集

D5 的原意是「别把 `codex exec` 探针当历史」，这个意图没错，错在用白名单实现：
codex 每新增一个入口（cli/vscode/…/未来还有）就静默漏一批，且**没有任何报错或 note**——
接口照常返回 200 + 旧数据，看起来像「会话没存」。

改成排除噪音（`exec` 前缀 + `{"subagent":…}` 子代理），其余真实会话全收。附带把 SQL 的
`limit` 放宽到 `limit*3` 再在 Python 侧截断：排除发生在 SQL 里，命中数可能少于 `limit`，
沿用原来的 `limit` 会让「有 8 条噪音 + 0 条真会话」变成假空。

### ⚠ 一条反直觉的取证记录（决定了判据只能落在 source 列）

原打算给测试写「探针标题长这样」的判据，结果实测 **exec 探针的 `title` 与
`first_user_message` 就是真实用户提问**——本仓探针统一发「只回复一个字：好」，
74 条同款。⇒ **内容层根本区分不了探针和人开的会话**，唯一可靠判据是 `threads.source`。
这也从侧面说明：任何基于「标题看起来像不像探针」的过滤都不可靠，别再走那条路。

### 测试（两个方向都验过判别力，不只验通过）

- `test_codex_excludes_exec_noise`：对账「结果集里一条 exec/subagent 都没有」。
  变异测试：去掉排除条件 ⇒ **判红**，报出 15 条泄漏的探针 id。
- `test_codex_shows_recent_sessions_not_only_cli`：取库里 `updated_at` 最大的非噪音线程，
  断言它出现在历史里。变异测试：退回 `source='cli'` ⇒ **判红**，报的正是
  「最新真实会话不在结果里」——即用户报障的原现象。

全量：L0-hermetic 908 OK / 零跳过，L1-host 45 OK。

## v0.13.60 — P2-A：xterm 5.5.0 → 6.0.0 整组升级 + canvas addon 移除 + WebGL 图集止血

> 由 `wt/01a0efca` 施工（PT-20260930-01 P2-A）。终端栈整组换版本，破坏性变更
> 逐条撞过真渲染，不是靠 changelog 抄的。

### ① 整组升级（8 个文件逐字节取证）

`@xterm/xterm` 6.0.0 + webgl 0.19.0 / fit 0.11.0 / search 0.16.0 / unicode11 0.9.0 /
web-links 0.12.0 / clipboard 0.2.0。全部取自 npm tarball 的**未压缩** `lib/` 产物，
md5 与仓内文件一致（登记表见 `static/vendor/README.md`）。

- **代价知情接受**：整栈 ~470KB → ~880KB（`xterm.js` 290→489KB、`addon-webgl.js`
  101→248KB）。换取的是可审计性 —— 压缩产物里搜不到 `clearTextureAtlas`，
  这次正是靠未压缩产物确认 6.0 仍带该 API 与 `onContextLoss`，才敢保留止血线与
  上下文自愈。局域网自用场景，长缓存下多 350KB 划算。
- `addon-clipboard@0.2.0` 声明依赖 `js-base64`，但产物已由 webpack 内联（实测
  `grep 3.7.8` 命中、无外部 `define("js-base64")`）⇒ 不需额外引入文件。
- `bracketedPasteMode`（粘贴闸门的依赖）在 6.0 重新实测仍在 —— 这条如果没了，
  项目里那段"对端开 2004 后 xterm 自动包粘贴"的注释就是错的。

### ② canvas addon 移除（三条实测证据，非拍脑袋）

1. `addon-canvas@0.7.0` 的 `peerDependencies` 是 `{"@xterm/xterm": "^5.0.0"}`
   —— 不覆盖 6.0，硬挂只会得到一个不工作的终端；
2. `grep -c CanvasRenderer` 在 xterm **6.0.0 与 5.5.0 核心里都是 0** ——
   canvas 从来就不在核心里，核心没给它开过后门；
3. 上游没有 6.0 兼容版，registry 上 `addon-canvas` 的 latest 仍是 0.7.0。

- 回落链收敛为 `webgl → dom`，死分支 `window.CanvasAddon && …` 一并删掉
  （留着永远走不到 = 与 vendor 不同源的死代码，哪天放回文件就悄悄复活成第二档）。
- **旧链接显式降级**：`?term=canvas` 与 localStorage 里的老存量都降级 dom 并
  `console.warn`、**写回 dom**（不写回则每页刷一条 warn，永不自愈）。不静默改写
  —— 留着旧 URL 的人要看得见"这条后门没了"，否则只会当成"改了参数没生效"再报一次。

### ③ WebGL 纹理图集止血（迁移前就该修的未修态）

上游 webgl 渲染器把每个用到的字形光栅化进纹理图集，**只按 LRU 换页、从不主动清空**；
终端会跑数小时（长会话 / `tail -f` / 编译进度），字形集单调增长 ⇒ 图集页用满后换页
开销上升，长期挂着显存占用偏高。`clearTextureAtlas()` 在 5.5 与 6.0 都是公开 API，
**本项目此前从未调用过** ⇒ 这是未修态，不是新缺陷。

- 每 2 分钟清一次（终端不可见 / 页面在后台时跳过，不打扰后台标签页）；
- 包在 try/catch 里，失败即**停掉这条线**并告警：清理绝不能把终端带崩；
- 字形表是惰性重建的（清掉后用到哪个重光栅化，多几十微秒），换来显存不单调涨。

### ④ 验收

- L0 单测 `tests/test_term_xterm6.py` **18 项**：canvas 四处清零（文件/模板/全局符号/
  回落链）、`?term=canvas` 与存量 canvas 的显式降级含写回、止血线存在且 try/catch
  且只在可见时跑、README 版本表 md5 与盘上文件逐字节对账、LICENSE 七节齐备、
  clipboard 无外部 Base64 依赖、模板 9 处 `?v=` 提手与内容一致。
- 真渲染 `tests/verify_term_xterm6.py` **15/15 PASS**。意外收获：headless 即使
  `--disable-gpu` 也拿到 SwiftShader 的 WebGL2 上下文 ⇒ **webgl 路径在 6.0 上
  被真跑通了**（不只量到 DOM 回落这一端）。
- 全量 **908 OK**（L0 hermetic 908 / skipped=0）。

## v0.13.58 — 终端状态跨客户端连续（TTL 判据改为「无生命迹象」）+ P0 止血批 + 夹具污染治本

> 09-29 用户要求：**任务执行与前端页面无关，换客户端接上去必须是同一份连续状态**。
> 基线 v0.13.57。本版由 `wt/01a0ec66` 合并（10 个提交）。

### ① TTL 判据从「客户端静默」改为「无生命迹象」

实弹取证（`TERM_IDLE_TTL=45s` 隔离实例）发现旧口径把「客户端静默」当死活判据：
agent 仍在每 2s 产出（`idle_s` 涨到 40s），会话照样被回收，另一端重连直接收到
**4410（已结束）**——正是用户报的那个现象。

- Session 拆两个时钟：`last_io`（客户端静默，仅展示）/ `last_activity`（会话有生命迹象：
  客户端交互 **或** pty 产出）
- `_reap()`：只有「无观看者 **且** pty 完全静默 **且** 超 TTL」才回收。
  有观看者绝不因静默被杀；pty 在产出也绝不被杀。
- **推翻旧口径** `test_idle_with_viewer_is_still_reaped`（「开着不动也该收」）：
  防配额占死改由三道闸承担——进程退出即摘表、`MAX_SESSIONS` 满则拒开、无人观看时 TTL 回收。
  代价知情接受：开着不动又不退出的会话会占名额，只能由用户点 × 结束。
- `to_dict` 暴露 `activity_s`；`idle_max_s` 与 `_reap` 同口径（否则 `/health` 假绿）。

### ② P0 止血批（同批合入，8 项）

- P0-1 `last_io` 只认真实交互，心跳不续命（`_touch`/`_touch_bytes` 为唯一判据）
- P0-2 `_reap` 主动 `waitpid` 探活，子进程自行退出也会被回收
- P0-3 `BlockingIOError`(EAGAIN) 单独捕获，退避重试而非杀连接
- P0-4 kill 侧补 `.ccr`/cmdline 佐证，防误杀非 CCR MainThread
- P0-5 resources 采样与 subprocess 改 `to_thread`，不再阻塞事件循环
- P0-6 `_api_rate` 加 IP 上界与窗口回收
- P0-8 `live_titles` 走线程池 + 指纹缓存

### ③ L0 测试夹具污染治本（这条一直压着 prepush 闸门）

实测：夹具根 `~/hub-l0test-fixtures` 累积 **230MB / 2406 个带 `.git` 的目录**（六个测试文件
各造各的、从不清理）⇒ 项目扫描当成真实项目，本机项目数 **176**（应 ≈44）⇒
L1 精度闸（≤50）与 prepush 闸门一起红。

- `tests/tiers.py` 新增 `l0_fixture_register/cleanup`，测试退出时删自己的垃圾
- localprojects：夹具根只在**扫描侧**排（不并进共用的 `EXCLUDE_PATH_PREFIXES`——
  那会让 cloudcli 合并侧把「真 git 仓」也砍掉，实测当场红）
- `_under_excluded` 收口「点名要扫的根及其后代豁免」，两处调用同口径
  （旧代码扫描侧只豁免根自身、合并侧完全不豁免 ⇒ 同一路径凭空消失）

### ④ 测试与实弹

- 新增 `tests/test_term_ttl_activity.py`（10 例）：观看者/产出/静默三态判据
- 改 2 条与新语义冲突的旧断言（推翻的判据与理由写进 docstring）
- 新增 3 只实弹探针：`probe_activity_trace.py` / `probe_ttl_live.py` / `probe_ttl_orphan.py`
- hermetic 814 全绿零跳过（含空 HOME）；host 43 全绿
- **实弹**（隔离实例 `TERM_IDLE_TTL=45s`，真 WebSocket）：持续产出 72s 会话存活 PASS /
  `activity_s` 被压住在 12s PASS / 1.8MB 输出 PASS / 换端重连拿到回放 PASS /
  换端后能继续执行命令 PASS（修前同一探针：重连收到 4410 已结束）

## v0.13.57 — 终端中文画成方块：字体栈补三平台中文字体 + 渲染器自愈/可切换

- 字体栈补 Linux/Windows/macOS 三平台中文字体（此前只有一档，缺哪档就掉回方块字）
- 渲染器自愈：context loss 后自动重画，不再白屏要手动刷新
- 渲染器可切换（webgl / canvas / dom），为 v0.13.54 的回落链留人工逃生口

## v0.13.56 — 终端尺寸所有权 claim/update + resize 去抖（paseo 融合 B2）

要防的真问题：桌面正开着 vim（120×40），手机端同一会话的页面在后台被
`ResizeObserver` 或 `visibilitychange` 唤醒，甩来一个 80×24 ⇒ 桌面的 vim 被压扁。
用户看到的是「我什么都没做，终端自己乱了」，且极难归因。

**服务端（`src/term.py`）**：

- `claim` = 「我是主人，按我的尺寸来」，**无条件夺权**（含同尺寸也要转移所有权——
  这条是 paseo 专门写的用例：少了它，端切换后尺寸就再也改不动）
- `update` = 「我只是几何变了」，**非所有者一律静默忽略**
- 老客户端没有 intent 字段 ⇒ 缺省 `claim`（与 paseo 同口径），否则一次前后端版本错配
  就会让尺寸永远改不动；**未知意图按 `update` 保守处理**——绝不让一个看不懂的字段
  把所有权检查旁路掉
- 行列 clamp 到合法区间：pty 尺寸是 `struct.pack("HHHH")`，把 `99999` 直接喂给它会抛
  `struct.error` 打断 WS 收包循环 ⇒「拖一下窗口」变成「终端断开」
- 所有者断开时交还所有权，否则悬挂 vid 会让所有客户端的 `update` 全部失效

**前端（02/03 分片）**：resize 100ms 去抖（`VERSION` 注释记为「后台那一端偷不走 PTY 尺寸」）；
`hub.js` 缓存提手随 B2 构建同步。

方案档：`docs/PASEO-FUSION-PLAN-20260929.md`；本版补执行记录 + 版本号对齐。

## v0.13.55 — 终端输出合并 coalescer（5ms 前后沿节流，paseo 融合 B1）

现状：PTY 每吐一块就发一帧 WS（`on_readable` 读一块 → 每观看者各塞一块 → pump 发一块）。
pty 典型块只有几十~几百字节，`npm run build` / agent 大段输出时会持续高频吐 ⇒ WS 帧洪水；
手机端解析不过来时队列（`maxsize=2000`）打满，触发「已丢弃 N 字节」——那是**丢内容**，
不是降帧率。

改法（思路取自 paseo `TerminalOutputCoalescer`，按 Hub 形态重写）：

- **前沿**：距上次刷出 ≥5ms 立刻刷 —— 按键回显延迟不升反降。只留后沿（普通 debounce）
  会给每次按键回显平白加一个完整窗口，paseo 文档点名这是错的做法。
- **后沿**：突发期间攒 5ms 一起刷。
- 部署在「PTY 读回调 → 每观看者队列」之间，即**离 PTY 最近的一层**（挪到更下游
  只是换了个挨打的地方）。每个观看者一个合并器。

同批落地 **P1-1 保序**（不做就是埋雷）：`[会话结束]` 与 `[process exited]` 都是带外消息，
发送前必须先 flush 合并器；pump 收尾额外把队列排空（循环已退出，没人再发那些帧），
否则退出提示会抢在最后一段输出**之前**。

同批修：资源页「结束/强杀」422 —— `killProc` 补 `Content-Type`。`api()` 不设 Content-Type，
fetch 传字符串 body 默认发 `text/plain` ⇒ FastAPI 解析不出 body ⇒ 三个 `Body(...)` 全 missing
⇒ 422。写闸门在 handler 前面且已放行，所以日志只见 `POST /api/resources/kill 422`、
不见 401 —— **极易误判成鉴权问题**。对照（带 token，假 pid=1，未杀任何进程）：
修前 `422 {"loc":["body","agent_id"],"msg":"Field required"}` → 修后
`404 "PID not belong to this agent"`。

## v0.13.54 — xterm addon 全家桶补齐（渲染器/宽字符/查找/粘贴，paseo 融合 B4）

借鉴 paseo 终端的 addon 全家桶（方案 `docs/PASEO-FUSION-PLAN-20260929.md` P1-3），
落盘 `static/vendor/` 走离线加载，版本与 `@xterm/xterm` 5.5.0 严格对齐。

1. **渲染器**：核心只内置 `DomRenderer`（每字符一个 DOM span，大输出与原生终端差一个
   数量级）。WebGL 优先、Canvas 回落、都失败才留 DOM；另补 `onContextLoss` 重画。
   CDP 实测（09-29）：`renderer=webgl`。
2. **Unicode11**：必须在 `open` **之前**挂并激活 `activeVersion='11'`（cell 宽度表在
   渲染器初始化时固化，之后再改不重算）。需 `allowProposedApi`。CDP 实测 `unicode=11`。
3. **查找**：`SearchAddon` + Ctrl/Cmd+F 浮层（`absolute`，不占 flex ⇒ 不踩 FitAddon
   按内容盒算行数的老坑），增量匹配、Enter/Shift+Enter 前后翻、Esc 关闭回焦。
4. **粘贴（正确性修复，非锦上添花）**：抢在 xterm 原生 paste 前做两件它不做的事——
   换行归一、把内嵌的 `ESC[201~` **降级成字面量 `[201~`**（否则对端提前结束粘贴模式，
   其后字节被当普通按键逐条执行 ＝注入面；paseo 同款见 `terminal-paste.ts:27`）。
   对端未声明 2004 时绝不自己发明包装（会糊一屏字面量）。
   CDP 实测：开 2004 → `ESC[200~a\rb ESC[201~`；内联终止序列 → `ESC[200~x[201~y ESC[201~`。

同批修两处：

- **资源监控页永远停在「加载中…」**：删掉 `loadResources` 开头的
  `if (resLoaded && !force) return;`。`go()`（01 分片）是
  `if (page === "resources" && !resLoaded) { resLoaded = true; loadResources(); }`
  ——**先置位再调用** ⇒ 首次进页这道内部闸门必然命中，函数空返回：不发请求、
  不写 hint、控制台零报错。同型 `lpLoaded`/`ghLoaded` 没炸，是因为
  `loadLocalProjects`/`loadGithubRepos` 内部没有这道闸门（进页只由 `go()` 一处把关
  是本仓纪律）。真渲染探针（1440/390 两档）：修前 `cards=0 hint=""` ⇒ 修后
  `cards=15 hint="共 15 个 Agent · 总 CPU 38.1% · 总内存 2290.1 MB"`。
- **hublog 夹具时间炸弹**：`tests/test_hublog.py` 夹具 `created_at` 原写死
  `2026-09-27T05:00Z`，而查询窗口是 `now-24h`（`src/hublog.py::_cutoff_iso`）⇒ 09-29 起
  7 条用例集体转红（`count=0`/`entries=[]`，看着像接口坏），pre-commit 闸门当天恒红。
  改由 `_ago_iso(minutes)` 相对当下取值。

## v0.13.53 — 三项资源/偏好修复

1. **结束进程时目标已不存在视为成功**（不是失败）。`_kill_pid` 把
   `ProcessLookupError`/`FileNotFoundError` 一并当失败，但 `OSError` 里混着
   「进程不存在」与「真的没权限」两种语义，混着判必然误报。改法：进程已不存在
   （kill 时或读 `/proc` 时）返回 `True`；其余 `OSError`/`PermissionError` 仍返回
   `False`。权限与归属判定不动。
2. **收藏/隐藏同步改为「本地为准、首次为空才拉后端」**（`09-local-projects.js` 的
   `lpSyncPrefs()` 与 `10-github-projects.js` 的 `ghSyncPrefs()` 同理）：本地已有数据
   就以本地为准，后台静默推送合并（并集），避免刷新/多端丢失收藏。
3. hublog 夹具时间改为相对当前（写死日期会随日历翻页变红）。

## v0.13.52 — 资源监控页：列出运行中 Agent 进程 CPU/内存，支持一键结束

- **后端**：`src/resources.py` 新增 `/api/resources`（聚合 `/proc` + systemd + docker，
  按 Agent 画像归类）与 `/api/resources/kill`
- **前端分片**：`static/hub/11-resources.js`（懒加载、展开进程详情、双按钮 Kill）
- **主入口**：`static/hub/01-core-boot.js`、`05-chat-and-history.js` 注入懒加载标志与
  `PAGE_LABELS`
- **模板**：`templates/index.html` 新增侧栏按钮、页面骨架、Sprite 图标 `i-book`
- **测试**：`test_ls_guard.py` 纳入 `11-resources.js` 红基线计数；`test_sprite_refs.py`
  校验 `i-book`
- 构建：`scripts/build_hubjs.sh` 产物 `hub.js?v=d5714000`

> 口径边界（README「安全边界」第 1 条同源）：**端口管理只读无 kill**
> （`src/ports.py` 头注释：NAS 上的服务由 systemd/守护方管理，越权杀进程违反本机军规）；
> 可 kill 的只有**本机 Agent 自己的进程**，且必须校 `/proc/<pid>/cmdline` 归属，
> kill 前前端二次确认。两者是不同子系统，别混为一谈。

## v0.13.51 — 漂移按持久化值**写回**（带备份）+ 续聊会话也注入 --model

> 用户 09-28 对 PT-20260928-01 的三项授权全部执行：① 合回主 checkout 上线；
> ② 漂移自动写回；③ 续聊也注入 `--model`。基线 v0.13.50。

### ① 续聊会话也注入 --model（`src/term.py`）

v0.13.41 起只有**新会话**追加 `--model`，续聊刻意不加（怕改坏 resume 语义）。
但续聊命令 `claude --resume <id>` 不带 flag 时读 agent 自己的配置文件 —— 而那份
文件正是被 CCR 改写的那个 ⇒ 「设置里是 Agnes、重启之后又变 qwen」在续聊这条缝上
照样漏。现两条通道都走 `modelcfg.terminal_argv()`（追加在模板末尾，不动
`--resume <id>` 这类位置参数；白名单外的 agent 仍返回 `[]`）。

**逐模板实弹**（探针实例 :3199 + 各家真实历史会话 id，连 WS 读首屏）：
claude / codex / grok / hermes 四条全部 `alive=true` 且首屏是各自的正常 TUI
（Claude Code banner、Codex、Hermes v0.21.4），无 unknown-option 类报错，
用完即 DELETE。

### ② 漂移写回（`src/modelcfg.py` `repair_drift()` + `src/main.py` 巡检）

`drift_report()` 只报不修治不干净：用户在别处直接敲 `claude` 不受 hub 注入影响。
现按持久化值把配置文件写回，**落笔前时间戳备份**（`_backup`），三条护栏：文件
不可写 / 缺失 / 持久化 ID 形状不合法 ⇒ 跳过并记原因，绝不硬写。开关
`HUB_MODEL_DRIFT_REPAIR=0` 可退回 v0.13.50 的只报模式。

**为什么还要定期巡检**：CCR 与 hub 都在开机时启动（今日实测同秒），CCR 改写可能
**晚于** hub 的启动修复 ⇒ 修完又被盖回去。故加 `model_drift_loop()`
（默认 300s，`HUB_MODEL_DRIFT_SWEEP_SEC` 可调）。

顺带修一处备份撞车：`_backup()` 只到秒，同一秒内连续落笔（设置页保存刚过、巡检
写回就跟上来）会让后一份覆盖前一份 ⇒ "留了备份"是假象。撞车即加序号。

**实弹**：探针实例对**真实 HOME** 落笔 —— claude 写回 `agnes/agnes-2.5-flash`
（env 三兄弟 + 顶层 model）、codex 写回 `alibaba/glm-5.3`（CCR 托管块 + 注释行），
各留 1 份备份；写回后 `GET /api/settings/models` 的 `drift` 为空；巡检轮跑过时
无漂移 ⇒ 不再重复落笔（无备份洪水）。

### 闸门

L0 hermetic **765 全绿**（v0.13.50 为 760，新增 5 例 `HRepairDrift`：写回+备份、
无漂移不写、dry-run 零字节、非法 ID 跳过、只读文件跳过）。

## v0.13.50 — 模型设置成为**所有**调用通道的默认值 + 配置文件漂移体检

> 施工会话：52b51a1a。基线：v0.13.49。用户诉求原话：「模型设置里面 Claude 设置为
> Agens 模型，但是新建任务后 Claude 还是 qwen……重启之前新建任务模型是对的，重启之后
> 又不是设置里面设置的模型了，请修复模型配置持久化」。

取证结论：**Hub 侧的持久化没丢，丢的是另外两处**。

- ① **chat 通道从不读那份持久化值**（这是"新建任务还是 qwen"的本体）。
  协同子任务 / 定时任务 / 对话页都走 `_chat_dispatch` → adapter，而 adapter 的
  `default_model` 是**服务启动时**由 config 算出来的常量（`.env` 没配
  `CLAUDE_CHAT_MODEL` ⇒ `registry.py:18` 兜底写死 `"qwen3.8-flash"`）。
  实测：生产实例 `POST /api/agents/claude/chat`（不带 model）打给 CCR 的是
  `requested_model=qwen3.8-flash`，而 `agent_models` 表里存的是
  `agnes/agnes-2.5-flash` —— 设置页改了跟这条链路毫无关系。
  修法：`modelcfg.chat_model(agent_id, requested)` 作为**唯一取值入口**
  （显式请求 > 持久化默认值 > adapter 自兜），`_chat_dispatch` 与
  `/api/agents/{id}/chat/stream` 各接一处（`src/main.py`）。
- ② **CCR 每次启动会把 `~/.claude/settings.json` 的 env 三兄弟改回旧值**
  ——这才是"重启之后就不对了"。今天 07:06:22 CCR 起来、07:06:28 落笔：
  `ANTHROPIC_MODEL / CCR_CLAUDE_CODE_MODEL / CODEXL_CLAUDE_CODE_MODEL` 被写成
  `alibaba/qwen3.8-flash[1m]`，而顶层 `model` **原样留着**（还是 agnes）。
  凡是**不带 `--model`** 的 claude 启动（续聊、用户在别处直接敲 `claude`）因此退回 qwen：
  控制中心由 env 决定，不是由 `model` 决定。
  修法：`modelcfg.drift_report()` —— **只读**体检，把「任一落点与持久化值不一致」
  的 agent 列出来（判据刻意做成"任一不一致即漂移"，不能因为顶层 model 还对就放行），
  经 `GET /api/settings/models` 的 `drift` 字段上屏，并在启动日志里打一行。
  **不自动写回**：`~/.claude/settings.json` 属共享配置受保护面，要不要落笔由用户裁定。

实测（探针实例 :3199 + production DB 副本，两条真实请求经 CCR 记账）：
- 补丁前同样的请求 ⇒ `requested_model=qwen3.8-flash`；
- 补丁后不带 model ⇒ `requested_model=agnes/agnes-2.5-flash`（CCR 请求日志 id 5926）；
- 显式带 `alibaba/qwen3.8-flash` ⇒ 仍是 qwen（显式值优先，未被改动，id 5927）。
启动漂移日志：`claude 持久化=agnes/agnes-2.5-flash 但配置文件里 env.ANTHROPIC_MODEL=
alibaba/qwen3.8-flash[1m]…`；`codex` 也查出一行（持久化 glm-5.3 / 文件现值 qwen3.8-flash）。

闸门：L0 hermetic **760**（零跳过，含新增 6 例）全绿；L1 44 跑出 1 红
（`test_sessions_store` 的 grok 标题用例），**主 checkout 未改动跑同一例同红** ⇒ 与本改动无关。
新增用例：`FChatChannelDefault`（取值顺序三条）、`GDriftReport`（只读体检三例，
含"只改 env、顶层 model 不动"这一 CCR 手法）。

## v0.13.49 — 侧栏历史会话块：4 项读数/几何调整

> 施工会话：49ece82b。基线：v0.13.48。用户诉求原话：「先做一个界面优化 就是 agents
> 菜单里的agent终端会话记录 1.删除历史会话字样 直接显示历史会话8条记录 2.删除会话记录
> 后面的时间 只显示日期 3.历史会话记录左边距与状态图标对齐 这样显示的摘要文字会更多」。

三项都是**同一件事的三个面：240px 侧栏里把「不是摘要的东西」挤出去**。
改前实测（`work/probe/sidebar_hist_geometry.py`，同一份注入数据）：
状态图标左边缘 x=40、历史行文本起点 x=58 ⇒ **左偏 18px**；时间戳宽 86px；
标题 `.hh-t` 被挤到 **0 宽**（`title_w=0`）——即摘要一个字都显示不出来。

- **条数 5→8**：`HIST_LIMIT = 8`（`05-chat-and-history.js`），请求随之变成
  `GET /api/term/history/{id}?limit=8`（后端 `le=20`，不必改）。
- **删标题行**：`histHtml()` 里 «历史会话（N）» 那一行整块删掉（含 `hh-hd` 的 CSS）。
  它宣告的信息（这是历史、共几条）由「行本身就是会话条目」自证；留着只是把 8 条往下顶。
- **时间只留日期**：`hhTime()` 去掉 `HH:MM`，返回 `MM-DD`。挑哪条续聊不靠天数内的钟点，
  分钟级信息仍在该行的 `title` 悬停里。时间戳 86px→39px。
- **左边距对齐状态图标**：`.nav-hist` 的 `padding-left: calc(var(--ico-md) + 6px)`（实测 24px）
  ⇒ **6px**，使历史行文本起点落在 `.nav-item .s-badge` 左边缘上（Δ=0，判据 |Δ|≤1px）。

改后实测（同数据）：`title_w` **0 → 62px**、时间戳 86→39px、Δ=0。
四档宽度扫描 390/1024/1180/1440 无破版（390 收起态按既有规则 `display:none`）。

闸门：L0 754（零跳过）/ L1 44 全绿；`test_hubjs_split` 逐字节相等（`hub.js` 已重建）。
实弹：`work/probe/e2e_sidebar_hist.py` —— **真鼠标点击** agent 行，断言 8 项全 PASS：
请求 URL 含 `?limit=8`、渲染 8 行（真后端）、无 `.hh-hd`、时间戳 5 字符、Δ=0、无失败 note。

## v0.13.48 — 操作 agent 直查日志：`scripts/hublog-cli.py`

> 施工会话：551f6b59。基线：v0.13.47。用户诉求原话：「以后的操作 agent 是否可以
> 直接查询日志，查看操作和错误」。

诉求是把日志面从「人看 UI」扩到「agent 自助取证」：`GET /api/hublog` 本就是只读
端点，但裸 curl 要自己拼口令与端口——口令极易被写进命令历史或子代理回显。

新增 `scripts/hublog-cli.py`（stdlib only，零新增依赖）：自己读 `.env` 取
`HUB_PASSCODE`/`PORT`，口令**只进 `x-hub-token` 请求头**，任何输出都不回显；参数
与页面完全一致（source/level/subject/q/window/limit，`--json` 透传原始响应，
`--list-subjects` 列白名单）。退出码可判别：**2 鉴权 / 3 连不上 / 4 参数非法 /
5 服务端错**，空结果打印「无匹配条目 + 查询条件」——避免子代理把「没匹配」误判成
「接口坏了」。关键字只走 `urlencode`，绝不拼进命令行。

闸门：L0 753（新增 `tests/test_hublog_cli.py` 11 例：env 解析、urlencode、
口令只进头、四种失败码、400 原因透传、HTTPError 响应体可读）/ L1 44 全绿。
实弹：对生产 :3102 跑默认 / 只看错误 / rest+subject / 错口令(2) / 非法 subject(4) /
端口错(3) 六路，退出码与正文均如预期。

## v0.13.47 — 系统菜单「运行日志」页删除，内容并入设置 → 日志

> 施工会话：551f6b59。基线：v0.13.46。用户诉求原话：「把系统菜单里面的运行日志
> 删除，内容合并到设置菜单的日志里面」。
>
> **为什么是「合并」而不是「搬家」**：运行日志挑的是 `profile_events` 里
> `source='rest'` 的三中心检索留痕，而 v0.13.46 的日志中心取的正是同一张表的
> **全量 source** —— 前者是后者的子集。所以不需要第二份取数逻辑，只要给日志中心
> 加一个 `source=rest` 视图 + `subject` 过滤，原页面的能力就一分不少地接住了。
>
> **怎么改的**：
>   - `src/hublog.py`：`source` 取值增 `rest`（= 原运行日志那一份）；新增 `subject`
>     参数，按 `runlog.SUBJECTS` 白名单校验（非法 400，与 `/api/runlog` 同口径）；
>     响应带回 `subjects` 清单供前端填下拉。事件条目的正文原本就带耗时 / 通道 /
>     查询词 / 降级路，rest 视图下这些字段照旧可读。
>   - 前端删四件套：`#page-runlog` section、05 分片 `SYS_PAGES`/`PAGE_LABELS` 里的
>     runlog 项、01 分片 `go()` 里的进页钩子、`static/hub/08-runlog.js` 整个分片。
>   - 设置 → 日志页：来源下拉增「运行日志（三中心检索 · rest）」，新增 `#logSubject`
>     下拉（后端清单填充，只填一次不覆盖用户选择；选到「服务日志」时自动隐藏）。
>   - 保留：`src/runlog.py` 的 `track()` 埋点（7 个端点的装饰器）与 `GET /api/runlog`
>     查询门面（外部/脚本仍可用）。变的只是**唯一 UI 入口**。
>   - 运行日志页的 `before_id` 游标翻页**不迁**：本页 `limit` 上限 500，一次拉够；
>     迁过来要多维护一个「下一页」状态，不划算。
>
> **闸门**：L0 742（新 `tests/test_runlog_merged.py` 11 例反向断言 + `test_hublog.py`
> 新增 `TestRunlogMerged` 5 例）、L1 44 全绿；日志页探针加两项断言——侧栏
> `[data-sys]` 清单里不许再有 `runlog`、切到 rest 来源必须出条目且**只出 rest**
> （实测 63 行、零非 rest 行），1440 / 390 两档全过。

## v0.13.46 — 设置加「日志」子菜单：journald 服务日志 + 操作事件聚合（错误级一键筛选 / 导出）

> 施工会话：551f6b59。基线：v0.13.45。用户诉求原话：「全面收集 agent hub 的
> 操作日志和错误日志」。
>
> **为什么必须两路合起来看**：hub 自己**不落文件**（systemd `StandardOutput=journal`），
> Traceback / 5xx / `[writegate] 401` 只在 journald 里 —— 不看 journald 就永远看不到
> "到底报了什么错"；而"操作日志"是 `profile_events` 全量 source（既有的「运行日志」页
> 只挑 `source='rest'` 的三中心检索留痕，本页与它互补，不是重复造）。
>
> **怎么改的**：
>   - 新增 `src/hublog.py` + `GET /api/hublog`：两路合并成按时间倒序的一条流，
>     支持 `source/level/q/window/limit` 过滤与 `format=text` 导出；零新表、
>     journald 现拉只读。
>   - 鉴权按**写方法判**（照抄 `/api/runlog`、`/api/audit/list`）：日志含 IP/路径/
>     查询词，未配口令 ⇒ 503（fail-closed）、错 ⇒ 401。
>   - `templates/index.html` 新增 `#page-settings-logs`（第四个设置子页，正文出页
>     **无浮层**；复用 `.set-diff` 骨架，不新增 CSS 类与色 token；无 inline onclick）。
>   - `static/hub/05-chat-and-history.js`：`SET_PAGES` 加 `settings-logs`；
>     `01-core-boot.js`：懒加载钩子 + 委托 `log-refresh/log-copy/log-export` +
>     页内口令框（缺口令不弹 prompt，focus 框 + 页内指引）。
>
> **两个实测坑（都是"看着有日志其实取错了"）**：
>   ① `--since` **必须配 `-r`**：带 `--since` 时 journalctl 从窗口起点**正序**读，
>      此时 `-n` 截的是窗口里**最旧**的 N 条（09-27 实测：拿到的是 24h 前那 50 条，
>      最新报错全丢）。② `--since` 收的是**本机时间**，拿 UTC 下界喂进去会偏一个时区。
>
> **闸门**：L0 738（新增 `tests/test_hublog.py` 19 例：级别判定、续行归并、
> 命令必须带 `-r`、关键字绝不进命令行、journald 不可用是数据不是异常、鉴权三态、
> 前端无 inline onclick/无 prompt）；L1 44 全绿；新探针
> `work/probe/e2e_settings_logs.py` 1440/390 两档断言零浮层、零 prompt、页内真出
> 日志行、切来源自动重拉、只看错误有 ERR 行或页内说明、缺口令给页内报错且焦点回口令框。
>
> **顺带修的**：复制在局域网 http（非安全上下文）下 `navigator.clipboard` 直接抛
> `Write permission denied` ⇒ 加 `execCommand` 二级兜底，两级都失败才提示改用「导出 .log」。

## v0.13.45 — 保存按钮不再置灰：一次点击走完「补预览 + 落笔」，口令彻底不走 prompt

> 施工会话：688b689d。基线：v0.13.44。用户第二轮报障原话：「还是无法保存」。
>
> **取证（这轮的关键证据在生产日志里）**：用户手机 192.168.5.99 在 16:28 / 16:30
> 两次进设置→模型页，抓到的请求只有 `GET /api/settings/models` 与 `GET /api/models`，
> **既没有 preview 也没有 apply** —— 前端压根没发请求。原因是保存按钮在预览前是
> `disabled`：**置灰按钮不派发 click 事件** ⇒ 委托收不到、toast 也不弹，全程零反馈。
> 用户选完模型直接点保存，就这样"保存不了"。
>
> **怎么改的**：
>   - `templates/index.html`：`#setApplyBtn` 去掉 `disabled`（只在保存进行中临时禁用防连点）。
>   - `static/hub/01-core-boot.js`：`settingsApplyModel()` 自己补齐前置条件 —— 没选
>     agent / 没选模型给页内指引；没有与当前选择匹配的预览结果就**自动跑一次预览**
>     再落笔（原来要求用户先点「预览变更」）；`settingsPickAgent` 与预览失败处不再
>     把按钮置灰；`finally` 里恢复可点。
>   - 口令**彻底不依赖 prompt**：模型页与 GitHub 页缺口令时一律 focus 页内口令框 +
>     页内文字指引（APP WebView / 部分手机浏览器会吞 `prompt()`，那种环境里弹窗等于
>     静默失败）。GitHub 页新增 `ghRenderError` 走同一口径。
>
> **闸门**：L0 719 / L1 44 全绿；`work/probe/e2e_settings_model_apply.py` 改成走
> **用户真实路径（不点预览直接保存）**，1440/390 两档断言：零浮层、零 prompt、
> 结果块含「已生效」、服务端 `hub_model` 与配置文件真值一致、口令框已清空、
> 保存完按钮恢复可点；新增场景二「口令留空再点保存」断言不弹 prompt 且给页内指引。
> `e2e_settings_model_menu.py` 的旧判据「没预览就禁用」反转成「不许置灰」。

## v0.13.44 — 设置→模型「保存不生效」：结果常驻回显 + 写前可写性预检 + 页内口令框

> 施工会话：688b689d。基线：v0.13.43。用户诉求原话：「设置菜单 agent模型设置后
> 保存并不能生效 请修复」。
>
> **取证结论（先证后修，不猜）**：保存链路本身是通的 —— 实弹 `POST
> /api/settings/model/apply` 对 claude / jcode / codex / hermes 都真的落了盘
> （配置文件与 Hub 侧 `agent_models` 同步变）。「不生效」是**三个可观测缺陷叠加**：
>   ① **保存成功但页面不回显**：状态行（当前 / hub 侧）、下拉、diff 区全部停在
>      保存前的样子，唯一反馈是一条 4.2 秒就消失的 toast ⇒ 用户据此判定没生效；
>   ② **失败同样只用 toast 说**：目标文件不可写（`chattr +i`，本机
>      `~/.pi/agent/settings.json` 实测就是）时返回 500「写入失败已回滚」，且
>      **备份已经先落了一堆**，用户目录里留下没用的副本、配置却没改成；
>   ③ **点保存先弹一个无关的「请输入终端鉴权 TERM_TOKEN」**：这两个设置端点只认
>      HUB_PASSCODE（已在 `writeauth.EXEMPT_PREFIXES` 登记），索 token 纯属多余弹窗；
>      而 APP 内嵌 WebView 会**直接吞掉 prompt**（返回 null）⇒ 端侧等于静默失败。
>
> **怎么改的**：
>   - `src/modelcfg.py`：`apply_model` 落备份**之前**先 `os.access(p, os.W_OK)` 预检，
>     不可写 → 409，错误信息带 `lsattr` / `chattr -i` 解除办法；`preview` 的 files
>     段新增 `writable`，预览阶段就把「这个文件写不动」摆出来。
>   - `static/hub/01-core-boot.js`：新增 `settingsRenderApplied` / `settingsRenderError`
>     —— 成功把「已生效：agent → 模型（含新开终端追加的 argv）+ 每个文件的变更与备份
>     路径」**常驻**写进 `#setDiffBox`，失败把原因与 HTTP 状态常驻写进同处；
>     `settingsRefreshMeta()` 用**重载后的真值**刷新状态行（不拿入参糊一个）；
>     `api()` 支持 `noToken`，模型/GitHub 写端点不再索 TERM_TOKEN；
>     `settingsPasscode()` 改成三源：页内口令框 → 本机缓存 → prompt（末源保留但不依赖）。
>   - `templates/index.html`：模型页与 GitHub 页各加一个 `type=password` 的页内口令框
>     （不是浮层，不违反浮层唯一三条），保存成功后清空，明文不留页面。
>
> **闸门**：L0 719 全绿（新增 2 例：`test_apply_refuses_unwritable_file_before_making_backups`
> 钉死「写不动就不许落备份」、`test_preview_reports_writability`）；L1 44 全绿；
> 新探针 `work/probe/e2e_settings_model_apply.py`（1440/390 两档）把保存这一步也走完，
> 断言：零浮层、**零 prompt**、结果块含「已生效」、状态行含新模型、服务端
> `hub_model` 与配置文件真值一致、口令框已清空。
>
> **取证副作用已回滚**：取证过程中改过的 jcode / codex / hermes 配置与 Hub 侧三行
> 已恢复取证前原值（claude 那行是用户原有的，保留），`*-hub-modelcfg-*` 中间备份已清。

## v0.13.43 — 设置从「右侧抽屉」改成左侧手风琴第四组：模型 / GitHub / 终端口令

> 施工会话：551f6b59。基线：v0.13.42。用户诉求原话：「设置菜单不是弹页面，
> 也要手风琴一样的下拉子菜单」。
>
> - **怎么改的**：`设置` 不再是侧栏底部常驻按钮 + 右边 400px 抽屉，而是 `#navTree`
>   里的**第四组手风琴**（AGENTS / 基础设施 / 系统 / 设置），三个子项与系统页
>   **同口径**：`data-sys` ⇒ 侧栏委托 ⇒ `go(page)` ⇒ 正文出页（窄屏顺带收侧栏）。
>   三个子项各是一个 `section.page.sys`，外套 v0.13.40 的统一骨架 `.sp/.sp-card`。
> - **顺带拆掉的**：`#settingsDrawer` 整个浮层、`#btnSettings` 的 `data-settings`
>   入口、子页 tab（`.set-tabs/.set-tab`）、`openSettings/closeSettings/settingsTab`、
>   Esc 里的 `closeSettings()`、`OVERLAY_IDS` 里的 `settingsDrawer`。
>   **09-23 事故正身（手机上浮层盖掉 92% 且无逃生路径）在设置这条路径上不再存在。**
> - **纪律照旧**：终端口令页的 5 个按钮从 inline onclick 改成 `data-settings-act`
>   走同一个委托；子页选择不落 localStorage（进哪页由点击决定）。
> - **闸门**：L0 717 全绿（改了 `tests/test_overlay_exclusion.py` 与
>   `tests/test_ghsettings.py` 的静态断言，钉死「抽屉不许回来」）；
>   `tests/verify_overlay_exclusion.py`（L1 真鼠标）重写成手风琴路径；
>   两个 `work/probe` 真渲染探针 1440/390 两档全过（零浮层、正文可点、导航收场）。

## v0.13.42 — 设置新增「GitHub」子菜单：远程地址 / key / 归属 / 克隆落点不再硬编码

> 施工会话：551f6b59。基线：v0.13.41。改动文件：新增 `src/ghsettings.py`、
> `tests/test_ghsettings.py`（27 例，L0 hermetic）、`work/probe/e2e_settings_github_menu.py`
> （真渲染取证）；改 `src/githubprojects.py`（去硬编码）、`src/main.py`（挂路由 + VERSION）、
> `src/writeauth.py`（写闸豁免登记）、`tests/test_writeauth.py`（豁免基线 4→8 并交代理由）、
> `templates/index.html`（第三个子页 + CSS）、`static/hub/01-core-boot.js`（子页逻辑）、
> `static/hub.js`（重建）。
>
> - **用户诉求**：「设置里加 GitHub 项目的地址和 key 的设置 —— GitHub 项目就不需要硬编码，
>   可以灵活设置远程仓库和操作远程仓库」。
> - **三条口径（用户 09-27 裁定）**：① **Hub 服务端 DB 优先**（`github_settings` 表，
>   DATA_DIR 内，重启仍在），可选「同时回写 `github.txt` / `.env`」（默认关，落笔前时间戳
>   备份）；② 操作范围 **只读 + 克隆**（测试连接 / 列仓库 / 单仓核对 / 克隆），不新增远端写；
>   ③ 地址 **任意 https 主机均可**（含自建 GHES `https://git.example.com/api/v3`），
>   但**拒绝明文 http、内网/回环/链路本地、URL 内嵌凭据** —— 否则填错一个地址就等于
>   把 key 明文发到内网任意主机（SSRF + 凭据外泄同案）。
> - **读取顺序**：DB（本页设置）→ 环境变量（`GITHUB_API_BASE`/`GITHUB_HOST`/`GITHUB_OWNER`/
>   `GITHUB_CLONE_BASE`/`GITHUB_TOKEN`）→ 内置默认。`githubprojects` 的四个取值函数
>   （`api_base()/git_host()/clone_base()/owner()`）**每次现读** ⇒ 改完立即生效、不用重启；
>   模块常量仍保留为兜底与既有 L0 的 patch 接缝。
> - **key 只写不读**：响应、日志、diff、备份文件名里只有掩码（`ghp_************x8ea`）
>   与来源（db/env/file）；上游错误正文一律过 `tdai_client.scrub(text, token)`
>   （`ghp_` 不在 `_KEY_PATTERNS`，只按位置替换才杀得掉）。
> - **四个写端点**（`test/apply/clear/refresh`）一律自带 HUB_PASSCODE：错→401、未配→503
>   （fail-closed）；已在 `writeauth.EXEMPT_PREFIXES` 登记理由（不叠 token 门，否则
>   用户在设置页永远改不动地址与 key＝自锁死）。
> - **验证**：L0 703 + L1 44 全绿、prepush 六闸全绿；影子实例（假 HOME + 端口 3199）
>   实弹——试连真 GitHub 拿到 `login=gztxt`、scope `repo,workflow,write:packages`、
>   配额 5000；apply 改落点 ⇒ `/api/github/repos` 立刻按新落点走；refresh 真拉到 72 个
>   仓库（本地命中 30）；clear 干净回落；真渲染探针（1440/390 两档）断言全过。

## v0.13.41 — 设置新增「模型」子菜单：选 agent → 选 CCR 模型 → 预览 → 口令落笔

> 施工会话：551f6b59。基线：v0.13.39。改动文件：新增 `src/modelcfg.py`、
> `tests/test_modelcfg.py`（25 例，L0 hermetic）、`work/probe/e2e_settings_model_menu.py`
> （真渲染取证）；改 `src/main.py`（挂路由 + VERSION）、`src/term.py`（会话拉起注入
> `--model`）、`src/writeauth.py`（写闸豁免登记）、`templates/index.html`
> （设置抽屉加子菜单 + CSS）、`static/hub/01-core-boot.js`（模型子页逻辑）、
> `static/hub/06-manager-tasks.js`（委托挂在启动尾）、`static/hub.js`（重建）。
>
> - **用户诉求**：「设置菜单还是空的，加个模型子菜单 —— 先选 agent，再选 CCR 的模型，
>   把所有 agent 的模型设置统一在设置里」。
> - **三条口径（用户 09-27 裁定）**：① **双写**——Hub 侧存 per-agent 模型（拉起终端时
>   按白名单注入 `--model`）+ 同时写该 agent 自己的配置文件；② **CCR 网关 Router
>   五场景只读展示、不写**（三方互斥军规保护面，且 ccr 运行中写 config.json 会被运行态
>   覆盖）；③ 写前预览 + 口令（HUB_PASSCODE）+ 时间戳备份三件套。
> - **落点（本机实测，非推断）**：claude `~/.claude/settings.json`（model +
>   env.ANTHROPIC_MODEL / CCR_CLAUDE_CODE_MODEL / CODEXL_CLAUDE_CODE_MODEL）、
>   jcode `[provider].default_model`、codex CCR 托管块 `model`、pi `defaultModel`
>   +`defaultProvider`（模型不在清单则补进 `models.json`）、grok `[model.ccr-hub]`
>   命名块 + `[models].default`、hermes `model.default/provider`。
>   codebuddy / qwenpaw **不可设置**（前者 `--model` 只认自有清单 hy4-preview 等，
>   与 CCR 的 provider/model ID 不通用；后者纯 Web 型）⇒ 设置页里置灰并给理由。
> - **为什么不做通用文件编辑器**：各家字段形状不同，通用写手必然退化成整文件重写
>   —— 09-07 pi 改 CCR 配置把同文件其它 profile 一起改坏就是那类事故面。这里是
>   **逐家白名单 + 定点行编辑**：jcode 的 `providers.deepseek-openai.default_model`、
>   codex 的 `approval_policy` 等无关键实测零改动。
> - **凭据不硬编码**：grok 新块的 `api_key` 复用文件里已指向 CCR 的那把，回落才读
>   CCR `config.json` 的 `APIKEY`；pi 新建 provider 同理。
> - **验证**：L0 676 + L1 44 全绿、prepush 六闸全绿；探针实例（假 HOME + 真 HTTP）
>   五家 agent 逐个落笔成功且各留一份备份；真渲染探针（1440 / 390 两档）断言浮层唯一、
>   默认落在「模型」页、选 agent 出 17 个模型、预览出 4 行 diff、遮罩/关闭按钮关得掉。

## v0.13.40 — 系统子菜单页：删页顶标题/分割线 + 十页统一骨架重排

> 施工会话：add6797a。基线：v0.13.39。改动文件：`templates/index.html`（系统页骨架
> CSS 段 + 10 个 `<section class="page sys">` 重写）、`static/hub/06-manager-tasks.js`
> （`renderPageCrumb` 只清空、新增 `mountPicks` 并在 boot 挂载）、
> `static/hub/04-terminal-ws.js`（`instTo` 重写后重挂芯片、`ctxPanel`/`runPanel`
> 显隐不再写死 block）、`static/hub/05-chat-and-history.js`（mcp server 目标列
> nowrap+title）、`static/hub/07-asset-panel.js`（健康徽标容器不拉伸）、
> `static/hub.js`（构建产物，md5 4674032c）、`tests/test_sys_pages_layout.py`
> （新增 14 例，L0 hermetic）、`src/main.py`（VERSION）。

- **用户报障（两条，同一处落点）**：①「系统菜单里的子菜单点进去，右边内容框顶部的
  标题和分割线都要删除」；②「右边页面的排版都要优化，实在是乱七八糟的 —— 每个
  子菜单的右边内容框页面都要优化和重新设计」。
- **①的真因不是"某个页面多写了标题"**：页顶那行是 `#opBar`（crumb + opTabs + 一条
  `border-bottom`），而全站唯一往 `#crumb` 写字的地方是 `renderPageCrumb()`；只要它
  不写字，`syncOpBar()` 判 void ⇒ 整条 opBar `display:none`，标题与分割线一起消失。
  所以改的是**唯一的写入点**，不是 10 个页面各自的 DOM —— 改一处即全站（含总览页）
  生效，也不留"以后新加页又冒出标题"的口子。`chat`（实体工作台）必须早退：它的顶栏
  由 `renderModeBar()` 接管，两边互写会打架（既有约定，本次保留）。
- **②的真因是"每页自搭一套"**：10 个系统页各写各的 `.panel` / 裸 `div`，于是卡头
  高度、内边距、滚动归属、长列表裁切全都不一样 —— 这正是"乱七八糟"的成因，也是
  那种"单看一页没毛病、连着点就跳"的观感来源。修法不是逐页调像素，而是**先立骨架再
  迁移**：`.sp`（页级纵向流）→ `.sp-card` → `.sp-hd`（卡头，右侧 `.sp-r` 放 hint/按钮）
  → `.sp-tools`（过滤条）→ `.sp-bd`（正文，`flush` 去内边距、`box` 限高滚动）→
  `.sp-note`（只读脚注），多块异质内容用 `.sp-grid`（`auto-fit/minmax`）拆 `.sp-cell`。
  10 页全部迁完，系统页内不再出现 `.panel`。
- **滚动归属只能二选一（实测踩过）**：单表/单列表页走**盒级**（表格进
  `.sp-bd.box.flush.tscroll`），盒是滚动容器 ⇒ `thead th{position:sticky}` 才生效；
  多块异质页走**页级**（`section.page.sys{overflow-y:auto}`）。第一版做反了
  （页级滚动 + 卡片 `overflow:hidden`），CDP 探针实测 `thTopAfter=-235` —— 表头根本
  没钉住，滚两屏就不知道列是什么。改盒级后复验 `thTopBefore===thTopAfter===165`、
  `scrollHeight 4550 / clientHeight 560`。
- **不新增断点的代价由 CSS 自己扛**：`.sp-grid` 初版给了 `.two/.three` 固定列数变体，
  窄屏覆盖写在 `@media(max-width:1100px)` 里 —— 而变体定义在该规则**之前** ⇒ 被"后
  定义的固定列数"覆盖，窄屏静默不塌列（闸门全绿但页面是错的，典型的静默失效）。修法：
  删变体，统一 `repeat(auto-fit, minmax(280px,1fr))`，1100px 断点恢复成只管 `.mem-grid`。
  `@media` 档位仍是白名单那 5 档（`test_no_new_breakpoint_added` 钉着）。
- **多选源改芯片，但数据源没换**：`memFedSrcs`(11) / `kbRoutes`(5) / `instTo`(7) 三处
  `<select multiple>` 是用户报的"选择框越出卡片下边框压住下面那行"。新增
  `mountPicks()` 只挂一层**可视芯片壳**：点击写回原生 `option.selected` 并派发 change，
  原生 select 加 `.picks-src` 隐藏。真源仍是 option 清单与 `selectedOptions`
  （`test_center_ui_l0` 还按 `<option value=…>` 逐个校验）⇒ 不许谁把它换成自造状态。
  `mountPicks` 幂等（`instTo` 的 option 每次 `loadSkills()` 都被重写，必须重挂）。
- **两处显隐写死 `display:block` 的连带伤**：`ctxPanel` / `runPanel` 现在是 flex 列，
  写死 block 会把它们打回块级 ⇒ 卡片内排版错位。改为 `style.display = ''`（让 CSS 的
  列布局说话）。探针复验 `ctxDisplay:"flex"`、DAG 4 节点、任务表 3 行。
- **迁移期零增删**：10 页 DOM 的 `id="…"` 集合前后 `comm` 比对一致（`homeChips` /
  `hTime` 是既有 JS 引用残留，非本次引入）⇒ 所有既有 JS 取元素与静态闸门的取位不变。
- **验证**：L0 hermetic **671/671 零跳过**（含新增 14 例）；`bash scripts/build_hubjs.sh`
  重跑两次产物一致。CDP 探针（生产 :3102）：10 页宽屏 `overflowX` 全 0；390×844 窄屏
  页面级 `overflowX` 全 0，横向滚动只发生在 `tscroll` 盒内（ports 475 / mcp 152 /
  tasks 287 / jobs 322 / runlog 182）；芯片壳 `chips 11 / options 11 / on 9 /
  selected 9 / select display "none"`，点第 4 个芯片后 `pi_sessions` 入选、
  `selectedNow` 变 10；联邦检索 10 条、kb 检索 12 条、skills 61 行、instTo 7 芯片；
  `window.__errs` 为空。
- **⚠ 本次没做的**：`tests/verify_asset_panel_live.py` 在 :3102 上三分钟无输出（与其
  launch_chrome 默认 390×844 及端口约定有关，未深追），改以自写等价窄屏探针复验同款
  判据（`{"w":326,"h":760,"inside":true,"overflowX":0,"cols":1}`）；**视觉校验无法做** ——
  本会话模型不支持读图，截图拿不到 ⇒ 上述结论全部建立在几何量/DOM 量探针 + 静态闸门上，
  不是"看着顺眼"。

## v0.13.39 — 修「选 pi 起会话 ⇒ 终端一屏 JS 堆栈」（子进程 PATH 前置 nvm node bin）

> 施工会话：688b689d。基线：v0.13.38。改动文件：`src/term.py`（新增纯函数
> `child_env()`，Session 子进程 env 改走它）、`src/profiles.py`（新增
> `extra_path_dirs()`，复用既有的 `_nvm_bins()`）、`src/main.py`（VERSION）、
> `tests/test_term_child_env.py`（新增 5 例，L0 hermetic）。
>
> - **用户报障**：候选框选 pi → 新建任务 → 终端出错（一屏 JS 源码）。
> - **真因（不是 pi 坏了、也不是会话起不来）**：`pi` 的 shebang 是
>   `#!/usr/bin/env node`。hub 跑在 systemd 单元里，`PATH` 不含 nvm ⇒ 内核把
>   **系统 node v20.20.2** 交给它；而 pi v0.85.1 的 bundle 用了 `node:fs` 的
>   `globSync`（Node 22+ 才有）⇒
>   `SyntaxError: The requested module 'node:fs' does not provide an export named 'globSync'`
>   启动即崩，`Node.js v20.20.2`。交互 shell 里 PATH 含 nvm（v24.18.0）⇒ 一切正常，
>   **所以「本机跑没事、hub 里必崩」**。
> - **为什么 v0.13.38 的 `which()` nvm 兜底不够**：那一层只保证「hub 找得到 pi 这个
>   **文件**」，管不到「pi 起来之后自己再找**解释器**」。两层必须都补：文件解析在
>   `profiles.which()`，解释器解析在子进程 `PATH`。
> - **修法**：`term.child_env()` 把 `~/.nvm/versions/node/*/bin`（新版优先）前置到
>   子进程 PATH。只补**会话子进程**、不动服务进程自身 PATH、不改 systemd 单元配置
>   （与前版同口径：改 systemd 属共享配置面，需另行授权）。
> - **验证**：以服务真实 PATH 起实例实弹 —— 修前 pty 输出即 SyntaxError（10 行短堆栈），
>   修后 175349 字节、无 SyntaxError、TUI 状态栏正常渲染且 `alive=true`（与交互 shell
>   下启动完全一致）；codebuddy 回归同样正常。L0 hermetic（新 5 例）+ L1 host + prepush 六闸。

## v0.13.38 — 两项目页 agent 候选框补 pi 与 codebuddy（Web 型卡也可按目录起会话）

> 施工会话：688b689d。基线：v0.13.37。改动文件：`src/profiles.py`（pi 加
> `terminal.cmd=pi`；codebuddy 加 `terminal.cmd=<WorkBuddy 包内绝对路径>`；
> 新增 `CODEBUDDY_CLI` 常量，可用环境变量覆盖）、`src/main.py`（VERSION）、
> `tests/test_profiles_codebuddy.py`（重写为 8 例：含「候选框口径」测试类）。
>
> - **用户报障**：本机项目 / GitHub 项目页的 agent 候选框少了 pi、QwenPaw、codebuddy。
> - **根因不是「会话起不来」**：候选框过滤器 `09-local-projects.js:165` /
>   `10-github-projects.js:205` 只收 `entries` 含 `term` 的卡片；这三张是 Web 型画像
>   （embed+新窗口），没有终端入口 ⇒ 被过滤。实弹反证：补入口后
>   `POST /api/term/sessions {agent_id: pi|codebuddy, cwd: 项目目录}` 均返回 `alive=true`。
> - **处置**：有 CLI 的补终端入口——pi（v0.85.1，默认交互 TUI，在 nvm bin 下）、
>   codebuddy（2.137.1）。**qwenpaw 无 CLI（`which` 落空）⇒ 保持仅原生界面。**
> - **codebuddy 的 cmd 必须是绝对路径**：裸名 `which` 落空（二进制只在 WorkBuddy 包内），
>   `shutil.which` 对带目录分隔符的路径原样返回，term.py 的 `which(cmd[0])` 才走得通。
> - **原生界面不受影响**：形态优先级 `embed > term > chat`，两卡 entries 顺序仍
>   `embed, open, term, detail` ⇒ 点卡片默认仍进 :30141 / :35431 原生界面。
> - **⚠ 上线后追修（同一版内）**：给 pi 加终端入口后，**pi 卡片整个消失**——
>   服务进程的 `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`
>   **不含 nvm**，而 `pi` 只装在 `~/.nvm/versions/node/v24.18.0/bin` ⇒ 生产
>   `which('pi')` 落空 ⇒ vitals 判 not_installed ⇒ 卡片被拦（交互 shell 里能
>   which 到，纯属 PATH 假象，`09-27` 上线后实测抓到）。修法：
>   `profiles.which()` 兜底目录增补 `~/.nvm/versions/node/*/bin`（新版优先），
>   只扩搜索目录、不改进程 PATH、不动 systemd 配置。闸门
>   `tests/test_which_nvm.py`（把 PATH 换成服务的真实值再断言 `which('pi')`
>   命中，彻底堵死"shell 里能跑就行"的假绿）。
> - **验证**：L0 hermetic 652/652 **零跳过** + L1 host 44/44 + prepush 六闸；实弹
>   pi/codebuddy 会话 alive=true（cwd 生效）并在测后删除；qwenpaw 仍按预期
>   400「无终端入口」；**以服务 PATH 起实例复验**：pi 卡片 running/usable 并在
>   候选框内（修前该卡整体消失）。

## v0.13.37 — Agents 菜单补 CodeBuddy Code 卡（`codebuddy --serve` 原生遥控界面 :35431）

> 施工会话：688b689d。基线：v0.13.36。改动文件：`src/profiles.py`（唯一功能改动：
> 新增 codebuddy 服务型 Agent 画像——`port=35431` + `ui=http://127.0.0.1:35431`，
> 无 cli/terminal ⇒ vitals 按 web-service 形态以自有端口应答为存在证据；
> detect 正则 `(^|/)codebuddy( |$)` 命中 `node …/cli/bin/codebuddy --serve …`）、
> `src/main.py`（VERSION）、`tests/test_profiles_codebuddy.py`（L0 4 例：形态/
> loopback/入口/进程识别）。
>
> - **入口=原生界面**：与 pi/qwenpaw 同形态（嵌入会话+新窗口+详情），不提供 pty
>   终端入口——`embed > term > chat` 的形态优先级下，加 terminal 会顶掉默认 embed。
> - **为什么 `cli` 必须留空**：`codebuddy` 二进制只在 WorkBuddy 包内
>   （`/opt/WorkBuddy/resources/app.asar.unpacked/cli/bin/`），不在 PATH；
>   写成 CLI 型会被 vitals 判 not_installed 整卡被拦。
> - **UI 写 loopback**：前端 `lanUrl()` 按访问主机名改写，LAN/Tailscale/APP 三
>   origin 各自可达；写死 IP 会跨网失效。
> - **验证**：L0 hermetic 648/648 零跳过 + L1 host 41/41；`/api/agents` 端点级
>   确认卡片（status=running, verdict=usable, entries=embed/open/detail）；真渲染
>   取证（headless chromium 1440×900 实拍 + Page.getFrameTree）：点击后 iframe
>   1166×596 加载 `:35431` 原生登录页，侧栏卡片在位（截图
>   `work/probe/render-codebuddy-embed.png`）。远端界面自身要求 Password（serve
>   启动时打印），属其原生行为，不经 hub。

## v0.13.36 — 两项目页收藏/隐藏落服务端（跨浏览器/端侧一致）

> 施工会话：01a0dc26（接续 09-26 晚 429 中断的前会话，会话检索续做）。
> 基线：v0.13.35。改动文件：`src/db.py`（app_prefs KV 表）、`src/prefs.py`
> （新模块：GET/PUT /api/prefs/{key}，键白名单 projects.lp/gh，值形状硬顶，
> PUT 走全局 write_gate + 显式 writeauth.decide 双保险，审计 setting/update）、
> `src/main.py`（挂路由 + VERSION）、`static/hub/09-local-projects.js` 与
> `10-github-projects.js`（lpSyncPrefs/lpPushPref 与 gh 对称四件：载入拉后端
> 偏好为准、切换回写、localStorage 降级为离线兜底）、`static/hub.js`（build
> 3681 行 md5 cb30ec50）、`templates/index.html`（?v= 提手同步）、
> `tests/test_prefs.py`（新 14 例）。

### v0.13.36 交付（上会话中断点：收藏/隐藏只存浏览器 localStorage，多端各自一套存档）

- **问题**：localStorage 按 origin 隔离——局域网 IP / Tailscale / 手机 WebView
  各一套存档，桌面标了收藏手机看不到，多端使用时状态必然分叉。
- **方案**：服务端 `app_prefs(key,value,updated_at)` 表，键白名单仅
  `projects.lp`/`projects.gh` 两枚（不是自由 KV）；值 `{"stars":[], "hidden":[]}`
  normalize 去空/去重/截断（串 512/条 2000）；坏行按无偏好处理不 500。
- **前端**：载入列表后拉一次后端偏好（命中即为准并回写 localStorage），行内
  收藏/隐藏切换后 fire-and-forget PUT；后端未升级（404）或离线时静默沿用
  本机存档——v0.13.32 单机语义完整保留，失败 toast 只提示一次。
- **验证**：L0 hermetic 全绿零跳过（含新 test_prefs 14 例：键白名单 404、
  normalize 截断/封顶、读写往返、坏行降级、匿名 PUT 401、审计 actor、
  前端四件与调用点钉死）；hermetic-clean 同绿。
- **顺带修既存环境红**：`scripts/run_tier.py` 的 fake-HOME 原落在 /tmp，
  被 v0.13.31 P1 的 `/tmp` 前缀剔除闸整段排除 ⇒ hermetic-clean 12 例时绿时红
  （随 TMPDIR 漂移）。假 HOME 改落 `~/hub-l0test-fixtures/` 下，与其它 L0
  夹具同域，hermetic-clean 恢复 644/644 稳定绿。
- **顺带修 L1 实况精度红**：`_merge_cloudcli` cc-only 分支的派生 worktree
  判定对照集合错用扫描根 ROOTS，应为**已收录 git 仓**（by_path）——
  CloudCLI 在 worktree 里开过会话后该路径以 cc-only 形态漏回主列表
  （/home/gztxt/agent-hub-wt-01a0dc26 实测泄漏）。修复后 42 项零泄漏。
- **顺带修 test_prefs 环境耦合**：write belt 用例改在 setUp 强钉
  TERM_TOKEN/清 HUB_PASSCODE——同进程全量跑时其它用例改写环境变量会造 401 假红。

## v0.13.35 — 勾选框宽度真因：.toolbar input 拉伸规则误命中 checkbox

> 施工会话：本会话。基线：v0.13.34。改动文件：`templates/index.html`
> （`.toolbar input` 两条拉伸规则加 `:not([type=checkbox])`）、`src/main.py`
> （VERSION 0.13.35）、`tests/test_localprojects.py`（新钉子
> test_toolbar_checkbox_not_stretched）。CSS 内联于模板，HTML 本就 no-cache
> ⇒ 用户刷新即生效，不依赖构建产物。
> 验证：L0 hermetic 630/630 零跳过；headless dump-dom 实测 lpHidden/ghForks/
> ghHidden 三个勾选框渲染宽 13px（原 180/140px 撑块）、flex:0 min-width:auto。

### v0.13.35 交付（用户需求「勾选框还是没与文字紧贴，应该是勾选框宽度太大」）

- **真因（用户诊断正确）**：不是 gap，是宽度——`.toolbar input { flex:1;
  min-width:180px }`（v0.13.34 为搜索框拉伸写的规则）命中了工具栏里**所有**
  input，checkbox 也被撑成 180px 宽块、文字被推到框外 ⇒ gap 设 0 也不紧贴。
  修复：基础 180px 与窄屏 140px 两条拉伸规则都加 `:not([type=checkbox])`
  排除；checkbox 回归浏览器自然尺寸（实测 13×13px，flex:0 min-width:auto）。
  上版 gap 0 的改动保留（两者配合才紧贴），此版是根因修复。

## v0.13.34 — 项目页布局批：顶栏清空 + 满高列表 + 勾选框紧贴

> 施工会话：本会话。基线：v0.13.33。改动文件：`static/hub/06-manager-tasks.js`
> （renderPageCrumb 对两项目页清空 ⇒ opBar 收起）、`templates/index.html`
> （两 panel flex 满高 + 列表 flex:1 + 勾选 label gap 0 + margin-bottom 0）、
> `src/main.py`（VERSION 0.13.34）、`static/hub.js`（build 3604 行）。
> 验证：L0 hermetic **629/629** 零跳过；L1 host 41/41；L2 布局探针
> opBar void(h=0)/列表 flex 满高/滚到底最后一行可见/gap=0px/零 JS 错。

### v0.13.34 交付（用户需求「两项目页顶部标题和分割线删除；页尾项目看不到；勾选框与文字紧贴」）

- **顶部标题+分割线**：renderPageCrumb 对 localprojects/github 清空 crumb ⇒
  syncOpBar 判 void ⇒ 整条 opBar 收起（高度归零）——上一版只删了面板内
  <h3>，opBar 里还留着一条「本机项目」+底边线，这就是用户仍看到的标题
  和分割线。其余页面面包屑照旧。
- **页尾项目可见**：原列表 max-height:calc(100vh - 320px) 是拍脑袋常数，
  窄视口下把面板底推出屏外。改为结构化高度：panel flex:1 + min-height:0
  （吃满 section.page 的剩余高度）、列表 flex:1 + min-height:0 + overflow-y、
  meta flex:none——列表永远精确止于视口底，页尾行滚必可达。
- **勾选框紧贴文字**：三个 label（lpHidden/ghForks/ghHidden）gap 4px→0，
  加 flex:none 防 toolbar 收缩挤压。

## v0.13.33 — GitHub 清单缓存裁定：拉一次永久缓存 + 选中单仓核对

> 施工会话：本会话。基线：v0.13.32。改动文件：`src/githubprojects.py`
> （LIST_TTL 300→0 永久缓存 + 新 GET /api/github/sync 单仓核对 +
> _gh_head/_local_head）、`static/hub/10-github-projects.js`（ghSelect 触发
> ghSyncCheck + 缓存龄期文案）、`src/main.py`（VERSION 0.13.33）、
> `tests/test_github_projects.py`（缓存永久钉子改写 + TestSyncEndpoint 6 例）、
> `static/hub.js`（build 3594 行）。
> 验证：L0 hermetic **629/629** 零跳过；L1 host 41/41；生产实测缓存命中
> 1505ms→98ms、切页/刷新零重拉、选中触发单仓核对。

### v0.13.33 交付（用户需求「github 仓库拉取一次后本地缓存，不要每次拉取浪费资源；只有选中后进入编辑状态之前才再次拉取同步；总目录手动刷新」）

- **列表缓存改为永久**：原 5 分钟 TTL 作废（LIST_TTL_S=0）——服务端拉一次
  后内存缓存永久有效（重启自然清空）；进页/过滤/收藏/隐藏/切页/刷新页面
  都不重拉（命中缓存 ~98ms vs 真拉 ~1.5s）；**「刷新」按钮（force=1）是
  唯一整表重拉入口**（用户裁定「总目录是手动刷新」）。
- **新增 GET /api/github/sync 单仓核对**（「选中后进入编辑状态之前同步」的
  后端半程）：前端 ghSelect 时打一次——比对本地 HEAD（纯文件读 .git/HEAD
  → refs，不起 git）与远端 HEAD（1 次 git ls-remote）；SHA 一致 = synced
  （✓ 提示）、不同 = diverged（⚠ 中性提示「可 pull/push 对齐」——单值比对
  无法判谁新，本地常是未 push 的新提交如 agent-hub，绝不误指 git pull）、
  本地无 = absent、取不到远端 = unknown（不是失败）。结果只作 #ghMeta 一行
  提示，不打断不开弹窗；核对有 ghSyncBusy 防抖。
- **明确不做**：进页自动重拉、定时后台同步任务、整表 HEAD 逐仓比对
  （72 仓 × ls-remote = 浪费，正是用户点名要砍的）。

## v0.13.32 — 项目页交互批：去面板标题 + 行内收藏/隐藏

> 施工会话：本会话。基线：v0.13.31。改动文件：`templates/index.html`（两项目页
> 去 <h3> 大标题 + lpHidden/ghHidden「显示隐藏」开关 + i-star sprite）、
> `static/hub/09-local-projects.js` + `static/hub/10-github-projects.js`
> （行内收藏/隐藏图标、收藏置顶、隐藏过滤、localStorage 持久）、
> `static/hub/01-core-boot.js`（LP/GH/四集合初始化前置——TDZ 真事故修复）、
> `src/main.py`（VERSION 0.13.32）、`tests/test_localprojects.py` +
> `tests/test_github_projects.py`（收藏/隐藏四件套钉子）+
> `tests/test_tdz_order.py`（新钉子：项目状态初始化须早于 bootstrap go()）、
> `static/hub.js`（build 3558 行）。
> 验证：L0 hermetic **623/623** 零跳过；L1 host 41/41；L2 真鼠标快探 8/8
> （标题已删/收藏置顶+刷新持久/隐藏消失+勾选回列/零 JS 错）。

### v0.13.32 交付（用户需求「删两项目页顶部标题和分割线；每个项目后加收藏和隐藏图标，收藏排最前，隐藏后不显示除非勾选顶部显示框」）

- **去标题**：两项目页删 <h3>（面包屑已示页名，标题是重复装饰）。
- **收藏（★）**：行尾星图标（新 i-star sprite + 既有 .act-btn 样式）；点后
  置顶（多条保持原相对序，concat 稳定排序）+ 行首 ★ 高亮；localStorage
  持久（键 hub.lp.stars / hub.gh.stars，走 lsGet/lsSet 守卫），再点取消。
- **隐藏（eye）**：行尾眼图标；点后从列表消失（隐藏选中项顺带解除选中）；
  勾选顶部「显示隐藏」开关才回列（回列行半透明 45% 以示状态）；
  localStorage 持久（hub.lp.hidden / hub.gh.hidden）。
- **修真 TDZ 事故（探针抓红）**：刷新回项目页（hub.page 恢复路径）时，
  06 顶层 go() 同步调 loadLocalProjects()，而 LP 的 var 初始化在 09 分片
  顶层（拼接序在 06 之后）⇒ LP.length 抛 TypeError ⇒ 列表卡死在「加载中…」。
  该 bug v0.13.30 起就存在，只是此前的探针没测过「刷新恢复」路径。修法：
  LP/GH/lpStars/lpHiddenSet/ghStars/ghHiddenSet 初始化前置到 01 分片
  （lpLoaded 同型双 var 纪律），test_tdz_order 加专项钉子。

## v0.13.31 — GitHub 项目菜单 + 本机项目精度收紧（79→42）

> 施工会话：本会话。基线：v0.13.30。改动文件：新增 `src/githubprojects.py`
> （GET /api/github/repos 远端清单+strict remote 本地匹配 + POST /api/github/clone
> 浅克隆）、`static/hub/10-github-projects.js`（GitHub 页，新第 10 分片）、
> `tests/test_github_projects.py`（L0 35 例）、`tests/verify_github_projects.py`
> （L2 真鼠标 9 判据）；改 `src/localprojects.py`（精度四闸 P1-P4 + _scan_roots
> 三元组 + cloudcli 降级纯富化）、`src/audit.py`（VALID_TYPES + repo）、
> `src/main.py`（挂路由 + VERSION 0.13.31）、`templates/index.html`（i-globe
> sprite + 侧栏按钮 + #page-github）、`static/hub/01-core-boot.js`（ghLoaded +
> go() 钩子）、`static/hub/05-chat-and-history.js`（PAGE_LABELS）、
> `tests/test_localprojects.py`（精度新例 31 例）、`tests/test_ls_guard.py`
> （分片清单 9→10）、`tests/test_term_focus_policy.py`（user:true 入口 4→5）、
> `static/hub.js`（build 3428 行）。
> 验证：L0 hermetic **613/613** 零跳过；L1 host 41/41；L2 真浏览器探针
> R1-R5 全过（按钮位置/fork 开关/选中/会话直达）；生产 :3102 实测
> localprojects count=42 dropped=28、github repos count=72 local_total=30。

### v0.13.31 交付（用户需求「复用本机项目菜单加 GitHub 项目菜单，列表加载远端所有仓库，其他逻辑一致，本地没有就克隆即时同步」+「79 个项目肯定是错的，精度要优化」）

- **精度收紧四闸（P1-P4）**：实测 79 = 56 git + 23 cloudcli-only，约 40 条垃圾。
  P1 /tmp 整前缀剔除（16 条探针残留）；P2 cloudcli 从独立项目源**降级纯富化**
  （cloudcli-only 行须过四关：非根自身/不在排除路径/盘上真实存在/是 .git 目录
  ——杀掉 sr、网络设备合并、wt-01a0db08 等存账幽灵）；P3 git worktree **结构性**
  剔除（.git 文件 gitdir: 指向另一已收录仓 ⇒ 派生检出；非派生保留标
  worktree:True——不按目录名猜，用户裁定「严格按证据」）；P4 备份归档剪枝
  （snapshots/git-backups/Hermes-backup/marketplace-cache/ARCHIVED- 前缀）。
  结果 **79→42**；每条剔除原因进信封 dropped（#lpMeta 可见，下一类误报
  用户自己能看见）。
- **GET /api/github/repos**：api.github.com /user/repos 分页全量（实测 72 仓：
  38 自建 + 34 fork），5 分钟缓存防烧限额；**本地匹配严格按 git remote URL**
  （读 .git/config 解析 slug 对账——同仓异名 techdocs-scripts↔scripts 命中，
  同名异仓不误配；根自身是仓的 technical-docs 也计入）。信封不带任何
  clone_url——克隆 URL 服务端现场重构。
- **POST /api/github/clone**：白名单 slug（REPO_RE 形状 + 远端清单成员双重
  校验，客户端只能「点名」不能「指路」）→ `git -c credential.helper= clone
  --depth 1` 浅克隆到 CLONE_BASE（默认 /fs/1000/ftp/技术文档，env 可改）；
  180s 上限、argv 列表无 shell、GIT_TERMINAL_PROMPT=0、失败清半成品、
  同仓已存在幂等 200、异仓 409、symlink 400。**token 三律**：env GITHUB_TOKEN
  → github.txt 首行，每次现读；永不打印/进返回值/进审计；上游错误正文过
  scrub 时 token 作位置参数（ghp_ 不在 _KEY_PATTERNS）。
- **前端**：侧栏常驻项「GitHub 项目」（本机项目之下、AGENTS 之上）；三态列表
  + fork 开关（默认隐藏 34 个 fork）+ 选中动作条；`ghStart` 两段式——本地有
  直接开会话（lpStart 同路），本地无先 clone 拿 path 再开会话（即时同步）。
- **四项裁定**：审计 action 用冻结枚举 `create`（不动 AUDIT_ACTIONS，"clone"
  会打 action_invalid 标记）；audit.VALID_TYPES + "repo"；worktree 结构判定
  非名字猜测；clone URL 不下发客户端。

## v0.13.30 — 本机项目菜单（项目检索 → 选 agent → 新建会话直达终端）

> 施工会话：本会话。基线：v0.13.29。改动文件：新增 `src/localprojects.py`
> （/api/localprojects 多根 git 扫描 + cloudcli 合并）、`static/hub/09-local-projects.js`
> （本机项目页，新第 9 分片）、`tests/test_localprojects.py`（L0 24 例）、
> `tests/verify_localprojects.py`（L2 真鼠标 10 判据）；改 `src/term.py`
> （CreateIn.cwd + _cwd_or_none 校验）、`src/main.py`（挂路由 + VERSION 0.13.30）、
> `templates/index.html`（侧栏按钮 + #page-localprojects）、`static/hub/01-core-boot.js`
> （lpLoaded + go() 懒加载钩子）、`static/hub/05-chat-and-history.js`（PAGE_LABELS）、
> `tests/test_term_launch_guard.py`（cwd 白名单 + TestCwdGate 钉子）、
> `tests/test_ls_guard.py`（分片清单 8→9）、`tests/test_term_focus_policy.py`
> （user:true 入口 3→4，lpStart 是新入口）、`static/hub.js`（build 3275 行）。
> 验证：L0 hermetic **572/572** 零跳过；L1 host 40/40；L2 真浏览器探针 10/10
> （点 agent-hub → 选 codex → 新建会话 → 终端页 on + pty cwd 对账 + 零 JS 错）。

### v0.13.30 交付（用户需求「agents 菜单上面新建本机项目菜单：自动检索本机所有项目不限目录，点项目名称选 agent 新建会话，自动跳转对应 agent 拉起会话」）

- **GET /api/localprojects**：os.walk 多根扫描（默认 `/home/gztxt, /fs/1000/ftp/技术文档,
  /vol1/@apphome`，env `LOCALPROJECT_ROOTS` 可覆盖；深度 ≤3、点目录/node_modules/venv 剪枝，
  git worktree 的 .git 文件也认）+ 合并 v0.13.29 的 cloudcli 项目（custom_project_name
  精确名胜出、sessions/last_activity 透传，normpath 去重）。实测 79 项 / 115ms；
  缺根与 cloudcli 降级均点名进 `errors`（「查不了」≠「没有」）。
- **term cwd 扩展（安全闸门不松反紧）**：`POST /api/term/sessions` 新增可选 `cwd`——
  校验链：pydantic max_length=500 → `_cwd_or_none()`（绝对路径 + 实盘存在 + 可疑字符
  栅栏）→ 只进 `os.chdir`。**命令拼装一字未动**（cmd 仍只出自画像白名单/后端模板，
  test_term_launch_guard 三条既有钉子原样通过）；权限论证：已持 TERM_TOKEN 者本可
  经 shell 画像（cmd=bash）cd 任意目录 ⇒ 传 cwd 无升级。新增 TestCwdGate AST 钉子：
  create_session 里 `body.cwd` 的每次读取必须包在 `_cwd_or_none(...)` 内。
- **前端**：侧栏常驻项「本机项目」（总览之下、AGENTS 手风琴之上——用户点名位置）；
  新 09 分片三态列表（busy/数据/失败含重试）+ 过滤 + 选中动作条（项目名 + agent 下拉 +
  新建会话）；`lpStart` 逐字仿 startAgent 仅多传 cwd——`gotoChat(agent,'term')` →
  `termConnect(..., {user:true})` 自动跳终端工作台并聚焦。var 状态变量防 TDZ（08 分片
  同教训）。
- **明确不做**：MCP 工具（未要求）、项目类型探测、claude 走 CloudCLI iframe（用户裁定
  统一终端链路）。

## v0.13.29 — CloudCLI 项目直达（列表 + 点击快速开始）

> 施工会话：本会话。基线：v0.13.28。改动文件：新增 `src/cloudcli.py`（projects/start
> 两端点 + JWT 铸造 + cc.start 埋点）、`tests/test_cloudcli.py`（L0 22 例）；改
> `src/main.py`（挂路由 + VERSION 0.13.29）、`src/runlog.py`（SUBJECTS + cc.start）、
> `src/hubmcp.py`（+hub_cloudcli_projects 工具）、`static/hub/01-core-boot.js`
> （showDetail('claude') 挂项目加载钩子）、`static/hub/04-terminal-ws.js`
> （loadCloudcliProjects/cloudcliStart——iframe 直达 /session/{id}）、`static/hub.js`
> （build 3160 行）。
> 验证：L0 hermetic **540/540**（524 旧+16 后端例 + 6 前端例）；全链实测（铸 token →
> GET projects 28 项 → POST start 创建会话 201 → DELETE 清理）；CloudCLI 服务不可达
> 时 projects 照常（直读 db 解耦）。

### v0.13.29 交付（用户需求「cloudcli 项目检索要完善/加载所有项目/精确名称/点击快速开始」）

- **GET /api/cloudcli/projects**：直读 /vol1/cloudcli/auth.db（`file:...?mode=ro`
  只读，与 cloudcli 服务活死**解耦**）——全部活跃项目，**名称精确**（custom_project_name
  优先回落 basename）、星标、活跃会话数、最近活动一条 JOIN 拿齐；归档项目不出现。
- **POST /api/cloudcli/start**：按写方法判（writeauth fail-closed）→ 铸 2h JWT
  （app_config.jwt_secret 现读不缓存 + users 首行，HS256 纯手搓零依赖）→ 转调
  cloudcli `POST /api/providers/sessions`（provider=claude）→ {sessionId, url}。
  cloudcli 不可达 ⇒ 502 如实报错（列表不受影响）。cc.start 埋 runlog。
- **前端直达**：Claude 详情抽屉尾部自动挂「CloudCLI 项目（N 个）」面板（加载中/
  失败/数据三态）；点「▶ 开始会话」→ gotoChat('claude') 进 embed → iframe src 覆写
  `/session/{id}`（**dataset.src 同步**防 applyChatMode 重置，地址行同步显示）→
  抽屉关闭 + toast。iframe 内鉴权态由 cloudcli 自己的 localStorage 管（跨源但同
  浏览器持久，hub 不传 token 不越权）。
- **MCP +hub_cloudcli_projects**：外部 agent 可查「用户在 CloudCLI 有哪些项目」
  （含会话活跃度，派发决策参考）。start 不进 MCP（写动作留给人）。

## v0.13.28 — 全 agent 记忆源 + 检索回退 + kb 页优化（批1~4 一批收口）

> 施工会话：本会话。基线：v0.13.27。改动文件：`src/memfed.py`（REGISTRY 6→10 源、
> _RG_TARGETS +4）、`src/hubmcp.py`（hub_memory_search +sources 参数全源默认、
> hub_kb_search 默认五路）、`src/main.py`（VERSION 0.13.28）、`templates/index.html`
> （memFedSrcs +4 option、kbRoutes local 补勾、QueryBar×2、kbBadges、CSS .tag.src-*
> 三变体）、`static/hub/04-terminal-ws.js`（memFedClear/kbClear、renderQueryBar、
> fedBadges 抽取共用、SRC_ABBR/srcTag、kb 路径行+下钻按钮）、新增
> `tests/test_mcp_fulltext_defaults.py`（4 例）、更新 `tests/test_memfed.py`
> （27→32 例）、`tests/test_center_ui_l0.py`（16→24 例）、`static/hub.js`（build 3090 行）。
> 验证：L0 hermetic **524/524**（507 旧+17 新）；MCP 直调冒烟（不传 sources ⇒ backends
> 11 路全源；kb 默认五路引擎串）；四新源生产 probe 全 ok（68/23/164/12 文件）；
> grok 专属词「Grok Memory Index」真实召回。

### v0.13.28 交付（用户三需求：全源覆盖 / 检索回退 / kb 页优化）

- **批1 四新源（「是否是本机所有 agent 的记忆」→ 是）**：claude_projects（
  ~/.claude/projects/**/memory/*.md，68 文件，weight 0.8 与 claude_mem 同级）、grok_memory
  （memory/*.md + sessions/**/prompt_history.jsonl，23 文件，0.6）、hermes_memory
  （memories/*.md + sessions/session_*.json，164 文件，0.6）、workbuddy_memory
  （USER/SOUL/IDENTITY.md + memory/ + sessions/，12 文件，0.6）。全走 rg_text 适配器零新代码；
  probe/白名单/RRF/runlog 埋点全链自动接管。glob 坑实测钉死：claude_projects 必须
  `**/memory/*.md`（`*` 不跨 / 实测 0 命中）；.bak/request_dump 被天然排除（闸门钉）。
- **批2 MCP 透传（「所有 agent 能够加载检索调用」的 MCP 侧落点）**：
  hub_memory_search 新增 sources 参数，默认 `"local,tdai," + enabled_ids()` 动态派生
  （新增源自动跟上不落一轮）；hub_kb_search 默认改 `",".join(kb.ROUTES)` 五路。
  REST 默认**不动**（"local,tdai" 是防注入链路默认突变的有意决策）。旧调用形态
  （不传 sources）不 400，backends 11 路。
- **批3 检索回退（「搜索完成后无法回退」）**：QueryBar header 条（当前检索「q」· N 条 ·
  清空↺）两页同构；memFedClear/kbClear 还原初始文案+清 hint/badges/输入框；
  **CENTER_HEALTH 故意不清**（侧栏体检态与检索结果语义解耦，清空≠洗健康态——钉子钉死）。
- **批4 kb 页观感**：源徽标 SRC_ABBR 缩写（CM/CP/PI/CX/GK/HM/WB/WS/AR/TV/TD/L1）+
  .tag.src-doc/src-session/src-index 三变体（明度区分不彩虹，v0.9 设计系统口径）——
  修复裸拼 `class="tag turbovec"` CSS 无定义静默灰底；fedBadges 逐路徽标行从 memFed
  抽出共用（kb 也挂，替换只 toast 的半吊子降级表态）；kb 结果卡加路径行 + 四根下钻
  按钮（workspace/archived 命中 → kbBrowse(首段)，复用单层能力零后端改动）；
  kbRoutes local 补默认勾（与 MCP 五路对齐）。

## v0.13.27 — 批3：三中心 UI 统一（加载三态 / 技能正文 / 文档树下钻 / 联邦检索入口）

> 施工会话：本会话。基线：批2。改动文件：`static/hub/01-core-boot.js`（boxBusy/
> boxFail 助手 + CENTER_HEALTH + OVERLAY_IDS+skillDocDrawer + api() err.http/payload）、
> `static/hub/04-terminal-ws.js`（六 loader 三态 + skillRead + kbBrowse 下钻 +
> memFedSearch）、`static/hub/05-chat-and-history.js`（侧栏三中心健康点）、
> `templates/index.html`（skillDocDrawer 抽屉 + kbCrumb + memFed 面板 + 占位统一）、
> `tests/test_overlay_exclusion.py`（DRAWERS+skillDocDrawer 闸门加严）、新增
> `tests/test_center_ui_l0.py`（L0 16 例）、`static/hub.js`（build 重建 2995 行）。
> 验证：L0 hermetic **507/507**（491 旧+16 新）；node --check 绿；TestClient 静态面
> 冒烟 14 项全过（占位/面板/抽屉/CSS/提手/JS 函数面）。

### 批3交付（三中心「操作逻辑·统一加载·显示·调用」层）

- **B 统一加载三态**：boxBusy/boxFail 全站助手；六 loader（loadMemories/loadSkills/
  loadSkillBudget/kbSearch/loadKbStatus/kbBrowse）统一「busy→数据/失败上屏+重试按钮」
  （此前失败只 toast，列表区停旧内容——分不清「没数据」与「挂了」）。初始占位统一
  「加载中…」。侧栏三中心行挂 CENTER_HEALTH 健康点（s-badge 色族：ok/warn/err）。
- **C 技能正文查看**：技能行「查看」按钮 → skillRead(name, route) → skillDocDrawer
  抽屉（OVERLAY_IDS 第三员，唯一性/遮罩/导航清收自动接管；overlay 闸门同步加严）。
  409 多路冲突读 err.payload.detail.candidates 渲染候选按钮；truncated 如实提示
  bytes_total。api() 错误对象 additive 挂 err.http/err.payload（批2 已铺）。
- **D 文档树下钻**：kbBrowse(sub) 参数化；顶层目录条目可点下钻（后端 kb.py 白名单
  校验单层）；kbCrumb 面包屑（知识库根 ▸ sub ×回根）；根内子目录如实标「暂只支持
  下钻一层」不装多层；errors 逐条上屏不静默。
- **E 记忆页联邦检索**：page-memory 顶部联邦面板（memFedQ 输入 + memFedSrcs 六源
  多选 + memFedBadges 逐路徽标 + memFedResults）。徽标四态口径与资产面板一致：
  绿=ok 有命中 / 黄=ok 零命中 / 红=挂了点名（不糊成绿）。按需触发，进页不自动跑
  （防埋点污染+防无谓联邦开销）。

## v0.13.27 — 批2：运行日志前端页（page-runlog 上线，六闸门更新）

> 施工会话：本会话。基线：批1。改动文件：新增 `static/hub/08-runlog.js`（分片源）、
> `tests/test_runlog_frontend.py`（L0 11 例）；改 `templates/index.html`（+page-runlog
> section）、`static/hub/05-chat-and-history.js`（SYS_PAGES 9→10 + PAGE_LABELS）、
> `static/hub/01-core-boot.js`（go() 钩子 + api() 错误对象 additive 挂 err.http/
> err.payload）、`tests/test_ls_guard.py`（分片清单 7→8 + 红基线 AFTER_RED 名单）、
> `static/hub.js`（build 重建 2857 行）。
> 验证：L0 hermetic **491/491**（480 旧+11 新）；TestClient 端到端冒烟（埋点→
> runlog 查询 200/无凭据 401/MCP 通道归因 web+mcp 并存实证）。

### 批2交付（运行日志「展示」层）

- **page-runlog**：时间/source/subject/状态点/耗时/通道/降级路数/查询词 八列表格；
  source/subject/status/window 四过滤 + id 游标翻页（before_id，不用 OFFSET）。
- **鉴权 UX 三态**（互斥）：busy → 数据/空窗；401/503 →「输入口令并重试」按钮
  （点击才 termToken() 弹框，不在 loadRunlog 里自动弹——05:297 教训）；GET 的
  token 由 runlogFetch 自带（api() 只给写方法带）。
- **api() additive**：错误对象挂 err.http/err.payload，既有调用方只读 .message
  不受影响；runlog 页靠 http 判鉴权态，后续技能 409 靠 detail.candidates 渲染。
- **RL_FIRST/RL_CUR 用 var**（不用 let）：go() 经 loadRunlog 读它们，let 的 TDZ
  静态序风险被 test_tdz_order 判红——07-asset-panel 同教训，改 var 即绿。

## v0.13.27 — 批1：运行日志后端（三中心检索留痕 + /api/runlog 查询门面）

> 施工会话：本会话。基线：v0.13.26 批6 收口后（`3b85cb3`）。
> 改动文件：新增 `src/runlog.py`（track 装饰器 + /api/runlog）、`tests/test_runlog.py`
> （L0 20 例）；改 `src/memory.py`（search/context 两端点挂装饰器）、`src/kb.py`
> （search/browse/status 三端点）、`src/skill.py`（list/read 两端点）、`src/hubmcp.py`
> （_get 带 x-hub-channel: mcp 通道头）、`src/db.py`（idx_prof_source 索引）、
> `src/hook.py`（画像聚合排除 rest）、`src/main.py`（挂路由 + VERSION 0.13.27）。
> 验证：L0 hermetic **480/480**（460 旧+20 新，0 skip）；import 冒烟 + 装饰器
> 成功/失败/DB炸/通道四路径直调实证。

### 批1交付（运行日志「收集」层）

- **零新表**：复用 profile_events（source='rest'，detail JSON 列存 q/limit/routes/
  channel/routes_ok/degraded/count）。`@runlog.track(subject)` 装饰器包 6+1 个只读
  检索端点（mem.search/mem.context/kb.search/kb.browse/kb.status/skill.list/
  skill.read），失败路径留痕后原样 re-raise（HTTP 诊断语义一字不动）。
- **埋点≠准入**：_fire 整体 try/except，DB 炸只 print 不 raise——红向用例
  「db.execute 猴补丁炸掉仍 200」钉死。
- **MCP 通道归因**：hubmcp._get 发 x-hub-channel: mcp 头，REST 端点读头记
  channel=mcp/web（头可伪造但仅遥测归因，非鉴权）。REST 层埋点天然覆盖 9 个 MCP
  工具的转调链。
- **GET /api/runlog**：source/subject/status 过滤 + window 时间窗 + id 游标翻页
  （append-only 表不用 OFFSET）。鉴权照抄 /api/audit/list 先例：GET 但按写方法判
  （运行日志含查询词可反推意图），401/503 fail-closed。
- **连带项**：hook.py 画像聚合 `WHERE source != 'rest'`（防高频检索霸榜把 agent
  画像挤出前 50）；防噪声红线（/api/agents、/api/ports、/health 不埋）写死在
  runlog.py 注释并有静态闸门。
- **q 落库前 scrub + 截 120 字符**（凭据脱敏与 kb/skill 错误路径同源）。

## v0.13.26 — 批5：三路 agent 接线完成（仓外共享配置，逐路授权执行）

> 施工会话：`01a0db08`。本批改动全部在 agent-hub 仓外（共享配置军规四件套，
> 用户已逐路授权），仓内零代码改动；接线对象为生产旧码（v0.13.25，禁重启），
> 故 MCP 工具面为旧版 9 工具——批6 合并+重启后自动升级为联邦版。
> ① claude `~/.claude.json`：mcpServers +hub（http 型 + x-hub-token header）；
> ② codex `~/.codex/config.toml`：[mcp_servers.hub]（streamable_http + ?token=
> 兜底路，600 权限）；③ pi `~/.pi/agent/extensions/hub-facade.ts`（新文件：REST
> 直连 GET 路零凭据、150ms 预算 input 自动注入、/hub-recall /hub-skills
> /hub-health 三命令、404 友好降级「后端未更新」不误报「挂了」）；
> ④ 生产 `.env`：JOB_SHELL_ALLOW 纳入 rebuild_turbovec.sh（cronjobs 模块级
> 常量，重启后生效；job 注册亦须重启后执行——PT-20260926-01）。
> 端到端证据：claude -p 真调 hub_memory_search（TDAI 3 条 5.0ms）；
> codex exec 真调 hub_kb_search（tdai_l1+turbovec RRF 8 条）；pi -p 加载
> 自证行 + /hub-recall 打到生产日志（GET /api/memory/search、/api/kb/search
> 两路 200）。

## v0.13.26 — 批4：前端技能中心 + 知识库中心（两页上线，六大中心齐）

> 施工会话：`01a0db08`（worktree `agent-hub-wt-01a0db08`）。基线：批3提交。
> 改动文件：`templates/index.html`（+2 section：page-skills/page-kb）、
> `static/hub/01-core-boot.js`（skillsLoaded/kbLoaded + go() 懒加载钩子）、
> `static/hub/04-terminal-ws.js`（技能/知识库两中心渲染函数块）、
> `static/hub/05-chat-and-history.js`（SYS_PAGES 8→10、PAGE_LABELS）、
> `static/hub.js`（build 产物重建，md5 提手自动同步）、`src/kb.py`
> （+GET /api/kb/browse）、`tests/test_kb_frontend_pages.py`（新增 L0 12 例）。
> 验证：L0 hermetic **460/460**（448 旧 + 12 新，含 hubjs_split 逐字节漂移
> 闸门）；verify_kb_federation 41/41 复验绿；node --check JS 语法绿。
> 真渲染（临时实例 + elementFromPoint 断言）按验收分工留批6集成批次。

### 批4交付（技能/知识库系统「前端展示与操作」层）

- **技能中心页（page-skills）**：技能清单（名/描述/发现点过滤）、软链安装
  （name × from_route × targets[] → POST /api/skill/install，幂等/409 语义后端
  已由批2钉死）、token 预算化清单（/api/skill/budget?max_tokens=N，默认 800，
  全条目/仅名/截断三段如实展示）。
- **知识库中心页（page-kb）**：五路联邦检索（tdai/turbovec/workspace/archived
  /local 多选，走批3 /api/kb/search）、逐路健康面板（/api/kb/status 五段、降级
  路点名不糊成绿）、文档树浏览（/api/kb/browse：workspace 四根顶层 + sub 单层
  下钻，根名白名单匹配防穿越）。
- **/api/kb/browse**：只读文档树端点。根定义与 memfed._RG_TARGETS
  ["workspace_files"] 同源（不另抄目录清单防两处漂移）；`sub` 走根名精确匹配
  而非路径拼接（`../etc`/`..`/`/etc`/`a/b`/`.` 全部 400 拒绝，未知根 404 带
  可用根清单）；根消失进 errors 不静默；KB_BROWSE_MAX=200 条目硬顶。
- **懒加载成对**：go() 里 skills/kb 各挂钩子，与 memory/ports 同构；加载失败
  toast 点名（降级路不让「查不了」糊成「没有」——资产面板 09-22 口径沿用）。
- **L0 12 例**：browse 五例（顶层/下钻/穿越拒绝/404/根缺失不静默）+ 前端七例
  （section 存在/SYS_PAGES/PAGE_LABELS/懒加载钩子/loader 函数/DOM id 成对/
  inline onclick 函数真存在防手滑拼错函数名）。


> 生成口径：`git log` 机械提取（版本号只在提交主题开头出现才起一节），另由人补「未上线批次」一节。
> 本文件只记「哪一版上线了什么」；施工过程与证据留在 `PENDING-TASKS.md`（PT 编号台账）。
> 生成时间 2026-09-24 19:3x（生成器＝一次性脚本，未入库；重跑请复制本文件头部的口径）。

## v0.13.26 — 批3：kb 联邦检索扩 workspace/archived 两路 + turbovec 重建脚本

> 施工会话：`01a0db08`（worktree `agent-hub-wt-01a0db08`）。基线：批2提交 `037d292`。
> 改动文件：`src/kb.py`（ROUTES 3→5 路、_fed_async 转调、权重修正、status 两段）、
> `tests/test_kb_federation.py`（新增 L0 9 例）、`tests/verify_kb_federation.py`
> （追加 B3a~B3f 六断言）、`scripts/rebuild_turbovec.sh`（新增）。
> 验证：L0 hermetic **448/448**（439 旧+9 新）；真源闸门 verify_kb_federation
> **41/41 PASS**；宿主级冒烟：五路并发 1975ms，workspace 6 命中/242ms、
> archived 6 命中/1827ms，status 段两源 available（workspace 294 文件/26ms、
> archived 5353 文件/53ms）。

### 批3交付（知识库系统「收集」层）

- **ROUTES 3→5 路**：新增 `workspace`（技术文档 MEMORY.md/memory/agent-knowledge/
  digest，实时 rg 全文）与 `archived`（会话备份 5353 文件，rg --no-ignore --hidden）。
  实现转调批1 memfed 适配器（rg 命令行坑的权威实现，免重踩）。
- **假接入护栏（实测修）**：低权源在满权 tdai 池下会被挤出融合前列（k=20 时
  融合分布仍 tdai 100%，两路 backends 绿但结果不可见＝摆设）⇒ kb 语境下
  workspace/archived 定位为**文档全文路**与 turbovec 同层，满权 1.0，靠 RRF_K
  摊平；修后 k=12 融合分布三源均衡（4/4/4）。L0 test_fused_results_really_
  include_fed_sources + verify B3d 断言固化。
- **kb_status 扩两段**：workspace/archived 健康表态（走 list_fed_sources 复用
  TTL 探测缓存，不重扫）。
- **scripts/rebuild_turbovec.sh**：turbovec 索引重建的执行体（索引是技术文档
  投影，重建 ≥1800s 长任务，幂等，超时硬顶 7200s，日志落 /vol1，dry-run 验证
  rc=0）。**注册成 hub job 需先扩 JOB_SHELL_ALLOW（生产 env=共享配置敏感面，
  逐路授权留批5）**——见 PENDING-TASKS PT-20260926-01。

## v0.13.26 — 批2：技能面扩三路 + 安装管理（软链双发现点）+ 预算化清单

> 施工会话：`01a0db08`（worktree `agent-hub-wt-01a0db08`）。基线：批1提交 `8b33a21`。
> 改动文件：`src/skill.py`（_DEFAULT_DIRS 4→7 路 + install/remove/budget 三端点）、
> `src/audit.py`（VALID_TYPES + "skill"）、`tests/test_skill_install.py`（新增 L0 15 例）、
> `tests/test_skill_facade.py` 钉子 4→7、`tests/verify_skill_facade.py` G1c/G10a 4→7。
> 验证：L0 hermetic **439/439**（424 旧+15 新，0 skip/0 fail）；真源闸门
> verify_skill_facade 56/57 PASS（唯一 FAIL G6a 为**既有红**：caveman 技能已从本机
> 消失，主 checkout 同 FAIL，非批2引入——留红报请，不顺手修）。
> 真源扫描：7 路全 ok（claude 18 / pi 4 / techdocs 1 / superpowers 14 / agents 12 /
> codex 7（typesafe-ai + .system 内置 6）/ workbuddy 6），62 条→去重 61（1 条软链别名）。

### 批2交付（技能系统「收集+共享」层）

- **发现点扩三路**：`_DEFAULT_DIRS` 新增 agents（~/.agents/skills，codex 等共享）、
  codex（~/.codex/skills，含 .system 内置）、workbuddy（~/.workbuddy/skills）；
  `_dedup` 按 realpath 合并同源软链（不重复计数，别名如实记录）。
- **POST /api/skill/install**：把源发现点的技能**软链**到多个目标发现点（56 号文档结论：
  各 CLI 发现点互不相通，软链同一权威副本是唯一不漂移手段）。安全：名字白名单
  regex、targets ⊆ 发现点表、源须含 SKILL.md、同 realpath 幂等 no-op、异 realpath 409
  拒绝覆盖；鉴权走 writeauth 全局中间件；审计 asset_audit bind。
- **DELETE /api/skill/remove**：只删软链（islink 才动手）；真目录＝权威副本，
  一律 409 拒绝（无主副本处置属独立待裁项，不在本端点顺手做）；审计 unbind。
- **GET /api/skill/budget?max_tokens=N**：token 预算化技能清单（批5 注入通道数据源，
  claude-mem-bridge 分层降级蓝本）：全条目贪心装填 70% 预算、降级 name-only、
  截断如实报 truncated/total（不静默缺货）。估算 chars/2.5 粗估，宁保守勿膨胀。
- **audit VALID_TYPES** + "skill"：GET /api/audit/list 查询侧可枚举技能审计事件。

## v0.13.26 — 批1：联邦记忆源 memfed（后端纯增量，同批收口 hallmark 视觉 M1~M6）

> 施工会话：`01a0db08`（worktree `agent-hub-wt-01a0db08`）。基线 `638b7cb`。
> 改动文件：新增 `src/memfed.py`（联邦源注册表+五路适配器）/`tests/test_memfed.py`（L0 27 例）/
> `tests/verify_memfed_federation.py`（真源闸门 23 判）+ `src/memory.py` 接入 + `src/main.py` 挂路由。
> 验证：L0 hermetic **424/424**（397 旧+27 新，0 skip/0 fail）；真源闸门 23/23 全 PASS
> （claude_mem FTS 7 命中/85ms、pi 12、codex 12、workspace 12、archived 12/705ms，RRF 融合含外部源条目，脱敏双道过闸）。

### 批1交付（记忆系统「收集」层）

- **源注册表**：`src/memfed.py` REGISTRY——六源 id/label/kind/weight/desc/timeout（claude_mem
  0.8 > workspace 0.7 > pi/codex 0.5 > archived 0.4；opencode 登记 disabled 不启用），
  `GET /api/memory/fedsources` 逐源 probe 健康与计数（TTL 10min 缓存）。
- **五路只读适配器**：claude-mem FTS5 MATCH（短语包裹防注入，回表 observations/session_summaries）
  + 四路 rg 字面匹配（pi/codex jsonl 会话、工作区文件记忆、归档备份）。
- **memory.py 联邦接入**：SOURCE_WHITELIST 动态扩容（仍默认 local,tdai——联邦源 opt-in，
  点名 `?sources=claude_mem,pi_sessions,...` 才启用），RRF 融合含外部源，backends 逐路诊断。
- **安全模型**（照 sessions_store）：sqlite 一律 mode=ro（WAL 退 immutable 快照）、
  rg 子进程硬超时、出站双道脱敏（scrub+mask_title，L0 有红向钉死）、失败逐路报 degraded 不炸链。
- **实抓 bug**：rg 默认尊重 .gitignore —— 会话备份/（.gitignore 123 行）与 ~/.codex 都被
  系统性漏掉（实测 2/5350 文件）；`--no-ignore --hidden` 后 codex 0→227、archived 2→5353。
  闸门 B3 红向当场抓出，不留隐患。

## v0.13.26 — hallmark 视觉审计 M1~M6 收口：字面色收 token、fr 轨道钉零、设计系统成文（前端单批）

> 施工会话：另一 pi 会话（hallmark 审计线，详见 `agent-knowledge/57-*.md`），本批由主集成会话收口提交。
> 基线 `9521bde`（v0.13.25）。改动文件：`templates/index.html`、`DESIGN.md`（+版本号收口）。
> 验证：L0 hermetic 397/397（收口提交前复跑，0 skip/0 fail）；真渲染四档（320/390/768/1280）
> 与 gate 50 的证据在该会话的 agent-knowledge/57，本收口不重复渲染取证。

### 改了什么（渲染零变化——取值与原字面量逐字相同，只是不再绕过 :root）

- **M2/M6 token 化**：内嵌条字面色 `#e0e0e0` / `#eeeeee` 收进 `--embed-line` / `--embed-bg`
  （与 `--divider` / `--hover` 是不同角色，禁止合并复用）；`--font-display = var(--font-mono)`
  （CJK-first 不引 webfont，display 与 body 同源是有意取舍）；
  `.btn.danger:hover` / `.start-btn:hover` 的字面 `#fff` / `#ffffff` 换 `--on-accent`。
- **M3**：端口/时间戳/表格数值列 `font-variant-numeric: tabular-nums`。
- **M4**：所有 grid 的 fr 轨道一律 `minmax(0, …fr)`，杜绝裸 `1fr` 被 min-content 顶破容器。
- **M1（DESIGN.md）**：新增「视觉系统」章——总原则（骨架中性灰阶，色相只发语义）、token 角色表、
  六条硬约束（禁字面色 / tabular-nums / minmax(0) / body 承重 hidden / 窄屏四档无横向溢出 / z-index 尺度）。
  只做语义索引不复制取值，避免制造第二份会漂的副本。

### 为什么单收口一版：版本撞号解排

`0.13.25` 已被「终端退出原因上屏」批（`9521bde`）占用并在产；视觉修复虽先一步写盘，
但从未提交。收口即 bump `0.13.26`，主树回到零未提交状态（C10 worktree 闸门解锁）。

## v0.13.25 — 终端退出原因上屏：把「为什么没了」从哑谜变成一句话（后端单批，待一次重启上线）

> 施工会话：claude（排查「agent-hub 菜单点 OpenCode 秒退」）。全程在主仓 `agent-hub`，
> 基线 `b9f5781`（v0.13.24）。改前备份 `src/term.py.bak-20260925_164357-退出原因上屏` 等三份。
> 测试：**L0 378 → 397（+19 例，新增 `tests/test_term_exit_reason.py`）、L1 40 不变，
> skipped=0、failures=0、errors=0**（`scripts/run_tests.sh all` 退出码 0）。
> 变异对照：把 `src/term.py` 还原到改前，新闸门 **7 failures + 21 errors**（有牙）；恢复后 19/19 OK。

### 真因不在 hub —— 排查记（供下次同类故障抄近路）

「菜单点 OpenCode 闪退、只剩 `[opencode] <defunct>` 僵尸」**不是 agent-hub 的 bug**：
spawn 链路（`profiles.which` 兜底命中 `~/.npm-global/bin/opencode`、pty、TUI 渲染）实测全部正常。
真因是 **bun(JavaScriptCore) 在整机 swap 耗尽时主动 abort**：
`ASSERTION FAILED: MemoryExhaustion` → `__builtin_trap()` → `ud2` → SIGILL，内核记 `trap invalid opcode`。
定性三步：① `dmesg` 见两次崩溃 `ip` 同为 `0x2607064`（确定性崩溃点，排除随机内存损坏）；
② `objdump` 该行 = `ud2`（运行时**主动** abort，非 CPU 缺指令；本机 Xeon E3-1226 v3 有 avx2/bmi2）；
③ `ulimit -v 700000 opencode` 秒级确定性复现，拿到 `MemoryExhaustion` 原文。
根因落点：`/etc/fstab` 早声明的 `/vol1/.swap/swap2`（4G，签名/NOCOW 均有效）因开机时
`vol1.mount` 未就绪 + `nofail` 静默吞失败，**从未激活**，4G swap 白躺数日（已 `swapon` 复活 + drop-in 修顺序，
swap 3G→7G）。详见 `wiki/entities/swap2-四G从未激活致bun程序自杀.md`。

### 后端：`src/term.py` —— 退出状态原先被丢弃，崩溃原因永远上不了屏

- **缺陷根**：`_cleanup()` 与 `_force_kill()` 里 `waitpid` 的 status 写作 `_st`/`status` 但**从未使用**
  ⇒ 进程怎么死的（信号几 / 退出码几）hub 一概不知，前端只收到一句无信息的「[会话结束]」。
- **新增 `describe_exit(status, hub_killed=False)`**（纯函数，单测直接喂 wait-status）：
  把 `WIFSIGNALED`/`WIFEXITED` 解成人话。**内存嫌疑信号**（SIGILL/SIGSEGV/SIGBUS/SIGABRT/SIGKILL）
  追加「（疑似内存不足）」；非内存信号（SIGTERM/SIGHUP/SIGINT/SIGPIPE）**不贴**内存标签（宁缺勿滥）。
- **歧义信号去误导**：SIGKILL/SIGTERM 既可能是内核 OOM-killer，也可能是 hub 自己发的
  （点 × / 空闲 TTL / 服务退出，见 `kill()`/`kill_all()`）。新增 `Session.hub_killed` 标志，
  两处主动发信号路径都置位 ⇒ `describe_exit` 如实说成「由 hub 主动终止」，
  **绝不把用户主动关会话渲染成"内存不足"**（红向钉子：`test_hub_killed_sigkill_says_hub`）。
- **无信息即沉默**：`describe_exit(None)` 返回空串，调用方回落到**与改前逐字节一致**的裸「[会话结束]」/
  「[process exited]」——拿不到原因就不编造（`test_callers_fall_back_when_empty` 钉死两处文案）。
- **退出状态首次记录优先**：`exit_status` 一旦记下不被后续 `waitpid`（多为 ChildProcessError）覆盖成 None
  （`test_first_status_wins`）。`to_dict()` 透出 `exit_reason` 字段，API/排查可见。
- **上屏两处**（`on_readable` 的 EIO 分支 + pump 收尾）都带上面因，桌面/手机/重连三种画面都看得到。

## v0.13.24 — 联邦门面收口批：会话导出前端按钮 / asset_audit 资产变更审计 / 记忆 staleness 观测（后端+前端同批，待一次重启上线）

> 施工会话：`01a0d6dd`，全程在自己的 worktree `agent-hub-wt-01a0d6dd`（分支 `wt/01a0d6dd`，基线 `40a1f89`）里改；
> 集成者合并 master 后才重建 `static/hub.js`（避 C7 build 产物单写者）。
> 设计稿 `docs/superpowers/specs/2026-09-25-federated-facade-closeout-design.md`（`42673c0`）、
> 计划 `docs/superpowers/plans/2026-09-25-federated-facade-closeout.md`（含逐任务红对照命令与预期）。
> 本批**不含 Docker/容器化**（用户 09-25 01:48 裁定：暂缓，留待后续迭代升级）。
> 测试：**L0 313 → 378、L1 35 → 40，SKIP=0、failures=0、errors=0**（`run_tests.sh` 退出码 0）；
> 新增 5 只测试文件共 **70 例**；`prepush.sh` 六项全 PASS（退出码 0）。

### 后端：资产变更审计（`asset_audit`）—— 补「34 条写路由有鉴权、0 条有审计」的缺口

- **`src/db.py`**：新表 `asset_audit`（append-only）+ 索引 `idx_audit_asset`/`idx_audit_created`；写口径
  `log_asset_event(asset_type, asset_slug, action, actor, detail)` 与既有 `log_profile_event` 同族。三条硬约束都有 L0 闸门钉着：
  ① **只 INSERT**（审计表可被 UPDATE/DELETE 就不叫审计）；② `detail` 落库前整体过 `sessions_export.redact_text`
  ——先 `json.dumps` 再对整串脱敏 ⇒ **嵌套层也覆盖**（只扫顶层值会漏 dict 里的 dict；实测 `{"raw":…,"nested":{"k":…}}` 命中 2 处）；
  审计行会成为下一次会话导出的正文，凭据写进去＝二次外流；③ `action` 不在 `AUDIT_ACTIONS` 枚举里 ⇒ 打 `action_invalid`
  标记，**绝不静默丢弃也绝不改写**（丢事件比记错更贵，改写毁掉取证原文）。
- **`src/writeauth.py`**：新增 `credential_name(provided, secrets)` 与 `actor_of(request)` 两个纯函数，`write_gate`
  在 allow/exempt 分支打 `request.state.actor`（`user:term-token|hub-passcode|anonymous|exempt`）。**`decide()` 签名一字不动**
  ——它的 `(verdict, reason)` 被中间件与 `/api/sessions/export` 共用，且 `tests/test_writeauth.py` 12 例钉着；
  改返回值＝零收益地撞 12 例既有闸门（实测零回归：既有 12 例仍全绿）。身份只记**凭据名**不记值
  （红向钉子：把名字换成凭据原文即 FAIL）。`actor_of` **绝不抛**（审计身份缺失不许把业务请求打挂）。
- **`src/mcpgw.py`（4 点）/ `src/memory.py`（6 点）**：全部资产变更点打审计。覆盖面用 **AST** 扫
  （`tests/test_asset_audit.py::route_audit_map`）而不是 grep —— grep 只能证明"文件里某处有这个词"，证明不了
  "这条路由的 handler 体内有"；漏一条写点即 FAIL，且不审的路由必须挂**书面理由**
  （`/mcp/servers/probe` 是预览语义不落库、`/mcp/call` 是调用不是变更且成败耗时已由 `profile_events` 记）。
  - `mcp_servers.env` 装的是凭据 ⇒ 审计**只记 `has_env` 布尔**，绝不记值。
  - ACL 解绑**先取旧行再删**，把旧值记进 detail —— 否则"解绑了什么"永久丢失。
  - **ACL 的 actor 一律取 `request.state.actor`（用户），`body.agent_id` 进 detail**（相对设计稿 §4 的执行期更正）：
    绑定 ACL 是"用户对某 agent 做的管理动作"，不是"agent 自己做的动作"；记成后者会把管理动作错归给 agent。
  - `memory` 侧 detail **不记正文**（正文已在 `memories` 表；重复记＝体积翻倍 + 多一处凭据面），只记元信息与字符数；
    `delete_l1` 是 `UPDATE status='deleted'` ⇒ 审计如实标 `soft=True`（写 "delete" 却不说清是软删＝"不静默改数据"的反面）；
    `put_l2/l3` 的 `touched` 点名动了哪个字段 —— `manual` 是用户手写补充，09-23 曾被 rebuild 静默覆盖过；
    批量导入记**一行汇总**（`asset_slug='batch'`，`ids` 截 50）：逐条写会让单次调用灌满表。
- **`src/audit.py`（新）**：`GET /api/audit/list` 只读查询门面，白名单 `VALID_TYPES` 七类，`limit` 钳到 1000。
  鉴权照抄 `/api/sessions/export` 既有先例：**GET 但按写方法判**（`decide("POST", …)`）—— 批量读审计行＝数据外流动作，
  且服务绑 `0.0.0.0:3102`，不按写判就是把变更史对整个局域网敞开。fail-closed：服务端没配口令 ⇒ **503 而不是放行**；
  拒绝日志只打 verdict/path/来源，绝不打凭据。

### 后端：本地记忆便签 staleness 观测（件 3 由「清理」改判为「观测化」）

- **`src/memstats.py`（新）**：`age_days`（无时区按 UTC 兜、垃圾输入回 `None` **绝不抛**）、`local_stats`（纯函数：
  行数 / `by_status` / 最老最新天数 / L2·L3 的 `has_manual`）、`verdict`（`fresh|stale|empty`，阈值 `STALE_DAYS=14`）、
  `collect()`（唯一碰 db 的入口，**只读 SELECT**）。**★ 本模块绝不 DELETE/UPDATE/INSERT/调 LLM/起后台任务**，
  由静态护栏钉死（`tests/test_memstats.py::TestModuleCannotMutate`，判**去注释后**的代码，否则 docstring 里的自律声明会被当成违规）。
  改判依据（生产库只读实测，2026-09-25）：`memories rows=4 status={'active':4}`、最老 `2026-09-06T03:51`、最新 `2026-09-06T03:57`
  ⇒ **零软删行、19 天没长过一行 ⇒ 清理任务会永远空转**；记忆权威副本在 TDAI(:8420)，本地表在 KB 融合里权重只有 0.2
  ⇒ 删它零收益、**报告它腐烂**才是净收益；且"后台自动重建 L2"有事故前例（09-23 rebuild 真重写过用户手写 L2）。
  文案口径：**只报告不清理**，`verdict` 里不许出现"已清理/将删除"这类字样（有闸门）。
- **`src/kb.py`**：`/api/kb/status` 返回体新增 `local_memory` 键（`rows/by_status/oldest_age_days/newest_age_days/docs/state/reason/authoritative_source/policy`）；
  `memstats.collect()` 失败 ⇒ 该键降级为 `{"state":"unavailable",…}`，**不拖垮整个状态端点**（逐路表态是 kb 四路联邦的立身口径）。
  端点仍 GET 无鉴权，但只暴露计数与天数 ⇒ 不新增泄漏面（与 `code_stale` 同级情报）。

### 前端：会话导出按钮（件 1 —— v0.13.23 只有后端端点，UI 上零按钮）

- **`static/hub/04-terminal-ws.js`**：`exportStateOf`/`exportStateText`/`chatSessExport` + `[data-export]` document 级委托监听；
  **`templates/index.html`** 只加 1 行（chat 会话工具条一个图标按钮，用**既有** `#i-share`；sprite 里没有 `i-download`，
  发明新 id 会渲染成空白）。三个实测出来的坑决定了实现形态：① 不能走 `api()` —— `isWriteMethod()` 只给
  POST/PUT/PATCH/DELETE 带 token，而导出是 **GET 却要写级鉴权** ⇒ 必 401；② 不能用 `api()` 取体 —— 它会 `JSON.parse`
  成对象，CSV/JSON **文件字节**就毁了 ⇒ 必须 `blob` + `createObjectURL` + `.download` + `revokeObjectURL`；
  ③ token **只走 `X-TERM-TOKEN` 头，绝不进 `?token=`**（会进服务端访问日志与浏览器历史）。
  文案四态互斥（照抄 `07-asset-panel.js` 红向口径）：**被拒绝不许说成"没有会话"** —— `need-token|bad-token|misconfig|error`
  四态一律明写"不是没有会话"，只有 `count=0` 才准说"确实是 0 条"；成功态如实报**脱敏命中数**
  （兑现后端 `X-Export-Redacted-Hits`；命中 0 处也要说，不许省略成"没打码"）。窄屏口径：只加 1 个图标按钮，
  不做 format/redact 一排开关（chrome 单行化优先级更高；给"原文出口"做 UI 需单独裁定 ⇒ 本轮不提供）。
  `bad-token` 时 `lsRemove('hub.term.token')` 清掉存量失效口令。顶层函数一律第 0 列收尾
  （`_hub_extract.extract_function` 用 `\n}\n` 定位函数尾；朴素花括号配对会被注释里的 `}` 截断，实测栽过）。

### 本批修的三个「闸门自身缺陷」（都是对着**正确实现**报红，属精度问题不是漏报）

1. **AST 覆盖面护栏看不见一层间接**：`put_l2/put_l3` 把审计收进模块级 helper `_audit_doc()`（避免把 `touched`
   字段推导复制两遍），第一版护栏只看 handler 体 ⇒ 判"漏审"。解法不是把逻辑抄回 handler，而是让护栏**解析一层本地调用**，
   并新增 `test_indirection_is_limited_to_one_level` 钉死"只准一层"（helper→helper→审计**不算**已审，否则覆盖面可被无限稀释）。
2. **substring 判定表达不了否定式**：文案故意写"不是没有会话"来消歧，却被 `assertNotIn("没有会话", …)` 判成
   "把被拒渲染成空态"。改为**否定式感知**（先摘掉 `不是没有会话`/`不是被拒` 再判），空态则改判精确前缀 `导出被拒`。
   同族：`env` 护栏把安全形态 `bool(body.env)` 也算成漏值 ⇒ 改为"先摘安全形态，余下再现 `body.env` 才算漏"。
3. **缩进错位让 test 变成嵌套函数 ⇒ 永不执行**：新增的 `test_indirection_is_limited_to_one_level` 被插到模块级注释之前，
   成了 `route_audit_map` 的嵌套 def —— 语法通过、import 成功、discover 收不到、总例数只少一个，肉眼极难发现。
   新增**元闸门** `TestGateSelfCheck`：① 任何 `test_*` 都不许嵌套在别的函数里；② 本文件收集例数有下界（≥21，少了即红）。
   同族前例＝`vitals_loop` 函数头丢失致健康检查成为不可达死代码、前端 TDZ 声明前访问。口径：**代码存在 ≠ 会被执行**。

### 顺带查出一处**既存**闸门空探针（未修，已报请）

`scripts/prepush.sh` 检查②「未推送区间的全历史 blob」把 `origin/master..HEAD` 直接当 rev 传给 `git grep`：
`git grep` 解析不了区间 ⇒ `fatal: unable to resolve revision` + **退出码 128**，而 stderr 被 `2>/dev/null` 吞掉、
`blob_hits` 恒空 ⇒ **无论历史里有没有凭据都打印 PASS**。该脚本自己在检查① 上方的注释正好警告过这个失效形态
（"有泄露反而走 else 报 PASS（空探针）"）。逐 rev 扫则确实命中本批早先提交里的 fixture 字面量。
按「发现既存问题 → 停手报请、不顺手修」处置：**未改该脚本**，改为把自己 HEAD 里的凭据形态样本全部改成
**运行时拼接**（`"sk-" + "B"*24`，照抄 `tests/test_sessions_export.py:27-29` 既有手法）⇒ 检查① 真实 PASS。
残留与裁定项见 `PENDING-TASKS.md`（fixture 字面量仍在本地 9 个未推送提交的 blob 里；是否改用 squash 合并由用户裁）。

### 测试与护栏

- L0 新增：`test_asset_audit.py` 22 例（表/写口径/AST 覆盖面/元闸门）、`test_writeauth_actor.py` 15 例、
  `test_audit_api.py` 7 例、`test_memstats.py` 13 例、`test_export_button.py` L0 8 例；L1 新增 `test_export_button.py` 5 例（真跑 node）。
- **每件都做了红对照**（把关键不变量改坏 → 闸门必须 FAIL → 还原后必须 OK），命令与预期输出逐条写在计划文件里。
  还原干净度用 md5 复核：注入前后 `build_hubjs.sh` 产出同为 `md5 ad0315a9`。
- 本批**不动** `static/hub/01|02|03|05|06|07-*.js`（`01a0d513` 会话正在改 02/03/06），不动
  `scripts/orchestration-check.sh`，不动 `scripts/prepush.sh`（只报请）。`src/main.py` 仅动 3 处
  （import 1 行 + `include_router` 1 行 + VERSION 及其注释块），`templates/index.html` 仅动 1 行 + build 脚本自动同步的 `?v=` 提手。

## v0.13.23 — /health 补上游网关(CCR)+画像检测时间、会话批量导出、默认网关回 CCR（后端批次，随 09-25 09:5x 重启上线）

> 施工会话：`01a0d5db`，全程在自己的 worktree `agent-hub-wt-01a0d5db`（分支 `wt/01a0d5db`）里改，
> 施工期**未合入 master、未重启、未碰前端**（避 C7 build 产物单写者：当时 `grok-01a0d5de` 正在 master 上发版）。
> 09-25 09:5x 用户授权重启 ⇒ 已合并 master 并上线。
> **版本号让位说明**：本批原自命名 v0.13.23，但合并时发现 master 上 `ff53581`（终端页空格接力，纯前端批次）
> 已占用 v0.13.23 这个标签 ⇒ 本批改为 **v0.13.23**，避免两批共用一个版本号（`VERSION` 只在后端批次 bump，
> 所以 master 的 `src/main.py` 当时仍是 0.13.20，两批并不真的冲突，冲突的只是 CHANGELOG 的标签）。

### 后端：/health 补两项情报 + 会话批量导出 + 默认网关修正（0924 方案档 §三「health 增强」「会话导出 P2.5」）

- **`/health` 新增 `ccr_gateway`**（`src/gwprobe.py`）：上游网关连通性 + **模型注册清单** + `watch` 断言。
  - 为什么：本机三次同源事故都是**模型 ID 失效而 /health 全绿**（09-06 `minimax-m3:free` HTTP 400、
    09-19 `'ultra'` 无效、09-23 `qwen3.8-flash` 缺 provider 前缀）—— 即 09-22 定名的「静默不可用」家族。
  - **解了 09-23 的 M1 阻塞**：台账记的是"拿不到 CCR 在线清单（401）"。09-25 实测用 hub 自己的
    `MANAGER_LLM_API_KEY` 打 `http://127.0.0.1:3456/v1/models` 返 **200 / 14 个 ID / 1.8ms** ⇒ 清单可常驻观测。
  - **改判一条错账**：`PT-20260923-05` 说 vitals 的 L4 探针模型 `agnes/agnes-2.0-flash` 是"CCR 免费池成员"。
    实测 14 个 ID 里带 free 的 **5 个全是 `openrouter/*:free`**，agnes 三档一个都不在 ⇒ 该前提不成立，
    M1 的价值改成"上游改名/下架可观测"（`watch.<id>` 布尔），清单已钉进 `tests/test_gwprobe.py`。
  - 口径：纯读缓存 + stale-while-revalidate（TTL 300s，单飞），**只兑情报不改 `status`**（同 `code_stale`）；
    绝不回显凭据（只给 `key_present`/`key_len`，端点只给 `scheme://host:port`）。
- **`/health` 新增 `profiles_last_check`**（`src/healthx.py`）：画像最近检测时间。
  只给 `last_sweep` 会被"新一轮扫了 6 家、漏了第 7 家"骗过 ⇒ 同时给 `oldest_check_age_s`（最坏值）与
  `unchecked`（在册却从没被扫到的家数，正是 09-22「在册却静默不可用 21 天」的形态）。
  抽成纯函数是因为分层铁律：**L0 不 import `src.main`**，写在 /health 里就只能靠 live 探针验。
  NaN/Inf 一律当"无值"（否则 `/health` 会吐出裸 `NaN` ⇒ 前端 `JSON.parse` 整页炸，自证端点自己失明）。
- **`GET /api/sessions/export`**（`src/sessions_export.py`）：批量导出 hub 自己的会话，`format=json|csv`。
  - **按写端点同等鉴权**：复用 `writeauth.decide`（fail-closed，没配口令 ⇒ 503 而非放行）。服务绑 `0.0.0.0:3102`，
    批量导出正文是数据外流动作，影响面比单条 `/messages` 大一个量级。
  - **默认脱敏且递归**（`redact=1`）：命中数在 `meta.redacted_hits` 如实回报，要原文须显式 `redact=0`。
    本工作区已三次被凭据外流打过（备份镜像 82 个活凭据文件、`wiki/log.md` 历史含 CCR web token、
    外发净仓被闸门拦下 3 个抄了真 token 的文档），导出件正是最容易被顺手 commit/转发的形态。
  - **刻意不做**：不导出外部 CLI（claude/jcode/codex/opencode/grok/hermes）的历史会话 —— 那是别的工具链的
    私有存档，批量外流属另一层隐私裁定，须用户点名。
  - 前端按钮**未做**：前端是 build 产物且当时正被另一会话占用（C7）；本次只交 API。
- **`src/config.py` 默认网关修正**：`MANAGER_LLM_BASE_URL` 默认值 `http://127.0.0.1:8082/v1`（FCC）
  → `http://127.0.0.1:3456/v1`（CCR）。FCC 已于 09-25 彻底退役（`PT-20260924-15`）⇒ 旧默认是个
  「.env 丢失/新克隆即指向死网关」的隐形故障源。生产行为不变（`.env` 早已 override 成 3456）。
- **`scripts/install-hooks.sh`（P5-9）：脚本已交，但本次刻意未安装**。
  hooks 落在 **common git dir**（worktree 与主 checkout 共用），装下去会立刻改变**正在提交的活会话**的
  提交路径 ⇒ 违反「施工期不得打断在跑会话」。已验证 `--dry-run` 零写盘、`--status` 如实报未安装；
  安装动作留到会话静默，命令：`bash scripts/install-hooks.sh`（逃生口 `--no-verify`，卸载 `--uninstall`）。
- **`VERSION` 0.13.20 → **0.13.23****：v0.13.21 是纯前端批次（按项目口径「VERSION 与清 `code_stale` 随下次
  后端改动同批」），本次是后端改动 ⇒ 一并 bump，重启后 `code_stale` 自动转绿。

### 闸门

- 新增三只 L0（hermetic、零网络、不 import `src.main`）：`test_gwprobe.py`(19) / `test_healthx.py`(8) /
  `test_sessions_export.py`(15) ⇒ **L0 271 → 313，`skipped=0`**；L1 host 35 全绿。
- **红对照（证明闸门不是恒真）**：把 `watch` 改成恒真 ⇒ `test_watch_detects_rename_not_stuck_true` FAIL；
  把脱敏改回"只扫顶层" ⇒ `test_nested_transcript_is_redacted` FAIL（2 failures）；复原后 313 全绿。
- 未跑：L2 live（需重启后才有新字段可验）、真浏览器探针（本次零前端改动）。
## v0.13.22 — 修「终端页焦点一掉，空格就丢」（Grok 会话窗口按空格出现重复文字）
- 报障（用户 09-25）：「Grok 的会话窗口不能使用空格键，使用就会出现重复的文字内容」
- 先立实测口径：**hub 的输入链路不会把空格发两遍** —— 影子实例里把 pty 设成 `-echo -icanon`
  交给 cat 逐字对账：`hello`→`hello`、1 个空格→`' '`、3 个空格→`'   '`、IME 上屏 `中`+空格各一份
- 真正会丢键的是**焦点**：焦点一旦落到终端外（手机上点过标题/会话芯片、桌面上点过页面任意处），
  按键既不进 pty 也没有任何提示（实测 `焦点=BODY` 时敲 `c`+空格+`d` ⇒ pty 实收 0 份）。
  用户下一步必然点一下终端再敲，而 Grok TUI 在主屏缓冲区反复重画整屏（首帧无 `?1049h` 备用屏）
  ⇒ 同一份文字在 xterm 里出现两遍，现象就被报成"空格一按就重复"
- 修法（`static/hub/06-manager-tasks.js`）：终端页可见 + 焦点不在任何输入位 ⇒ 把**可打印字符**
  （含空格）交给终端，并 `preventDefault` 挡掉浏览器把空格当翻页；组合键与 Enter/Backspace
  等非可打印键一律放行（不发明新语义）。抢焦点仍走唯一入口 `termFocusWanted({user:true})`
  ⇒ 由 `tests/test_term_focus_policy.py` 现场抓过一次（无守卫的 `term.focus()` 判红）后改正
- 新闸门 `tests/verify_term_key_relay.py`（L2 真键盘，10 判据）：A 焦点在终端里 `a b` 恰一份（改前也成立，
  作对照）· ★B 焦点在终端外 `c d` 恰一份（**改前必红**：实收 0 份）· C 搜索框敲 `e f` 时 pty 收 0 份
  且文字进搜索框（P2-11 口径不破）· D 文档与终端视口都不因空格滚动 · E 零 JS 异常
- 复验：`verify_key_focus_guard.py` 全部通过、`verify_claude_menu_term.py` 15/15 未回退、L0 271 / L1 35 全绿
- 上线方式：纯静态改动 ⇒ 不重启生产；`VERSION` bump 仍随下次后端改动同批

## v0.13.21 — 修「菜单点 Claude Code 进不去终端页」（前端即时生效，`VERSION` 未 bump）
- 现象（用户 09-25 报障）：左侧菜单点 Claude Code ⇒ 右侧一块白页（CloudCLI `:3010` 的登录页，实测首页文案
  "Welcome Back / Your session expired"），而**终端页在站内没有任何入口**
- 根因三处：① `openEntity()` 与 `defaultModeOf()` 各写了一份 `embed > term` 优先级，而 claude 恰是唯一
  同时带 embed（discovery 见 cloudcli 端口活就注入「原生会话」）与 term 的实体；② v0.12.3 把菜单行内动作
  图标 `display:none`、模式 tab 也已停用 ⇒ 进去就切不回来（当时的注释写着"入口不丢"，实测不成立）；
  ③ `gotoChat` 把**推导出的**形态也写进 `hub.chatmode.<id>` ⇒ 默认被固化成假偏好，只改默认救不回存量浏览器
- 修法：`defaultModeOf()` 升为形态**唯一真源**（有原生终端的 Agent 先给终端页），`openEntity` 改为复用它；
  偏好换键 `hub.chatmode2.<id>` 且只在用户**点名**形态时写盘（分档偏好不变量②）；embed/term 两个面板头各加
  一枚互切按钮（`#embedToTerm` / `#termToEmbed`，按该实体有无对应 entry 显隐）⇒ 嵌入入口保留、终端入口必达
- 影响面实测：`/api/agents` 里同时有 embed 与 term 的实体**只有 claude 一个**（pi/qwenpaw/网关/服务无 term
  入口 ⇒ 形态不变，真渲染闸门里以 Pi 作对照组）
- 闸门：`tests/verify_claude_menu_term.py` 真鼠标 15/15（红基线跑在修复前 `fa14a0d` 影子实例＝8/15，
  R3/R5/R7 三条同时 FAIL；探针刻意先把旧键污染成 `embed` ⇒ 证明存量浏览器自愈）
  + 新增 `tests/test_entity_mode_source_of_truth.py`（L0 6 例，钉住「形态优先级只允许一处 / 偏好键只有一个 /
  推导不写盘」，红对照取 `fa14a0d` 的 hub.js）；L0 265→271、L1 35 全绿
- 真鼠标 A/B（生产 :3102 vs `fa14a0d` 影子实例 :3198）逐实体比对：唯一差异＝claude 由 embed 变 term，
  其余形态判定改前改后一致 ⇒ 零回归
- 上线方式：纯静态改动 ⇒ **不重启生产**（Jinja auto_reload + `?v=` 内容派生提手即时生效；重启会 `kill_all()`
  掉用户正在跑的终端会话）；`VERSION` bump 与清 `code_stale` 按 AGENTS 口径随下次后端改动同批

## v0.13.20 — FCC 退役收尾（菜单不再列 FCC）
- `src/profiles.py`：删除 `fcc` 网关卡片（端口 8082 / 面板 18083 均已不存在）；`/api/agents`不再生成该条目
- 保留说明：`CLI_ALIASES` 里的 `fcc-*` 入口壳名（`fcc-codex`/`fcc-pi` …）不删，`which` 打不到即自然跳过；已加注释标记包于 09-24 卸载
- 背景与全部取证：台账 `PT-20260924-15`（FCC 与 CCR 四把上游 key 逐枚相同、provider 为 CCR 子集、
  Claude 档实为 Qwen 别名、今日真实请求 0）；知识条目 `agent-knowledge/45`

## v0.13.19 — P3 工具注册表门面 + P4 资产面板  （~~代码就绪、未上线~~ → **已亍 2026-09-25 03:0x 随 v0.13.20 的重启一并上线**）
- `/mcp/tools` 恢复上游 `inputSchema` 透传；截断必留痕（`description_truncated` + `description_chars`；
  `DOC_CHARS_MAX=160` 与旧字面量等价 ⇒ 行为零变化）
- 新增 `/mcp/registry`：按 agent 解算生效工具与 ACL（与 `/mcp/call` **同源解算**，不建新表、不可能漂移）
- `_resolve_acl()` = ACL 判定唯一真源（deny 覆盖 allow、与规则行序无关）；模块文档串与实现对齐
- `MCP_LIST_RETRY_S` 失败短缓存；`probe` 绕缓存（读写两侧都绕）
- 🔧 根因修复：`mcp_call` 不再无条件 `db.init_db()` 重指全局连接（曾致测试写入生产库）；
  `src/db.py` 新增 `is_open()` / `current_path()`
- 前端新增「资产」页 `/assets`：工具/记忆/知识/技能四路统一台账，**六态诚实区分**
  （有结果 / 确实零命中 / 待输入检索词 idle / 按设计未接入 unwired / 部分后端不可用 / 端点不可用）
- 闸门：L0 265（0 skip）· L1 35 · `verify_mcp_facade` 44/44 · **`verify_asset_panel_live` 真渲染 25/25**
- 上线影响实测：**只在生产存在的路由 0 条**（净新增 4 条），写端点 401 闸门出自 `src/writeauth.py`（HEAD 即有）

## v0.13.18 — P2 技能门面（未单独上线，已并入上面那批）
## ⚠️ v0.13.17 / v0.13.16 — 本仓已不可达（09-24 18:2x–18:5x `.git` 被替换为 08:48 快照）
- 丢失：`src/ls_guard.py`、`src/ls_probe.py`、`tests/test_ls_guard_live.py`、本会话的 `tests/verify_ls_guard.py`
- 实测影响＝**对现网零回归**：生产进程 12:36:22 启动、`src/__pycache__` 内无这两个模块的 .pyc
  ⇒ 属「从未上线的在制品丢失」，处置见 `PENDING-TASKS.md` → `PT-20260924-13`

---

## 历史（已上线部分，按提交倒序；`附带` ＝ 该版本内非版本号的提交）

## v0.13.15 — opencode 历史会话接入：sessions_store 补 opencode_sqlite 适配 + 前端白名单同步  (2026-09-24)

## v0.13.14 — 修「新装 CLI 进不了菜单」：scan/run 补定向重判 + 候补序真名优先；opencode 补 L4 探针  (2026-09-24)

## v0.13.13 — 静态与存储两层加固：localStorage 全量加守卫、启动判定去 commit 指针依赖、immutable 不再发校验器  (2026-09-24)

## v0.13.12 — 窄屏收起行为不再依赖 origin 存量（修"局域网会收/Tailscale 不会收"）  (2026-09-24)

## v0.13.11 — 根治 APP 侧 `_navHtml` TDZ（顶层 IIFE 早于 let 声明）+ ?v= 内容派生与 immutable 缓存口径  (2026-09-23)

## v0.13.10 — 端侧自检面板补三项定罪字段：异常行号 / 真实字节 / 符号阶梯  (2026-09-23)

## v0.13.9 — go() 校验 hub.page 坏值并回退 + 端侧自检面板 ?diag=1（17/17 红绿闸门）  (2026-09-23)

## v0.13.8 — 修窄屏「设置」抽屉盖住整页且导航不关（浮层唯一性三条不变量）  (2026-09-23)

## v0.13.7 — 窄屏白板事故的两只线上探针入册 + tests/README 计数纠漂  (2026-09-23)
- 附带： `4c86130` v0.13.7 vitals 菜单门禁补实：menu_noul 判定不再要求 source=='jev'
- 附带： `babcbea` v0.13.7 修窄屏抽屉白遮挡：侧栏折叠偏好改「一档一键 + 加载不写盘 + 断点单源」

## v0.13.6 — hub.js 源码拆分：6 个 part + 纯拼接构建，产物逐字等价（md5 c6ec4dcc 不变）   拆的是源码组织，不是运行时形态：模板仍引 /static/hub.js，缓存键、前端探针读的   文件、生产进  (2026-09-23)
- 附带： `a08b1ce` v0.13.6 探针跟上闸门：chat 需带 x-hub-token（不带凭据拿到的 401 是正确行为，不是缺陷） 实测：带凭据 4.1s success=True model=qwen3.8-flash response='收到收到' 
- 附带： `0a172c4` v0.13.6 P1-7 扩展：34 条写路由统一到中间件闸门 + 堵掉 MCP ACL 的"自报家门即免检"
- 附带： `d0d7cab` v0.13.6 探针自修：chat 回复字段是 response（第一版按 reply/text 读，把成功判成空）   实测顶层键 agent_id/session_id/message/timestamp/success/agent/r
- 附带： `426c937` v0.13.6 生产体检探针 verify_prod_smoke.py：一轮取齐 19 项，且不打扰在用终端   重启是要紧动作，而 09-23 立了「探活最多 2 次即停」—— 所以体检必须单轮复合，   不能一个端点一个端点串行试。**
- 附带： `8ca5ac2` v0.13.6 修 agent-hubctl.sh 自调用：`bash agent-hubctl.sh restart` 时 $0 没有 ./，尾巴静默失败
- 附带： `f6e83cb` v0.13.6 收尾：Batch C 记账 + 四个浏览器探针登记进 tests/README

## v0.13.5 — P2-10/11：焦点只跟"用户主动"，全局快捷键加 target 守卫  (2026-09-23)
- 附带： `a9c5b4f` v0.13.5 P2-9：终端帧改跨帧共用解码器 + DECSET 扫描带尾巴（原计划只当性能项，实测是画面错误）
- 附带： `f761e50` v0.13.5 P2-8：白屏自愈改按「视口行」判空 —— 此前只要有 scrollback 它就永不触发
- 附带： `f72b1a6` v0.13.5 P2-12 改判后落地：回放闸门补上 DCS（DECRQPS 会被作答，此前完全没覆盖）
- 附带： `1c12070` v0.13.5 P1-7：终端"清单/销毁"补服务端鉴权 —— 堵住「列 → 拿 sid → 杀」这条局域网打断链
- 附带： `97457d2` v0.13.5 修回归：上一提交把 `async def vitals_loop():` 函数头吃掉了（服务起不来）+ 加语义级 L0 护栏
- 附带： `510ea9d` v0.13.5 P0-2 补强：/health 新增 code_matches_head —— 未提交代码在生产跑时不再自称"跑的是 HEAD"

## v0.13.4 — L4 探活预算闸门：一轮最多 2 次即停（2026-09-23 用户裁定）  (2026-09-23)

## v0.13.3 — 补终端配色端到端渲染探针（CDP），并做红-绿验证证明它不是恒绿  (2026-09-23)
- 附带： `58e44f1` v0.13.3 嵌入式终端统一为原生黑底白字（2026-09-22 用户裁定，参考 grok 观感）

## v0.13.2 — 补「续聊起在会话自己的目录」的验收探针与回退分支单测 - tests/probe_resume_cwd.py：可复跑的端侧探针，三条判据   ① 回执 cwd == 历史条目自己的 cwd  ② /proc/<pid>/  (2026-09-22)
- 附带： `000bb6b` v0.13.2 版本号 + README 记两条新裁定（跨目录 5 条、续聊进对应目录）
- 附带： `1f22e34` v0.13.2 历史改为跨目录取最近 5 条；续聊时终端起在「那条会话自己的目录」

## v0.13.1 — 版本号 + README 记一条缺陷口径（平铺仓库不得先截断再过滤）  (2026-09-22)
- 附带： `7020def` v0.13.1 修左侧历史不完整：jcode 平铺目录不得「先取最新20再过滤」

## v0.13.0 — 版本号 + spec/plan 归档（含 7 条执行偏差回写）  (2026-09-22)
- 附带： `5d961a6` v0.13.0 前端：左侧历史会话下拉 + 顶栏芯片去字母（问题原文当标题）
- 附带： `cd92ee8` v0.13.0 后端：会话仓库适配层 + history/resume API + 活会话中文标题

## v0.12.4 — 存底（原样入库，零改写）：静态资源 no-store→no-cache+ETag304/gzip、终端白屏自愈(termWriteReplay/termRepaint/termHealBlank)+连接中走马灯  (2026-09-22)

## v0.12.5 — 侧栏收到 240px + scan 项名称截到主谓部分  (2026-09-22)

## v0.12.3 — 左侧菜单：恢复在线/离线方块，删除行内状态文字  (2026-09-22)

## v0.12.2 — 宽屏左侧菜单：只留「名称 + 状态」，滚动条隐藏  (2026-09-22)

## v0.12.1 — 窄屏抽屉：状态紧贴名称，行内空白移到行尾  (2026-09-22)
- 附带： `3267f7f` v0.12.1 窄屏抽屉只留「名称 + 状态」
- 附带： `2c14686` v0.12.1 侧栏名称优先完整显示，状态改定宽滚动窗

## v0.12.0 — 判定分层：自检定生死、模型应答只出情报  (2026-09-22)

## v0.11.1 — 版本号  (2026-09-22)
- 附带： `02a99a6` v0.11.1 判定不可用统一跳过不出卡（含静态画像）

## v0.11.0 — 可用性判定层 vitals：装了≠能用，假卡出菜单、抖动不固化  (2026-09-22)

## v0.9.0 — 黑白体系重构：拆「浅灰画布 + 纯白内容面」两层；辅助文字与内容框对比度全部达 WCAG AA  (2026-09-19)

## v0.8.1 — 选中态改半透明；修复嵌入式终端把鼠标上报灌进 pty 刷乱码  (2026-09-18)

## v0.8.0 — UI 设计令牌化：字号音阶 + 中文优先字体栈 + SVG 图标 sprite；补语义色层；嵌入式终端接入 token 并修复底部被裁  (2026-09-18)

## v0.7.2 — 移除排序按钮；终端会话仅显示当前选中agent；优化中文字体栈  (2026-09-18)
