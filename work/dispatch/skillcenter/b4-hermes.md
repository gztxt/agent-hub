# b4 — hermes 技能面取证（只读）

> 落盘者：主会话（单写者，`agent-dispatch` §1.2）。内容由 pi 子代理 `hermes-recon`（Explore，只读）
> 产出，主会话已对关键断言逐条自验证（§4.3），**并纠正了原报告 2 处错数**，见文末 §7。
> 取证时间：2026-10-03｜主机 zzst / gztxt｜hermes 入口 `/home/gztxt/.local/bin/hermes`

## 0. 结论（一句话）

运行时加载面 = `/home/gztxt/.hermes/skills` + 外接 `/home/gztxt/.hermes/skills-hot`。
`hermes-agent/skills` = 内置种子源（不被扫描）；`.ekko/skills` = 另一组件（Ekko Studio）。
hermes **跟随符号链接**。

## 1. 运行时加载面（证据）

| 事实 | 绝对路径:行号 / 配置键 |
|---|---|
| 本地技能目录定义 | `/home/gztxt/.hermes/hermes-agent/hermes_constants.py:1473` → `get_skills_dir()` = `get_hermes_home() / "skills"` |
| HERMES_HOME 平台默认 | `hermes_constants.py:59` → `Path.home() / ".hermes"` |
| HERMES_HOME 解析序 | `hermes_constants.py:112-117`（contextvar → env → 平台默认） |
| 工具层常量 | `tools/skills_tool.py:63` → `SKILLS_DIR = HERMES_HOME / "skills"` |
| 加载根列表 | `agent/skill_utils.py:420-428` `get_all_skills_dirs()` = `[get_skills_dir()] + create_dir + external_dirs` |
| 外接目录读取 | `agent/skill_utils.py:359-381` `get_external_skills_dirs()` 读 `skills.external_dirs` |
| 配置键 | `/home/gztxt/.hermes/config.yaml:332`（`skills:`）、`:344`（`external_dirs:`）、`:345`（`~/.hermes/skills-hot`） |

- `~/.hermes/skills`：35 个顶层条目，层级分类（creative / devops / github / productivity / software-development …）。
- `~/.hermes/skills-hot`：32 个条目 = **16 个活软链**（→ `/fs/1000/ftp/技术文档/skills/*`，16/16 含 `SKILL.md`）
  + 16 个 `*.deadlink-20261003` 悬空残留（→ 已删的 `/vol1/@apphome/trim.openclaw/data/workspace/skills/`）。
  **主会话已实测复核：活链 16 / 死链 16 / 活链含 SKILL.md 16。**

## 2. 另两处的角色

### 2.1 `~/.hermes/hermes-agent/skills`（58 条）— 打包种子源，运行时**不被扫描**
| 证据 | 路径:行号 |
|---|---|
| 种子源取址 | `tools/skills_sync.py:74-75` `_get_bundled_dir()` |
| 用途（文件头 docstring） | `tools/skills_sync.py:2-4`「Copies repo skills/ into `~/.hermes/skills/`, tracking each synced skill's origin hash in `.bundled_manifest`」 |
| 落地目标 | `tools/skills_sync.py:35-37` `SKILLS_DIR` / `MANIFEST_FILE = SKILLS_DIR/".bundled_manifest"` |
| 覆盖策略 | `tools/skills_sync.py:4-6` 用户已改的 SKIP、用户已删的不回填 |
| 可被环境变量改写 | `hermes_constants.py:409-411` `HERMES_BUNDLED_SKILLS` |
| 落地物证 | `~/.hermes/skills/.bundled_manifest`（2727 B，`name:sha256` 行） |

不在 `get_all_skills_dirs()` 的三个来源里 ⇒ 是 `~/.hermes/skills` 的**上游**。

### 2.2 `~/.hermes-web-ui/.ekko/skills/default`（22 条）— 另一组件（Ekko Studio）
| 证据 | 路径:行号 / 输出 |
|---|---|
| 归属 | 位于 `hermes-web-ui` 组件的 HOME，与 `~/.hermes` 分离 |
| 不是 HERMES_HOME | `ls ~/.hermes-web-ui/.ekko/config.yaml` → No such file or directory（HERMES_HOME 必备 `config.yaml`，`hermes_constants.py:1468-1470`） |
| hermes 源码零引用 | `grep -rn "\.ekko" --include=*.py ~/.hermes/hermes-agent/` → 无输出 |
| 同名不同物 | 与 `~/.hermes/skills` 撞名 **14** 条，**14/14 内容均不同**（主会话实测，见 §7） |

## 3. 是否跟随符号链接 — **是**

