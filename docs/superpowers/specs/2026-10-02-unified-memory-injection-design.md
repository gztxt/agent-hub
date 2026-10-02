# 统一记忆注入通道设计（D1 + D1.5，2026-10-02）

> 立项病根（2026-10-02 实测取证，非推断）：
> `GET /api/memory/context` 收了 `sources` 里 9 路联邦源 ID、**过白名单校验不报 400**，
> 然后在函数体里被静默丢弃——实测回包 `backends` 只有 `tdai_profile` 与 `local`，
> `took_ms` 6~21ms（预算是 1.2s，几乎全空）。而 `/api/memory/search` 早已接 memfed。
> ⇒ **agent 唯一的开局注入通道拿不到任何联邦记忆**；「搜索能聚合、注入不能」是分裂的根源。
> 这是本仓反复警告的「全指标绿而功能层已死」同族：HTTP 200、无异常、无告警。

## 一、D1：注入通道接联邦（能力 + 可观测）

### 改动面（4 文件，全部仓内）
| 文件 | 改动 | 备份后缀 |
|---|---|---|
| `src/memfed.py` | `search_fed` 加**可选** `wall_s` 墙钟参数（默认 `None` = 现行为不变） | `.bak-20261002_122141-D1-inject-fed` |
| `src/memory.py` | `injection_context` 接联邦 + 新增 `fed_chars`/`fed_top` 参数 + 第 4 段 | 同上 |
| `static/hub.js` | 记忆页 `sources` 兜底值与后端默认对齐 | 同上 |
| `tests/test_memfeed_inject.py` | 新增 L0 闸门 G1~G5（新建文件，无需备份） | — |

不改：`memfed.REGISTRY`（10 路已就绪）、`hubmcp` 工具面、`.env`、任何仓外共享配置。
**D1 单独上线对 agent 无可见召回收益**（默认行径由 D1.5 才打开）——这是有意的分步：
先把通道接通并可断言，再单独翻默认，避免一次改动同时动「能力」与「默认」两件事而无法二分定位。

### 墙钟语义（`search_fed(wall_s=...)`）
- `wall_s is None`：走原 `asyncio.gather`，**27 例既有 L0 断言一字不变**。
- 给定 `wall_s`：改 `asyncio.wait(timeout=wall_s)`，预算内完成的正常收集；
  未完成的记 `{"ok": False, "error": "skipped_budget"}`。
- 不取消未完成者：`asyncio.to_thread` 不可取消，线程按各源自带 `timeout_s` 自行收尾；
  只 `add_done_callback` 取走异常引用以免 asyncio 报 `exception was never retrieved`。
- **`skipped_budget` 不算故障**：它进 `backends[]`（`ok=false` ⇒ 自动进 `degraded`），
  与真故障同路表态、但 `error` 字样可区分——不静默、不混淆。

### 响应契约（纯 additive，老消费者零影响）
```jsonc
{ "context": "...", "chars": N, "truncated": bool,
  "backends": [ /* 逐路 {name,ok,count,ms,error} */ ],
  "degraded": ["pi_sessions"],
  "fed": { "sources": 9, "done": ["claude_mem"], "skipped_budget": ["archived_sessions"], "chars": 1832 },
  "took_ms": 812.3 }
```

### 注入包第 4 段（拼装顺序 = 截断优先级）
```
## 长期 Profile（TDAI 权威源）   ← 最高优先，永不挤掉
## 近30天工作记忆
## 相关记忆                      ← local + tdai_l1 RRF（既有）
## 其他 Agent 记忆（联邦）         ← 新增
```
每条 `- [{源 label}] {内容[:160]}`；逐源最多 `fed_top`（默认 3）条，
整段最多 `fed_chars`（默认 2000，≤4000）字，按 RRF 名次轮转取，
被预算挡在外面时段尾写「另有 N 路未在预算内返回：…」——**保持不静默**。

## 二、D1.5：打开默认行径（让收益可见）

