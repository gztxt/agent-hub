# 联邦门面收口批（export 按钮 / asset_audit / 记忆可观测化 / 台账卫生 / hooks）设计稿

**日期**：2026-09-25　**取证基线**：`master@40a1f89`（v0.13.23，生产 boot sha 与 HEAD 一致，`code_stale=False`）
**会话**：`pi-01a0d6dd`　**施工位置**：worktree `~/agent-hub-wt-01a0d6dd`（分支 `wt/01a0d6dd`）
**授权**：用户 2026-09-25 12:5x「授权执行 ABC，另外最后单独执行 D」+ 13:0x「按推荐值全走」

---

## 0. 为什么先写这份稿：来料分析有 3/6 条与仓内实况不符

本批的输入是一份外部分析（列举 6 项"尚未执行或需优化"）。逐条实测后，**只有 3 项是真开口**。
把核验结果写在这里，是为了避免下一轮再拿同一份过期清单开工（09-25 台账已两次登记同类"方案事实错误"）。

| 来料断言 | 仓内实况（取证命令见脚注） | 判定 |
|---|---|---|
| 前端资产面板 P4 未完成 | `static/hub/07-asset-panel.js` 在册；`tests/test_asset_panel.py` 是 L0+L1 闸门（"四态可区分"红向）；台账 PT-20260924-13 记「P3+P4 实装完成」 | **断言过期** |
| `/api/sessions/export` 前端按钮未做 | 前端 `grep export` 零命中；仅 `src/main.py:531` 后端；PT-20260925-07 待办#3 明写 ❌未开工 | **真开口 → 件 1** |
| git hooks 未安装 | `.git/hooks/` 只有 `*.sample`；待办#4 定的窗口条件（grok turn 结束 + `term_sessions=0`）**此刻成立** | **真开口 → 件 5** |
| Docker / Compose 未做 | 用户 09-25 裁定「暂缓、留待后续迭代**升级**」，台账原话"是延期不是否决"；本轮**禁引任何容器依赖** | **越界，不入本轮** |
| 工作流 DAG 引擎未实现 | `src/tasks.py` 已是完整 DAG：SQLite 状态机 + asyncio 拓扑并发 4（`MAX_CONCURRENCY`）+ 600s 熔断（`TASK_TIMEOUT_S`）+ `sweep_stale_tasks()` 兜底 + LLM 拆解 JSON 校验（环/引用/深度） | **断言错误** |
| turbovec 检索结果未结构化 | `src/kb.py:64 _ROW` 正则 + `parse_turbovec_stdout()` 已产出 `{path, chunk, score, source}` | **已实现** |
| `asset_audit` 表缺失 | 全仓 13 表无 `asset_audit`；`grep -rn audit src/*.py` **零命中**（连写入点都没有）；`src/writeauth.py` 在册 | **真开口 → 件 2** |
| `memory.py` 的 consolidate 应后台化 | `memory.py` **无 consolidate**、**无任何 TTL/过期语义**；`delete_l1` 是软删（`status='deleted'`） | **前提有误 → 件 3 改判** |

脚注（取证命令）：`git log --oneline -12`、`grep -n CREATE TABLE src/*.py`、`grep -rn audit src/*.py`、
`grep -nE "^(async )?function " src/tasks.py`、`ls .git/hooks/`、生产库只读计数（`file:...?mode=ro`）。

**生产实况**（单轮复合探针，探活预算 1/2）：`status=ok`、`version=0.13.23`、`code_stale=False`、
`running_matches_head=True`、`git_sha_boot=40a1f89f`、`term_sessions=0`、`db_ok=True`、30 字段；
`systemctl --user is-active agent-hub`=active，PID 1090289 @ `0.0.0.0:3102`；TDAI @ `127.0.0.1:8420`。

**编排体检**（`scripts/orchestration-check.sh`，只读，退出码 1）：C3/C3b **FAIL** —
`src/main.py ← 01a0d2d9,01a0d409`、`PENDING-TASKS.md ← 4 会话(含 codex)`、`orchestration-check.sh ← 2 会话`；
C5 WARN（2 个产物在 `/tmp`）；C4/C6/C7/C8/C10/C11 全 PASS。

