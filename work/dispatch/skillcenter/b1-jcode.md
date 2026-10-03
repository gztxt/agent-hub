# B1 · jcode 技能加载面侦察

- **执行者**：pi 子代理 `Explore`（`2cb7cf63-8cb7-447`），只读，56 分钟 / 151,825 token / 31 tool use
- **状态**：在 turn limit 处收尾，**自己没写产物文件**（纯只读无写工具）；本文件由**主会话代写**
- **原始证据**：`/tmp/pi-subagents-1000/.../tasks/2cb7cf63-8cb7-447.output`（171,101 B，瞬时物）
- **复核**：主会话独立重跑全部关键取证，**并补上了子代理漏掉的一整块**（见 §3）

---

## 1. 结论一：jcode 的三个发现根 —— 证实

子代理的核心结论成立。**最硬的证据不是字节偏移，是 jcode 自己给用户看的报错文案**
（`jcode-linux-x86_64.bin` 偏移 15381877 附近）：

```
No skills loaded.

Skills are loaded from:
- ~/.jcode/skills/<skill-name>/SKILL.md   (global)
- ./.jcode/skills/<skill-name>/SKILL.md   (project-local)
- ./.claude/skills/<skill-name>/SKILL.md  (compatibility)
```

用户可见的帮助串 ≙ 维护者写下的发现面，比反编译的偏移可信得多。

## 2. 结论二：**项目本地根**是 D1 模型里根本没有的维度 ← 本条才是增量

子代理只说了「3 个根」，没有指出要害。D1 的 20 路**全部是绝对路径的全局根**，
而 jcode 认 `./.jcode/skills` 与 `./.claude/skills` —— **相对当前工作目录**。

也就是说：同一条 `agent-dispatch`，放在 `~/.claude/skills/` 会被所有路看见，
放在**项目里的** `./.claude/skills/` 则完全不在 agent-hub 的建模范围内。
「发现点」在 D1 里被等同于「一个绝对目录」，这个等式在 jcode 面前不成立。

本仓与工作区当前**都没有** `./.claude/skills` 或 `./.jcode/skills`，
所以这不是当下的可见性缺口，而是**模型缺口**——已登记 `PT-20261002-14`。

## 3. 子代理漏掉的一整块：jcode 有一个 `skill_registry`

主会话在 `skills.agents/skills` 字面量附近挖到一段注册表区域（偏移 ~17597689），
里面有这些相邻字符串（**按出现顺序**）：

```
skills.agents/skills … .claude/plugins … .claude/skills … .codex/skills, + plugins
installPath.jcode … agents.claude … installed_plugins.json … cacherepos … skill_registry
```

⇒ jcode 很可能还扫：`agents/skills`、`.claude/plugins`、`.codex/skills`（+ plugins）。

**这条必须标清置信度**（不把字符串表相邻当代码路径）：

| 断言 | 置信 | 依据 |
|---|---|---|
| `~/.jcode/skills` / `./.jcode/skills` / `./.claude/skills` | **高** | 用户可见报错文案逐条列出 |
| jcode 还扫 `agents/skills` · `.claude/plugins` · `.codex/skills` | **中** | 仅字符串表相邻，**未见代码路径**；不排除是插件清单格式的描述 |

`agents/skills` 已被 D1 第 3 路覆盖（`~/.agents/skills`），无新增。
`.claude/plugins` 是**真问题**，见 §4。

## 4. `~/.claude/plugins`：107 条技能，且 D1 排除的子树不是有内容的那棵

| 查什么 | 结果 |
|---|---|
| `~/.claude/plugins` 下 `SKILL.md` 总数 | **107**（几乎全在 `cache/thedotmack/claude-mem/13.13.1/skills/`） |
| D1 排除的是哪棵 | `/home/gztxt/.claude/plugins/marketplaces` |
| 107 条实际在哪 | `cache/` 子树 —— **不在被排除的那棵里** |
| `cache/` 是否在任何路由下 | **否**（主会话实测：不在 `claude` 路由下，也不在任何 `SKILL_DIRS` 根下） |

⇒ 这 107 条**不是被排除的，是压根没被扫到**。二者性质不同，处置也不同。

**更要紧的一条**：jcode 二进制里那段技能示例写的是

```
frontend-design … npx skills add anthropics/skills --skill frontend-design
                 (or Claude Code: /plugin marketplace add …)
```

而 `frontend-design` 的实际落位是
`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/frontend-design`
—— **正是 D1 以「marketplace 缓存」为由整块排除的那棵子树**。

⇒ D1 的排除理由（缓存/暂存，非真实发现点）与 jcode 自身的安装指向**相互矛盾**。
这不能靠一次 grep 定案，**已登记 `PT-20261002-13` 追加项**待实测裁定。

## 5. 数字对账：D1 记的 58 可复现（我先误判了一次「差一」）

| 度量 | 值 | 说明 |
|---|---|---|
| `ls -d ~/.jcode/skills/*/ \| wc -l` | 57 | **只数顶层目录** |
| `skill._scan_many('jcode', ...)` 接受 | **58** | 57 顶层 + `ima-skills/notes/SKILL.md` + `ima-skills/knowledge-base/SKILL.md` 两条嵌套（其中 1 条被白名单拒读） |

⇒ **与 D1 记录完全一致，无 discrepancy。** 我最初据 57 vs 58 判「差一」是**度量了不同的东西**
——与本轮 `skill._scan_one()` 返回 dict 我当序列迭代（打印 6 = 键数）是**同一类错误**。

**两次同型错误 ⇒ 立一条判据：跨来源比数字之前，先确认两个来源量的是同一个东西。**
