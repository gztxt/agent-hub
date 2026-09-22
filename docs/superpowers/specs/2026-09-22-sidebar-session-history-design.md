# 设计：左侧菜单 Agent 历史会话下拉（v0.13.0）

- 日期：2026-09-22　目标版本：v0.13.0　仓库：`/home/gztxt/agent-hub`
- 需求方：gztxt　设计/待执行者：pi（**执行者须由用户指定，见 §9**）
- 基线依赖（21:50 复核实况）：菜单那笔已入库（`b1c1926 v0.12.3` 恢复方块·删行内状态文字、`cc41636 v0.12.5` 侧栏收 240px），
  但同仓**仍有未提交的在改**：`src/main.py` +43 / `static/hub.js` +146（终端切换重绘修复）/ `templates/index.html` +13。
  本设计触碰同一批文件同一区域 ⇒ **开工前置 = 工作树干净（`git status --porcelain` 只剩 `?? docs/`）+ 用户点名我为唯一执行者**（09-07 并发施工事故同源风险）。
  届时 HEAD 版本号以实际为准，**设计内不写死任何宽度/间距**：一律读当期 CSS 变量（`--sb-w` 现值 240px；`--nav-st-w` 已随 v0.12.3 删除，勿再引用）。

## 1. 问题

终端页「看不到会话记录」，且现有可见记录是一串十六进制字母，读不出内容。实测根因三条：

1. `src/term.py:163` `GET /api/term/sessions` **只返回内存中的活会话**：hub 重启即清零、空闲 `TERM_IDLE_TTL=2700s` 回收，注释明写「已退出记录不再以僵尸条目出现」→ 绝大多数历史根本不在返回集里。
2. `static/hub.js:641` `termChipHtml()` 用 `String(s.id).slice(0,4)` 当标签 → 用户看到的是 `a3f9`/`7b21` 这类**字母编号，不含任何语义**。
3. 09-20 按用户指令删掉「暂无活会话」占位字后，无活会话时那一行**纯空白**，观感等同"坏了"。

用户裁定方向：会话记录**不以字母/数字代替，以问题原文当标题**；入口放到**左侧菜单 agent 名称下方，点击展开下拉**；点条目直接在该 agent 终端里**接着这条历史继续对话**（可续 = 走各 CLI 自己的 resume）。

## 2. 取证矩阵（全部实测，无推测项）

