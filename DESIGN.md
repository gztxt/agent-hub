# Agent Hub（智管）设计方案

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│            Agent Hub（智管）                         │
│              Python FastAPI + Vue 3                 │
│              端口 :3102 (0.0.0.0)                   │
├─────────────────────────────────────────────────────┤
│  Dashboard    │  Agent List  │  Chat  │  Memory  │
│  (教室视图)   │  (管理面板)   │ (统一对话)│ (记忆中心) │
└───────────┬───────────────────┬────────────────────┘
            │                   │
     ┌──────▼──────┐    ┌──────▼──────┐
     │  Agent Adapters   │  Memory Sync    │
     │  (适配器层)       │  (同步层)       │
     └───┬─────┬─────┬───┘    └──────┬──────┘
         │     │     │               │
    ┌────▼──┐ ┌▼────▼──┐ ┌─────────▼───┐
    │Claude │ │  pi    │ │   jcode     │
    │(CCR:3 │ │(:30141)│ │  (:3457)    │
    │ 456)  │ │        │ │             │
    └───────┘ └────────┘ └─────────────┘
              │
         ┌────▼────┐
         │  TDAI   │
         │(:8420)  │
         │(记忆中心)│
         └─────────┘
```

## 核心模块

### 1. Agent Discovery（自动发现）
- 扫描已知 Agent 配置（~/.claude, ~/.pi, ~/.jcode）
- 检测端口占用（:3456, :30141, :3457, :8420）
- 自动生成 Agent Registry（SQLite）

### 2. Agent Adapters（适配器）
| Agent | 协议 | 认证 | 端点 |
|-------|------|------|------|
| Claude | CCR OpenAI 兼容 | x-ccr-core-auth | :3456/v1/chat/completions |
| pi | HTTP REST | Bearer Token | :30141/api/* |
| jcode | OpenAI 兼容 | x-ccr-core-auth | :3457/v1/chat/completions |
| TDAI | REST | 无 | :8420/* |

### 3. Unified Chat（统一对话）
- 单 Agent 对话：转发到对应 Adapter
- 多 Agent 串流：Manager Agent 调度
- 会话历史：统一存储在 SQLite

### 4. Memory Center（记忆中心）
- L1：可检索记忆（语义搜索）
- L2：近 30 天工作记忆
- L3：长期 Profile
- 同步到 TDAI（如有）

### 5. Dashboard（教室视图）
- 所有 Agent 状态可视化
- 一键启动/停止（CLI Agent）
- 内嵌 Web UI（pi/TDAI）
- 实时日志

## 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 后端 | Python FastAPI | 0.104+ |
| 前端 | Vue 3 + Vite | 3.4+ |
| 数据库 | SQLite | 内置 |
| 异步 | asyncio + httpx | - |
| 部署 | 直接运行 | :3102 |

## 目录结构

```
~/agent-hub/
├── src/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理
│   ├── discovery.py         # Agent 自动发现
│   ├── registry.py          # Agent 注册表（SQLite）
│   ├── adapters/
│   │   ├── base.py          # 抽象基类
│   │   ├── claude.py        # Claude/CCR 适配器
│   │   ├── pi.py            # pi 适配器
│   │   ├── jcode.py         # jcode 适配器
│   │   └── tdaI.py          # TDAI 适配器
│   ├── chat.py              # 统一对话路由
│   ├── memory.py            # 记忆管理
│   └── scheduler.py         # 定时任务
├── static/                  # 静态资源
├── templates/               # HTML 模板
├── data/
│   ├── agents.db            # SQLite 数据库
│   └── logs/                # 日志
├── agent-hubctl.sh          # 控制脚本
└── .env                     # 环境变量
```

## API 设计

```
GET  /api/agents              # 列出所有 Agent（含状态）
GET  /api/agents/:id          # Agent 详情
POST /api/agents/:id/chat     # 发送消息
GET  /api/sessions            # 会话历史汇总
GET  /api/memory              # 记忆中心
POST /api/memory/sync         # 同步到 TDAI
GET  /api/tasks               # 编排任务列表
POST /api/tasks               # 创建任务
GET  /api/docs                # 文档索引
GET  /health                  # 健康检查
```

## 实施计划

### Phase 1（核心骨架）- 2小时
- [ ] 项目结构搭建
- [ ] Agent Discovery 模块
- [ ] SQLite Registry
- [ ] 基础 API（/api/agents, /health）
- [ ] 简单 Web UI（HTML + JS）

### Phase 2（适配器层）- 3小时
- [ ] Claude/CCR Adapter
- [ ] pi Adapter
- [ ] jcode Adapter
- [ ] 统一对话接口

### Phase 3（记忆中心）- 2小时
- [ ] 本地记忆存储
- [ ] TDAI 同步
- [ ] 语义搜索（可选）

### Phase 4（Dashboard）- 2小时
- [ ] 教室视图 UI
- [ ] 实时状态刷新
- [ ] 日志查看器

总计：约 9 小时
