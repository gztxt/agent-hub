import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

/**
 * agenthub 的 e2e 试点配置（2026-10-09）。
 *
 * 分工边界（勿越界，这是本试点存在的前提）：
 *  - **这里**只做语义断言 —— 「页面上看得见的文字/角色/可见性」。
 *  - **几何判据**（侧栏窄屏≤60px、gap 4/1/1px、overflow 裁切）继续归
 *    `tests/verify_*.py` + `_cdp_min.py`。理由：那批判据要的是 rect/pixel 量，
 *    且已在生产跑了两个多月、判例齐备；e2e 虽有 `locator.boundingBox()`，
 *    但把它接回「扫 320→1920 宽度轴 + 断言可量化」是重写而非复用。
 *    把几何判据搬过来是**倒退**，不是升级。
 *
 * 为什么值得引入：改完 UI 后「回归三十个页面」从人肉变成自然语言 + 零 token 回放。
 * 这正是现有 L0/L1/L2 覆盖不到的部分（它们证伪，不证「用户还能走通流程」）。
 *
 * ── 模型（军规：零新增凭据 /非会话测试默认走免费池）──
 * 走本机 CCR 网关（`127.0.0.1:3456`），复用既有 `ccr-profile` 凭据。
 * 模型 ID 必须是 CCR `GET /v1/models` 的**实测返回**：
 *   `openrouter/openrouter/free` —— 路由器写法，上游摘成员自动换档，不受轮换影响。
 * 🚫 绝不用 `openrouter/auto`：它会自动挑最便宜的可用模型，最便宜的**可能是付费模型**
 *    ⇒ 直接产生账户扣款（AGENTS.md §1 硬规则 4）。
 *
 * 该模型的 tool call + 图像能力已实弹验证（2026-10-09，见 red-proof.md）：
 *   - tool call：`finish_reason=tool_calls`，参数解析正确；
 *   - 图像输入：读出 8×8 红 PNG 的颜色 = 「红色」。
 * 若上游某天把这道题路由到审核/分类类模型，会返回标签而非正文⇒ agent.assert 假绿。
 * 真跑agent 批次时必须看run summary 里的模型名与调用数，对不上就当失败。
 */
const local = createOpenAICompatible({
  name: 'ccr',
  baseURL: process.env.CCR_BASE_URL ?? 'http://127.0.0.1:3456/v1',
  apiKey: process.env.CCR_API_KEY,   // 只从环境变量读，**不写进任何文件**
});

export default {
  targets: [
    {
      name: 'desktop',
      engine: web({ viewport: { width: 1280, height: 720 } }),
      // 生产实例。只读打测，不注入 header（注：header 会禁 HTTP 缓存并挡掉 service worker）。
      app: { url: process.env.APP_URL ?? 'http://127.0.0.1:3102' },
    },
  ],
  agents: {
    default: {
      model: local.chatModel(process.env.E2E_MODEL ?? 'openrouter/openrouter/free'),
    },
  },
  // CI 下默认 read-only（回放但不写回录制）；本地 read-write。
  cache: process.env.CI ? 'read-only' : 'read-write',
} satisfies E2EConfig;