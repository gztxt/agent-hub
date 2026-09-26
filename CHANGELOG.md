# CHANGELOG

> 生成口径：`git log` 机械提取（版本号只在提交主题开头出现才起一节），另由人补「未上线批次」一节。
> 本文件只记「哪一版上线了什么」；施工过程与证据留在 `PENDING-TASKS.md`（PT 编号台账）。
> 生成时间 2026-09-24 19:3x（生成器＝一次性脚本，未入库；重跑请复制本文件头部的口径）。

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