---

## 1. 目标与非目标

**目标**：把联邦门面路线上**三个真开口**补完（用户可感知的导出按钮、资产变更可追溯、本地记忆便签腐烂可观测），
并把两件治理债（台账卫生落盘、hooks 安装）收掉。

**非目标（本轮明确不做，理由写死避免下轮重提）**：

- Docker / Compose 容器化 —— 用户裁定暂缓；且容器化必然要求停机切换，与"禁随意重启生产"同向。
- 工作流 DAG 引擎 —— 已实现（`src/tasks.py`）。
- turbovec 结果结构化 —— 已实现（`parse_turbovec_stdout`）。
- 前端资产面板 —— 已实现（`07-asset-panel.js` + 闸门）。
- **不建 `knowledge.db` / 不做 ETL / 不灌数据** —— 联邦门面路线的立身之本（`kb.py`/`skill.py`/`memory.py`
  三处顶部注释均记："hub 只做门面 + 归并 + 审计，一个字都不存"）。本批新增的 `asset_audit` 表
  **不是**第二权威副本：它记录的是"hub 自己库里那些资产的变更事件"，权威副本仍是各资产本身。
- 端口 kill 能力 —— README 明标"只读无 kill（NAS 军规）"，主动设计决策。

---

## 2. 全局约束（每个任务都隐含遵守）

1. **改前必备份**：`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<说明>`（说明 ≤10 字，短横线连接）。无备份不写。
2. **禁 `sed -i` / 全局替换**（09-07 事故根因）。
3. **施工只在自己的 worktree**；主 checkout 只由集成者合并 + 重建 `static/hub.js`（C7 单写者）。
4. **子代理只读**，写一律由主会话串行落盘（C4 闸门守着；用户 09-24 13:20 单会话可追踪裁定）。
5. **产物不落 `/tmp`**（C5）。中间件进仓内 `work/`（已在 `.gitignore`）。
6. **重启须用户逐次授权**；本批后端两件攒成**一次**重启申请，重启前实测 `term_sessions==0`。
7. **探活预算 ≤2 次**（已用 1）；同一判据重试 ≤2 次；**禁把 SKIP 当 PASS**。
8. **凭据零回显**：任何日志/报错/审计 detail/前端文案都不得出现 token 原文（本工作区三次外流前例）。
9. **测试三层口径**（`tests/tiers.py` 唯一真相源）：L0 hermetic 零宿主依赖且**不许 SKIP**；
   L1 host 打 `@tiers.host_only`；L2 live 是 `verify_*.py`/`probe_*.py`。当前 L0=313 / L1=35。
10. **每件配红对照**：把关键不变量改坏，闸门必须 FAIL；不 FAIL 的闸门等于没有。
11. **当天 commit + 沉淀**（C8）：`agent-knowledge/` 一条 + 台账一条（`PT-20260925-10`）。

---

## 3. 件 1｜export 前端按钮（纯前端，不需重启）

### 落点

`static/hub/05-chat-and-history.js` 的 `histHtml(aid)`（每 agent 历史面板头）。
**不手改** `templates/index.html`（今天被 `01a0d5de` 写过；它只会被 `build_hubjs.sh` 自动改写 `?v=` 提手——
那是构建脚本的既有行为、由集成者执行、脚本自带首次改动 `.bak`，不算我方手工编辑）、**不改** `03-agents-cards.js`
（`01a0d513` 活跃中，且 `termToken()` 只调用不修改）、**不改** `02/06`（今天被写过）。

### 三个实测出来的坑（决定实现形态）

1. **不能用现成的 `api()`**：`01-core-boot.js:96` 的 `isWriteMethod()` 只给 POST/PUT/PATCH/DELETE 带 token，
   而 export 是 **GET 却要写级鉴权**（`main.py:531` 显式 `writeauth.decide("POST", request.url.path, …)`）
   ⇒ 走 `api()` 必 401。
