# A4 索引投影层设计（memindex，2026-10-02）

> 承接 `2026-10-02-unified-memory-injection-design.md` 第六节。该节原文写「本轮**不出**设计，
> D1 完成后另立」——**本文件即那份另立的设计**，第六节的待查异常已全部查清（见附录 A）。

## 〇、病根（实测，非推断）

联邦 10 路源里**只有 `claude_mem` 有真索引（FTS5）**，其余走 `rg` 逐文件扫描 ⇒ O(文件数) 无索引。
2026-10-02 实测单轮 `/api/memory/search`：`pi_sessions` 1894ms、`codex_sessions` 2028ms、
`archived_sessions` 2506ms——三路**必然超时**（源自身 `timeout_s=1.8`），所以 D1.5 把它们踢出默认行径。
这不是「慢」，是**默认行径上三路记忆恒等于不存在**。

投影层要做的事只有一件：让这批慢源从「必然超时」变成毫秒级，从而**有资格进默认行径**。

---

## 一、决策台账（六问已全部裁定，含实测依据）

| # | 议题 | 裁定 | 依据 |
|---|---|---|---|
| D1 | 范围 | **A：只做慢源加速** | `claude_mem` 52~148ms 本来就不慢，为它重写在役 FTS5 通路＝为一个不慢的源冒回归风险 |
| D2 | <3 字查询 | **A：显式回退 `rg`** + 响应标 `short_query_fallback` | trigram 结构性短板；实测普通表 LIKE 全表扫 1GB≈1.0s，等于把 D1 立的 1.2s 预算自己吃掉 |
| D3 | 记录粒度 | **A：格式无关分块**（短行累积 ~1000 字符成一条，超长行整行不切） | 四种会话格式一视同仁，不引入四套解析器的长期维护债；内容零丢弃 ⇒ 召回严格等于今天的 `rg` |
| D4 | 落位 + 闸门 | **A+A**：`agent-hub/data/memindex.db`；低水位 **fail-closed 回退 `rg`，绝不自动删索引** | 索引是我生成的派生数据，但我没资格替用户销毁已建好的东西 |
| D5 | 投影语料 | ~~A：全部文本 1040MB~~ **原前提作废** → **A2（重裁定）：rg 口径 258MB + grok/hermes 的 skills/docs，排 logs/bin/marketplace-cache/agent 本体**，13563 文件 / 501.2 MB | **2026-10-02 实测证伪原数字**：1040MB 与 rg 口径 258MB、整 home 1829MB **均不相符**，来源不可追溯。详见 §1.1 |
### 1.1 D5 前提已作废：1040MB 不可追溯（2026-10-02 实测）

| 口径 | 文件数 | 文本量 |
|---|---|---|
| **rg 口径**（今天生产实际扫的） | 6086 | **258.3 MB** |
| **skills/docs 定向 + rg**（D5 本轮重裁定 = A2） | 13563 | **501.2 MB** |
| 整家 agent home | 61227 | 1829.0 MB |
| D5 原记 | — | 1040 MB（**与上三者均不相符**） |

差在家目录里的**非记忆大件**：`~/.hermes/hermes-agent` 1.4G、`~/.grok/marketplace-cache` 620M、
`~/.grok/downloads` 522M、`~/.grok/bin` 313M、`~/.workbuddy/logs`+`traces` 451M。

D5 原句「含 grok/hermes 的 skills/docs/插件」按字面执行，**今天会把 Grok 下载的 marketplace 包与
Hermes 程序本体当成记忆索引**。A2 是 D5 原意的忠实兑现：**收 skills/docs/插件，排 logs/bin/
marketplace-cache/downloads/agent 本体**。

**连带发现（独立于本决策，另案修）**：`workbuddy_memory` 按现行 glob `{*.md,*.json}` 只命中
**11 个文件 / 76 KB**，却因「实测 47ms 快源」而在 `FED_FAST_SOURCES`——它快是因为几乎啥也没匹配。
全指标绿而功能层空，本仓反复警告的形态。**A2 不动它的口径**（那 732MB 里的 logs/traces/security
本就不该进记忆源），修 glob 另案提请。

### 1.2 数字改判（D5 前提作废后，全部估算重算）

