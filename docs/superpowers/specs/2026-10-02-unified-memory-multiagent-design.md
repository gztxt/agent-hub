# 统一记忆 + 知识库全 agent 接入设计（批次①生产生效 / 批次②技能分发，2026-10-02）

> 承接两份已落盘设计，不重开新题：
> - `2026-10-02-unified-memory-injection-design.md`（D1 + D1.5，v0.13.65 已实现）
> - `2026-10-02-memindex-a4-design.md`（A4 索引投影层，已合并 `7675193` / `bd2ba48`）
>
> 本设计只回答**两件事**：①已写好的东西怎么在生产生效并被证明；②怎么让 9 家 agent
> 真的能调到统一记忆与知识库。台账 `PT-20261002-11`。

## 零、授权与边界（用户 2026-10-02 23:2x 逐点拍板）

| 项 | 裁定 | 落成 |
|---|---|---|
| 范围 | **C：分两批串行** | ①生产生效+复验 → ②agent 侧接入 |
| 生产重启 | **解禁**（09-23「禁重启生产」就本批解除） | 唯一入口 `systemctl --user restart agent-hub.service` |
| pi 重启 | **一起重启** | 重启 pi = 终止本会话 ⇒ 走 §五交接，**自证由新会话产出** |
| 批次②形态 | **只装技能**（option a） | **不碰任何 agent 配置文件**（`CLAUDE.md` / `AGENTS.md` / 各 config 一律不动） |
| 台账 | **由 AI 追加** | `PENDING-TASKS.md`（工作区规则「改表无需授权」），已取备份 `PENDING-TASKS.md.bak-20261002_233038-登记PT11` |
| git 写 | 沿用 10-02「1.git落库」解禁 | 只 `git add` 本设计书 + 台账两个路径，**不扫那 28 个他会话未提交文件** |
| push | 仍禁 | 只落库不推送 |

**单写者协议处置**：`orchestration-check.sh` = FAIL=1 / WARN=1，今天被 ≥2 会话写过的 6 个文件是
`src/memindex.py` / `src/main.py` / `CHANGELOG.md` / `scripts/run_tier.py` /
`tests/probe_memfeed_inject.py` / `tests/test_term_scroll_sensitivity.py`。
**本设计两批都不需要改这 6 个文件中的任何一个**（①只重启+复验，②只加技能），故冲突不构成阻塞；
若执行中被迫要改，立即停手报请。C8 WARN（今晚 3 会话有施工无沉淀）由本条台账 + 一篇
`agent-knowledge`偿还。

## 一、现状取证（生产当前态，2026-10-02 22:5x 实测）

| 判据 | 现值 | 取证命令 |
|---|---|---|
| 生产单元 | user unit `~/.config/systemd/user/agent-hub.service`（Sep30），`run_dualstack.py` pid 3015，端口 3102/3103 | `systemctl --user show agent-hub.service -p FragmentPath` |
| 版本落后 | `version=0.13.64`、`git_sha_boot=a640c0b7` ≠ `git_sha_now=c71ec3b5`、`needs_restart=true`、`code_stale=true` | `curl -s 127.0.0.1:3102/health` |
| 联邦源 | 10 路注册，9 路 `ok`（53~121ms / count 304~5565），`opencode_sessions` `disabled` | `GET /api/memory/fedsources` |
| 知识库 | `kb.py:60` `ROUTES=(local,tdai,turbovec,workspace,archived)`，后两路转调 memfed | `grep ROUTES src/kb.py` |
| **agent 侧同步** | `verify-skill-distribution.sh` 9 家 skills 目录全 PASS——但铺的是 **agent-dispatch 一个技能**，不是记忆/知识库 | 该脚本实测 exit=0 |
| **claude 的记忆接入** | `~/.claude/skills/system-memory/SKILL.md` = **839 字节 / 2026-08-03 20:45** 快照，而 `MEMORY.md` 已 **35152 字节 / 09-24 00:21** ⇒ **内容漂移 30 天** | `stat -c '%y %s'` |
| **pi 自己的接入** | `~/.pi/agent/extensions/hub-facade.ts`（10066B，`rev=20261002b-D1.5`）在扩展目录里，**但本会话工具清单无任何 `hub_*` 工具** ⇒ pi 侧自动注入当前是断的 | 本轮工具清单对照 + `grep rev= ` |

