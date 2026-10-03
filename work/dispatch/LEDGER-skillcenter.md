# 派发台账 · agent-hub 技能中心（§9 三落点）
会话短 ID：01a0ff65｜机器扩展：pi-subagents **0.19.0**（实测；技能 §3.6 仍写 0.17.1＝过期）
状态枚举：queued / running / completed / steered / aborted / stopped / error

| 片 | 执行者 | 产物绝对路径 | 状态 | 判据 |
|---|---|---|---|---|
| A1 | **外部 CLI `codex`**（改派：断言需 shell 计数，claude 无 Bash） | work/dispatch/skillcenter/a1-codex-shell.* | **error** | exit=1 stdout=0B；`403 All target providers failed` @ CCR `/v1/responses`。CCR 本身活着（3456 在听、无 token `/v1/models`=401）⇒ 路由/模型可达性问题。禁触面 ⇒ 停手报请，不重试 |
| B1 | pi 子代理 `Explore`（jcode 技能加载面） | work/dispatch/skillcenter/b1-jcode.md | **completed**（子代理在 turn limit 收尾且**自己没写文件**，主会话代写） | ✅ 产物已落盘 + **主会话独立复核并补上子代理漏掉的一整块**。核心结论成立，最硬证据是 jcode **用户可见报错文案**逐条列出的三个根：`~/.jcode/skills`(global) / `./.jcode/skills`(project-local) / `./.claude/skills`(compatibility)。**增量不在“3 个根”而在于后两个是相对 cwd 的** ⇒ D1 把「发现点」等同于「一个绝对目录」的模型缺口（已登记 PT-20261002-14）。费用 151k token / 31 tool use / 56 min |
| B2 | pi 子代理 `Explore`（qwenpaw 技能加载面） | work/dispatch/skillcenter/b2-qwenpaw.md | **completed** | ✅ 产物已由主会话落盘。产出：有发现面（`skill_paths`+`skill_pool`）；2 软链在但**清单零命中、池 API 只读清单 ⇒ 不可见**。带出台账纠正：`PT-11` 的「9/9 体检 PASS」是链接体检非生效体检 |
| B3 | pi 子代理 `Explore`（opencode 技能加载面） | work/dispatch/skillcenter/b3-opencode.md | **completed**（子代理在 turn limit 收尾且**自己没写文件**，主会话代写产物） | ✅ 产物已落盘 + **主会话独立复核 6/6**。结论**反证 D1 第 20 路是假发现点**：`~/.config/opencode/skill` 不被 opencode 读取（`Path.config,"skill` 零命中 + `opencode.json` 无 `skills` 键 ⇒ `skills.paths` 未配置）。功能无损失（同名副本已在 opencode 真扫的 `~/.claude/skills/`），损失的是列表可信度。费用 234k token / 33 tool use / 55 min，**性价比很差**（主预算耗在 185MB 二进制的 `grep -o -P`，一次 900s 超时） |
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

---

## B3 带出的两项 D1 遗留（均已登记 `PT-20261002-13`，只取证未动手）

| 项 | 取证 | 性质 |
|---|---|---|
| `opencode` 路是假发现点 | 二进制字面量 `GA="skills/**/SKILL.md"` + `bA=".claude"`/`xA=".agents"`；`Path.config,"skill` 计数 **0**；`~/.config/opencode/opencode.json`（2482 B）**无 `skills` 键** | **判据失效**——D1 用「看起来像发现点」立项，须改用「有代码路径/配置键接通」 |
| 62 条第三方技能对 agent-hub 不可见 | 全路由实测 接受 381 / 拒读 62；62 条 realpath **全在** `/fs/1000/ftp/技术文档/` 下 = `mattpocock-skills` 50 + `crawl4ai` 8 + `hallmark` 2 + `Agent-Reach` 2。`_allowed_roots()` 只放行 `技术文档/skills` | **可见性缺口**——Claude/opencode 读得到，agent-hub 读不到 ⇒ **D2 检索永不推荐这 62 条** |

> 两条都触及 `src/skill.py` 的生产常量与安全白名单，**不属已授权 D2 范围** ⇒ 按「批次授权 ≠ 手段解禁」停手，等用户裁定。
> `PT-20261002-13` 已写明：若放宽白名单，必须同时给出「构造越界软链并断言仍被拒读」的负向用例，不得只放宽不放测。

