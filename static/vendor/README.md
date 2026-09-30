# static/vendor —— 离线终端栈（来源与版本登记）

本目录是**离线内嵌**的第三方前端组件，不走 CDN（本机外网不稳，且终端是核心路径，
CDN 挂掉等于全站终端不可用）。全部为 MIT，许可证全文见同目录 `LICENSE`。
另有一个内联依赖：`addon-clipboard.js` 内嵌了 `js-base64` 3.7.8（webpack 打包进去了，
见下方硬耦合点），故不需要额外文件。

## 版本登记（2026-09-30 从 5.5.0 整组升到 6.0.0，逐字节取证）

| 文件 | npm 包 | 版本 | tarball 内路径 | 本地文件 md5 |
|---|---|---|---|---|
| `xterm.js` | `@xterm/xterm` | **6.0.0** | `lib/xterm.js` | `d7aaaef27ff18a0e8deff9b29439090e` |
| `xterm.css` | `@xterm/xterm` | **6.0.0** | `css/xterm.css` | `5403ebbaef7d20632abb0716beb9c437` |
| `fit.js` | `@xterm/addon-fit` | **0.11.0** | `lib/addon-fit.js` | `25f0510074e274cfac85ffed228242dc` |
| `addon-webgl.js` | `@xterm/addon-webgl` | **0.19.0** | `lib/addon-webgl.js` | `d6d59dc92de5740e179ef48379c14a93` |
| `addon-search.js` | `@xterm/addon-search` | **0.16.0** | `lib/addon-search.js` | `c122e9ae81ea99f275e5acc95a91500d` |
| `addon-unicode11.js` | `@xterm/addon-unicode11` | **0.9.0** | `lib/addon-unicode11.js` | `03bb0e47bc45f3c4563d58f09e2e3ac2` |
| `addon-web-links.js` | `@xterm/addon-web-links` | **0.12.0** | `lib/addon-web-links.js` | `629a69f5a69d08740352c0496da54f7b` |
| `addon-clipboard.js` | `@xterm/addon-clipboard` | **0.2.0** | `lib/addon-clipboard.js` | `012702420ba625606c8681e4b5943471` |

**取证方式**：每个文件都取自对应版本 npm tarball 的原始产物，`md5sum` 与仓内文件一致。
升级前的 5.5.0 全套留有备份（`work/probe/vendor/backup-5.5.0/`，gitignore，不入库），
需要二分时可比对。

**为什么 addon 版本不能凭印象写**：这批文件此前**没有任何版本标记**（压缩产物里找不到
`VERSION`、包名或版本串），一旦上游发新版无从判断本机跑的是哪版，出问题也无法二分。
本表的每个版本号都是把候选版本的 npm tarball 逐个解出、与本地文件 md5/正文比对得出的
—— 不是从 registry 元数据抄的（registry 只说"有这些版本"，不说"你这个文件是哪版"）。

## 为什么这一版改用未压缩产物（+350KB 是有意付的代价）

5.5.0 时 `xterm.js` / `xterm.css` / `fit.js` 取自 jsDelivr 的**压缩**产物，
6.0 这批全部改取 npm tarball 的**未压缩** `lib/` 原始产物。原因与代价：

- 收益一：**可审计**。压缩产物里搜不到 `clearTextureAtlas`、`onContextLoss` 这类符号，
  排障时无法确认"这个 API 到底在不在"（本次正是靠未压缩产物确认 6.0 仍带这两个 API，
  才敢保留项目里的图集止血线与上下文自愈）。同一条取证对 5.5 也成立。
- 收益二：**md5 可直接对账**，不再有"去头注释"这种每次都要重做的手工步骤。
- 代价：整栈从 ~470KB 涨到 ~880KB（`xterm.js` 290KB→489KB、`addon-webgl.js`
  101KB→248KB、`addon-unicode11.js` 12KB→52KB、`addon-search.js` 12KB→79KB）。
  本项目是**局域网内网自用**场景，首屏只加载这些文件一次、可长期缓存，换来可审计性划算。
  若将来上公网或在意首屏，应改走 minify（并把 minifier 及其版本一并登记，否则会重新
  掉回"文件里没有版本标记"的老坑）。

