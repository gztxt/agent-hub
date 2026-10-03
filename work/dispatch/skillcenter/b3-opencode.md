# B3 · opencode 技能加载面侦察

- **执行者**：pi 子代理 `Explore`（`9479028f-099e-482`，Bun/pi-subagents 0.19.0），只读
- **状态**：子代理在 turn limit 处收尾，**自己没写产物文件**（纯只读模式无写工具）；本文件由**主会话代写**
- **原始证据**：`/tmp/pi-subagents-1000/.../tasks/9479028f-099e-482.output`（瞬时物，未入库）
- **复核**：主会话**独立重跑了全部关键取证**（见下「复核」节）。子代理的结论成立，但其中一处取证失误被当场纠正
- **费用**：234k token / 33 tool use / 55 分钟。**性价比很差**——它把主要预算花在 185MB 二进制的 `grep -o -P` 上，有一次 900s 超时

---

## 1. 结论一：`~/.config/opencode/skill` **不是** opencode 的技能发现点

子代理的证据是 Bun 单文件二进制里的字节偏移。这类证据最易取错（偏移漂移、多 chunk、字符串表），故**主会话独立复核**，结论一致。

**二进制里的真实字面量**（`/home/gztxt/.npm-global/lib/node_modules/opencode-ai/bin/opencode.exe`，185,632,896 B，偏移 99146575 附近 900 B 原文）：

```js
var bA=".claude", xA=".agents",
    GA="skills/**/SKILL.md", SA="{skill,skills}/**/SKILL.md", KA="**/SKILL.md", MA="customize-opencode"
```

⇒ opencode 1.18.34 的发现面是：`~/.claude/skills/**`、`~/.agents/skills/**`、项目内 findUp 同两项、项目目录下的 `{skill,skills}/**`，以及配置键 `skills.paths`（递归扫 `**/SKILL.md`，即上面的 `KA`）与 `skills.urls`。最后两条来自二进制里内嵌的技能编写文档（偏移 104303958）。

**决定性的最后一环**（子代理没做、主会话补的）：

| 检查 | 结果 |
|---|---|
| `grep -a -c -F 'Path.config,"skill'` | **0 命中** ⇒ 无代码路径扫 `~/.config/opencode/skill` |
| `~/.config/opencode/opencode.json` 里有 `skill` 键吗 | **没有**（文件 2482 B，无 `skills` 段）⇒ `skills.paths` 未配置 |

⇒ **D1 登记的第 20 路 `opencode`（`/home/gztxt/.config/opencode/skill`）是一条假发现点**：它报出的 2 条技能 opencode 从不读取。

那 3 个软链本身是好的（目标 `SKILL.md` 均存在）：

```
agent-dispatch  -> /fs/1000/ftp/技术文档/skills/agent-dispatch
crawl4ai        -> /fs/1000/ftp/技术文档/crawl4ai/skill/crawl4ai
unified-memory  -> /fs/1000/ftp/技术文档/skills/unified-memory
```

但同名副本已在 `~/.claude/skills/`（opencode 确实扫），**所以功能上没有损失**——损失的是 agent-hub 列表里那 2 条是虚的。

## 2. 结论二：opencode **跟随**符号链接

调用点传 `symlink:!0`（`grep -aob -F 'symlink:!0'` 共 6 处命中），底层 PathScurry 默认 `followSymlinks:!0`（偏移 29960889）。

## 3. 版本取证更正

子代理报的 1.18.34 **是对的**；在册的 **1.18.32 已过期**（09-23 装机后升过级），与「结论须绑定版本号」口径一致。

---

## 4. 主会话复核记录（含一处对子代理/对自己的纠错）

| 项 | 结果 |
|---|---|
| 独立复核结论一 | ✅ 成立（字节字面量 + `Path.config,"skill` 零命中 + 配置无 `skills` 键，三条独立） |
| 独立复核结论二 | ✅ 成立 |
| 版本号 | ✅ 1.18.34（在册值过期） |
| **我自己的一次测量错误** | 首次统计 `~/.agents/skills` 条数时，我写了 `for it in skill._scan_one(...)`——该函数返回 **dict**，迭代得到的是**键名**（`ok/items/ms/...`），于是打印出「6 条」。那是**键数不是技能数**。真实值 12 接受 + 28 拒读 = 40，与 D1 记录一致。**先量后断言，量错就当没量** |

## 5. 顺带带出的真缺口（子代理没看到，主会话比对白名单时发现）

D1 记录「白名单外拒读累计 62」，本次把落点分布打出来了，**62 条全部是 09-25 装的那批第三方技能**：

| 仓库（`技术文档/` 下） | 被拒条数 |
|---|---|
| `mattpocock-skills` | 50 |
| `crawl4ai` | 8 |
| `hallmark` | 2 |
| `Agent-Reach` | 2 |

它们的 `realpath` 落在 `/fs/1000/ftp/技术文档/<repo>/`，而 `_allowed_roots()` 只放行了 `/fs/1000/ftp/技术文档/skills`。后果链是实的：

1. Claude / opencode **能**读到这 62 条（软链在它们真扫的目录里）
2. agent-hub 统一列表**看不到**（拒读）
3. ⇒ D2 的 `/api/skill/relevant` **永远不会推荐它们**

即「可见性缺口」而非「安全边界」问题——`/fs/1000/ftp/技术文档/` 是本机自研/引入代码的权威归档根，不是系统目录。**但这属安全边界变更，已登记 `PT-20261002-13`，未动手。**

## 6. 验收

- [x] 产物落盘于 `work/dispatch/skillcenter/b3-opencode.md`（绝对路径 + 二进制偏移，非行号——Bun 单文件无行号可言）
- [x] 全部关键断言由主会话独立复核，6/6
- [x] 子代理自身的取证缺陷已记录（未查用户配置就下「不生效」的结论，差最后一环）