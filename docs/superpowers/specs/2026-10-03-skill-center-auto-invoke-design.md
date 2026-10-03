# 技能中心改版：统一列表 + 全 agent 自动调用 + 前端重构

- 日期：2026-10-03
- 状态：设计已获用户批准（①执行 ②三项授权全点头 ③追加「技能中心前端页面也要优化设计」）
- 落位：`/fs/1000/ftp/技术文档/agent-hub`（生产仓，单写者=本会话 `01a0ff3a`）
- 版本基线：agent-hub `v0.13.65`（生产 `code_matches_head=True`，boot sha `b01ff746`）
- 相关：PT-20261002-11（统一记忆全 agent 接入，批次②技能分发 9/9 PASS，**遗留未闭合项④「开局自动带上」**）

---

## 0. 结论

hub 做**唯一权威的技能门面**（发现点全收 + 真相关性检索 + 预算化注入 + 调用记账），
agent 侧**分四档落地**。不追求「一套代码通吃 10 家」—— 物理上只有 2 家有输入钩子。

三项授权（用户 2026-10-03 点头）：

1. 改 `~/.claude/settings.json` 加 `UserPromptSubmit` hook
2. 重启一次 `agent-hub.service`（仅此一次）
3. git commit（**禁 push**）

---

## 1. 硬证据（本轮亲自跑，禁 SKIP 当 PASS）

| 断言 | 实测输出 |
|---|---|
| hub 通道读技能正文的次数 | `profile_events` 中 `subject='skill.read'` **全库 0 次** |
| 同期看清单的次数 | `skill.list` **33 次**、`mem.search` 181 次 ⇒ **清单有人看，正文一次没读** |
| 技能清单预算 | `GET /api/skill/budget` → `budget=800 used_est=796 total=77` |
| 门面覆盖面 | 7 路白名单 = 77 条；磁盘实有实证加载 **~240 条** |
| 「相关」是否实现 | `/api/skill/list` docstring 自述「不区分大小写**子串过滤**」⇒ 未实现 |
| 注入通道 | `hub-facade.ts`(rev=20261002b-D1.5) `input` 钩子只查 `/api/memory/search`；`budget` 端点注释自称「批5 注入通道数据源」却**无人调用** ⇒ 死代码 |
| pi-web 版本 | 装 `0.9.3`，npm `latest=0.10.0`；本会话跑在 pi-web 内（`PI_WEB_HOSTNAME=0.0.0.0`） |
| 体检 | `scripts/orchestration-check.sh` → FAIL=0 WARN=1（C8 本会话未沉淀 = 本设计书闭合） |

---

## 2. 根因

| # | 根因 | 证据 |
|---|---|---|
| R1 | **可见性**：hermes 110 / jcode 56 / hermes-web-ui 22 / qoder 系 52 条只在自己家的目录里 | 7 路 vs 磁盘实测 |
| R2 | **预算打满**：`800/796`，装不下降 name-only，再多 `truncated` | `/api/skill/budget` |
| R3 | **注入通道死代码** | 读 `hub-facade.ts` 全文 |
| R4 | **「相关」没实现** | `skill_list` docstring |
| R5 | **hermes 声明/消费不一致**：`config.yaml` 指 `skills-hot`（实测 0 条 SKILL.md；PT-11 记 16 条），实读 `~/.hermes/skills`（110 条） | 两处实测 |
| R6 | **无观测**：页面与台账都答不出「哪些技能真被用过」 | `skill.read`=0 |

---

## 3. 架构

```
20 路发现点（SKILL_DIRS 扩展，全配置化；每次请求重扫，**不存第二份副本**）
   │ _dedup 按 realpath 合并重叠（hermes 110 ∩ hermes-agent 58 自动归一）
   ▼
/api/skill/list | read | status | install | remove    ← 现有，语义不动
/api/skill/budget                                     ← 现有，改为相关性排序装填
/api/skill/relevant?q=&n=            ★新  同步 BM25 召回 top-K（~300 条内存打分，20~60ms）
                                          └→ jev 异步精排（fire-and-forget + TTL 300s 缓存）
/api/skill/zombies?days=7            ★新  两源记账的零调用榜 + 置信度
   │
   ├─ 推送型   pi: hub-facade input→context 钩子（零配置）
   │           claude: settings.json + UserPromptSubmit hook（★授权 1）
   ├─ 可见性型 install 端点铺软链到 10 家发现点（只建链不复制；remove 只删链）
   ├─ 兜底型   MCP: hub_skill_relevant / hub_skill_read（claude·codex·opencode·qwenpaw 已通）
   └─ 记账型   profile_events（已有 skill.list/skill.read）+ 新增 skill.inject + 各家会话日志旁路
```