### 2.1 后端默认
`/api/memory/search` 的 `sources` 默认由 `"local,tdai"` 改为快路集
`local,tdai,claude_mem,workbuddy_memory,claude_projects`。
取值依据（2026-10-02 实测 `/api/memory/search` 全联邦单轮）：
`workbuddy 47ms`、`claude_mem 148ms`（唯一有 FTS5 索引的源）、
`claude_projects 261ms`；并联墙钟 ≈ 最慢源 ≈ 300ms 量级。
**不选 `pi_sessions`/`codex_sessions`/`archived_sessions`**：实测三者 rg 超时（1.9/2.0/2.5s），
且 `archived_sessions` 要扫 5622 个文件——它们留给 A4 的索引投影。

### 2.2 pi 扩展预算（共享配置，已按军规点名授权 + 时间戳备份）
文件 `~/.pi/agent/extensions/hub-facade.ts`（**实体文件，非软链**，不触技术文档）。
- 改：`pi.on("input")` 的 `hubGet(..., 150)` → `600`。
- 理由：联邦快源并联 ≈300ms，150ms 预算会让 pi 每次自动注入**必然超时静默丢弃**——
  改了后端默认而不改这里，等于把分裂从「没接」变成「接了但永远拿不到」。
- rev 自证串：`rev=20260926a-批5` → `rev=20261002b-D1.5`（pi extensionCache 不看 mtime，
  **需重启 pi 进程**才生效；日志行可断言进程内存里是哪一版）。
- `/hub-recall`（3000ms）与三命令不动。

## 三、闸门（G1~G8，L0 hermetic：不起服务、不打网络、不 import src.main）

| 闸门 | 断言 | 红向 |
|---|---|---|
| G1 | `sources=local,tdai,claude_mem` ⇒ `backends` 出现 `claude_mem` | 改前必红（本次病灶本身） |
| G2 | `sources=ghost` ⇒ HTTP 400 | 白名单不放宽 |
| G3 | 慢源 + `wall_s` 极小 ⇒ 200 不抛错、`skipped_budget` 非空、**快源仍收得到** | 墙钟不能把已完成的也吃掉 |
| G4 | 默认不带 `sources` 的 `/api/memory/context` ⇒ `fed.sources==0`（D1 不动**端点**默认） | 防止误翻端点默认 |
| G5 | AST 级：注入路径对外部源零写 | 沿用 `test_memstats.py::TestModuleCannotMutate` 同款写法 |
| G6 | 只读语义补强（`_fed_section` + `memfed.py` 双侧零写关键字） | 附录实测 5 处 |
| **G7** | `hub_memory_context` 签名暴露 `sources` 且默认空串 | **口子不得被删** |
| **G8** | 不传 ⇒ 转发 `memory.FED_FAST_SOURCES`；显式传 ⇒ 逐字转发 | **不得在工具层拄一份默认字符串** |

**G7/G8 是 2026-10-02 追加**（见八），它们钉的是一个**静默失败**：工具面改回纯权威库
不会报错，只会少掉别家 agent 的记忆。

**禁把 SKIP 当 PASS**：G3 用真 `search_fed` + 真慢源（sleep 夹具），不是把 fake 的
`skipped_budget` 字符串直接塞回去自证。

## 四、验证路径（不碰生产）
1. `python -m unittest tests.test_memfed tests.test_memfeed_inject tests.test_kb_federation -v`
   —— 确认既有 27 例不回归 + 新 5 例。
2. 临时端口起一份实例（先确认 `EMBED_PROXY_PORT` 等独占口不冲突）跑单轮复合探针取齐证据。
3. **生产重启需用户单独点头**（沿用「禁重启生产」边界）；切换命令写好待执行。

## 五、回滚
```bash
cd /home/gztxt/agent-hub
cp src/memory.py.bak-20261002_122141-D1-inject-fed src/memory.py
cp src/memfed.py.bak-20261002_122141-D1-inject-fed src/memfed.py
cp static/hub.js.bak-20261002_122141-D1-inject-fed static/hub.js
cp ~/.pi/agent/extensions/hub-facade.ts.bak-20261002_122141-D15-budget \
   ~/.pi/agent/extensions/hub-facade.ts
```