## 为什么删掉了 addon-canvas（v0.13.60）

三条实测证据，不是拍脑袋：

1. `addon-canvas@0.7.0` 的 `peerDependencies` 是 `{"@xterm/xterm": "^5.0.0"}`
   —— **不覆盖 6.0**。硬挂着只会得到一个不工作的终端。
2. `grep -c CanvasRenderer` 在 xterm **6.0.0 核心与 5.5.0 核心都是 0** —— canvas
   渲染器从来就不在核心里（`grep _canvasWidth` 命中的是 DOM 渲染器的尺寸字段，无关）。
   也就是说 canvas 一直只是个第三方 addon，核心没给它开后门。
3. 上游没有发布 canvas 的 6.0 兼容版：registry 上 `addon-canvas` 的 latest 就是 0.7.0。

代价要讲清楚：**canvas 回落这条中间档没了**，webgl 挂不上就直接落 DOM 渲染（慢，不白屏）。
本机无物理 GPU 的场景会因此变慢 —— 但真要修，正确做法是让 webgl 在无 GPU 时也别挂，
而不是留一个装不上的包。

## 重新取一份的流程（本机可复现）

```bash
# 走国内镜像（AGENTS.md §5：GitHub 直连超时，npm 需镜像）
curl -sO https://cdn.npmmirror.com/packages/@xterm/addon-webgl/0.19.0/addon-webgl-0.19.0.tgz
tar xzf addon-webgl-0.19.0.tgz
cmp package/lib/addon-webgl.js static/vendor/addon-webgl.js   # 一致才算引入成功
```

要点：

1. **取 `lib/` 里的未压缩原始产物**。不要取 CDN 另存版：jsDelivr 会在文件头插 5 行注释
   （`Skipped minification because …` / `Minified by jsDelivr …`），既让 md5 永远对不上，
   也让"这文件是哪版"更难查（5.5.0 那批就是这么混进来的）。
2. 候选版本要按 **semver 降序扫稳定版**（`^\d+\.\d+\.\d+$`）。xterm 的 registry 里
   混着几百个 `0.20.0-beta.*`，只看 registry 返回的"最后 30 个版本"会全落在 beta 上，
   永远扫不到真正的稳定版。
3. **`.map` 文件没进仓**。源码映射指向的 `.map` 不在 git 里，浏览器 devtools 会报
   404，属已知取舍（终端栈未压缩产物近 900KB，映射文件另有 4MB+，不值得入库）。
4. addon 的 `dependencies` 要**逐个判**：`addon-clipboard@0.2.0` 声明依赖 `js-base64`，
   但它的 `lib/addon-clipboard.js` 已由 webpack 内联（实测 `grep 3.7.8` 命中），
   所以不需要额外引入文件。判断依据是产物里有没有 `define("js-base64")` 这类外部引用，
   不是看 package.json。

## 与代码的耦合点（改这些前先读）

- **canvas 分支已删除**（v0.13.60）：`03-agents-cards.js::termLoadRenderer` 的回落链现在
  只有 `webgl → dom`。**若将来再引入 canvas addon，必须同步恢复那里的分支**，否则
  `?term=canvas` 与老链接会静默落到 dom（现在会 warn，但白屏风险在恢复分支前是零）。
  这条耦合是双向的，别只改一边。
- **`?term=webgl|dom` 查询参数**：可在 webgl / dom 之间手切（webgl 上下文丢失时的
  逃生口，v0.13.57 加的渲染器自愈）。`?term=canvas` 现在**显式降级 dom 并打 warn**，
  而不是静默改写 —— 留着旧 URL 的人要能看见"这条后门没了"。
- **Unicode11 必须在 `open` 之前挂并激活 `activeVersion='11'`**（cell 宽度表在渲染器
  初始化时固化，open 之后再改不重算）。
- addon 与 `@xterm/xterm` 版本**严格对齐**（v0.13.54 的引入约定）：升级要整组升，
  不要只升其中一个。当前这一组是 6.0.0 + webgl 0.19.0 / fit 0.11.0 / search 0.16.0 /
  unicode11 0.9.0 / web-links 0.12.0 / clipboard 0.2.0。