| | D6 初版估（基于 1040MB） | **实测后估（基于 501.2MB）** |
|---|---|---|
| 语料 | 1040 MB | **501.2 MB / 13563 文件** |
| 基础索引 | 4.5–5.7 GB | **~2.0 GB** |
| 建库耗时 | 12.2 min | **~6 min** |

**这些仍是估算而非测量值** ⇒ §5.1 的 dry-run 容量闸与本表同为**待校准项**，
以 `--scope dry_run` 的真数为准。

被否掉的选项留档（防后人重提）：
- `detail=none` → **否决**，实测 phrase 查询直接报 `fts5: phrase queries are not supported`。
- D2-B（短查询走普通表 LIKE）→ 否决，1.0s 吃掉 1.2s 预算。
- D2-C（bloom 预筛 ~150MB）→ 否决，多一套匹配语义与 trigram 结果不一致＝调试噩梦，且 A4 体量 +40%。
- D3-B（每行一条，1400 万行）→ 否决，索引 2 倍、建库慢 10 倍；hermes 那 876 万行平均 42 字符（JSON 碎片）无检索价值。
- D3-C（抽 role/content）→ 否决，四套格式解析器＝长期维护债，某格式改版就静默漏索引。

---

## 二、数据模型与构建（已定）

落位 `data/memindex.db`（SQLite + WAL + `synchronous=NORMAL`，与仓内既有惯例一致）。

```sql
CREATE TABLE fed_proj(                      -- 分块内容表（D6：全文入库）
  source TEXT, path TEXT, realpath TEXT,    -- realpath 唯一键（codex/archived 273/273 重叠靠它去重）
  l1 INT, l2 INT,                           -- 首尾行号 → 可回溯到 path:行号区间
  mtime INT, size INT,                      -- 增量判定与陈旧标注
  chunk_no INT, text TEXT);
CREATE UNIQUE INDEX ux_proj ON fed_proj(realpath, l1, chunk_no);
CREATE VIRTUAL TABLE fed_proj_fts USING fts5(
  text, content='fed_proj', content_rowid='rowid', tokenize='trigram');
-- +3 触发器（AFTER INSERT/UPDATE/DELETE 维护 FTS）
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);  -- schema_version/index_age/last_build/est_final
```

**构建流程**（实测 1499 行/s、110 万 chunk ≈ 12.2 分钟、分批提交 WAL 峰值 172MB、临时溢出 0）：

1. **枚举**：按源根逐个 `os.scandir` 递归，**根符号链接必须 `realpath` 后跟随**
   （实测 `~/.codex/sessions` 是根软链，GNU `find`/`du` 在此**静默漏遍历**：`ls`/`os.walk`/`rg` 都看到 273，
   `find` 只看到 1、`du` 报 0。**实现绝不能用 `du`/`find` 估容量**）。
2. **去重**：`realpath` 全局集合，同一文件只投影一次；归属按源优先级
   （`codex_sessions` 先于 `archived_sessions`，`claude_projects` 先于 `workspace_files`）。
3. **容错**：`stat`/打开失败**跳过并计入 `skipped_race`，绝不中断**
   （实测 `~/.hermes/skills-hot/internet-search` 枚举后立刻消失，单次盘点 17 例）。
4. **分块**：格式无关；短行累积到 ~1000 字符成一条；**超长行整行不切**
   （pi 平均 1707 字符/行、76% 的行 >200 字符，切了破坏语义）。
5. **排除**：二进制（`\x00` 探测）与 >20MB 文件（与 `RG_MAX_FILESIZE=20M` 对齐，保证召回等价）。
6. **落盘顺序**：`fed_proj` 与触发器同批建 → 批量 INSERT（每 N 条 commit 一次，压 WAL 峰值）
   → **最后** `INSERT INTO fed_proj_fts(fed_proj_fts) VALUES('optimize')`。**触发器必须在首批 INSERT 之前建好**
   （上一轮探针就是踩了「触发器建在 insert 之后」⇒ 0 命中，方向错在时序）。

---

## 三、查询路径（新增 `kind="fts_proj"`）

`memfed.FedSource.kind` 现有 `'sqlite_fts' | 'rg_text' | 'disabled'` 三值 ⇒ 新增第四值 `'fts_proj'`。