## 六、A4 全量（索引投影层）——本轮**不出**设计，D1 完成后另立

病根已由本轮实测定位：9 路源里**只有 `claude_mem` 有真索引（FTS5）**，其余 8 路每次查询
对整棵树跑 `rg` ⇒ O(文件数) 无索引。测得三路直接超时（`pi_sessions` 1894ms、
`codex_sessions` 2028ms、`archived_sessions` 2506ms）。
待投影源体积：`~/.pi/agent/sessions` 196M + 技术文档/会话备份 250M = **446M**；
根分区 89%（7.1G 可用）、/vol1 87%（23G）。
投影落 hub 本地 db —— 是**投影不是第二权威副本**（各源仍各自权威），可 `rm` 重建。

> ⚠️ 待查异常：`~/.codex/sessions` `du` 报 **0 字节**但 probe 计数 273 个文件；
> 且它在 rg 满负载时要跑 2s。A4 设计前必须先解释这个矛盾，否则投影会建在一份空数据上。

## 七、YAGNI 砍掉
不做写回（C 阶段）、不做投影（A4）、不动 `.env`、不碰其他共享配置、
不启用 `opencode_sessions`（09-26 已判不合格，禁顺手启用）。

> 原稿此处写着「不改 MCP 工具面」，2026-10-02 被**实测推翻**并已实现，见第八节。
> 教训：当时为了「diff 小」而 YAGNI 掉的那一层，恰好是唯一能让 claude/codex
> 拿到联邦记忆的层 —— 它不影响 D1 自身的测试结果，只影响 D1 对真实接入方的收益。

## 八、2026-10-02 追加：工具面开口子 + 主力仓归属

### 8.1 MCP 工具面（推翻原 YAGNI 第 3 条）
实测（本树 `tests/probe_memfeed_inject.py` 真 TDAI + 真联邦目录）：

| 路径 | 改前 | 改后 |
|---|---|---|
| `hub_memory_context()` | 17ms / 1585 字符 / `fed.sources=0` / **无联邦段** | 230ms / 快路 5 源全 ok / 含联邦段 |
| `hub_memory_search()` | 10 路全开（`sources` 默认 `local,tdai` + `memfed.enabled_ids()`） | **不改**（显式召回工具，广度优先） |

- `sources: str = ""` → 空串取 `memory.FED_FAST_SOURCES`（唯一真源），显式传则逐字转发；
- 慢三路（pi/codex/archived，实测 1.9~2.5s）**不在**默认里，等 A4；
- 端点默认仍为 `local,tdai`（G4 不变）—— 口径只在工具层开口，直调 HTTP 的旧行为零变更。

### 8.2 双树：`/home/gztxt/agent-hub` 不是生产树（版本 0.13.61 vs 0.13.64）
生产进程 `run_dualstack.py`（pid 3015）执行的是 **`/fs/1000/ftp/技术文档/agent-hub`**
（git `a640c0b`，VERSION 0.13.64）；`/home/gztxt/agent-hub`（git `96f73a6`，VERSION
0.13.61）是旧副本。两者**独立 inode，非软链**，且已确认存在真实差异：

- 主力仓 `src/memory.py`、`src/memfed.py`、`src/hubmcp.py` 与旧树 0.13.61 基线**逐字节相同**
  ⇒ 0.13.62~0.13.64 只碰了终端移动端/测试口径，记忆层同步**无冲突**；
- 但 `static/hub/04-terminal-ws.js::DEFAULT_CWDS` 两树**指向各自目录**，且 `static/hub.js`
  差 303 行 ⇒ **产物与分片源不得跨树覆盖，只能定点改 + 各自跑 `scripts/build_hubjs.sh`**。

旧树处置：只作为回滚备份保留，**不再施工**；后续改动一律在主力仓做。
（禁 git 写，故主力仓改动以 `cp` 时间戳备份 + `git diff` 复核作为双凭证。）