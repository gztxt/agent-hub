import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import type { Browser } from '@e2e-dev/web';

/** 侧栏的 class 字符串。抽出来是因为 `toHaveClass` 在本框架里不存在。 */
const sidebarClass = (browser: Browser) =>
  browser.locator('.sidebar').getAttribute('class').then((c) => c ?? '');

/**
 * agent 步骤扩量（2026-10-09）。
 *
 * 目的：验证「自然语言写用例」在主流程上是否真省力。
 * 写法上守两条已踩过的坑：
 *  ① 判据只钉「某物在/不在」，不钉「某个量变了」（poll会采到过渡态）；
 *  ② 每条 agent 步骤后都跟一条**与模型无关**的 locator 断言 ——
 *     agent 的 passed 来自模型判断，只有 locator 才是页面事实。两者不一致以 locator 为准。
 *
 * 标识全部来自对真实页面的逐 tab 实测（不凭记忆，见 red-proof.md）。
 * ⚠️ 侧栏收起后的 48px 宽度是**几何量**，按边界裁定归tests/verify_*.py，不在这里断言。
 */

/** 本机项目页：占位符「按名称 / 路径过滤…」是该页独有（GitHub 项目页是「全名」）。 */
test('agent 能切到「本机项目」并看到本项目专有的过滤框', async ({ app, agent, screen }) => {
  await app.open('/');
  await expect(screen.getByRole('tab', { name: '总览' })).toHaveAttribute('aria-selected', 'true');

  await agent.act('切换到「本机项目」标签页');

  await expect(screen.getByRole('tab', { name: '本机项目' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(screen.getByPlaceholder('按名称 / 路径过滤…')).toBeVisible();
  // 互斥：GitHub 项目页那个「全名」框此刻必须不可见。
  // ⚠️ 用 toBeHidden() 而**不是** toHaveCount(0)：实测页面容器靠 display 切换，
  // 节点始终留在 DOM 里，count 恒为 1 ⇒ 用 count(0) 会恒红。
  // 「DOM 里没有」与「在 DOM 里但隐藏」是两种形态，两种判据不能混用。
  await expect(screen.getByPlaceholder('按名称 / 全名过滤…')).toBeHidden();
});

/** GitHub 项目页：同上，用「全名」占位符把两页区分开。 */
test('agent 能切到「GitHub 项目」并看到仓库专有的过滤框', async ({ app, agent, screen }) => {
  await app.open('/');
  await expect(screen.getByRole('tab', { name: '总览' })).toHaveAttribute('aria-selected', 'true');

  await agent.act('切换到「GitHub 项目」标签页');

  await expect(screen.getByPlaceholder('按名称 / 全名过滤…')).toBeVisible();
  // 同上：隐藏而非删除
  await expect(screen.getByPlaceholder('按名称 / 路径过滤…')).toBeHidden();
});

/**
 * 资源页：**刻意不走 agent**，直接点「刷新」。
 *
 * ⚠️ 这是本批最重要的一个负面结论（2026-10-09 实测）：
 * 首版这里写的是 `agent.act('点击「刷新」按钮…')` ⇒ **每次都超时**（119s 后 TEST_TIMEOUT）。
 * 报告里的 cache 字段说得很清楚：
 *   `mode: agent-concluded, reason: end-mismatch, replayedActions: 1, totalActions: 1`
 * 即：回放了那一下点击，但**结束状态复核不通过** ⇒ 交接给 agent 重新接管 ⇒ 慢且贵。
 *
 * 根因不是 agent 不会点，而是**资源页的状态本质动态**：本机 Agent 随时启停，
 * 列表条数、CPU/内存读数每次都变。docs/cache.mdx 明写「每次读数不同的文本
 * （时间、id、计数）不作为结束状态的证据」—— 所以这里的回放**永远 miss**。
 *
 * ⇒ **回放缓存不是万能的：目标页面状态越动态，越不适合做agent 步骤。**
 * 判据本身仍保留（刷新后列表可用），只是不经模型 ⇒ 零 token、毫秒级。
 * 若哪天要让 agent 点这一页，必须先给资源页一个稳定的数据源（冻结/夹具）。
 */
test('「资源」页点刷新后列表仍可用（不走 agent：页面状态动态）', async ({ app, screen, browser }) => {
  await app.open('/');

  // ⚠️⚠️ 本批最隐蔽的一个坑，务必读：
  //侧栏那个「资源」tab 的**可见文字是「资源」，但 aria-label 是「资源监控」**。
  // 可访问名按 a11y 规则 aria-label 优先 ⇒ 语义查询必须用「资源监控」，
  // 写 { name: '资源' } 会 30s 超时后报 LOCATOR_NOT_FOUND。
  //
  // 为什么值得单独写一条元断言：前面「本机项目」「总览」都好用，
  // 因为它们的 aria-label 恰好等于可见文字 ⇒ **侥幸能中**。
  // 一旦某个 tab 的 aria-label 与文案不一致（中文里极常见：「资源」→资源监控、
  // 「设置」→偏好设置），照着界面文字写的判据就会整条失效。
  await expect(screen.getByRole('tab')).toHaveText(['总览', '本机项目', 'GitHub 项目', '资源']);
  await screen.getByRole('tab', { name: '资源监控' }).click();

  // 正对照：先钉住起点，否则「刷新后还在」可能只是「从没离开过资源页」
  await expect(browser.locator('#page-resources')).toBeVisible();
  // 只钉「汇总读得到、非空」，**不钉文案**：实测文案是「总计：N 个 Agent · CPU …」，
  // 但 N 与百分比每次刷新都变 ⇒ 钉文案等于把环境噪声焊进断言。
  expect(((await browser.locator('#resSummary').textContent()) ?? '').length).toBeGreaterThan(0);

  await screen.getByRole('button', { name: '刷新' }).click();

  await expect(browser.locator('#resList')).toBeVisible();
  // 列表非空：条数不钉死（随本机 Agent 启停漂移），但不能是空的（刷新若打坏页面，这里立刻红）
  expect(await browser.locator('#resList > *').count()).toBeGreaterThan(0);
});

/**
 * 侧栏收起/展开：**直连控件、不经模型**（2026-10-10 改）。
 *
 * 原版是 `agent.act('收起左侧边栏')`。2026-10-10 复跑 5 次里 **2 次失败**：
 * 模型有时只发 1 次调用，`agent.act` 自报成功（步骤打 ✓）但侧栏**没真收起**，
 * 随后那条与模型无关的 class 断言把它判红；失败样本 token 反而飙到 170k。
 * ⇒ 与「资源页」同理：**确定性动作不需要模型**，直点控件既稳又零 token。
 *   agent 版本的教训保留在 README 与判例里，不再拿它当闸门。
 *
 * ⚠️ 只断言**状态标识**（class），不碰 48px 宽度像素——后者是几何量，按边界归
 *    `tests/verify_*.py`。
 * ⚠️ 没有 `toHaveClass` 方法（又一次 API 想当然），用 `getAttribute` 自己判。
 */
test('侧栏可收起与展开（直连 #btnSideToggle，不经模型）', async ({ app, browser }) => {
  await app.open('/');

  // 正对照：收起前侧栏是展开态（class 不含 collapsed）
  expect(await sidebarClass(browser)).not.toMatch(/collapsed/);

  const toggle = browser.locator('#btnSideToggle');
  await toggle.click();
  expect(await sidebarClass(browser)).toMatch(/collapsed/);

  // 往返：再点一次必须展开 ⇒ 证明确实是「切换」，而非「点一下就卡住」（防 stuck 假绿）
  await toggle.click();
  expect(await sidebarClass(browser)).not.toMatch(/collapsed/);
});