```
q 的字符数 < 3  ──► 走既有 _search_rg_text（同一批源根），响应加 short_query_fallback=true
q 的字符数 ≥ 3  ──► fed_proj_fts MATCH ?
                    MATCH 项复用 memfed._fts_match_q（整包引号短语、内部引号剔除）
                    ORDER BY rank LIMIT n
                    JOIN fed_proj 取 text → 自算命中窗口（不用 snippet()）
```

**为什么自算命中窗口而不吃 `snippet()`**：出站契约要的是 `CONTENT_SNIPPET=300` 的单行片段
（与 `rg -m1` 同形），而 D6 全文入库后命中窗口的上下文本来就在库里，自算才能给出
`path:l1-l2` 的行号区间（`snippet()` 给不出可回溯区间）。

**出站仍走 `_sanitize_content` 双道脱敏**（scrub 凭据 → mask_title token=KV → 截断）。
投影内容是**原文**，脱敏只在出站做——D6 全文入库的价值就在于库里是干净原文，
分析类能力（摘要/聚类）可以在库内直接做，不必回源 rg。

**降级链**（任一环失败都诚实降级，不假装）：
`索引缺失/损坏 → rg` ｜ `水位不足 → rg + proj_disabled:low_disk` ｜ `<3 字 → rg + short_query_fallback` ｜
`命中 0 条且 index 陈旧 → 照常返 0，但带 stale:true`。

---

## 四、增量刷新与触发

**增量**：`os.scandir` 走一遍全部源根取 `mtime/size`（约 4.8 万文件），与 `fed_proj` 里已有的
`(realpath, mtime, size)` 比对 → 只重投影变动文件、删除已消失文件的行。**内容零丢失是硬要求**：
删行的条件只能是 realpath 消失或 schema 变更，不能是「看起来旧」。

**触发**（受两条既有红线约束）：
- **禁写 crontab**（2026-09-19 在册）⇒ **不做定时任务**。
- 查询路径上做**惰性陈旧检查**：`index_age` 超阈值时**照常服务旧索引**并加 `stale:true`，
  绝不因陈旧而阻塞或返回空。
- 显式刷新走 MCP 工具面 `hub_memindex_rebuild(scope)`（scope: `full` / `incremental` / `dry_run`），
  与 D1.5 给 `hub_memory_context` 开的 `sources` 口子同款做法。

**探针**：`_probe_one` 对 `fts_proj` 源**不跑 `rg --files`**（那是 O(文件数) 的老毛病），
改读 `meta`（`index_age` / chunk 数 / 陈旧文件数），并在 `note` 里写清「索引覆盖 N 文件 / M chunk，
最后构建于 T」。**纳管 ≠ 可用**的老坑（2026-09-23 opencode 假阴性）在此继续适用：探针报绿
只证明索引可读，不证明召回正确 ⇒ 必须另配一条内容级闸门（见 G13）。

---

## 五、磁盘闸门（D6 全文入库后的**数字改判**）

D4 的阈值（<8G / <3G）是**按 1.8GB 索引**设的；D6 把索引推到 4.5–5.7GB，余量账必须重算：

| | D4 时（1.8GB） | **D6 后（4.5–5.7GB）** |
|---|---|---|
| **技术文档配额**可用 | 23G | **30G**（清理 8.62GB 后，83%） |
| 索引占余量 | 8% | **15–19%** |
| 阈值语义 | 可用 <8G 拒建/拒增量 | **不变，但必须先过 dry-run 容量闸** |

### 🚨 5.1 同一个 `/fs` 有**两个容量**，闸门必须量对路径（2026-10-02 实测）

```
df -h /fs/1000/ftp/技术文档  →  169G  138G   30G  83%   ← 配额视图（索引真正受限的那个）
df -h /fs                   →  2.9T  2.8T  186G  94%   ← 设备视图
```

`findmnt` 显示**两者是同一个挂载**（`trimafs` on `/fs`，同一组 OPTIONS，无独立挂载点）
——即 trimafs **按目录树做配额**，`df` 的容量取决于你**用哪条路径问**。