**结论**：agent 侧同步的病根不是「目录没建」（09-30 已建成 9/9），而是
**①分发的是快照内容（必然漂移）②没有「真能调到」的判据（只查目录存在）③连 pi 自己都没接上**。

## 二、批次①：v0.13.65 + A4 生产生效并复验

### 步骤（顺序不可换：先记账 → 后动生产）

1. **补治理欠账**（已完成一半）：本设计书 + `PENDING-TASKS.md` `PT-20261002-11` + 一篇
   `agent-knowledge`（含 v0.13.65/A4/G15 的结论与回滚命令）。
2. **重启前闸门**（全绿才允许重启）：
   - `scripts/run_tests.sh all` ⇒ L0 + L1 + 收集器对账（`unittest` == `pytest`）
   - `pytest tests/test_memfeed_inject.py` ⇒ G1~G8 **16 例**
   - `static/hub.js` 与 `static/hub/*.js` 拼接逐字节一致；`templates/index.html` 的 `?v=`
     == `md5(static/hub.js)[:8]`
   - 任一红 ⇒ **不重启**，如实报 FAIL
3. **重启**：`systemctl --user restart agent-hub.service`（唯一入口，**禁** `pkill`/`killall`）。
   随后读启动横幅 + `/health`。

### 复验判据（逐项 PASS/FAIL，**禁把 SKIP 当 PASS**）

| # | 判据 | 期望 |
|---|---|---|
| P1 | `version` | **`0.13.65`**（已核 `src/main.py:80 VERSION = "0.13.65"`，无歧义） |
| P2 | `git_sha_boot` == `git_sha_now` == `HEAD` | 三者同 SHA |
| P3 | `code_stale=false`、`needs_restart=false`、`code_matches_head=true` | 全 false/true |
| P4 | `code_fp_boot` == `code_fp_worktree` == `code_fp_head` | 三方一致 |
| P5 | `GET /api/memory/fedsources` | 10 路；9 路 `ok`；A4 后慢源 `count` 变化可解释；`opencode_sessions` 仍 `disabled`（**不得顺手 enable**，属范围外） |
| P6 | **真请求** `GET /api/memory/search`（不传 sources） | 联邦段落真出结果；并联墙钟 ≈300ms 量级（D1.5 的收益判据，不看代码看数字） |
| P7 | **真请求** `GET /api/memory/context?sources=<FED_FAST_SOURCES>` | `fed.sources>0` 且注入包含第 4 段「其他 Agent 记忆」 |
| P8 | MCP 工具面 | `hub_memory_context` 的 `sources` 口子可用（空串 ⇒ 取 `FED_FAST_SOURCES`） |
| P9 | A4 生效 | `data/memindex.db` 存在（实测 **1720246272 B = 1.7 GB**，mtime 10-02 18:07）；3 路慢源（基线 1894 / 2028 / 2506 ms）降到毫秒级 |
| P10 | `journalctl --user -u agent-hub` 本次启动后 | 0 error、0 traceback |
| P11 | 静态面 | `GET /` 200；线上 `hub.js` 与仓内一致；无 `.bak` 泄漏到响应 |

### 回滚

```bash
cd /fs/1000/ftp/技术文档/agent-hub
git checkout a640c0b7 -- src/hubmcp.py src/memfed.py src/memory.py src/main.py \
                        static/hub.js static/hub/04-terminal-ws.js templates/index.html
systemctl --user restart agent-hub.service
curl -s 127.0.0.1:3102/health   # 期望 version=0.13.64 sha=a640c0b7
```
（**禁**对 `.git` 做 `reset --hard` 或任何恢复/替换；只准 `git checkout <sha> -- <path>`。）

## 三、批次②：9 家 agent 接统一记忆 + 知识库

### 核心设计决策：零内容快照，只指向实时源

