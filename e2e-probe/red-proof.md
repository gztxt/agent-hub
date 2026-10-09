# 红向自证与踩坑记录（e2e 试点第一批）

> 2026-10-09 · 会话 `ca14b919` · worktree `agenthub-wt-ca14b919`
> 判例：`agent-knowledge/101-e2e试点的三个假绿与API先读原则.md`

本批 9 条用例**全绿不构成"判据有效"的证据**。本文件记录每个方向的红向自证，
以及构造过程中撞到的四个真实问题（含两个我自己写错的判据）。

## 环境（复现所需的全部前提）

```bash
cd /fs/1000/ftp/技术文档/agenthub-wt-ca14b919/e2e-probe
export PLAYWRIGHT_BROWSERS_PATH=/vol1/venv/c4ai-browsers   # 复用本机已有二进制
export E2E_TELEMETRY_DISABLED=1                # 禁遥测：不向外发数据
npx e2e run
```

**零下载是实测结论，不是推断**：`@e2e-dev/web` 依赖 `playwright-core@1.63.0`，
其 `browsers.json` 要 chromium revision **1243**；本机 `/vol1/venv/c4ai-browsers/`
下`chromium_headless_shell-1243` 正好在位且有 `INSTALLATION_COMPLETE`
⇒ e2e 的首跑浏览器探测（`install.js: headlessShellInstalled`）直接判已装，不下载。

⚠️ 但**完整 chromium build 在本机不存在**（只有 headless shell）。
⇒ `--headed` 或任何需要完整 chromium 的场景仍会触发下载，而下载默认走 CDN，
本机 github/外网直连不通 ⇒ **必须显式给代理**，否则会卡住。
本批全部headless，未触发。

## 红向自证（3 向，逐条实测）

| 向 |手法 | 结果 |
|---|---|---|
| A | `aria-selected` 断言改成不可能值 `'nonexistent-value'` | **1红 4 绿**（精准，非全红） |
| B' | 点**已在的** tab（内容不变） | **1 红 4 绿** |
| D | 把仍存在的挂载点 `#page-chat` 加进「必须不存在」清单 | **1 红 3 绿**，`expected 0 / observed 1` |

三向都是「只该红的红」，不是全红 ⇒ 判据有**分辨力**（能指认是哪一条坏了），
不是"改一个字就全线崩"。

> 首版红向 B **没转红**，那次失败比红更有价值——见下。

## 撞到的四个真实问题

### ① 假绿：`expect.poll` 采到了过渡态（红向 B 不转红的真因）

首版判据是"`main` 文本长度变化了"：

```ts
await expect.poll(len).not.toBe(before);   // ❌ 恒绿
```

改点**已在的** tab 时，长度实测恒等（25242 → 25242），但判据仍绿。
机制：click 期间页面会短暂重渲染，poll 的**第一次采样**采到了那个长度差就立刻通过。

**结论：「某个量变了」不是好判据，「某个物在/不在」才是。**
长度类指标要配采样时序约束；可见性类是离散态，没有过渡态可钻。

### ② 依赖渲染时序的可见性同样不可靠

第二版拿 `#page-chat` 的可见性当判据，也红了。实测裸 `goto` 后 **4 秒内一直是
`display:none`**，换个等待策略又变可见 ⇒ 该元素的可见性由异步渲染时序决定。

**结论：先量、后写判据。** 判据选取必须来自对真实页面的多次观测，
且要挑**离散、稳定、与时序无关**的量。

### ③ 我凭记忆写错了断言（两次）

- `#hErrors` 被我写进「08-08 已删挂载点」清单 ⇒ 转红。实查：它是**活挂载点**，
  写端在 `static/hub/01-core-boot.js:538`，只在有启动异常时填 `innerHTML`。
  "我记得它被删了" ≠ "它不存在"。
- 第二次把「常态为空」也写死 ⇒ 又转红。`#hStale` 实际显示「需重启」，
  因为 `/health.code_stale=true`（主 checkout 5 条未提交⇒ 运行指纹与 HEAD 不符）。
  它是**如实反映**，不是坏了。

⇒ 第二版改成**一致性断言**（挂载点显示什么，必须由 `/health` 的哪个字段解释），
这比"常态为空"更强：任何环境下都恒真，真出问题时照样红。

⚠️ **不该把「健康态」写进断言**——健康态是环境变量，写死等于把 CI 的偶发状态
焊进用例。

### ④ API 先读原则（我因此报废了两条用例）

同一个文件里连错三次：`app.page()` 不存在、`locator.innerText()` 实为 `textContent()`、
`browser.getByRole()` 不存在（语义查询全在 `screen` 上，`browser` 只有 CSS/XPath
的 `locator()`）。

**结论：写测试代码前先把 `node_modules/**/dist/*.d.ts` 读完**，别照着 Playwright
的肌肉记忆写——e2e 有自己的 `App` / `Screen` / `Browser` 三件套。

## 观测数据（供后续批次复用）

```
总览页可见(main 内 button/h2/h3): Agent / 注册自定义 Agent / 自动扫描 / 端口占用 / 遥测
本机项目页可见:                 重扫 / 新建会话
两边互斥，无交集。
```

⚠️ 这份差集我观测了两次才拿到一致结果：第一次用了 `.slice(0,10)` 截断+ 不同时长
等待，两次结论互相矛盾。**取差集必须完整取、不截断。**

## 收集器对账

```
npx e2e list  →  9
npx e2e run   →  Tests 9 passed
```

对账平 ⇒ 闸门确实跑了 9 条，不是"收集器没收集到所以全绿"。