| agent | hub 终端画像 | 磁盘历史仓库 | 标题来源（优先级） | 该 cwd 条数 | resume 参数（`--help` 逐字） | 判定 |
|---|---|---|---|---|---|---|
| grok | ✅ cwd=`/fs/1000/ftp/技术文档` | `~/.grok/sessions/<URL百分号编码cwd>/<uuid>/summary.json`（字段 `info.id`/`info.cwd`/`session_summary`/`created_at`/`updated_at`/`num_messages`） | `session_summary` → 首条 user → `未命名会话` | **88** | `-r, --resume [<SESSION_ID_OR_TITLE>]` | ✅ 上 |
| claude | ✅ 同上 cwd | `~/.claude/projects/<slug(cwd)>/<uuid>.jsonl` | 首条 user 消息（实测 `'你好'`；该格式**无** title/summary 字段，实测类型统计已确认） | **37** | `-r, --resume [value]` / `-c, --continue` | ✅ 上 |
| jcode | ✅ 同上 cwd | `~/.jcode/sessions/session_<动物名>_<epochms>_<hex>.json`（`session-metadata-v1.sqlite3` 的 `recent_sessions` 会被 prune，**只作辅助不作准**；`ambient/transcripts/` 空） | `todo_title`（118 行仅 3 条有值）→ 首条 `role==user && display_role!="system"` → `short_name` | **89** | `--resume [<RESUME>]`（不带 ID 会进交互选择器）＋ `-C, --cwd <CWD>` | ✅ 上 |
| hermes | ✅ cwd=`/home/gztxt` | **`~/.hermes/state.db`** 表 `sessions`(46列)+`messages`；`~/.hermes/sessions/` 已 8-05 停写（旧导出）、`terminal-sessions/` 只是 pts→会话指针 | `sessions.title`（实测中文：`修复Claude网关连接错误方案`）→ 首条 active user 消息 | cwd 过滤仅 20，**152/209 条 `cwd` 与 `title` 双 NULL** | `--resume SESSION, -r SESSION`（`ID or title or 'latest'`）、`--no-restore-cwd`、`--in DIR` | ✅ 上（**不按 cwd 过滤，改按 `source='cli'`**） |
| codex | ✅ cwd=`/fs/1000/ftp/技术文档` | **`~/.codex/state_5.sqlite`** 表 `threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,archived,has_user_event)`；`created_at/updated_at` = epoch 秒；rollout jsonl 按**日期**分桶（`~/.codex/sessions` 是软链 → `技术文档/会话备份/codex/sessions`，故 `find` 不带 `-L` 会假报 0） | `threads.title`（实测 35/35 全有，中文：`修复QwenPaw不能使用的问题`） | 5，其中 **`source='cli'` 仅 1 条**（其余是 `codex exec` 探针） | `resume [OPTIONS] [SESSION_ID]`、`--last`、`--include-non-interactive`、`-C, --cd <DIR>`、`--all`(关闭 cwd 过滤) | ✅ 上（**只列 `source='cli'` 且 `cwd` 命中**） |
| qoder | ✅ 同上 cwd | `~/.qoder/projects/<dash编码cwd>/<sessionId>.jsonl`（编码＝逐字符把非 `[A-Za-z0-9]` 换 `-`，实测 `/fs/1000/ftp/技术文档` → `-fs-1000-ftp-----`） | `last-prompt` 行的 `lastPrompt` → 首条 user（**无 AI 摘要**，实测值 `hi`） | **0**（该目录仅 4 个只有 `state.json` 的无正文骨架；`qodercli --list-sessions` 原文 `No previous sessions found for this project.`） | `-r, --resume [id]` ＋ `-w, --cwd <dir>`（严格按 cwd 分仓，必须带 `-w`） | ✅ 上（当前必然显示空态，用到就有） |
| pi | ❌ `terminal: None` | `~/.pi/agent/sessions/--<编码cwd>--/*.jsonl`；现成索引 `~/.pi/agent/pi-web-session-index.json`（236 条，含中文 `firstMessage`） | `firstMessage` | 210 | `--session <path|id>`、`-r, --resume`；须 `/home/gztxt/.nvm/versions/node/v24.18.0/bin/pi`（PATH 里的 `pi` 已 09-21 卸载，裸跑 node 版本不匹配报 SyntaxError） | ❌ **本期不上**（用户裁定；需新增终端画像＋写死绝对路径，属另一件事） |
| shell(bash) | ✅ | 无会话仓库 | — | — | — | ❌ 不显示历史入口 |

**会话 id 形状（供校验正则）**：grok/claude/codex/qoder = UUID；jcode = `session_[a-z]+_\d{13}_[0-9a-f]{16}`（⚠️ `short_name` 不唯一，实测 117 文件仅 74 个 distinct，`sheep`×3 → **必须传完整 session_id**）；hermes = `\d{8}_\d{6}_[0-9a-f]{6}`。

## 3. 决策记录（用户裁定，逐条）

| # | 裁定 |
|---|---|
| D1 | 行为＝**A：点历史条目直接在终端里 `--resume` 续聊**（不做只读消息流渲染） |
| D2 | 呈现＝**问题原文当标题**，禁止字母/数字编号出现在用户可见处（含顶部芯片） |
| D3 | 位置＝**左侧菜单 agent 行下方展开**；点 agent 行 = 进工作台 **+** 展开该 agent 历史；再点当前已展开行 = 只收起；同一时刻只展开一个 |
| D4 | 范围＝**全上**：grok + claude + jcode + hermes + codex + qoder；**pi 本期不上** |
| D5 | codex 只列 `source='cli'`（避免把 `codex exec` 探针当历史） |
| D6 | 每 agent **默认 3 条**，按最近活跃倒序；本期**不做「显示全部」入口**（API 保留 `limit` 参数，前端要加只需改一处常量） |
| D7 | 只显示 profile cwd 的会话（hermes 例外：`cwd` 大量为 NULL，按 `source='cli'` 全量） |
| D8 | 施工顺序：等 09-22 在跑的改动提交落地、工作树干净后开工（用户选①）；开工前重新核一次当期 `--sb-w` 与菜单行结构 |

## 4. 架构

### 4.1 新模块 `src/sessions_store.py`（唯一新增后端文件）

单一职责：**只读**地把「某 agent 在某 cwd 下的历史会话」读成统一结构；不写、不删、不改任何 agent 的仓库。

