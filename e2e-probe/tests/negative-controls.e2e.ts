import { test } from '@e2e-dev/web';
import { expect } from 'e2e';

/**
 * 负对照（红向自证）。
 *
 * 为什么单独一个文件：本仓判例反复吃过「断言搜不到就当通过」的假绿
 * （agent-knowledge/96「测试假绿」、100「挂载点仍在」正对照缺失）。
 * 正向用例全绿**不能证明**判据有效——只有「把页面改坏时它真的转红」才能。
 *
 * 运行方式（默认应全绿，因为每条都是断言一个「确实不存在/确实是错值」的事实）：
 *   npx e2e run tests/negative-controls.e2e.ts
 * 真正的红向自证在 red-proof.md 里记录：改断言 → 必须转红 → 改回。
 */

/** 反向断言范式：钉住「不存在的东西确实不存在」。若被实现引入，本条立刻转红。 */
test('负对照：页面里确实没有这些 2026-08-08 已删的挂载点', async ({ app, browser }) => {
  await app.open('/');

  // 底部状态栏（v0.13.100 删）与顶栏已摘除的健康灯/会话数块，都不该再存在。
  //
  // ⚠️ `#hErrors` **不在此列** —— 首版把它写进来导致本条转红，实查发现它是**活挂载点**
  // （写端 `static/hub/01-core-boot.js:538`，仅在有启动异常时填 innerHTML）。
  // 「我记得它被删了」不等于「它不存在」：这正是本仓判例反复强调的取证纪律。
  for (const sel of ['#ftTime', '#hHealth', '#hAgents', '#badge-classroom']) {
    await expect(browser.locator(sel)).toHaveCount(0);
  }
});

/**
 * 活挂载点的**状态一致性**断言。
 *
 * 首版写成「常态为空」⇒ 转红，实查发现 `#hStale` 显示「需重启」。查因：`/health` 的
 * `code_stale=true`（主 checkout 有 5 条未提交改动 ⇒ 运行指纹与 HEAD 不符），
 * 挂载点是**如实反映**，不是坏了。
 *
 * 教训（比断言本身重要）：**不该把「健康态」写进断言**——健康态是环境变量，
 * 断言它等于把 CI 的偶发状态焊死在用例里。改成钉「一致性」：
 * 挂载点显示什么，必须由 `/health` 的哪个字段解释。这条在任何环境下都恒真，
 * 且真出问题时（挂载点显示陈旧内容/永不更新）照样转红。
 */
test('活挂载点 hStale/hErrors 与 /health 的字段一致（非陈旧、非死挂载）', async ({ app, browser }) => {
  await app.open('/');

  const health = await browser.evaluate(async () => {
    const r = await fetch('/health');
    return r.json() as { code_stale: boolean; needs_restart: boolean };
  });

  // hStale 有内容 ⟺ code_stale；两者必须同真同假，不许出现「报了需重启但没提示」或反之。
  const staleText = ((await browser.locator('#hStale').textContent()) ?? '').trim();
  const showsStale = staleText.length > 0;
  expect(showsStale).toBe(health.code_stale);
  if (showsStale) expect(staleText).toContain('重启');

  // hErrors 只在真有启动异常时显示；无异常则必须为空。
  // 断言的是「空 ⟺ /api/agents 类数据无异常」，此处简化为：与 hStale 不同源，
  // 所以它为空是本机的真实预期（实测 errs=0）；若哪天红了，看 report 的screen dump。
  const errText = ((await browser.locator('#hErrors').textContent()) ?? '').trim();
  expect(errText).toBe('');
});

/** 反向断言范式：href 断言会因改名转红，而不是继续静默通过。 */
test('负对照：顶栏外链不是旧仓名 agent-hub', async ({ app, browser }) => {
  await app.open('/');

  const href = await browser.locator('.gh-link').first().getAttribute('href');
  expect(href).not.toContain('gztxt/agent-hub');
  // 防「外链整个没了」被上面那条 not.toContain 放过 ⇒ 正向钉住它确实在
  expect(href).toContain('gztxt/agenthub');
});

/** 反向断言范式：aria-selected 的错误值必须被拒。 */
test('负对照：「总览」不是未选中态', async ({ app, screen }) => {
  await app.open('/');

  const selected = await screen.getByRole('tab', { name: '总览' }).getAttribute('aria-selected');
  expect(selected).not.toBe('false');
});