# Agent Hub（智管）设计方案

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│            Agent Hub（智管）                         │
│         Python FastAPI + 原生 JS（无构建）          │
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
| 前端 | 原生 JS（`static/hub.js`，无框架无构建）+ 本地 vendor xterm.js/fit.js | - |
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

## 视觉系统（2026-09-26 补：hallmark 审计 M1）

> 本节补齐一个真实缺口：此前 `DESIGN.md` 只有架构与实施计划，**零视觉约束**，
> 而真正的设计系统住在 `templates/index.html` 的 CSS 注释里（v0.9 配色、v0.10.1 说明、
> pi-web 内嵌条尺寸契约）。后果是任何后续 agent 读本文档都学不到视觉规则，改页面必然漂。
> **权威源仍是 `templates/index.html` 的 `:root` 块**，本节只做语义索引，不复制取值
> （复制即制造第二份会漂的副本）。

### 一条总原则

**UI 骨架一律中性灰阶，色相只发给语义。** 层级靠「底色差 + 描边 + 字重 + 留白」表达，
不靠颜色装饰。允许出现色相的只有四个语义位：`--ok`（成功）/ `--warn`（告警）/
`--danger`（危险）/ `--busy`（进行中，≠ 健康绿）。

### token 角色表（取值以 `:root` 为准，此处只定义"谁负责什么"）

| 角色 | token | 用途约束 |
|---|---|---|
| 画布 | `--canvas` | 页面底，承托内容框 |
| 内容面 | `--bg` / `--surface` / `--surface-2` / `--field` | 卡片、输入、表格、代码块、框内次级块 |
| 描边 | `--border` / `--divider` / `--line` | 框体分隔；**内嵌条专用** `--embed-line` / `--embed-bg`（与 divider/hover 是不同角色，禁止合并复用） |
| 文字三级 | `--text-1` / `--text-2` / `--muted` | 全部须达 WCAG AA 正文 4.5:1 以上 |
| 实心块字色 | `--on-accent` | 主按钮 / 用户气泡 / 徽标上的字；**禁止写字面 `#fff`** |
| 选中·聚焦 | `--sel-*` / `--focus-*` | 一律半透明，不用实黑反色块 |
| 字体 | `--font-sans`（正文）/ `--font-mono`（数据）/ `--font-display`（＝mono，展示位） | CJK-first，**不引入 webfont**（中文字体动辄数 MB）；display 与 body 同源是有意取舍 |

### 硬约束（改动前必读）

1. **禁止字面色**：任何 `#rrggbb` / `rgb()` / `oklch()` 只能出现在 `:root` 内。需要新值就先进
   token 块再起名字，然后 `var()` 引用（hallmark 称 mid-render token improvisation）。
2. **数字列必须 `font-variant-numeric: tabular-nums`**（端口、时间戳、表格数值）。
3. **grid 的 fr 轨道一律 `minmax(0, …fr)`**，禁止裸 `1fr` —— 轨道内的 xterm/`<pre>`/表格
   固有宽度大会顶破容器，钉到 0 后溢出由子元素自己的 `overflow-x: auto` 承接。
4. **`body` 是 `height:100vh` + flex column + `overflow:hidden` 的 app shell**，内层各自滚动。
   这层 `hidden` 是**承重**的：改成 `visible` 会造出横向滚动，改成 `clip` 需连带回归测试
   全部 fixed 抽屉（`#detailDrawer` / `#settingsDrawer` / `.sidebar`）。
5. **窄屏判据**：320 / 390 / 768 / 1280 四档均须 `documentElement.scrollWidth == clientWidth`
   （无横向溢出）。真渲染取证法见 `agent-knowledge/57`（headless chromium + iframe 视口法，
   因为 `--window-size` 在本机 chromium 152 有 500px 下限）。
6. **z-index 尺度**：45 / 46 / 50 / 60 / 99（尚未 token 化，见 `agent-knowledge/57` m1）。