2. **不能用 `api()` 取体**：它 `JSON.parse` 后返回对象，会毁掉 CSV/JSON 文件字节
   ⇒ 必须 `fetch` → `blob()` → `URL.createObjectURL` → 临时 `<a download>` 点击 → `revokeObjectURL`。
3. **不能用 `location.href='…?token=…'`**：token 会进服务端访问日志与浏览器历史
   ⇒ token 走 `X-TERM-TOKEN` 头（`writeauth.TOKEN_HEADERS` 认 `x-hub-token`/`x-term-token`）。

### 不变量（照抄 `07-asset-panel.js` 的"四态可区分"红向口径）

`401 缺凭据` / `401 凭据不匹配` / `503 服务端未配口令` / `200 且 count=0` 四态在**文案上不可混淆**：
被拒时绝不许出现"没有会话/零条"这类会被读成"资产为空"的字样。
成功后 toast 回报 `X-Export-Count` 与 `X-Export-Redacted-Hits`（**不静默改数据**：打码命中数必须说出来）。
默认 `redact=1`；要原始字节须用户显式改选 `redact=0`（UI 上做成显式勾选，不做默认）。

### 闸门

新增 `tests/test_export_button.py`：
- **L0**（静态，只读 `static/hub.js` 与 shard 真文本）：必须出现 blob 下载路径；必须带 `X-TERM-TOKEN`/`x-hub-token` 头；
  **绝不许出现 `?token=` 拼接**；四态文案互斥（被拒分支的字符串集合与"空结果"分支的字符串集合交集为空）。
- **L1**（`@tiers.host_only`，node 真跑从 hub.js 原样抽出的渲染/判定函数，复用 `tests/_hub_extract.py`）：
  喂 401/503/200-count0/200-count3 四种载荷，断言输出文案归类正确。
- **红对照**：把"被拒"文案改成"没有会话" ⇒ L0 必须 FAIL。

### 生效与回滚

纯前端 ⇒ **不需重启**。但必须重建 `static/hub.js`（`bash scripts/build_hubjs.sh`，原子写 + `node --check`），
且该脚本会自动改写 `templates/*.html` 的 `?v=` 提手（内容派生 md5 前 8 位，首次改动自带 `.bak`）
⇒ **这一步只能由集成者在主 checkout 做**，且要先确认无其他会话在 build（C7）。
`tests/test_hubjs_split.py` 会判"拼接结果与仓库里的 hub.js 逐字节相等"，漂移即红。
提交后 `/health.code_stale` 可能转 true —— 那是该字段的设计意图（AGENTS.md 已记：不为此重启生产）。
回滚：`git revert` 该提交 + 重跑 `build_hubjs.sh`。

---

## 4. 件 2｜asset_audit：资产变更可追溯（后端，需重启）

### 表结构（合并方案档 §4.3 与来料 4.3 两版）

```sql
CREATE TABLE IF NOT EXISTS asset_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type  TEXT NOT NULL,   -- mcp_server|mcp_acl|memory_l1|memory_doc|agent|session|setting
    asset_slug  TEXT NOT NULL,   -- 资产标识（server_id / acl_id / mid / layer / agent_id / session_id）
    action      TEXT NOT NULL,   -- create|update|delete|bind|unbind|rebuild
    actor       TEXT NOT NULL,   -- user:term-token | user:hub-passcode | agent:<slug> | system
    detail      TEXT DEFAULT '{}',  -- JSON；**绝不许含凭据原文**
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_asset ON asset_audit(asset_type, asset_slug);
CREATE INDEX IF NOT EXISTS idx_audit_created ON asset_audit(created_at);
```

落点 `src/db.py` 的 `SCHEMA` 尾部（照抄 `profile_events` 的 append-only 形态）+ 新 helper
`log_asset_event(asset_type, asset_slug, action, actor, detail=None)`，签名与 `log_profile_event` 同族
（`execute(INSERT…)`，`detail` 用 `json.dumps(ensure_ascii=False)`）。