---

## 4. 发现点清单

### 4.1 现有 7 路（不动）

`claude` 18 · `techdocs` 18（自研权威副本）· `superpowers` 14 · `agents` 12 · `codex` 6 · `workbuddy` 6 · `pi` 3

### 4.2 拟新增 13 路

| route | 路径 | 实测条数 |
|---|---|---|
| `hermes` | `~/.hermes/skills` | 110 |
| `hermes-agent` | `~/.hermes/hermes-agent/skills` | 58（重叠由 dedup 归一） |
| `hermes-web` | `~/.hermes-web-ui/.ekko/skills` | 22 |
| `jcode` | `~/.jcode/skills` | 56 |
| `grok` | `~/.grok/skills` | 1 |
| `grok-bundled` | `~/.grok/bundled/skills` | 9 |
| `picoclaw` | `~/.picoclaw/workspace/skills` | 8 |
| `qoder` | `~/.qoder/security/skills` | 1 |
| `qoderwake` | `~/.qoderwake/resources/builtin-skills` | 7 |
| `qoderwake-gen` | `~/.qoderwake/runtime-generations/skills` | 14 |
| `qoderwake-shadow` | `~/.qoderwake/run/shadow-skills` | 2 |
| `qoder-alpha` | `~/.qoder-alpha/extensions/skills` | 14 |
| `opencode` | `~/.config/opencode/skill` | 0（**目录在、内容空**，status 如实报） |

### 4.3 待重验 2 项（不得写成定论）

- `~/.codebuddy/skills`：PT-20261002-11 记 1 项，本轮 `maxdepth 3` **未列出**
- `~/.hermes/skills-hot`：PT-11 记 16 项，本轮 `maxdepth 4` **0 项**

⇒ 施工 D1 第一步就是逐层 `find` 重验这两处，结果写进 `status` 而非文档。

### 4.4 明确排除 11 处

- 市场缓存 5：codex `.tmp/plugins` 504 · workbuddy connectors-marketplace 716 · codebuddy marketplaces 171 · grok marketplace-cache 72 · claude plugins/marketplaces 41
- 快照/备份 6：`技术文档/snapshots` 664 · `全量备份` 247 · `.mnt-jishu` 946 · `Hermes-backup` 125 · `.pi-upgrade-backup` 2 · `work` 40

**排除项必须在 `/api/skill/status` 与前端自检卡里列出「条目数 + 排除理由」**，不静默（军规：禁把 SKIP 当 PASS）。

---

## 5. 相关性口径（用户选 A：BM25 同步 + jev 异步）

| 层 | 实现 | 延迟 | 失败处置 |
|---|---|---|---|
| 同步 BM25 | 对 ~300 条 `name + description + 目录名 + frontmatter triggers` 内存打分；中英混排按词切 + CJK 二元组 | 20~60ms | 必成功路径，无外部依赖 |
| 异步 jev | `choice` 把 top-K 精排到 top-3，`SKILL_JEV=0` 可整关 | 70~500ms | fire-and-forget + TTL 300s 缓存；**探活 ≤2 次**，2 次不通即记「jev 不可达」永久退回 BM25，不重试不轮询 |

**降级链**：`jev 缓存命中 → BM25 top-3 → name-only 清单 → 空清单 + 一行「本轮无匹配」`。任一层失败**不阻塞输入**（沿用 facade 600ms 预算 + 静默降级先例）。

**注入预算**：top-3 全描述（≤300 token）+ 命中域 name-only 挤剩余，pi / claude 各 800 token 可调。

---

## 6. 四档落地分工

| agent | 推送 | 可见性（精选集） | 兜底 MCP | 记账 |
|---|---|---|---|---|
| pi | ✅ 零配置 | ✅ | ✅ REST | ✅ |
| claude | ✅ **授权 1** | ✅ | ✅ 已通 | ✅ |
| codex / opencode / qwenpaw | ❌ 无输入钩子 | ✅ | ✅ 已通 | ✅ |
| hermes / jcode / grok / codebuddy / qoder | ❌ 无输入钩子 | ✅ | 待验 | ✅ |

**不铺全部 240 条**：claude 有 `skillListingBudgetFraction`，240 条会撑爆每轮上下文。可见性只铺**精选集**（自研 18 + PT-11 高价值集 + 各家 bundled），全量走 hub + MCP 按需取。

推送型机制取证结论：**10 家里只有 pi（扩展钩子，已实证）与 claude（官方 `UserPromptSubmit`，现 `hooks=null`）有输入推送**；jcode `[hooks]` 只有 `pre_tool_timeout_ms`，非输入钩子。

---

