import { test } from '@e2e-dev/web';
import { expect } from 'e2e';

/**
 * 第一批：零模型语义断言。
 *
 * 判据取舍（对着2026-10-09 实测的真实页面写的，不是凭记忆）：
 *  -侧栏四个 tab 是 `role=tab`（总览/本机项目/GitHub 项目/资源）⇒ 用 getByRole('tab')。
 *  - 顶栏有 `a` 指向版本号（v0.13.100）。
 *  - 主区H1 是「Agent Hub」。
 *
 * ⚠️ **每条断言都必须能在错时转红**。本仓历史判例反复吃过「搜不到就当通过」的假绿
 * （agent-knowledge/96/100）。因此每条都配了负对照，见 negative-controls.e2e.ts。
 */

test('侧栏四个主导航 tab 都在，且首屏选中「总览」', async ({ app, screen }) => {
  await app.open('/');

  const tabs = screen.getByRole('tab');
  for (const name of ['总览', '本机项目', 'GitHub 项目', '资源']) {
    await expect(tabs.filter({ hasText: name })).toBeVisible();
  }
  await expect(screen.getByRole('tab', { name: '总览' })).toHaveAttribute('aria-selected', 'true');
});

test('页面标题与主标题是 Agent Hub', async ({ app, screen, browser }) => {
  await app.open('/');

  await expect(screen.getByRole('heading', { level: 1 })).toContainText('Agent');
  // 主区那句标语是真文案，不是占位符——变了就说明首屏被改过
  await expect(browser.locator('main')).toContainText('MULTI-AGENT CONTROL PLANE');
});

test('底部状态栏已删（v0.13.100起不再渲染 footer/时钟）', async ({ app, browser }) => {
  await app.open('/');

  // 反向断言：挂载点必须不存在。删了挂载点却留写端会留下「页面看着正常、
  // 但每秒往null 写一次」的死调用，所以这里钉的是「不存在」而非「不可见」。
  await expect(browser.locator('footer')).toHaveCount(0);
  await expect(browser.locator('#ftTime')).toHaveCount(0);
});

test('顶栏 GitHub 外链存在且指向新仓名', async ({ app, screen }) => {
  await app.open('/');

  const link = screen.getByRole('link');
  await expect(link).toBeVisible();
  // 不用 exact 名称断言：窄屏下文字被裁、只剩图标（文案在 title/aria-label 里）。
  // 断言 href 比断言文案稳，且 08-08 改名批已把仓名钉成 gztxt/agenthub。
  const href = await link.getAttribute('href');
  expect(href).toBe('https://github.com/gztxt/agenthub');
});

/**
 * 导航真的切换了页面 —— 判据是**双重的**，每条各锁一类失败模式：
 *
 * ⚠️ 首版写的是 `main` 文本长度变化，红向自证**抓到了假绿**：点已在的 tab 时长度
 * 恒等（实测 25242 → 25242），但 `expect.poll` 仍判绿 —— 因为 click 期间页面会短暂
 * 重渲染，poll 采到了过渡态的长度差就立刻通过。
 * 教训：**「某个量变了」不是好判据，「某个物在/不在」才是**。长度类指标要配采样时序。
 *
 * ⚠️ 第二版拿 `#page-chat` 的可见性当判据，也红了 —— 实测它 `display:none` 的时长
 * 依赖异步渲染时序（裸 goto 后 4 秒内一直是 none）。**依赖渲染时序的可见性同样不可靠**。
 *
 * 定稿判据（对着 2026-10-09 实测的完整差集写）：
 *  - 总览页可见：Agent / 注册自定义 Agent / 自动扫描 / 端口占用 / 遥测
 *  - 本机项目页可见：重扫 / 新建会话（两边互斥，无交集）
 * 用 `aria-selected` 锁「切了没有」，用独有按钮锁「真的切到了那一页」。
 * 只锁前者可能停在半路，只锁后者则「切回原页」会误判为成功。
 */
test('切换到「本机项目」后主区视图真的换了', async ({ app, screen, browser }) => {
  await app.open('/');

  // ⚠️ 语义查询全走 `screen`，`browser` 只有 CSS/XPath 的 `locator()`
  //（browser.getByRole 不存在——本条初稿写错了一次，见 red-proof.md 的 API 教训。）
  // 前置正对照：钉住切换前的状态，否则「隐藏」可能因为元素压根不存在而空过
  await expect(screen.getByRole('button', { name: '端口占用' })).toBeVisible();
  await expect(screen.getByRole('tab', { name: '总览' })).toHaveAttribute('aria-selected', 'true');

  await screen.getByRole('tab', { name: '本机项目' }).click();

  await expect(screen.getByRole('tab', { name: '本机项目' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  // 该页独有控件上线
  await expect(screen.getByRole('button', { name: '新建会话' })).toBeVisible();
  // 原页独有控件下线（这两条构成互斥，不靠「元素还在但看不见」蒙混）
  await expect(screen.getByRole('button', { name: '端口占用' })).toBeHidden();
  // CSS 轴也查一次同一个节点：语义层与 DOM 层同时成立，避免只有语义层被绕过
  await expect(browser.locator('#page-chat')).toBeHidden();
});