# Agent Hub（智管 Web）

本机多 Agent 统一管理中心（无头 Web 版），**语义移植自 [Zafer-Liu/Agent_Manager](https://github.com/Zafer-Liu/Agent_Manager) v0.3.0（Apache-2.0）**——
移植数据模型/协议口径/交互语义，未复制上游源文件；桌面形态（进程起停/Tauri 命令/
Qdrant sidecar）按本机 NAS 军规有意裁剪。评估全文见
`/fs/1000/ftp/技术文档/Agent_Manager_融合评估报告.md`。

## 功能（v0.2.0，2026-09-06 融合完成）

| 模块 | 来源 | 实现 |
|---|---|---|
| 教室视图 Dashboard（讲台+座位+状态动画） | Dashboard.tsx 语义 | templates/index.html |
| Manager Agent 自然语言指挥官 | mcp_agent.rs::manager_chat | src/manager.py + src/llm.py（FCC Anthropic 工具环，服务端真执行，会话落盘）|
| 统一对话（claude/jcode→CCR:3456，含 usage/历史回放）| — | src/adapters/openai_compat.py + registry.py |
| 三层记忆中心 L1/L2/L3 + 注入 context | memory_* 子系统 | src/memory.py + db.py（LLM 压缩重建，manual 区隔离）|
| Hook 遥测端点（覆盖式 upsert + 用量聚合）| agent_http.rs/telemetry_store.rs 口径 | src/hook.py（+Bearer 鉴权补上游缺）|
| 端口管理（枚举+归属+Agent 标注，**只读无 kill**）| ports.rs | src/ports.py |
| 项目类型自动识别 + 自定义 Agent 注册 | scan_project_dir | src/sources.py + main.py |

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
