# CHANGELOG

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