## 7. 记账与僵尸榜（两源 + 置信度）

| 数据源 | 能记到什么 | 局限 |
|---|---|---|
| hub `profile_events` | 精确、含 agent 归属 | 只覆盖走 hub 的读；直读磁盘记不到 |
| 各家会话日志 grep `SKILL.md`（pi jsonl / claude projects / jcode / codex…） | 能看到直读盘 | 只对有日志的 5~6 家可行 |

**僵尸判据**：`days_idle ≥ 7` 且两源皆零 → `confidence=high`；仅 hub 源为零 → `confidence=medium`（直读未取证，**不得报 high**）。
**建议动作只出建议**（补 triggers / 铺软链 / 评估退役），**绝不自动删**。

---

## 8. 前端改版设计

### 8.1 现状缺陷（取证于 `templates/index.html:1635-1690` 与 `static/hub/04-terminal-ws.js:586-680`）

| # | 缺陷 | 证据 |
|---|---|---|
| F1 | **同一事实两处真相**：HTML 写死「7 路发现点」，JS 动态写路数 ⇒ 加到 20 路后静态那行说谎 | `templates/index.html` 工具条内 `span.hint` |
| F2 | 77→240 条后是一坨长列表：无分组、无计数、无排序 | `renderSkillList()` 仅 filter+map |
| F3 | `/api/skill/relevant` 建好后**前端无入口** | `renderSkillList` 只会子串过滤 |
| F4 | 预算卡是纯文本墙，被裁区只加粗，无装填可视化 | `loadSkillBudget()` `join('<br>')` |
| F5 | 零调用不可见：列表看不出「从未被读过」 | `skill.read`=0 在页面上无体现 |
| F6 | 发现点无自检视图：哪路 ok / 降级 / 0 条 / 被 realpath 拒读只压成一行文案 | `degraded.join(',')` |
| F7 | 装不到 10 家：目标下拉来自 backends，无体检入口 | `instTo` 单个多选 |
| F8 | 窄屏：新增表格型内容必须遵守分档偏好五条不变量 | 分档不变量（09-23 事故后成文） |

### 8.2 新信息架构：1 主区 + 3 诊断区

**主卡「技能发现」**
- 工具条：搜索框 + 发现点多选芯片（复用 `.picks`）+ 视图切换「列表 / 按发现点分组」+ 排序「名称 / 调用热度 / 最近使用」+ **筛选芯片「只看 7 天零调用」**
- 列表项：`名称 + 来源路徽章 + 描述（2 行截断）+ 末次调用时间或「从未调用」徽章 + 查看按钮`
- 分组视图：每组头显示 `路名 + 条数 + 状态点（ok / 降级 / 0 条 / 有拒读）+ 展开`（240 条时防 12 屏长滚）

**诊断卡 A「发现点自检」（新增）**
- 每行：`路名 / 条数 / 状态 / 被 realpath 拒读数 / 动作`
- 单独一段列**排除项**（市场缓存 5 + 快照 6，各显条目数与理由）

**诊断卡 B「相关性实验室」（新增）**
- 输入一句话任务 → 打 `/api/skill/relevant` → 显示 `top-N（名称 + 描述 + 得分 + 命中词 + jev 是否参与 + 耗时）`
- 并显式一句：「本次注入会用哪 3 条、会不会被预算裁掉」⇒ **把后端新能力变成可见可验**

**诊断卡 C「注入预算」（改版）**
- 装填进度条 `used_est / budget` + 三档分区（全条目 / 仅名 / 被裁）各自条数 + 被裁清单折叠
- 显式一句：「被裁 = 注入时 agent 看不到它」

### 8.3 硬约束

- **共享数字唯一真源**：删掉 HTML 里的静态「7 路发现点」，路数/条数全部由 `/api/skill/list` + `status` 渲染
- **构建纪律**：技能中心代码在 `static/hub/04-terminal-ws.js`（**`static/hub.js` 是生成物**）⇒ 改源模块后跑 `scripts/build_hubjs.sh`，并用 `tests/test_hubjs_split.py` 验逐字节相等
- **窄屏**：断点只允许 `HUB_NARROW_MQ`（`static/hub/01-core-boot.js:6` 已有唯一真源）一处；自检表窄档改「行内两行 + 芯片」，禁固定列宽 `<table>`；首屏解析偏好**只读不写**；键名带档位
- **浮层**：技能正文抽屉继续只经 `openOverlay('skillDocDrawer')` 打开（浮层三条红线）

### 8.4 前端闸门