```python
SESSION_STORES: Dict[str, dict] = {
  "grok":   {"kind": "grok_dir",     "id_re": UUID_RE,       "resume": ["grok",   "--resume", "{id}"]},
  "claude": {"kind": "claude_dir",   "id_re": UUID_RE,       "resume": ["claude", "--resume", "{id}"]},
  "jcode":  {"kind": "jcode_json",   "id_re": JC,            "resume": ["jcode",  "--resume", "{id}"]},
  "hermes": {"kind": "hermes_sqlite","id_re": HM,            "resume": ["hermes", "--resume", "{id}"]},
  "codex":  {"kind": "codex_sqlite", "id_re": UUID_RE,       "resume": ["codex",  "resume",   "{id}"]},
  "qoder":  {"kind": "qoder_dir",    "id_re": UUID_RE,       "resume": ["qodercli","-w", "{cwd}", "-r", "{id}"]},
}
```

统一条目（一次 `list_history()` 的返回元素）：

```json
{"agent":"grok","id":"01a0c91d-…","title":"agent hub 宽屏 左侧菜单 1.不要有滚动条…",
 "ts":1790072862, "msgs":205, "cwd":"/fs/1000/ftp/技术文档"}
```

适配器要点（每条都对应 §2 的实测事实）：
- **grok**：目录名 = `urllib.parse.quote(cwd, safe='')`；每子目录读 `summary.json`；缺文件退回 `chat_history.jsonl` 首条 user。
- **claude / qoder**：slug 编码各按 §2 规则正向生成（汉字 → `-`），**不做逆向解析**（逆向有歧义：`-fs-1000-ftp-----` 解不回 `/fs/1000/ftp/技术文档`）。排序用文件 mtime；标题只读文件**前 64KB** 找首条 user / `last-prompt`。
- **jcode**：枚举 `~/.jcode/sessions/*.json`，**按 stem 去掉同名 `.bak`**（实测 114 个 .bak）；`working_dir` 字段过滤；时间取 `last_active_at`（ISO8601 **纳秒**，Python 侧截到微秒再解析）。
- **hermes / codex**：sqlite 一律 `sqlite3.connect("file:<path>?mode=ro", uri=True)`，**只 SELECT**，SQL 带 `ORDER BY … DESC LIMIT ?`。hermes 排序 `last_activity_at DESC`（REAL epoch 秒）；codex 排序 `updated_at DESC` 且 `WHERE source='cli' AND cwd=? AND archived=0`（**时间列实测为 epoch 秒整数**：`updated_at=1789928341 → 2026-09-21`；实测唯一命中行 `{id:01a0bffd-…, title:'修复QwenPaw不能使用的问题', source:'cli', archived:0, has_user_event:0}` ⇒ **`has_user_event` 不可当过滤条件**，那条真会话是 0）。
- **兜底标题**：三者全空 → `未命名会话 MM-DD HH:MM`；**绝不回落成 id 前缀**（那是 D2 要禁的东西）。
- **上限**：每 agent 最多扫 60 个候选（mtime 倒序），命中 `limit` 条即停；整次 `list_history()` 硬超时 1.5s，超时返回已取到的部分 + `note:"扫描超时，仅显示已读到的 N 条"`。

### 4.2 API（都挂 `term.py` 的 router，复用现网 `TERM_TOKEN` 校验与 `x-term-token`/`?token=` 双通道）

| 端点 | 行为 |
|---|---|
| `GET /api/term/history/{agent_id}?limit=3` | 返回 `{items:[…], cwd, source:"disk", note}`；无画像终端入口 → 400；仓库不存在/读失败 → `items:[]` + `note` 中文说明（**不用 5xx 打断菜单**）。按 `(agent_id,cwd,limit)` 缓存 15s（防 30s 轮询反复扫盘） |
| `POST /api/term/sessions`（扩字段） | body 增可选 `session_id`。校验链：`TERM_TOKEN` → 画像有 `terminal` → `id_re.fullmatch(session_id)` → **该 id 必须出现在刚列出的实盘清单里** → 用 `resume` 模板拼 argv → `profiles.which()` 解析可执行 → `Session()` 起 pty。任一步不过：400/404，**不回显任何命令串** |

安全模型不降级：客户端**永远传不了命令**，只能传 `agent_id` + 白名单正则形状且实盘存在的 `session_id`；argv 模板写死在后端表里，等价于现有「画像白名单」的延伸。

### 4.3 前端

