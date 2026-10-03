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
| C1 | pi 子代理 `Explore`（仓内检索基础设施摸底） | work/dispatch/skillcenter/c1-search-infra.md | **error** | 派发后 593ms / 0 token / 0 tool call 即结束，只留下 user 消息未回内容 ⇒ **派发失败**（非侦察失败）。**未重试**：主会话用 `grep -n` 直接只读取证完成同一目标，结论已写入 v0.13.67 版本注释与设计书 §5.1 |

---

## D2 落地后的取证结论（2026-10-03，主会话亲自 grep）

| 查什么 | 结论 |
|---|---|
| Python 版 BM25 / TF-IDF / cosine / `rank_bm25` / `jieba` | **无**；`requirements.txt` 无检索库 ⇒ BM25 必须自己写（零新依赖） |
| FTS5 倒排 | `src/memindex.py:431` 有，`tokenize='trigram'`；`search()` 在 `:692` |
| 分词踩坑记录 | `src/kb.py:20`：默认 `unicode61` 下中文查询「端口」只召回 2 条、LIKE 召回 8 条；`src/memindex.py:80` 记短查询受分词器限制 |
| 现有 `/api/skill/list?q=` | `src/skill.py:434` `_match_q` 是纯子串匹配，**不是相关性检索** |
| token 估算 | `src/skill.py` `_CHARS_PER_TOKEN = 2.5`，先装填到预算 70% 再降级为仅名称 |
| jev 权威契约 | `技术文档/scripts/jev.py`：POST `https://api.typesafe.ai/v1/systemone`，model `jev-latest`，体 `{state,model,questions}`，响应在 `answers`；`score.criteria` **必须字符串列表**（传 dict → 422）；403 = key 没生效 |
| key 取证 | 环境变量 `TYPESAFE_API_KEY` 已设置（长度 108）；**未读**禁触的 `~/.pi/agent/env.typesafe` |
