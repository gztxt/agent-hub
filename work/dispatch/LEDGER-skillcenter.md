# 派发台账 · agent-hub 技能中心（§9 三落点）
会话短 ID：01a0ff65｜机器扩展：pi-subagents **0.19.0**（实测；技能 §3.6 仍写 0.17.1＝过期）
状态枚举：queued / running / completed / steered / aborted / stopped / error

| 片 | 执行者 | 产物绝对路径 | 状态 | 判据 |
|---|---|---|---|---|
| A1 | **外部 CLI `codex`**（改派：断言需 shell 计数，claude 无 Bash） | work/dispatch/skillcenter/a1-codex-shell.* | **error** | exit=1 stdout=0B；`403 All target providers failed` @ CCR `/v1/responses`。CCR 本身活着（3456 在听、无 token `/v1/models`=401）⇒ 路由/模型可达性问题。禁触面 ⇒ 停手报请，不重试 |
| B1 | pi 子代理 `Explore`（jcode 技能加载面） | work/dispatch/skillcenter/b1-jcode.md | running | 文件存在且含绝对路径+行号 |
| B2 | pi 子代理 `Explore`（qwenpaw 技能加载面） | work/dispatch/skillcenter/b2-qwenpaw.md | **completed** | ✅ 产物已由主会话落盘。产出：有发现面（`skill_paths`+`skill_pool`）；2 软链在但**清单零命中、池 API 只读清单 ⇒ 不可见**。带出台账纠正：`PT-11` 的「9/9 体检 PASS」是链接体检非生效体检 |
| B3 | pi 子代理 `Explore`（opencode 技能加载面） | work/dispatch/skillcenter/b3-opencode.md | running | 同上 |
| A2 | 外部 CLI `claude`（纯读代码复核） | work/dispatch/skillcenter/a2-claude-review.* | **error** | exit=1 stdout=153B；`apiKeyHelper ... ccr-claude-code-api-key-default-claude-code: not found`（exit 127）。`~/.claude-code-router/bin/` 下 codex/grok/pi 三个 helper 都在，唯独 claude 那个从未开通。禁触面 ⇒ 停手报请 |