**`templates/index.html`**：新增 `.nav-hist` 一块样式（承接 v0.12.3/v0.12.5 当期口径：行内只有「状态方块 + 名称」，无状态文字；历史行高与 `.nav-item` 同级、字号 `--fs-xs`、左内缩 1 个图标位，宽度全部走 `--sb-w`/既有 token，不写死 px）。不加滚动条、不加边框盒，保持 09-22「菜单不出滚动条」与 09-22「侧栏收到 240px」两项要求。

**`static/hub.js`**：
- 新状态 `histOpen`（当前展开的 agent id，`localStorage['hub.hist']`）+ `HIST`（`{agent_id: {items,note,ts,loading,err}}`）。
- `navItemHtml(a)` 行内**不加任何新图标**；改由 `renderNav()` 在该 agent 的 `<button class="nav-item">` 之后追加 `<div class="nav-hist">…</div>`（仅当 `histOpen===a.id`）。历史块随 `_navHtml` 一起参与 diff；数据源是 JS 缓存，所以 30s `loadAgents` 刷新**不会闪空白**（异步回来后 `renderNav()` 重绘）。
- 时间一律绝对格式 `MM-DD HH:MM`（**不用「刚刚/N 分钟前」**——相对时间每轮变化会打破 diff 稳定性，导致滚动动画与展开态被重绘打断）。
- 每行：`标题`（CSS 单行截断，全文进 `title` 属性）＋ 右侧时间；整行 `onclick=termResume(agent,id)`。
- 三种非成功态各出一行灰字（有信息量，不留空白）：`加载中…` / `该目录 0 条可续会话（qoder：仅 4 条无正文骨架）` / `历史读取失败：<原因>`。
- 点 agent 行（`button[data-entity]` 委托处）：`histOpen!==id` → 置开 + 发 `GET history` + 照旧 `openEntity(id)`；`histOpen===id` → 只收起（**不离开页面**）。
- 新 `termResume(agent_id, sid)`：`POST /api/term/sessions {agent_id, session_id}` → `gotoChat(agent_id,'term')` → `termConnect()`；成功后顶栏芯片由中文名接管。

### 4.4 顶部芯片去字母（D2 的后半）

活会话标签**不再显示 hex sid**，改按 pid 关联各 agent 自己的活会话登记表取中文标题：

| agent | 登记表（实测存在） | 取到 |
|---|---|---|
| grok | `~/.grok/active_sessions.json` → `{session_id,pid,cwd,opened_at}` | ✅ |
| claude | `~/.claude/sessions/<pid>.json` → `{pid,sessionId,cwd,startedAt,status}` | ✅ |
| hermes | `~/.hermes/runtime/active_sessions.json` → `pid → session_id` | ✅ |
| jcode | `~/.jcode/active_pids` 是**目录**（实测 18:27 后为空，取不到样本条目） | 无样本 → 走兜底 |
| codex/qoder | 无 pid 映射 | 走兜底 |

兜底文案 `新会话 MM-DD`（新建、agent 尚未落摘要时，以及 jcode 这类暂无 pid 映射样本的）。hermes 登记表结构实测为 `{"entries":[{pid, session_id, started_at, surface, …}]}`（`pid→session_id` 直取）。关联方式：`Session.pid`（pty 子进程）↔ 登记表里的 `pid`；实现在 `sessions_store.live_titles(agent_id)`，`termRefreshList()` 拿到活会话后合并标题。`termKillOne`/`termConnect` 的 `.cur`/DOM 逻辑不变。

## 5. 安全与隐私

- 新端点全部要求 `TERM_TOKEN`；失败只记一行拒绝原因，**不记 token 值**（沿用 `term.py:_check_term_token` 口径）。
- 只读：sqlite 全 `mode=ro`；不删 `.bak`、不碰 `*.lock`、不跑 `codex migrate-rollouts --apply`（会改状态索引，属另一件事）。
- **标题密钥遮蔽**：历史标题就是用户原话，本机有过 token 明文入库的前例（CCR Web 入口）。`sessions_store._mask()` 统一处理：`(token|api[_-]?key|secret|password|passwd)\s*[=:]\s*\S+` → `<masked>`，以及裸 24+ 位 `[A-Za-z0-9_-]` 串 → `<masked>`。只在展示层遮蔽，不改磁盘。
- 不读取 `~/.hermes/whatsapp/`（登录凭据类目录）。
- 菜单不出隐私到公网：hub 仍绑 127.0.0.1 / 局域网信任域，本期不改绑定面。

## 6. 验收（逐项附真实输出，任一 FAIL 不称完成）

