# Agent Hub（智管）

本机多 Agent 统一管理中心，复用 Agent_Manager 设计理念，纯 Web 方式访问。

## 功能

- **教室视图**: 所有 Agent 状态可视化
- **自动发现**: 扫描已知 Agent（claude/pi/jcode/tdai）
- **统一 API**: `/api/agents`, `/api/sessions`, `/api/memory`
- **记忆中心**: TDAI 记忆同步（Phase 3）

## 访问

- **本地**: http://127.0.0.1:3102/
- **局域网**: http://192.168.5.102:3102/
- **Tailscale**: http://100.117.232.62:3102/

## 控制

```bash
~/agent-hub/agent-hubctl.sh start   # 启动
~/agent-hub/agent-hubctl.sh stop    # 停止
~/agent-hub/agent-hubctl.sh status  # 状态
~/agent-hub/agent-hubctl.sh log     # 日志
```

## 技术栈

- Python 3.11 + FastAPI
- Vue 3 + Vite（待实现）
- SQLite（本地存储）
- aiohttp（异步 HTTP）

## 目录结构

```
~/agent-hub/
├── src/
│   ├── main.py          # FastAPI 入口
│   ├── config.py        # 配置管理
│   ├── discovery.py     # Agent 自动发现
│   └── adapters/        # Agent 适配器
├── templates/           # HTML 模板
├── static/              # 静态资源
├── data/                # 数据目录
├── agent-hubctl.sh      # 控制脚本
└── .env                 # 环境变量
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| GET | /api/agents | 列出所有 Agent |
| GET | /api/agents/:id | Agent 详情 |
| POST | /api/agents/:id/chat | 发送消息 |
| GET | /api/sessions | 会话历史 |
| GET | /api/memory | 记忆中心 |

## 状态

- ✅ Phase 1: 核心骨架（完成）
- ⏳ Phase 2: 适配器层（进行中）
- ⏳ Phase 3: 记忆中心
- ⏳ Phase 4: Dashboard 优化