依据：`~/.claude/skills/system-memory` 是 08-03 的 839B 快照，`MEMORY.md` 35KB/09-24。
**任何把记忆正文复制进 agent 目录的做法都会重演这个漂移**。所以
`unified-memory` 技能正文**只写「怎么调、什么时候该调、调不到怎么办」，不写记忆内容**。

### 三层形态

| 层 | 内容 | 落位 |
|---|---|---|
| **L-传输** | `unified-memory/SKILL.md`：① 统一记忆入口（`/api/memory/search`、`/api/memory/context`、MCP `hub_memory_*`）② 知识库入口（`kb.py` 5 路由）③ 联邦 10 路清单 ④ 判据：先检索后动手、端侧确认才结项、探活 ≤2 次、禁把 SKIP 当 PASS | 权威副本 `/fs/1000/ftp/技术文档/skills/unified-memory/`（工作区内，非受保护面） |
| **L-分发** | 9 个符号链接，照 `agent-dispatch` 已实证的形态 | 见 §四 路径声明 |
| **L-校验** | 扩 `scripts/verify-skill-distribution.sh` 为三态：链接 / frontmatter / **该 CLI 是否真读这个目录**；加「记忆接入」专项——查**真能取到联邦段落**，不只查目录存在 | 只读体检脚本（铁律：只读） |

### 跨工具链改动声明（军规要求，先说清再写）

- **谁改**：pi 会话（本轮），单一执行者。
- **改哪些路径**：**新增 10 个**——1 个新建目录（工作区内）+ 9 个新建符号链接。
- **原值**：9 个目标目录下**均无** `unified-memory` 条目（各自已有 `agent-dispatch`
  符号链接指向 `/fs/1000/ftp/技术文档/skills/agent-dispatch`，形态已实证 9/9）。
- **新值**：`unified-memory` → `/fs/1000/ftp/技术文档/skills/unified-memory`。
- **影响面**：9 家 CLI 各自多出一个技能条目；**不改任何配置文件、不改既有技能、不改模型、
  不碰 CCR**。
- **回滚**：`for d in <9 目录>; do rm "$d/unified-memory"; done`（只删本轮新建的符号链接，
  不删权威副本）；`rm -rf /fs/1000/ftp/技术文档/skills/unified-memory`。
- **受保护面声明**：9 个目标中 `/home/gztxt/.pi/agent/skills` 落在军规受保护面
  `~/.pi/agent/**` 内。本轮**只新建一个符号链接、不改 `settings.json` / `auth.json` /
  `models.json` 任何既有键**。

### 本批第一个交付物是只读取证，不是写入

9 家支持机制矩阵（每家 1 次探针，≤2 次预算），逐家记：`能否读该 skills 目录` /
`记忆入口形态` / `实测输出`。**首条红旗先查**：pi 自己的 `hub-facade.ts` 为何没加载
（本会话无 `hub_*` 工具）——它是「所有 agent 自动调用」的最小反例，机制没搞清就铺 9 家
等于铺 9 个未验证插头。

## 四、批次②写入路径（9 家，形态逐字照 agent-dispatch）

```
/home/gztxt/.pi/agent/skills/unified-memory      -> /fs/1000/ftp/技术文档/skills/unified-memory
/home/gztxt/.claude/skills/unified-memory        -> 同上
/home/gztxt/.codex/skills/unified-memory         -> 同上
/home/gztxt/.grok/skills/unified-memory          -> 同上
/home/gztxt/.hermes/skills/unified-memory        -> 同上
/home/gztxt/.workbuddy/skills/unified-memory     -> 同上
/home/gztxt/.jcode/skills/unified-memory         -> 同上
/home/gztxt/.config/opencode/skill/unified-memory-> 同上
/home/gztxt/.qwenpaw/skills/unified-memory       -> 同上
```

## 五、pi 重启与交接（重启即终止本会话）

`hub-facade` 的 `rev=20261002b-D1.5` 需重启 pi 进程才生效（D1.5 设计书 §2.2：extensionCache
不看 mtime），而**重启 pi-web（pid 1440，:30141）= 杀掉本会话**。因此：