### actor 从哪来（本件唯一的真设计问题）

`writeauth.decide()` 现在只回 `(verdict, reason)`，**不解析身份**，且 `tests/test_writeauth.py` 12 例钉着签名。
三种做法与取舍：

| 做法 | 判定 |
|---|---|
| 改 `decide()` 回三元组 | 语义最干净，但撞 12 例既有测试 + 撞 export 端点调用点 ⇒ **否** |
| **新增独立纯函数 `credential_name(provided, secrets)`，`decide()` 一字不动** | 零回归、L0 可空 HOME 取证、只回凭据**名**不回值 ⇒ **采用** |
| 各 handler 内联判定 | "漏一条就等于没做"，正是 `writeauth.py` docstring 反对的形态 ⇒ **否** |

- `credential_name(provided, secrets) -> "term-token" | "hub-passcode" | "anonymous"`：
  按 `secrets_from_env()` 的顺序（`TERM_TOKEN`, `HUB_PASSCODE`）用 `hmac.compare_digest` 比对，
  命中即回**名字**；未命中回 `"anonymous"`。绝不返回值本身，也不返回索引以外的信息。
- `write_gate` 的 allow/exempt 分支加一行 `request.state.actor = "user:" + credential_name(...)`（exempt 路径回 `"user:exempt"`）。
- handler 侧一律 `getattr(request.state, "actor", "system")` ⇒ 审计不依赖每个 handler 自觉。
- MCP 侧 actor 取 `body.agent_id` → `"agent:<slug>"`；后台循环（sweeper/loop）→ `"system"`。

### 审计覆盖面：**只审变更，不审读**（裁定）

来料 4.3 建议把"`skill.py` 的读取请求、`kb.py` 的检索请求"也写审计 ⇒ **不采纳**，理由三条：
① 读事件已由 `telemetry_events` / `profile_events` 覆盖（576 行在册）；
② 给每次 KB 检索加一次写 = 在热路径上加锁写盘，而 KB 检索有 1.2s/2.0s 硬预算，写入抖动会直接吃掉预算；
③ 4 行 memories 对 576 行 profile_events 的量级下，审计表会被读事件淹没 ⇒ 信噪比崩，可追溯性反而下降。
方案档 §4.3 原意（"Wiki 编辑、技能注册、工具注册、ACL 变更"）本来就全是**变更**。

覆盖点（34 条写路由里的**资产类变更**，逐条给理由；非资产类如 `chat`/`stream`/`probe`/`verify`/`detect`/`scan` 不审）：

| 落点 | 路由 | asset_type / action |
|---|---|---|
| `src/mcpgw.py` | `POST /mcp/servers`、`DELETE /mcp/servers/{sid}` | `mcp_server` / create·delete |
| `src/mcpgw.py` | `POST /mcp/acl`、`DELETE /mcp/acl/{acl_id}` | `mcp_acl` / bind·unbind |
| `src/memory.py` | `POST /api/memory/l1`、`/l1/batch`、`DELETE /api/memory/l1/{mid}` | `memory_l1` / create·delete |
| `src/memory.py` | `PUT /api/memory/l2`、`PUT /api/memory/l3`、`POST /api/memory/l2/rebuild` | `memory_doc` / update·rebuild |
| `src/main.py`（**冲突面，见下**） | `POST/DELETE /api/agents…`、`PATCH/DELETE /api/sessions…`、`POST /api/settings/term-token` | `agent`·`session`·`setting` |

**`src/main.py` 是 C3 冲突文件**（今天 `01a0d2d9`+`01a0d409` 写过，`01a0d6df` 12:53 仍活跃）。处置分两层：

- **必需改动只有 1 行**：`app.include_router(audit_mod.router)`，加在 `main.py:207-214` 那组
  `include_router` 尾部（纯追加，冲突面极小）；在 worktree 里改，由集成者合并。
