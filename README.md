# Agent Hub（智管 Web）

本机多 Agent 统一管理中心（无头 Web 版），**语义移植自 [Zafer-Liu/Agent_Manager](https://github.com/Zafer-Liu/Agent_Manager) v0.3.0（Apache-2.0）**——
移植数据模型/协议口径/交互语义，未复制上游源文件；桌面形态（进程起停/Tauri 命令/
Qdrant sidecar）按本机 NAS 军规有意裁剪。评估全文见
`/fs/1000/ftp/技术文档/Agent_Manager_融合评估报告.md`。

## 功能（v0.2.0，2026-09-06 融合完成）

### 实体画像与分类（2026-09-06 P1/P2 增补，用户批准）
- **画像驱动检测**（`src/profiles.py`）：按本机实际运行三路判定（/proc 进程正则 + systemd user 单元 + docker 容器），**端口只作端点发现不作存在性判据**；kind 五分：`agent/gateway/service/memory/tool`
- CLI 型 Agent 三态：running（有进程）/ installed（which 可拉起，含 ~/.local/bin 兜底）/ stopped；Hermes/JCode/CloudCLI 等已入画像
- **卡片入口数据驱动**：Agent=原生会话(embed)/终端(term)/对话(chat)/↗UI；网关·服务·工具=**仅快捷方式**（打开面板/详情），无对话按钮
- 教室页分区：座位=仅 Agent；「⚙️基础设施」折叠区放网关/服务/记忆/工具
- **统一对话页三模式**：①有原生 Web UI 的 Agent（pi/qwenpaw/cloudcli）→ iframe 即时嵌入（实测无 XFO 阻塞）；②CLI Agent（claude/jcode/hermes/shell）→ **hub 自建 pty 终端**（`src/term.py`：WebSocket + xterm.js 本地化于 static/vendor/，命令白名单=画像 terminal.cmd，任意命令注入 400 拒绝，空闲 TTL 45min，重启全销毁）；③带上游模型的 Agent（claude/jcode，经 CCR OpenAI 兼容口）→ hub 内直连对话框（含模型 / 工作目录 / 会话切换）。**v0.10.0 起移除「智管对话」（hub-self）**——原自带 LLM 对话框功能初级、实测长期无人使用，已整体删除
- 终端鉴权：可选 `TERM_TOKEN`（ws ?token=）；bash 卡可 `TERM_ALLOW_BASH=0` 关闭
- favicon 404 已修（内联 SVG data-URI）

| 模块 | 来源 | 实现 |
|---|---|---|
| 教室视图 Dashboard（讲台+座位+状态动画） | Dashboard.tsx 语义 | templates/index.html |
| Manager Agent 自然语言指挥官 | mcp_agent.rs::manager_chat | src/manager.py + src/llm.py（FCC Anthropic 工具环，服务端真执行，会话落盘）|
| 统一对话（claude/jcode→CCR:3456，含 usage/历史回放）| — | src/adapters/openai_compat.py + registry.py |
| 三层记忆中心 L1/L2/L3 + 注入 context | memory_* 子系统 | src/memory.py + db.py（LLM 压缩重建，manual 区隔离）|
| Hook 遥测端点（覆盖式 upsert + 用量聚合）| agent_http.rs/telemetry_store.rs 口径 | src/hook.py（+Bearer 鉴权补上游缺）|
| 端口管理（枚举+归属+Agent 标注，**只读无 kill**）| ports.rs | src/ports.py |
| 项目类型自动识别 + 自定义 Agent 注册 | scan_project_dir | src/sources.py + main.py |

### 左侧历史会话下拉（v0.13.0，2026-09-22）

- 点 agent 名＝进工作台 + 展开该 agent 的历史会话（同时只展开一个，再点同一行只收起）
- 数据源是**各 CLI 自己的盘上仓库**（`src/sessions_store.py` 只读适配层，不写不删不过滤缓存），覆盖 grok / claude / jcode / hermes / codex / qoder；pi 无会话仓库，不进集合
- 标题＝**用户当初那句问题原文**（非 agent 自生成摘要、非字母 sid），命中口令/长密串则糊为 `<masked>`
- 默认 **5 条**，**跨目录**按时间取最近（v0.13.2 裁定，撤销「只显示画像目录」）；条目目录≠画像目录时行尾标出目录尾名
- 时间统一绝对 `MM-DD HH:MM`（相对时间会破侧栏 DOM diff）
- 续聊时终端起在**那条会话自己的目录**（`session_cwd()`，目录已不存在则退回画像目录）——跨目录之后这条是必需项
- 「先取最新 N 个候选再按目录过滤」是 v0.13.0 的一个真缺陷（jcode 平铺混 cwd，被探针占满窗口 ⇒ 89 条只显 1 条）；
  v0.13.1 改为全量遍历 + 字节级 `working_dir` 预筛，且续聊存在性校验不再依赖被截断的展示清单
  （v0.13.2 进一步改为跨目录，过滤条件整体移除，但该教训仍然成立：**平铺混 cwd 的仓库不可先截断再过滤**）
- 点条目＝终端里 `--resume <id>` 续聊；命令只出自后端模板，客户端最多传一个过正则且实盘存在的 id
- 顶栏会话芯片同步改中文标题：pid 反查拿不到时走 `resume_of` 直查盘上标题（jcode 只在退出时写 last_pid，codex/qoder 无 pid 登记表）

### 嵌入式终端配色：原生黑底白字（v0.13.3，2026-09-22 裁定 / 09-23 落地）

- 用户裁定：所有需要终端界面的 agent 一律参考 grok 原生终端观感——**黑底白字**，不要浅色底
- 落点是**单点**：`templates/index.html` 的 `--term-*` token（全站唯一 xterm 实例只读这套 token，`hub.js` 不写死色值），
  改一处即 claude / grok / hermes / codex / pi / jcode 同步生效，天然满足「所有终端界面统一」
- `hub.js` 的 `T()` 兜底值同步改深色：token 缺失（CSS 未加载）时**也不回退浅色**，避免白底闪现
- ANSI 16 色取 xterm.js 默认板（与 grok 原生终端同族），黑底上可读；原浅底那套低饱和板已作废
- 取证（09-23 线上实吐）：`--term-bg:#000000 --term-fg:#ffffff --term-cursor:#ffffff --term-sel:#b0d0ff40`，
  且 `hub.js` 兜底 `T('bg','#000000')/T('fg','#ffffff')`

### L4 探活预算：一轮最多 2 次即停（v0.13.4，2026-09-23 用户裁定）

- 裁定原话：「**探活测试 2 次即结束，不要反复频繁探测**」
- 改前缺陷：失败态（`timeout` / `rate_limited` / `model_unsupported` / `no_output`）**完全不设保鲜**，
  而 `SWEEP_EVERY=900s` ⇒ **96 轮/天/家**都在重烧真请求。实测 09-22 16:45→09-23 07:37 约 15 小时，
  单 claude 一家连烧 **68 次**；单次峰值 RSS 270MB（L4 并发 2 ⇒ 540MB），而本机 swap 已用 90%
  ⇒ 抖动期必然演成内存尖峰风暴（CPU 不是问题：全天 ≤ 0.17%）
- 现口径：一个 `VITALS_RT_TTL`（24h）窗内最多真跑 `VITALS_RT_MAX_TRIES`（默认 **2**）次，
  用完即**停到窗过期**；窗过重新给满预算（自愈不断线，但不风暴）。成功（`answered`）归零并转保鲜；
  `blocked_by_account` 不占预算也不重烧；假卡/坏卡连首次都不给。预算**跳重启不失忆**（`tries` 随 state 落盘）
- 根因另记：探针默认 `RT_MODEL=agnes/agnes-2.0-flash` 是 CCR **免费池成员** ⇒ 按共享配置军规第 4 条
  必然周期性失效（本裁定只封住重试风暴，未动模型选择；选稳定模型 ID 另需在线清单取证）
- 验证：`tests/test_vitals_retry.py` 17 条（全量 97 条 = L0 76 + L1 21，见下「测试分层」）+ `tests/verify_rt_budget.py` 真 CLI A/B 实测

### Manager LLM 模型 ID 修正（v0.13.5，2026-09-23，经用户批准）

- 原 `.env`：`MANAGER_LLM_MODEL=anthropic/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`。三处不对：
  ① 该 ID **不在** FCC `GET /v1/models` 的 22 条清单里（FCC 用下划线 `open_router/`，`openrouter/` 是 CCR 写法，
     两者互不通用）；② FCC 对**任意** bogus ID 都回 200（实测连 `this/does-not-exist-xyz` 也 200）
     ⇒ 写死的 ID 并不证明选中了那个模型；③ 带 `:free` ⇒ 违反共享配置军规第 4 条，
     且 `fcc-refresh-free.sh` 每天 12:30 重排兜底链。DB 里 09-06 的 25 条
     `HTTP 400: All target providers failed` 是该路由历史上真失败过的旧证据。
- 现值：`tokenrouter/qwen3.8-flash`（清单内、非 free、与 FCC 自身 `MODEL` 及 `src/config.py:42` 默认一致）。
- 验证：走 `src/llm.chat_tools_loop` 真工具环 ⇒ `thought→toolcall→toolresult→thought→answer`，
  取回约定标记 `HUBMGR-7731`（6.8s）。回滚：`.env.bak-20260923_100120-fix-manager-model`。

### 测试分层与推前闸门（v0.13.5，2026-09-23）

- 三层：**L0 hermetic 76** / **L1 host 21** / **L2 live 6 份探针**；口径唯一真相源 `tests/tiers.py`，
  用法与理由见 `tests/README.md`。旧口径只有一个 `Ran 71 OK`，其中相当一部分断言的是
  「这台 NAS 恰好存在的目录形态」，换机必红 ⇒ 文档里"加个 GitHub Actions 跑 pytest"不成立
  （仓内无 `pytest`/`ruff`/`requirements-dev.txt`，全仓 stdlib `unittest`）。
- `bash scripts/run_tests.sh hermetic-clean` 把 HOME 换成空目录真模拟干净 runner：L0 76 例全绿、**跳过 0**。
  `scripts/run_tier.py` 把「L0 出现 SKIP」判为 FAIL(退出码 2)——否则被 skipTest 蒙过的用例在
  干净 runner 上等于零覆盖却报绿。
- `bash scripts/prepush.sh`：照工作区 36 号文档 §4b 五步推前闸门 + 测试闸，**只报路径不报值**；
  默认不推送（加 `--push` 才推）。埋雷仓实测 4 项 FAIL、输出明文命中 0 次。
  **未装 git hook**（会影响同仓并行的其他会话）；要长期生效自行
  `ln -sf ../../scripts/prepush.sh .git/hooks/pre-push`。

### 自证与生命周期收口（v0.13.5，2026-09-23）

- **P0-1 修好一个静默三天的 500**：`POST /api/agents/{id}/chat` 自 v0.10.0（commit `2cf96fa`）起每请求必 500
  —— 那次提交把 `tools` / `repair_mode` 从 `ChatRequest` 删了，调用点还写 `req.tools`（AttributeError）。
  事故形态最贵：`/health` 全程 200、vitals 全绿，而前端 `hub.js` 恰好只调这个坏端点 ⇒ 直连对话框整条不可用。
  现回归测 `tests/test_pydantic_attr_drift.py` 用 AST 静态取证（不 import main，避免拉起 lifespan），
  把「handler 访问模型未声明字段」这一类缺陷全部扫掉。
- **P0-2 `/health` 能自证跑的是哪份代码**：新增 `git_sha_boot` / `git_sha_now` / `code_stale` / `boot_at` /
  `uptime_s` / `pid` / `db_ok` / `term_sessions` / `term_idle_max_s`（全 additive，前端 `pollHealth` 只读 `status`）。
  立项理由即实况：进程报 v0.13.2 而 HEAD 已 v0.13.3，旧口径无从区分「改了没重启」与「跑的是最新」。
  sha 解析在未初始化（`selfattest.py`）：非 git 环境一律返回空且**不**虚报 stale；取 sha 不 fork 子进程。
- **P0-3 `agent-hubctl.sh` 交回单一拥有者**：不再生成/采信 `data/agent-hub.pid`，起停一律 `systemctl --user`；
  任何 kill 前先校 `/proc/<pid>/cmdline` 是不是 hub 本尊（旧版 `kill $(cat pidfile)` 在 PID 复用时会误杀无关进程，
  工作区里那枚 pidfile 写的 85512 就是个已不存在的陈旧的）；`stop`/`restart` 前读 `/health.term_sessions`，
  有活终端会话则拒执（shutdown 钩子会 `kill_all()` 连坐），确要承担才 `FORCE=1`。`status` 现在与
  `systemctl --user is-active` 一致（旧版在服务 active 时错报「❌ 未运行」）。
- **P0-4 终端回收不再寄生在前端轮询上**：`_reap()` 原唯一调用点是 `list_sessions()`，浏览器一关就没人跑
  ⇒ 45min 空闲 TTL 形同虚设、`MAX_SESSIONS(8)` 可被占满后新终端直接 429。现 `term.reap_loop()`
  （`TERM_REAP_INTERVAL`，默认 60s）由 `startup()` 挂后台，单轮异常不致死循环。
  端侧实测：建会话后**只读 /health**（它不调 `_reap`），TTL=8s 下会话自行归零。

## 访问

- 本地: http://127.0.0.1:3102/ ｜ 局域网: http://192.168.5.102:3102/ ｜ Tailscale: http://100.117.232.62:3102/

## API

```
GET  /health                          POST /api/agents/{id}/chat    {message,session_id?}
GET  /api/agents/{id}                 POST /api/agents/{id}/chat/stream (SSE)
POST /api/agents/detect  {dir}        GET  /api/sessions | /api/sessions/{id}/messages
POST /api/agents         {dir,name?}  GET  /api/ports
DELETE /api/agents/{id}               GET  /api/memory/l1|search|l2|l3|context|l2/rebuild
POST /telemetry/events/{source}       GET  /telemetry/events | /telemetry/usage/summary

GET  /api/term/history/{agent_id}?limit=3   盘上历史会话（标题＝用户问题原文，需 x-term-token）
POST /api/term/sessions  {agent_id, session_id?}   带 session_id 即续聊该条历史
```

## 配置（.env）

- `CCR_OPENAI_KEY` 留空时自动读 `~/.config/jcode/provider-ccr.env`（3456 OpenAI 口实测唯一有效链）
- `MANAGER_LLM_BASE_URL=http://127.0.0.1:8082`（FCC，Anthropic /v1/messages）+ `MANAGER_LLM_API_KEY` + `MANAGER_LLM_MODEL`
- `HOOK_AUTH_TOKEN` 留空=仅回环可写；对外接入必须设置

## 控制

```bash
~/agent-hub/agent-hubctl.sh {start|stop|restart|status|log}
```

## 二期候选（上游已有、本机未移植）

工作流 DAG 引擎（建议基于官方 `mcp` Python SDK 重写）、MCP 客户端、
Cloudflare Tunnel 发布、Skill 共享库、L1 自动提取（Hook 采集→LLM 提炼）。

## 安全边界（与上游的差异，均为有意设计）

1. 不做任意进程起停/kill——NAS 服务归 systemd，遵守「不删改在用工具」约束
2. telemetry/hook 带鉴权选项——上游零鉴权的补课
3. 凭据仅在 .env(0600) 与 jcode env 文件，不引入机器名派生加密反模式