### 一处自我纠错（值得记，因为它是判据级别的）
首次统计 `~/.agents/skills` 条数时我写了 `for it in skill._scan_one(...)`——该函数返回 **dict**，迭代得到的是**键名**（`ok/items/ms/...`），于是打印出「6 条」。真实值 **12 接受 + 28 拒读 = 40**，与 D1 记录一致。**先量后断言；量错就当没量。**

### 第二次同型错误（B1 对账时）——已成一条判据
看到 `ls -d ~/.jcode/skills/*/ | wc -l` = 57 而 D1 记 58，我判「差一」。
实际：`_scan_many` 接受 **58** = 57 顶层 + `ima-skills/notes` + `ima-skills/knowledge-base` 两条嵌套（1 条被白名单拒读）。
⇒ **与 D1 完全一致，无 discrepancy**；错因又是「拿两个不同口径的数字对比」。

> **判据：跨来源比数字之前，先确认两个来源量的是同一个东西。** 本轮两次同型错误（dict 当序列、顶层 vs 递归）。

---

## D3 落地与旁路实测（2026-10-03）

**实现**：`src/skill_usage.py`（`counts`/`zombies`/`snapshot`）+ `GET /api/skill/zombies?days=7` + `runlog.SUBJECTS` 增 `skill.relevant`/`skill.inject` + 闸门 `tests/test_skill_usage.py`（L0 29 例）。版本 **0.13.68**。

**计划书与现实的三处出入（照抄会直接炸）**：

| 计划书写法 | 实际 | 后果 |
|---|---|---|
| `db.fetchall(sql, params)` | 真实 API 是 `db.query(sql, params) -> list[dict]`（`db.py:197`） | AttributeError |
| `_all_items()` | `skill.py` 里**不存在**该函数 | NameError |
| `_WINDOW = max(1, min(365, days)) if (days := 7) else 7` | 模块级死代码，且把 `days` 泄漏进模块命名空间 | 误导读者以为窗口是全局常量 |

**旁路验证形态**（既有先例）：临时端口 **3199 + 只绑 127.0.0.1**，PID 记进 `work/sidecar/d3-verify.pid`，收场只按该 PID `kill`（禁 `pkill`）。生产 3102 全程 `active`，未被抢占。**唯一一次重启授权未动用。**

### 探针结果（单轮复合，一次取齐）

| 端点 | 结果 |
|---|---|
| `/health` | `version=0.13.68` `code_stale=False` |
| `/api/skill/zombies?days=7` | `total=365` `zombies=263` **`counted=0`** `confidence=medium` `direct_source=not-implemented`；建议分布 `widen_visibility` 261 / `retire_review` 2 |
| `/api/skill/relevant?q=这个任务该派给谁去做&n=3&rerank=true` | HTTP 200 / 4374 B / `took_ms=1064.5` / `backends=20`；`agent-dispatch` **15.18 居首** |

### 两条必须带走的结论

1. **`counted=0` 证明「保守方向」设计是对的**：注入链（D4/D5）还没建，365 条技能全无记账，于是 63% 上榜。榜单此刻**没有决策价值**，但它**诚实**——`counted` 字段让前端能区分「真的没人用」与「压根没仪表盘」。这正是把封顶值做成可断言字段换来的东西。
2. **jev 首次上真网，结论比预期更强**：对噪声（`arkcli-*`）的 confidence **0.85** 高于对正确答案（`agent-dispatch`）的 **0.53**。⇒ D2「jev 不改写排序」不是保守，是**必须的**。
3. **`rerank=true` 时 1064.5ms > hub-facade 的 600ms 预算** ⇒ **D4 必须 `rerank=false`**，否则每次输入都走静默降级、白烧 600ms 还拿不到候选。

### 我在这轮犯的三个错（都记下来，因为都是「量/判」层面的）

| 错 | 怎么发现的 | 教训 |
|---|---|---|
| `for it in skill._scan_one(...)` 把返回的 **dict** 当序列迭代，打印出「6 条」（其实是键数） | 与 D1 记录 12+28=40 对不上 | 先量后断言；量错就当没量 |
| 探针 URL 里直接塞裸中文，没走 `--data-urlencode` ⇒ 响应非 JSON，误判为端点 FAIL | 看旁路日志无异常 + 重取状态码 200 | 中文查询参数一律 `--data-urlencode`；**先看日志再重试** |
| 解析脚本按 `score`/`rank`/`bm25.took_ms` 取值，全错；且在重解析前把响应体删了 | KeyError | 响应体先落盘再解析；字段名以 `_rel_row` 源码为准，不凭印象 |