- **5 条资产路由的审计调用**（agents / sessions / settings）做成**独立可选子任务**；
  若合并窗口内 `main.py` 仍被他人占用，则该子任务**顺延不硬闯**，并在台账与交付报告里
  如实标注「覆盖面 N/M 已审，5 条待下一窗口」（N/M 以实际落地的调用点数为准，不预估）。

件 2 的**必做面**（不受 `main.py` 冲突影响）＝`src/db.py` + `src/audit.py`（新）+ `src/mcpgw.py`
+ `src/memory.py` + `src/writeauth.py`，今天均无人写。

### 查询端点

`GET /api/audit/list?asset_type=&asset_slug=&limit=`，落 `src/db.py` 同批新增的 `src/audit.py`（新文件，零冲突），
自带 `APIRouter`，由 `main.py` 一行 `include_router` 挂载（这是本件对 `main.py` 的**唯一必需改动**，1 行）。
**按写级鉴权**：照抄 `main.py:531` export 端点的既有先例 —— `writeauth.decide("POST", request.url.path, …)`
强制按写方法判（审计日志含 actor 与变更细节，属数据外流动作）。fail-closed：服务端未配口令 ⇒ 503。

### 闸门

新增 `tests/test_asset_audit.py`（L0，不 import `src.main`）：
- 表在 `SCHEMA` 里；`log_asset_event` 是 append-only INSERT（不得含 UPDATE/DELETE）。
- `credential_name` 纯函数：命中回名、未命中回 `anonymous`、**返回值里绝不出现入参凭据的子串**。
- **覆盖面静态护栏**（与 `test_writeauth.py` 同口径）：扫 `src/mcpgw.py`+`src/memory.py` 的资产类变更路由，
  断言每条都有 `log_asset_event` 调用；漏一条即 FAIL（"漏一条就等于没做"）。
- **红向**：把 `detail` 里塞一个 `sk-…` 形态假凭据 ⇒ 断言写入前被打码（复用 `sessions_export.redact_text`）或拒绝。
- **红对照**：删掉任一处 `log_asset_event` 调用 ⇒ 覆盖面护栏必须 FAIL。

### 生效与回滚

需重启（与件 3 合并为一次申请）。回滚：`git revert` + 重启；表是新增的，`CREATE TABLE IF NOT EXISTS`
对旧库幂等，回滚后遗留空表无害（不 DROP，避免误删审计证据）。

---

## 5. 件 3｜本地记忆便签 staleness 可观测化（B 项改判，后端，需重启）

### 改判依据（实测）

`memory.py` **无 consolidate、无 TTL/过期语义**；`delete_l1` 是软删。生产库只读实测（`mode=ro`）：

```
tables=13   memories rows=4  status={'active':4}  最老=2026-09-06T03:51  最新=2026-09-06T03:57
memory_docs rows=1   chat_sessions rows=127   chat_messages rows=310
profile_events rows=576   mcp_servers rows=2   mcp_acl rows=4   db size=1.27MB   WAL=0
```

⇒ 4 行全 active、零软删行、**19 天没长过一行**。后台清理任务会永远空转；
而"后台自动重建 L2"这条路径**有事故前例**（`writeauth.py` docstring：09-23 rebuild 真的重写了用户手写 L2 记忆，
被迫按 09-06 在册副本逐字回滚）；且记忆权威副本在 TDAI(:8420)，删本地便签零收益。

三个选项与裁定：

| 选项 | 裁定 |
|---|---|
| B-1 撤销 | 净收益≈0，符合"按净收益裁定" |
| **B-2 改判为 staleness 可观测化** | **采用**：非破坏性、零删除、零 LLM 调用，让"腐烂"可见而不是被静默清理 |
| B-3 真做 TTL 删除 | 破坏性；需用户定 retention；与"唯一权威副本"冲突 ⇒ 否 |

### 落点与形态

新增 `src/memstats.py`（新文件，零冲突），纯函数 + 一个 `APIRouter`：

