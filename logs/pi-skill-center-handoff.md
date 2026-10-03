# 交接件 · 技能中心统一列表与自动调用（2026-10-03）

> 新会话读这一份即可续接。**先读它，再动任何东西。**

## 一句话状态

技能门面 D1→D7 全部落地，源码 **v0.13.70**，提交 `f2e11dd`（未 push）。
**重启 agent-hub 后**生产即上 v0.13.70，`/api/skill/relevant` 与 `/api/skill/zombies` 生效。

## 做了什么（7 个批次）

| 批次 | 交付 | 版本 |
|---|---|---|
| D1 | 发现点 7 路 → **20 路** + `EXCLUDED_DIRS` 15 项 + 扫描四态（ok/empty/missing/error） | 0.13.66 |
| D2 | `GET /api/skill/relevant` BM25F（零新依赖）+ jev 附加信号（不改写排序） | 0.13.67 |
| D3 | `GET /api/skill/zombies` + `src/skill_usage.py`（置信度封顶 medium + `counted`） | 0.13.68 |
| D7 | 技能中心前端：统一列表 + 自检卡 + 相关性实验室 + 注入预算 | 0.13.69 |
| D5 | `~/.claude/settings.json` 接 `UserPromptSubmit` → `scripts/hub_skill_inject.py` | 0.13.70 |
| D6 | `scripts/skill_visibility_sync.py`，四路建成 **66 条软链** | 0.13.70 |
| D4 | `~/.pi/agent/extensions/hub-facade.ts` rev=`20261003a-D4`，input 并联注入 top-3 | — |

## 关键数字（均为实测，非估计）

- 语料：**20 路 / 去重后 365 条**；白名单外拒读 **62** 条
- `/api/skill/zombies?days=7`：`total=365` `zombies=263` **`counted=0`** `confidence=medium`
- `/api/skill/relevant`：`rerank=false` 墙钟 **287ms**；`rerank=true` **1064.5ms**
- jev 实测：`agent-dispatch`（正确）confidence **0.53** vs 两个 `arkcli` 噪声 **0.85**
  ⇒ **jev 对噪声更自信，这是「不改写排序」的生产级实证**

## 三条最容易踩的坑

1. **`rerank=true` 必须关**（两条注入通道都如此）。它 1064.5ms，pi 钩子预算 600ms、Claude hook 500ms。
2. **`/api/skill/zombies` 的 `counted=0` 是诚实信号**，不是故障——注入链刚建，365 条全无记账。
3. **别拿 `verify_*.py` 当闸门**。它是 L2 手工跑、不被收集、提交时不会执行。
   本批的静态闸门一律 L0（`tests/test_*.py`）。

## 待验收（**我方不可判定，须你在端侧做**）

| 项 | 怎么做 | 期望 |
|---|---|---|
| pi 注入生效 | 新 pi 会话发一句「这个任务该派给谁去做」 | 上下文出现「可能用得上的技能」块 |
| Claude 注入生效 | Claude 里发一句任务 | 同上 |
| 可见性第三层 | 问某家 agent「你有几个技能」 | claude 64 / agents 58 / codex 20 / workbuddy 25 |
| 前端窄屏 | 手机打开技能页 | 无横向溢出（**我这轮没做 390px 真渲染**） |

## 待你裁定（我一律没动）

- **`PT-20261002-13`**：opencode 假发现点 / 62 条第三方技能不可见 / `~/.claude/plugins` 107 条与排除表冲突
- **`PT-20261002-14`**：发现点缺「项目本地」维度（`./.claude/skills`）
- **`PT-20261002-12`**：realpath 去重漏 55 条字节级重复
- **CCR**：codex 腿 403、Claude 腿 helper 缺失（exit 127），均在 `~/.claude-code-router/**` 禁触面
- **skills-hot 16 条悬空残留**清理 · **qwenpaw reconcile**（禁改面）
- **D6 的 `pi` 路**（`~/.pi/agent/**` 受保护面，需**整树**授权才能补）

## 回滚命令

```bash
# hub-facade.ts（D4）
cp ~/.pi/agent/extensions/hub-facade.ts.bak-20261003_155814-d4-skill-inject ~/.pi/agent/extensions/hub-facade.ts
# claude settings（D5）
cp ~/.claude/settings.json.bak-20261003_160129-d5-userprompt-hook ~/.claude/settings.json
# 可见性软链（D6）：只删本批建的软链，不动实体目录
python3 -c "import pathlib,os;[os.unlink(p) for p in pathlib.Path('/home/gztxt/.claude/skills').iterdir() if p.is_symlink() and '技术文档/skills' in str(os.readlink(p))]"
```