---

## 派发批次收口（2026-10-03）

| 腿 | 状态 | 产物 |
|---|---|---|
| B1 jcode | **completed** | `work/dispatch/skillcenter/b1-jcode.md` |
| B2 qwenpaw | **completed** | `work/dispatch/skillcenter/b2-qwenpaw.md` |
| B3 opencode | **completed** | `work/dispatch/skillcenter/b3-opencode.md` |
| B4 hermes | **completed** | `work/dispatch/skillcenter/b4-hermes.md` |
| C1 仓内检索 | **error**（派发失败） | 主会话 grep 补齐，结论已入 v0.13.67 注释与设计书 §5.1 |
| A1 codex | **error** | CCR 403，禁触面未修 |
| A2 claude | **error** | CCR helper 缺失（exit 127），禁触面未修 |

**五条腿 3 成功 / 3 失败，成功率 50%**；两条外部腿（A1/A2）的失败**均在 `~/.claude-code-router/**` 禁触面**，
一条子代理腿（C1）失败于派发本身而非侦察。可用的四条全部只读、全部有主会话独立复核。

**四条腿合起来把 D1 的立论推翻了两处**：
1. B3：第 20 路 `opencode` 是假发现点（`PT-13`）
2. B1：「发现点 = 一个绝对目录」这个模型漏掉了项目本地根（`PT-14`）

**成本对照**：四条腿合计 ≈ 660k token / 3.5 小时。若由主会话直接 grep，binaries 部分约 20 次命令即可拿到
**同样或更强的证据**。⇒ 下次侦察优先主会话直取；派发只留给「跨多目录真需要遍历」的部分
（B4 扫 hermes 120 条 + hermes-agent 58 条 + hermes-web 22 条的那部分确实值）。

---

## D7 技能中心前端（2026-10-03，v0.13.69）

**闸门从 `verify_*` 改成 L0**：计划书写的 `tests/verify_skill_center_ui.py` 若照办，
按仓内 `tests/README.md` 的分层口径它属 **L2 live（需服务、手工单跑、不被 `discover -p "test_*.py"` 收进来）**，
而 pre-commit 只跑 `run_tests.sh hermetic` ⇒ **那个名字的东西在提交时根本不会跑**。那不叫闸门，叫摆设。
改为 `tests/test_skill_center_ui.py`（L0，23 例静态断言）。

**闸门自己蒙对过一次，已修并留下反向验证**：首版 `rerank` 断言用 `assertIn("rerank=false", JS[端点起 400 字])`，
而代码里根本没有该字面量（实际是 `'&n=5&rerank=' + (useJev ? 'true' : 'false')`），
**命中的是上方注释里那句说明**。属「文字存在 ≠ 已生效」那一族（TDZ / `vitals_loop` / 跨档镜像声明）。
现改为剥注释后判代码，并补「jev 开关出厂不得带 `checked`」。两条均已反向验证会红（改坏→红、还原→绿）。

**真渲染取证**（旁路 3199 + obscura，1280×720）：6 个新元素全部非零尺寸、`jsErrors=[]`、
`docOverflow=0`、切页正常，且 **`jevChecked=false` 在真浏览器里成立**——不止源码层。

**未做、如实标注**：
- **窄屏（390px）真渲染没做**：obscura 不暴露视口参数，改不了视口。分档偏好不变量 ③
  目前只有静态证据（无第二处 `innerWidth < 768`、`.sk-budget` 无固定宽），**不算端侧验收**。
- 三张卡片在浏览器里的**实际 API 填充内容**没截图（`--eval` 执行时异步请求尚未回来，卡片仍是「加载中…」尺寸）。
- pi / Claude 端侧真实调用：**未做**，待 D4/D5 完成后由用户在端侧验收。

**体积**：`static/hub.js` 重建后 5803 行，md5 `c062c0b7`；`tests/test_hubjs_split.py` 3 passed（逐字节等于拼接结果）。