- `local_stats(conn_like) -> dict`：`rows`、`active`、`soft_deleted`、`superseded`、
  `oldest_age_days`、`newest_age_days`、`docs`（L2/L3 各自 `updated_at` 龄与 `manual` 是否非空）。
- `verdict(stats) -> {"state": "fresh|stale|empty", "reason": "…"}`：**纯判定**，阈值写死为常量并注理由
  （`STALE_DAYS = 14`：与本机"结论必须绑定取证日期"的口径同族；超过两周没动过的本地便签，
  在 KB 融合里权重只有 0.2，实际上已经不参与决策 ⇒ 报 stale 而不是删）。
- 挂到 **`/api/kb/status`**（`kb.py` 今天无人写 ⇒ 零冲突，**不碰 `main.py`**）：
  在既有 `kb_status()` 的返回里加一个 `local_memory` 键。
- **绝不自动删、绝不自动 rebuild、绝不调 LLM**。

### 闸门

新增 `tests/test_memstats.py`（L0）：喂构造行集断言 `oldest_age_days`/`state` 计算正确；
空表 → `state="empty"` 且 reason 里不出现"清理/删除"字样（红向：这个模块不许暗示它会动数据）；
**红对照**：把 `STALE_DAYS` 判定改成"顺手 DELETE" ⇒ 静态断言 FAIL（模块文本里出现 `DELETE FROM memories` 即判红）。

### 生效与回滚

需重启（与件 2 同批）。回滚：`git revert`；`/api/kb/status` 少一个键，前端未消费 ⇒ 零影响。

---

## 6. 件 4｜台账卫生落盘（C 项，技术文档仓，不改 agent-hub 代码）

### 真实剩余量（dry-run 实测，与来料描述不同）

三份补丁件**此刻全被守卫拒绝**，原因各异：

| 补丁件 | dry-run 结果 | 含义 |
|---|---|---|
| `ledger-patch-01a0d69a.json` | `BAD PT-20260925-09 台账里已有 1 条同名标题` | 已落盘，作废 |
| `ledger-patch-01a0d69a-fix1.json` | 1 条锚点 0 次 + 1 条 OK | 部分已落盘；owner 行已被他人改过 |
| `ledger-patch-01a0d5db.json` | **3 条 BAD（C1 横幅已落盘）+ 7 条 OK 未落盘** | 7 条被 3 条过期锚点**连坐**（应用器全有或全无） |

未落盘的 7 条 = 5 条 C2 状态行规范化 + 2 条 M1 改判横幅。
真源是 `scripts/make-ledger-patch.py`，JSON 是**派生产物** ⇒ 正解是**重跑真源重生补丁件**
（基于当前台账文本重算锚点），而不是手改 JSON。

### 做法

1. `python3 scripts/make-ledger-patch.py` 重生（**用户已批准动他人 01a0d5db 的成品件**）。
2. `apply-ledger-patch.py --dry-run` 核验：全部锚点唯一 + PT 防撞通过。
3. 静默窗成立时落盘（自带时间戳备份；实测此刻窗成立：目标文件 54 分钟未被写、窗内 0 写者、无 turn 进行中）。
   **禁 `--force`**；被拒就留成品件如实报，不硬闯。
4. 落盘后跑 `orchestration-check.sh` 复验 C1/C2。
5. 我自己的新条目取号 **`PT-20260925-10`**（当前最大 `PT-20260925-09`），靠应用器的 PT 防撞兜底
   （`01a0d6df`/`01a0d61a` 可能同时取号；撞号则顺延到 `-11` 并如实记录）。

### 风险

`01a0d6df`（12:53 活跃，提及台账 17 次）与 `01a0d61a`（提及 11 次）可能随时写台账 ⇒ 守卫是唯一裁判。
本件**排在代码两件之后**执行，避免我长时间占着静默窗。

---

## 7. 件 5｜hooks 安装（D，末批单独执行）

`scripts/install-hooks.sh` 已交付但刻意未装（hooks 落 **common git dir** ⇒ 一装即改掉**全部 6 个会话**
（含活 worktree `wt/01a0d5db`、codex、claude）的提交路径）。用户裁定：放最后单独执行。