**后果**：闸门若写成 `shutil.disk_usage("/fs")` 或 `df /fs`，会读到 **186G**，
于是「可用 > 8G」恒真、闸门形同虚设，5.7GB 建库跑到一半撞 30G 配额上限才失败——
**而失败点在写入中途，产物是半截的库**。

**钉死**：水位一律取 `os.statvfs(<memindex.db 所在目录>)`，
且**目录必须取索引文件自己的父目录**（`agent-hub/data`），不得用 `/fs`、`/` 或任何上层路径代问。
闸门 G17 断言此点（夹具：同一 fs 下两条路径容量不同，断言实现取的是父目录那条）。

**新增 dry-run 容量闸（必需，因构建峰值未实测）**：全量建库前先按 D3 规则采样 N=2000 个文件，
外推出 `est_final_bytes`；**要求 `可用 ≥ est_final × 1.5 + 2GB`** 才允许 `--scope full`。
理由：上一轮只实测到 WAL 峰值 172MB（那是分批提交下的探针规模），
**全文入库 4.5–5.7GB 的真实峰值未实测**，属已知未知——宁可多问一句，不可把 30G 赌进去。

**水位线行为（D4 沿用，一字不改）**：

| 可用 | 行为 |
|---|---|
| `< 8G` | 拒绝建库/增量 → **fail-closed 回退 rg**，响应标 `proj_disabled: low_disk` |
| `< 3G` | 同上 + **拒绝一切增量**（连小步刷新也不做） |
| 任何水位 | **绝不自动删除索引**（D4 的核心理由：索引是我生成的，但我没资格替你销毁） |

---

## 六、闸门（G9~G16，L0 hermetic：不起服务、不打网络、不 import `src.main`）

| 闸门 | 断言 | 红向 |
|---|---|---|
| G9 | 建库后 `SELECT COUNT(*) FROM fed_proj_fts` **> 0** | 触发器时序错（上一轮探针正是栽在这） |
| G10 | 根符号链接下的文件被枚举到（构造根软链夹具，断言计数 > 0） | 退回 `find`/`du` 语义 ⇒ codex 273 变 0 |
| G11 | 同一 realpath 跨源只投影一次（codex∩archived 夹具） | 去重失效 ⇒ 索引虚胖 |
| G12 | `stat` 失败文件被跳过且 `skipped_race` 计数 > 0，**构建不中断** | 容错缺失 ⇒ 一个竞态毁掉整次 12 分钟建库 |
| G13 | **内容等价**：同一夹具上 `fts_proj` 命中集 **⊇** `rg` 命中集 | 「索引可读」冒充「召回正确」（纳管≠可用） |
| G14 | 2 字中文查询：走 rg 且响应带 `short_query_fallback=true` | 短查询静默返 0（trigram 表上 `LIKE '<3字'` 实测**撒谎返 0**） |
| G15 | 出站 item 逐条过 `_sanitize_content`（凭据串不出现） | 库里是原文 ⇒ 脱敏漏一处就泄漏 |
| G16 | 索引缺失 / 损坏 / 低水位 ⇒ 返 rg 结果且带降级标注，**不抛错不返空** | 静默不可用（本仓反复警告的形态） |
| **G17** | **水位取 `statvfs(索引父目录)`**，不是 `/fs` 或 `/` | 同一 fs 两容量（30G vs 186G）⇒ 量错路径＝闸门虚设 |

**禁把 SKIP 当 PASS**：G13 必须真跑 `rg` 与真 FTS 对拍，不得把 fake 命中集塞回去自证；
G10/G11/G12 用真 `os.symlink` / 真竞态夹具。

---

## 七、改动面与回滚

| 文件 | 改动 | 备份后缀 |
|---|---|---|
| `src/memindex.py` | **新建**：枚举/去重/分块/建库/查询/增量/闸门 | —（新建无需备份） |
| `src/memfed.py` | `kind='fts_proj'` 分派 + `_PROJ_TARGETS` + `_probe_one` 分支 | `.bak-<ts>-A4-memindex` |
| `src/hubmcp.py` | 新增 `hub_memindex_rebuild` 工具（scope 三值） | 同上 |
| `.gitignore` | 排除 `data/memindex.db*`（派生数据，**不入 git**） | 同上 |
| `tests/test_memindex_proj.py` | 新增 G9~G16（新建） | — |
| `tests/README.md` | 更新实测计数（仓内惯例：数字须实测，禁止 SKIP 记 PASS） | 同上 |