- 本会话**只做**：批次①全部 + 批次②写入 + 写交接件（`agent-knowledge` 一篇含 §二 判据表
  与 §五 交接清单 + `logs/pi-memory-handoff.md`）。
- **新会话自证项**（由用户在重启后开新 pi 会话触发，或授权我以「最后一个动作」自重启）：
  1. 工具清单里出现 `hub_memory_context` / `hub_memory_search`；
  2. 启动日志出现 `[hub-facade] ✅ hub 门面已加载 … rev=20261002b-D1.5`；
  3. 调 `hub_memory_context()`（**不传 sources**）⇒ 返回含联邦段、耗时 <600ms；
  4. 三项任一不成立 ⇒ 记 FAIL 并按 §二 回滚，**不得**报「已完成」。

## 六、测试与验证口径（两批共用）

| 层 | 手段 | 通过线 |
|---|---|---|
| L0 | `scripts/run_tests.sh hermetic` | 0 失败 / 0 错误 / **0 跳过** |
| L1 | `scripts/run_tests.sh host` | 同上 |
| 收集器对账 | `run_tests.sh` 自动 `unittest` vs `pytest` | 例数相等，否则红 + 退出码 2 |
| L2 | 仓内真机闸门 | 13/13（不因本批改动回退） |
| 生产 | §二 P1~P11 | 逐项 PASS，**禁把 SKIP 当 PASS** |
| agent 侧 | 每家 1 条**真调用**取证 | 逐家 PASS/FAIL/**未实证**（三态，不许二值化） |

## 七、明确不做（YAGNI + 边界）

- 不把 `MEMORY.md` / `agent-knowledge` 正文复制进任何 agent 目录（⇒ 第 4 份会漂移的副本）。
- 不改 9 家任何配置文件（用户已定「只装技能」）；**不**声称「开局自动带上记忆」——
  本轮交付口径是「**技能可被自动触发，agent 自行判断何时调**」。
- 不碰 CCR / FCC / `~/.pi/agent/settings.json` 既有键 / 不新增 `:free` 默认模型。
- 不 `enable` `opencode_sessions`（属范围外）。
- 不重跑 A4 全量建库（除 §二 P9 只做只读存在性与耗时取证）。
- 不推送 git。

## 七之二、执行前必须知道的两条物理约束（不构成阻塞，但要记账）

1. **A4 索引已占 1.7 GB**：`data/memindex.db` = 1720246272 B，建于 10-02 18:07。
   磁盘水位按 memindex 设计书的教训**必须按索引父目录量**：`df /fs` 读 169G/85%，
   `df /fs` 另一路读 2.9T/94%，`df /`（根分区）63G/89%。**三条数不一致，本轮不裁决
   哪条为准**，只如实记账：建库已完成，剩余容量未达危险线，但「重建一次 = 再 1.7 GB」
   属需要授权的动作，本轮不重建。
2. **`FED_FAST_SOURCES` 真值已核**：`src/memory.py:50`
   `FED_FAST_SOURCES = "local,tdai,claude_mem,workbuddy_memory,claude_projects"`
   （5 路，不含 3 路慢源）。P6/P7 的取证**必须用这个字符串**，不得另抄一份。

## 八、未闭合项（需端侧/用户裁定）

1. **手机端结项**：§二 P1~P11 全绿只是**本机中间态**；统一记忆注入在手机端的表现属本机
   不可判定，结项权在用户端（沿用 09-21 两层判据）。
2. **pi 侧自证**：见 §五，**本会话无法自证**，必须新会话产出。
3. **「自动调用」的真实强度**：本轮只到「技能可被触发」。若日后要「开局自动带上」，
   须另行点名授权动各家 `CLAUDE.md`/`AGENTS.md`/config（军规点名制白名单）。
4. **pi 侧 hub-facade 未加载的根因**未查（§三红旗），可能影响 §五第 1 项。
5. **技术文档仓 28 个未提交文件**属他会话，本轮不代为落库（单写者协议）。
