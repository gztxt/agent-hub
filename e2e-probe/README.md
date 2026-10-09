# e2e-probe —— tester-army/e2e 试点

> 2026-10-09 · 会话 `ca14b919` · 分支 `wt/ca14b919`
> 判例：`agent-knowledge/101-e2e试点的三个假绿与API先读原则.md`（含全部红向自证记录）

## 这是什么

对 [`tester-army/e2e`](https://github.com/tester-army/e2e)（Apache-2.0）的**定点试点**：
在 agenthub 上验证「自然语言写用例 + 零token 回放」这条路值不值得走。

**不是** agenthub 测试体系的新增层，是**并行的旁路**。现有 L0/L1/L2 一律不动。

## 职责边界（越界即倒退）

| |归属 |
|---|---|
| 语义断言（「页面上看得见什么」） | **这里** |
| 几何断言（rect / pixel / gap / 扫宽度轴） | `tests/verify_*.py` + `_cdp_min.py`（不动） |
| 静态结构、模板与产物一致性 | L0pytest（不动） |
| 依赖真宿主目录的断言 | L1 host（不动） |

理由：几何判据要的是 rect/pixel 量且判例齐备，e2e 那边虽有 `locator.boundingBox()`，
但接回「扫 320→1920 + 量化断言」是重写而非复用。

## 跑法

```bash
cd e2e-probe
export PLAYWRIGHT_BROWSERS_PATH=/vol1/venv/c4ai-browsers   # 复用本机已有 chromium
export E2E_TELEMETRY_DISABLED=1                            # 禁遥测
export CCR_API_KEY="$(grep -hoE '^JCODE_PROVIDER_CCR_API_KEY=.*' \
  ~/.config/jcode/provider-ccr.env | cut -d= -f2- | tr -d '\"'"'"' ')"

npx e2e run                      # 全量
npx e2e run tests/smoke.e2e.ts   # 只跑零模型那批（不需要 CCR_API_KEY）
npx e2e list                     # 列出收集到的用例（对账用）
```

## 三条环境铁律

1. **`PLAYWRIGHT_BROWSERS_PATH=/vol1/venv/c4ai-browsers` 必须设**。
   `@e2e-dev/web` 依赖 `playwright-core@1.63.0`，要 chromium revision **1243**；
   本机 `/vol1/venv/c4ai-browsers/chromium_headless_shell-1243` 正好在位
   ⇒ **零下载**。不设这个变量它会去找`~/.cache/ms-playwright`（本机为空）然后去 CDN 下载，
   而本机外网直连不通 ⇒ 卡住。
   ⚠️ 本机**只有 headless shell、没有完整 chromium build** ⇒ `--headed` 仍会触发下载，
   需要显式给 `HTTPS_PROXY=http://127.0.0.1:7890`。本试点全程 headless。

2. **`openrouter/openrouter/free`，绝不 `openrouter/auto`**。
   `auto` 的语义是「自动挑最便宜的可用模型」，最便宜的**可能是付费模型**
   ⇒ 直接产生账户扣款（AGENTS.md §1 硬规则 4）。经 CCR 时ID 写作
   `openrouter/openrouter/free`。它已被实弹验证支持 **tool call** 与**图像输入**
   （见 red-proof.md），满足 agent 步骤的硬要求。

3. **凭据只从环境变量读**，不写进任何文件。`CCR_API_KEY` 缺失时零模型用例仍全绿，
   只有 agent 用例会报 `MODEL_PROVIDER_FAILED`。

## 实测数据（2026-10-09）

| 场景 | 耗时 | 模型调用 | token |
|---|---|---|---|
| 零模型用例（9 条） | ~5s | 0 | 0 |
| agent 步骤**首次**（录制） | 26.41s | 2 | 22.5k |
| agent 步骤**二次**（回放） | **1.91s** | **0** | **0** |

回放快 **13.8 倍**且零 token —— 这是这个框架唯一的、也是决定性的价值点。

## 成本边界

- 本试点**零扣费**：模型走 CCR 免费池，框架本地跑不用订阅。
- `Kernel` / `smol` / `EAS`（托管浏览器与设备）**要账号要钱，本试点一律未用**。

## 判据纪律（本批踩过的坑，详见 red-proof.md）

- **不许用「某个量变了」当判据**。首版用文本长度变化，`expect.poll` 采到click
  期间的过渡态 ⇒ 恒绿假绿。改用「某物在/不在」。
- **不许把「健康态」写进断言**。`#hStale` 显示「需重启」是因为
  `/health.code_stale=true`（主tree 5 条未提交），它是**如实反映**不是坏了。
  改成一致性断言（显示什么必须由 `/health` 哪个字段解释）。
- **不许凭记忆写断言**。我写「`#hErrors` 已删」⇒ 转红，实查它是活挂载点
  （写端 `static/hub/01-core-boot.js:538`）。
- **写测试代码前先读 `node_modules/**/dist/*.d.ts`**。别照Playwright 肌肉记忆写——
  e2e 有自己的 `App`/`Screen`/`Browser`，语义查询全在 `screen` 上，
  `browser` 只有 CSS/XPath 的 `locator()`。我在一个文件里连错三次。

## 现状与后续

- 现状：**10 条用例（9 零模型 + 1 agent）全绿，7 个红向方向全部转红**。
- **未接入 prepush 闸门**，刻意保持旁路。理由：agenthub 的 prepush 有六道闸，
  引入一个 0.x 且「小版本会破坏性变更」的外部框架进闸门，是长期负债。
  要接入应等 agent 批次稳定 + 锁定确切版本号。
- `e2e init` 写的 `.e2e/`、`node_modules/`、`package-lock.json` 均已 gitignore。
  **`.e2e/cache/` 是否入库需决策**：入库则CI 能回放（省钱），但录制里含
  「动作+目标描述+输入文本」⇒ 当测试数据审。