**不改**：`claude_mem` 的 FTS5 通路（已在役、被 G1~G8 钉住）、`FED_FAST_SOURCES`
（**放量是 A4 验证通过后的独立一步**，不在本轮）、`.env`、任何仓外共享配置。

回滚 = `rm data/memindex.db*` + `git checkout` 上述文件。投影是**派生数据不是第二权威副本**，
各源仍各自权威，删除即可重建。

---

## 八、三项待裁定 → **已裁定**（2026-10-02 17:28，用户「执行123」）

1. **放量时机 → 裁定「本轮不放量」。** 三路（`pi/codex/archived`）加回 `FED_FAST_SOURCES`
   **不在 A4 本批**，等索引建好 + 探针实测三路都落到百毫秒级后**单独取证一次**。
   理由：默认行径变更是一次独立的可观测面（它影响每次开局），与「把慢源变快」分开才能二分定位。
   - 本批交付形态：显式 `sources=` 传三路时走 FTS，默认行径**一字不变**。
   - 解除条件（写进台账）：三路 `fts_proj` 探针各 ≤200ms 且 G13 内容等价闸门全绿 ⇒ 提请放量。
2. **容量闸倍率 → 裁定「先跑 `dry_run` 取真数再定倍率」。**
   上一轮只实测到 WAL 峰值 172MB（分批提交下的探针规模），全文入库 4.5–5.7GB 的
   **全量构建峰值属已知未知**。先跑一次 `--scope dry_run`（采样 N=2000 文件外推），
   以实测 `est_final_bytes` 与「构建期实测峰值」定倍率，**写死 1.5 属于拍脑袋，本批不接受**。
3. **备份轮换 → 裁定「排除 + 写 README 说明」。** `memindex.db`（4.5–5.7GB）不进
   `data/backups` 轮换。理由：它是**可完全重建的派生数据**，各源仍各自权威；
   备份它 = 备份一份能重算的东西，同时把备份链体积抬高两个数量级。
   代价（必须写进 README）：**丢了就得重建，重建要 12.2 分钟**；重建期间检索降级回 `rg`（变慢，不返错）。

### 7.1 与 1/2/3 无关但同批记录的施工约束

- **禁写 crontab** ⇒ 索引刷新不做定时任务（见第四节）。
- **禁重启生产** ⇒ A4 代码入库后**同样不自动生效**，切换需单独点头，与 v0.13.65 同形态。

---

## 附录 A：附录——上一轮遗留的「待查异常」已结清

| 异常 | 结清结论 |
|---|---|
| `~/.codex/sessions` `du`=0 / `find`=1 但 rg 读到 273 | **符号链接语义**，非 FUSE 故障。`~/.codex/sessions → /fs/…/会话备份/codex/sessions`，GNU `find`/`du` 默认 `-P` 不跟随**根**软链（`find -H` 才是 273）。`os.scandir`/`os.walk`/`rg` 正常 ⇒ 枚举必须走 scandir |
| codex 273 与 archived 重叠 | 实测 **273/273 全为子集**，交集 273 ⇒ realpath 去重即可；该次查询没实测到重复命中，故**不宣称现存缺陷**，只当设计约束 |
| `~/.grok` / `~/.hermes` 是不是会话目录 | **不是**，是 agent 的整个 home。**2026-10-02 复查修正**：初版记「真文本只占 1008.8MB / 4.09GB」，该比例结论仍成立（非记忆大件被 `rg` 的二进制/大小过滤排除），但**绝对量已被后续实测取代**——当前 rg 口径 258.3MB、整 home 1829.0MB，见 §1.1。体积大头：`hermes-agent` 1.4G、`marketplace-cache` 620M、`downloads` 522M |
| 行粒度实测 | 1400 万行 / 1040MB，平均 74 字符；hermes 876 万行平均 **42** 字符；pi 10.8 万行平均 **1707** 字符、76% 超 200 字符 ⇒ D3 的分块规则正由这三组数字推出 |
