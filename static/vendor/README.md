# static/vendor —— 离线终端栈（来源与版本登记）

本目录是**离线内嵌**的第三方前端组件，不走 CDN（本机外网不稳，且终端是核心路径，
CDN 挂掉等于全站终端不可用）。全部为 MIT，许可证全文见同目录 `LICENSE`。

## 版本登记（2026-09-30 逐字节取证）

| 文件 | npm 包 | 版本 | 取证方式 |
|---|---|---|---|
| `xterm.js` | `@xterm/xterm` | **5.5.0** | 文件头 jsDelivr 注释 `Original file: /npm/@xterm/xterm@5.5.0/lib/xterm.js` + tarball 正文比对 |
| `xterm.css` | `@xterm/xterm` | **5.5.0** | 同上（`css/xterm.css`） |
| `fit.js` | `@xterm/addon-fit` | **0.10.0** | 文件头 jsDelivr 注释 `@xterm/addon-fit@0.10.0/lib/addon-fit.js` + 去头后正文与 tarball 逐字节相同 |
| `addon-webgl.js` | `@xterm/addon-webgl` | **0.18.0** | 与 npm tarball `lib/addon-webgl.js` **md5 完全一致**（无头注释，故可直接比对） |
| `addon-canvas.js` | `@xterm/addon-canvas` | **0.7.0** | 同上，md5 一致 |
| `addon-search.js` | `@xterm/addon-search` | **0.15.0** | 同上，md5 一致 |
| `addon-unicode11.js` | `@xterm/addon-unicode11` | **0.8.0** | 同上，md5 一致 |
| `addon-web-links.js` | `@xterm/addon-web-links` | **0.11.0** | 同上，md5 一致 |
| `addon-clipboard.js` | `@xterm/addon-clipboard` | **0.1.0** | 同上，md5 一致 |

**为什么 addon 版本不能凭印象写**：这批文件此前**没有任何版本标记**（压缩产物里找不到
`VERSION`、包名或版本串），一旦上游发新版无从判断本机跑的是哪版，出问题也无法二分。
本表的每个版本号都是把候选版本的 npm tarball 逐个解出、与本地文件 md5/正文比对得出的
——不是从 registry 元数据抄的（registry 只说"有这些版本"，不说"你这个文件是哪版"）。

## 重新取一份的流程（本机可复现）

```bash
# 走国内镜像（AGENTS.md §5：GitHub 直连超时，npm 需镜像）
curl -sO https://cdn.npmmirror.com/packages/@xterm/addon-webgl/0.18.0/addon-webgl-0.18.0.tgz
tar xzf addon-webgl-0.18.0.tgz
cmp package/lib/addon-webgl.js static/vendor/addon-webgl.js   # 一致才算引入成功
```

要点：

1. **取 `lib/` 里的原始产物**，不要取 CDN 另存版。jsDelivr 会在文件头插 5 行注释
   （`Skipped minification because …` / `Minified by jsDelivr …`），那会让 md5 永远对不上，
   也会让"这文件是哪版"更难查。本目录里 `xterm.js`/`xterm.css`/`fit.js` 三个文件
   **带那层头注释**（历史遗留，取自 jsDelivr），其余 6 个 addon 是 tarball 原样。
   比对带头的文件时先 `tail -n +6` 或直接看头部 `Original file:` 注释。
2. 候选版本要按 **semver 降序扫稳定版**（`^\d+\.\d+\.\d+$`）。xterm.js 的 registry 里
   混着几百个 `0.20.0-beta.*`，只看 registry 返回的"最后 30 个版本"会全落在 beta 上，
   永远扫不到真正的 0.18.0。
3. **`.map` 文件没进仓**。源码映射指向的 `.map` 不在 git 里，浏览器 devtools 会报
   404，属已知取舍（终端栈产物 500KB+，映射文件不值得入库）。

## 与代码的耦合点（改这些前先读）

- **`03-agents-cards.js` 有 canvas 渲染器分支**：xterm 升 6.0 时若删 `addon-canvas.js`，
  这里必须同步改，否则终端白屏。这是当前版本组合的硬耦合，不是可选优化。
- **`?term=canvas` 查询参数**：可在 webgl / canvas / dom 之间手切（webgl 上下文丢失时
  的逃生口，v0.13.57 加的渲染器自愈）。
- **Unicode11 必须在 `open` 之前挂并激活 `activeVersion='11'`**（cell 宽度表在渲染器
  初始化时固化，open 之后再改不重算）。
- addon 与 `@xterm/xterm` 版本**严格对齐**（v0.13.54 的引入约定）：升级要整组升，
  不要只升其中一个。