1. `GET /api/term/history/grok?limit=3` 返回 3 条，`title` 为中文问题原文，**无十六进制 sid 出现在任何 title 字段**。
2. 逐 agent 点开下拉：grok(88) / claude(37) / jcode(89) / hermes(全量 cli) / codex(1) 出条目；**qoder 出空态灰字说明**（不是空白）。
3. 点击续聊：五个 agent 各点 1 条 → 终端里出现该历史上下文，且让它跑一次**真工具调用**（`hostname` 回显 = 主机名）→ 军规「文本通 ≠ 配置对」的工具环要求。
4. 顶栏芯片：新建 grok 会话后先显示 `新会话 HH:MM`，落摘要后刷新变中文标题；**画面里不再出现 `a3f9` 这类字母编号**。
5. 交互 D3：点 A 展开 A → 点 B 收起 A 展开 B → 再点 B 只收起且不离开页面；`localStorage['hub.hist']` 复位可见。
6. 稳定性：展开历史时 30s 自动刷新连续过 2 轮，DOM 不闪、展开态不丢（`_navHtml` diff 生效）。
7. 窄屏（<768px 抽屉）：历史块跟随当期侧栏宽度（现 `--sb-w: 240px`）不撑破、不出横向滚动条；手机真机点一条能续聊。
8. 全程 3102 生产进程**不重启**、您在跑的会话不被打断；验证走临时端口 3199 旁路，生产切换命令单独交给您。
9. 回归：`GET /api/term/sessions` 与 `POST {agent_id}`（不带 session_id）行为不变；kill 会话、TTL 回收、hub 重启全销毁三条老行为不变。

## 7. 施工规程（受 `/fs/1000/ftp/技术文档/AGENTS.md`「共享配置变更军规」约束）

1. 开工前置：`git status --porcelain` 除 `?? docs/` 外必须为空（在改的 main.py/hub.js/index.html 已各自成 commit）；若几件事仍混在一笔里，**先拆成独立可回滚的提交再叠本设计**。
2. 待动文件逐个先读现值 → 时间戳备份：`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<说明>`（`src/sessions_store.py` 为新文件免备份；`src/term.py`、`static/hub.js`、`templates/index.html` 必须备份）。
3. **禁 `sed -i`／全局替换**；改动一律定点 edit。
4. 提交拆两笔、各自可回滚：`v0.13.0 后端：会话仓库适配层 + history/resume API` / `v0.13.0 前端：左侧历史下拉 + 芯片中文化`。
5. 端到端证据不齐不称完成；不通过回滚本次备份，不在故障态叠加下一处改动。

## 8. 非目标（本期一律不做）

pi 终端画像与 pi 历史（D4）·「显示全部」/分页/搜索 · 跨 cwd 汇总 · 只读消息流渲染 · 删除/重命名历史条目 · 清理 codex legacy rollout / hermes 停写目录 · 改动 hub 端口或绑定面 · 会话内容做语义检索（那是 memory 线的事）

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 与在跑的 grok 施工会话并发改同批文件（09-07 事故形状；21:50 实测仍有 +146/+43/+13 未提交） | **D8**：等其结项提交后才动；开工前 `git status --porcelain` 必须干净，否则只取证不动手 |
| 大仓库扫描拖慢菜单（jcode 117 json / claude 37 个大 jsonl / grok 88 目录） | 60 候选上限 + 64KB 头部读 + 15s 缓存 + 1.5s 硬超时降级 |
| 标题含口令/密钥明文 | §5 `_mask()`；只在展示层 |
| 中文标题被字母数字串误伤（如 `v0.12.1`、`P2`） | `_mask()` 只打 24+ 位长串，短 token 不动 |
| resume 语义按 cwd 过滤（codex picker / qoder 分仓 / jcode `-C`） | 一律带 profile cwd 起 pty；qoder 显式 `-w <cwd>`；hermes **不加** `--no-restore-cwd`（让它自己 cd 回记录目录） |
| 动物名不唯一导致 jcode 续错会话 | 只传完整 `session_id`，禁 `short_name` |
| 展开态被 30s 重绘冲掉 | 展开态与数据都在 JS + `localStorage`，HTML 里只画结果 |

## 10. 回滚

- 前端笔出问题：`git revert <前端 commit>`（历史下拉消失，终端功能不受影响）。
- 后端笔出问题：`git revert <后端 commit>` 并确认 3102 未重启；`.bak-*` 文件作二次兜底。
- 两笔互相独立：`sessions_store.py` 是新增文件，revert 即整体摘除，无残留写路径（本期零磁盘写入）。