新增 `tests/verify_skill_center_ui.py`，断言：
① HTML 内无静态路数文案 ② 列表渲染含「从未调用」徽章与末次调用字段 ③ 自检卡覆盖 backends 全部路 + 排除段 ④ 相关性实验室命中 `/api/skill/relevant` ⑤ 预算卡三档分区齐全 ⑥ 断点唯一（无第二处 `innerWidth < 768`）

---

## 9. 授权与禁区

**已授权**：改 `~/.claude/settings.json`（+备份）· 重启一次 `agent-hub.service` · git commit（禁 push）

**禁区不变**：禁 push · 禁 `pkill` · 禁改 CCR/FCC · 禁动 hermes/qwenpaw/codebuddy/jcode 任何配置 · 禁把 240 条全铺 · 禁自动删技能 · 子代理只读 · 产物不落 `/tmp` · 禁直接改 `static/hub.js` 生成物

---

## 10. 验收判据（逐项判 PASS/FAIL/**不可判定**，禁 SKIP 当 PASS）

| # | 判据 | 方法 |
|---|---|---|
| P1 | 20 路全部可列，去重后条数与 excluded 计数一致 | `/api/skill/list` + `/status` 逐路报 ok/skipped |
| P2 | `/api/skill/relevant` 同步层 <150ms，top-3 命中 8 组人工标注任务 | 单轮探针 + 固定夹具 |
| P3 | jev 可关可降级；2 次不通即永久 BM25 | 注入假端点跑 2 次即停 |
| P4 | pi 侧注入后下一轮 agent **真去读**了 top-1 | `profile_events.skill.read` 增量；**终验由用户端侧确认** |
| P5 | claude hook 生效 | hook 日志 + 一次真实对话 |
| P6 | 软链体检 10/10 PASS，每条 `read -L` 可解析 | 复用批次②体检脚本 |
| P7 | 僵尸榜可断言：字段齐、置信度分级正确 | 夹具造零读技能 |
| P8 | 备份存在、改动清单 + 时间戳落盘 | `ls -la *.bak-*` |
| P9 | 新增路由同步进 `_allowed_roots`，无 realpath 越界拒读 | `/api/skill/read` 逐路由 smoke |
| P10 | 前端 6 条闸门全 PASS | `tests/verify_skill_center_ui.py` |
| P11 | `build_hubjs` 后 `hub.js` 与源模块逐字节相等 | `tests/test_hubjs_split.py` |
| P12 | 端侧确认（pi + claude 各 1 次真调用） | **用户确认，AI 不自证** |

---

## 11. 回滚

`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<说明>` 逐文件回滚；软链用 `install`/`remove`（只删链）；claude hook 摘除；重启回旧进程。
**任一验收不过立即回滚本次备份并停手，不在故障态追加实验。**

---

## 12. 分期

| 期 | 内容 | 完成线 |
|---|---|---|
| D1 | 20 路发现点 + 排除清单 + `/status` 如实报账 + 待重验 2 项 | P1 |
| D2 | `/api/skill/relevant`（BM25 + jev 可关 + 降级链） | P2/P3 |
| D3 | 记账 + 僵尸榜 | P7 |
| D4 | pi 推送注入（改 `hub-facade.ts`，零配置） | P4 本机 PASS，端侧待确认 |
| D5 | claude hook（授权 1） | P5 |
| D6 | 10 家软链铺精选集 + 体检 | P6/P9 |
| D7 | 前端改版（改 `04-terminal-ws.js` + `index.html` + 重建） | P10/P11 |
| — | pi-web 0.9.3→0.10.0 | **用户执行**：升级 + 重启 → 新会话续接（交接件落 `agent-hub/logs/`） |

估 **2.5~3.5 小时**（不含用户确认等待）。

---

## 13. pi-web 升级交接件（用户执行，本会话不自杀）

```bash
# ① 备份并升级（老包按先例改名留痕，禁直接覆盖）
cd ~/.npm-global/lib/node_modules/@agegr/pi-web.old-0.9.3 2>/dev/null || true
mv ~/.npm-global/lib/node_modules/@agegr/pi-web \
   ~/.npm-global/lib/node_modules/@agegr/pi-web.old-0.9.3
npm view @agegr/pi-web version          # 确认 0.10.0
npm i -g @agegr/pi-web@0.10.0
# ② 重启（本会话在此终止）
systemctl --user restart pi-web.service
# ③ 升级后自证（三项，缺一不算通过）
pi-web --version
curl -s -m 5 http://127.0.0.1:30141/ -o /dev/null -w 'pi-web HTTP %{http_code}\n'
curl -s -m 5 http://127.0.0.1:3102/api/skill/relevant?q=技能检索 -w '\n'
```

新会话读 `agent-hub/logs/pi-skill-center-handoff.md` 续接 D4~D7 收尾。
