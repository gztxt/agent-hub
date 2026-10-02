# pi 重启交接件 · 统一记忆门面自证

> **给重启后的新会话**：本文件是 `PT-20261002-11` 批次① 的最后一棒。上一会话已完成生产生效与 P1~P11 复验
> （证据见 `agent-knowledge/77-unified-memory-生产生效v0.13.65与A4夹具双面孔.md`），
> **只剩下面这 3 项无法在重启前自证**——因为它们要求「pi 已经带着新代码起来」。

- **交接时刻**：2026-10-02 23:56 CST
- **生产基线**：agent-hub `v0.13.65` / `8ad1c8c1` / 代码指纹 `2c1da9551e7a`，`code_stale=False`
- **台账**：`PT-20261002-11`

---

## 一、你要自证什么（3 项，逐项给 PASS / FAIL / 未实证）

### ① pi 的工具清单里到底有没有 `hub_*`

- **期望**：出现 `hub_memory_context`、`hub_memory_search` 等工具。
- **现状（重启前实测）**：**一个都没有**。`~/.pi/agent/extensions/hub-facade.ts` 确实存在（约 10066 字节，
  内含 `rev=20261002b-D1.5`），但它是**普通文件、不是符号链接**，且 `~/.pi/agent/settings.json` 里
  只声明了 `skills: ["~/.claude/skills"]` 与 packages，**没有显式列出该扩展**。**根因未查明。**
- **怎么测**：直接看本会话工具清单。**不要**用「文件在」推断「工具在」——
  09-23 `vitals_loop` 函数头丢失、09-24 TDZ 都是同一个家族：**代码存在 ≠ 会被加载**。

### ② 启动日志里有没有门面加载行

```bash
# 找 hub-facade 的加载记录（上一会话在已查日志路径里没找到，路径可能不全，下面是兜底搜法）
grep -rIl "hub-facade" ~/.pi/ 2>/dev/null | head
journalctl --user --since "-10min" --no-pager | grep -iE 'hub-facade|hub_memory' | head
```

- **期望**：有一行明确的门面注册日志。
- **若仍无** ⇒ 与 ① 互为印证：**扩展压根没被加载**，此时不要再去查 token/URL，先查加载机制
  （`settings.json` 的 `extensions`/`packages` 字段、文件名约定 `extensions/*.ts` 是否被 pi 扫到）。

### ③ 不带 `sources` 调 `hub_memory_context`，应有联邦段 + 毫秒级耗时

- **调用**：不传 `sources`（唯一真源 `src/memory.py:50`
  `FED_FAST_SOURCES = "local,tdai,claude_mem,workbuddy_memory,claude_projects"`）。
- **期望**：返回的 `context` 里**带「其他 Agent 记忆（联邦）」段**，且**整次调用在百毫秒级**。
- **注意**：若走 MCP，`/hub-mcp/*` **必须带 `x-hub-token`**（`TERM_TOKEN`/`HUB_PASSCODE`），
  不带就是 401（详见知识库 77 第四节）。
- **若 ① 已经 FAIL**（工具压根不在），本项记 **未实证**，**不要**改用 HTTP 直连 agent-hub 冒充「pi 门面通过**
  ——那是两条不同的链路。

---

## 二、纪律（上一会话踩过的，别再踩）

1. **探活同一判据最多 2 次**；2 次无结论就以 FAIL / 未实证 上报停手，**禁第三次重试与轮询**。
2. **不把 SKIP 当 PASS**，不把「文件在 / 端点 200」当「门面通了」。
3. **端侧确认才算结项**：本机绿 = 中间态，手机/浏览器实际能用 = 结项。
4. 若结论要改判旧条目，**加横幅、不删原文**，并带版本号与取证日期。
5. **只读优先**：本轮未获授权改 `~/.pi/agent/**`。若确需改（如把 `hub-facade.ts` 改成符号链接或补
   `settings.json` 的 extensions 字段）⇒ **停手报请**，先说清「谁改 / 改哪个文件 / 原值 / 新值 /
   影响面 / 回滚命令」。

---

## 三、批次① 已闭合的部分（不用重做）

- ✅ 生产已重启生效：`v0.13.65` / `8ad1c8c1` / `code_stale=False` / 启动后 0 异常日志
- ✅ P1~P11 全绿（含 **P9：A4 投影把 3 路慢源从 1894/2028/2506ms 压到 318/160/42ms**）
- ✅ 拦下的那 4 条测试红已定位并修复（**夹具补双路径 + AST 守派发**，提交 `8ad1c8c1`），
  全套闸门现为 **L0 983 例 0 失败 0 跳过 / L1 51 例 / 对账 1034=1034**
- ✅ 沉淀：`agent-knowledge/77-unified-memory-生产生效v0.13.65与A4夹具双面孔.md`

**未开工的是批次②**（9 家 Agent 技能安装），等用户点名 `~/.pi/agent/skills/unified-memory` 这个
受保护路径后才能动。