- `agent/skill_utils.py:792` → `os.walk(skills_dir_str, followlinks=True)`。
- 佐证：`~/.hermes/skills/unified-memory`、`~/.hermes/skills/agent-dispatch` 即为软链（→ `/fs/1000/ftp/技术文档/skills/…`）。
- **⚠️ 悬空软链陷阱**：`agent/skill_utils.py:379-381` 外接目录用 `p.is_dir()` 过滤，**悬空软链返回 False 被静默跳过**（仅 `logger.debug`，L381）。
  历史事故即此：`skills-hot` 16 条全悬空时表现为「目录存在但加载到 0 个技能」。
  （`config.yaml:336-338` 的注释称 2026-10-03 实测全悬空，**该注释已过期**——同日 00:37 已重指技术文档，现 16/16 活链。）

## 4. 排除项（hermes 自身裁掉的）

`agent/skill_utils.py:23-28` `EXCLUDED_SKILL_DIRS` 含 `.git .github .hub .archive .curator_backups
.locks .venv venv node_modules site-packages __pycache__ .tox .nox .pytest_cache .mypy_cache
.ruff_cache`；`skill_utils.py:31` `SKILL_SUPPORT_DIRS = (references, templates, assets, scripts)`
为技能内附件目录，不作独立技能扫；`skill_utils.py:789-797` `_org/` token 门控（`~/.hermes/skills/_org` 不存在，当前无影响）。

## 5. 对 D6 的落点建议

铺精选软链应落 **`~/.hermes/skills-hot`**（外接目录，官方语义＝只读 + 同名让位，
`skill_utils.py:361-363`），**而非**直接塞 `~/.hermes/skills`（会被 `skills_sync.py` 的记账/清理逻辑波及，
`tools/skills_sync.py:4-6`）。
**注意**：本机 `skills-hot` 已有 16 条活链指向自研权威副本 `/fs/1000/ftp/技术文档/skills/`
⇒ D6 对 hermes 应当是「**核对而非新建**」，且须先清理 16 条 `.deadlink-20261003` 悬空残留（属既存问题，停手报请，不顺手删）。

## 6. 边界自陈

- 子代理一条 `grep -rn "ekko" ~/.hermes-web-ui/coding-agent/` 误命中 codex rollout 会话 jsonl（属禁读面）。
  **该输出未用于本报告任何结论**，§2.2 全部结论来自其余独立证据。
- 按指令未读 `.skills_prompt_snapshot.json`、`hermes-agent/skills/AGENTS.md` 及任何记忆/会话备份
  ⇒ 「实际注入 prompt 的技能清单」未取证；本报告基于源码加载路径静态推导。

## 7. 主会话自验证与纠正（§4.3）

| 项 | 子代理原述 | 主会话实测 | 处置 |
|---|---|---|---|
| `~/.hermes/skills` 条数 | 118 | **120**（`_scan_one` 的 `os.walk(followlinks=True)`） | 原述 118 系 `find` 不跟软链所致；差值恰为经软链到达的 `unified-memory` + `agent-dispatch` 2 条 |
| `skills-hot` | 16 活链 | 活链 16 / 死链 16 / 活链含 SKILL.md 16 | ✅ 一致 |
| hermes-web 与 hermes 撞名 | 「重名 2 个」 | **撞名 14 条，且 14/14 内容均不同** | ❌ 原述错数，已纠正 |
| 真正重复在哪 | 未测 | **hermes ∩ hermes-agent：realpath 交集 0，技能名交集 58，其中 55 条字节相同、3 条内容不同** | 🔴 **新发现 D1 缺陷**，见 §8 |

## 8. 🔴 派发带出的 D1 缺陷（待修，勿顺手改）

`_dedup` 只按 **realpath** 合并，而 `hermes-agent/skills` → `~/.hermes/skills` 是**拷贝**关系
（`tools/skills_sync.py:2-4`），不是软链 ⇒ **55 条字节级重复抓不到**，统一列表会把它们算成两条。

- 安全口径应为 **(name, 内容 sha256)**：可折叠 55 条真重复，同时**必须保留**
  hermes-agent 那 3 条（`hermes-agent` / `hermes-agent-skill-authoring` / `systematic-debugging`，
  本地一侧被改过）与 hermes-web 那 14 条（同名不同物）。
  ⇒ **盲目按 name 合并会吞掉真内容**，按 realpath 合并则漏掉真重复，两端都错。
- 附带：同名的 `hermes-agent` / `hermes-agent-skill-authoring` / `systematic-debugging` 属自研技能，
  已存在「同一技能在两处各自演化」的漂移，值得单独立项。
