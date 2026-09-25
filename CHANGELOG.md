# CHANGELOG

> 生成口径：`git log` 机械提取（版本号只在提交主题开头出现才起一节），另由人补「未上线批次」一节。
> 本文件只记「哪一版上线了什么」；施工过程与证据留在 `PENDING-TASKS.md`（PT 编号台账）。
> 生成时间 2026-09-24 19:3x（生成器＝一次性脚本，未入库；重跑请复制本文件头部的口径）。

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
