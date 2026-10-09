import { test } from '@e2e-dev/web';
import { expect } from 'e2e';

/**
 * agent 步骤冒烟：只验「链路能不能跑通」，不写正式回归用例。
 *
 * ⚠️ 这批会**真实调用模型**（非会话测试 ⇒ 走 CCR 免费池 `openrouter/openrouter/free`，
 * 零扣费）。首次跑通后 `agent.act` 的动作会被录进 `.e2e/cache/`，
 * 之后同一条走**回放、零模型调用** —— 这正是本框架的成本卖点。
 *
 * 判据写成可断言的量：
 *  - agent 步骤本身返回 verdict（passed）；
 *  - 随后那条 locator 断言真的成立（不靠"agent 说它成了"）。
 * 后者是有意的**双层校验**：agent 的 passed 判定来自模型判断，
 * 只有 locator 断言才是页面事实。两者不一致时以 locator 为准。
 */
test('agent 能点开「本机项目」并点到唯一控件', async ({ app, agent, screen, browser }) => {
  await app.open('/');

  // 前置正对照：起点状态必须先钉住，否则「点开了」可能只是「本来就在」
  await expect(screen.getByRole('tab', { name: '总览' })).toHaveAttribute('aria-selected', 'true');

  await agent.act('切换到「本机项目」标签页');

  // 第二层：页面事实（模型说通了不算数）
  await expect(screen.getByRole('tab', { name: '本机项目' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(screen.getByRole('button', { name: '新建会话' })).toBeVisible();
  await expect(screen.getByRole('button', { name: '端口占用' })).toBeHidden();
});