窗口条件此刻成立（`term_sessions=0`）。顺序：

1. `bash scripts/install-hooks.sh --status`（看现状，留证）
2. `bash scripts/install-hooks.sh --dry-run`（看会做什么）
3. `bash scripts/install-hooks.sh`（安装；脚本自带"绝不静默覆盖既有 hook"与备份）
4. `--status` 复验 + 打印逃生口（`git commit --no-verify` / `git push --no-verify` / `--uninstall`）
5. 自证生效：一次 `git commit --allow-empty` 走过 pre-commit（L0 必过）与 pre-push（六道）

**语义**：测试失败 = 拦（fail-closed）；环境缺失 = 放行并大声警告（fail-open，避免把工具问题伪装成代码问题）。

---

## 8. 执行顺序与合并

```
① worktree 内：件 2（asset_audit）→ 件 3（memstats）   [后端，同批一次重启]
② worktree 内：件 1（export 按钮）                       [前端，需集成者重建 hub.js]
③ worktree：L0+L1 全绿 0 skip → commit → push
④ 集成者（主 checkout）：merge --no-ff wt/01a0d6dd → build_hubjs.sh → prepush.sh → push
⑤ 一次重启申请（用户授权）→ 生产复验
⑥ 件 4（台账卫生，静默窗）
⑦ 件 5（hooks 安装，末批单独）
⑧ 沉淀 agent-knowledge/ + 台账 PT-20260925-10 + 当天 commit
```

**为什么这个顺序**：后端两件不碰 build 产物，先做完可尽早申请重启；前端件必须最后由集成者统一重建
`hub.js`（C7 单写者）；台账与 hooks 都是治理债，排在代码之后以免长时间占用静默窗与提交路径。

## 9. 验收判据（全批）

- L0 hermetic 全绿 **`skipped=0`**（现 313，新增后上升并如实报数）；L1 host 35 全绿。
- 每件的红对照实测 FAIL（附输出）。
- `prepush.sh` 六道通过；`test_hubjs_split.py` 逐字节相等。
- 重启后 `/health`：`version` 递增、`code_stale=False`、`running_matches_head=True`、`git_sha_boot==HEAD`、`db_ok=True`。
- **真功能验证**（非仅文本通）：① 前端真点一次导出 → 落地文件 + `X-Export-Count`；
  ② 真做一次 ACL 变更 → `asset_audit` 里出现对应行且 `detail` 无凭据原文；
  ③ `GET /api/kb/status` 回 `local_memory.state`。
- **探活预算口径（显式声明，防违约）**：09-23 裁定是「探活最多 2 次即结束、禁反复频繁探测」，
  其意图是禁轮询与禁重试。本批跨**三个互不相同的判据**各取一次单轮复合证据：
  ① 基线（已用，12:4x）；② 重启前（`term_sessions==0` + `pgrep -P <hub-pid>` 为空，照抄 01a0d5db 11:26:20 做法）；
  ③ 重启后（`/health` 30 字段一次取齐）。合计 3 次、**每次回答的是不同问题**；同一判据不重试第 2 次，
  任一次不可判定即停手如实报 FAIL，**禁第 4 次**。
- **端侧结项**：手机端窄屏下导出按钮可点、文案不混淆 —— 本机对手机端零探针能力，
  此项**只能由用户确认**，交付报告里标注为中间态并预留回填位。

## 10. 未闭合项与判定权归属

| 项 | 状态 | 判定权 |
|---|---|---|
| 手机端导出按钮可用性 | 本机不可观测 | **用户**（端侧确认＝结项） |
| `main.py` 的 5 条资产路由审计覆盖 | 视合并窗口 | 我方如实报覆盖面，顺延不硬闯 |
| hooks 安装后对其他 5 个会话的影响 | 装后观察 | 用户（可随时 `--uninstall`） |
| `asset_audit` 长期体积治理（保留期） | 本轮不做 | 用户（未来裁定 retention） |
