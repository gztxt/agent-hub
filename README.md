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

## 访问

- 本地: http://127.0.0.1:3102/ ｜ 局域网: http://192.168.5.102:3102/ ｜ Tailscale: http://100.117.232.62:3102/

## API

```
GET  /health                          POST /api/manager/chat        {message,session_id?}
GET  /api/agents                      POST /api/agents/{id}/chat    {message,session_id?}
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
