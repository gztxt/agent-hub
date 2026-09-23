# tests/ —— 三层口径（唯一真相源：`tests/tiers.py`）

本目录不是"一套测试"，是**三套**，混跑会把"全绿"变成谎报。分层前本仓只有一个数字
（`Ran 71 tests ... OK`），而其中相当一部分断言的是**这台 NAS 上恰好存在的目录形态**
—— 换台机器（或干净 CI runner）它们必然红，于是"能不能上 CI"这个问题一直没有答案。

| 层 | 判据 | 换机行为 | 数量（09-23） |
|---|---|---|---|
| **L0 hermetic** | 只依赖纯函数 / `tempfile` / AST 读源码。不读 `~/.claude` 等真盘、不起服务、不打网络、不 fork pty、**不 import `src.main`** | 结论必须一模一样；**出现 SKIP 即分层放错**，闸门判 FAIL（退出码 2） | 93 |
| **L1 host** | 断言本机真实仓库形态（`~/.grok/sessions`、`~/.claude/projects`、`~/.jcode/sessions`、`~/.qoder/projects`、`~/.hermes/state.db`、`~/.codex/state_5.sqlite`、`/fs` 真目录） | 显式 `SKIP(host-dependent)` + 因果与解法，**绝不静默通过** | 21 |
| **L2 live** | 需要服务在跑：`verify_*.py`、`probe_*.py`（其中四个是**浏览器探针**：要本机 chromium，不需要服务，见下节） | 手动单跑；不被 `discover -p "test_*.py"` 收进来 | 12 |

## 怎么跑

```bash
bash scripts/run_tests.sh hermetic         # 只 L0，以"零跳过"为闸
bash scripts/run_tests.sh hermetic-clean   # L0 + 把 HOME 换成空目录 ⇒ 真模拟干净 runner
bash scripts/run_tests.sh all              # L0 + L1（本机全量）
bash scripts/run_tests.sh host             # 只 L1
bash scripts/run_tests.sh probe verify_p1_backend.py http://127.0.0.1:3199
venv/bin/python -m unittest discover -s tests -p "test_*.py"   # 老口径（97 例，L0+L1 混在一起）
```

强制开关：`HUB_HOST_TESTS=1|0` 覆盖自动探测（探测判据＝上面六个宿主路径是否**全部**存在）。

## 新测试该放哪一层

1. 能用 `tempfile` 造出前提 ⇒ **L0**（首选）。例：`test_staticguard.py` 全部在临时目录里造
   `static/`、兄弟目录 `static_evil/`、软链、`.bak` 件。
2. 必须读这台机器的真实仓库 ⇒ **L1**，打 `@tiers.host_only`（class 或 method 都行）。
3. 需要一个在跑的 hub ⇒ **L2**，命名 `verify_*.py` / `probe_*.py`，`HUB_BASE`/首参给地址，
   并**自带红-绿对照**（同一份探针打改前/改后两棵树，别只给一个绿灯）。

## 两条硬规矩（都对应过真实事故）

- **不 import `src.main`**：它一 import 就跑 lifespan —— 开真库 `data/agents.db`、起后台
  探针任务、绑端口。单测碰它等于碰生产数据。需要测路由里的判据时，把判据抽成纯函数
  （见 `src/staticguard.py`：正是为此从路由里抽出来的）。
- **不靠 `skipTest` 蒙过 L0**：L0 的 `skip` 在干净 runner 上意味着"这条什么都没测却报绿"。
  能力探测失败就退化成一条仍然成立的断言（`test_staticguard.py` 里软链两例就是这么写的），
  或者干脆打上 host 标。

## 为什么 L2 探针是必需的，不是"手工验证"

`test_*.py` 看不见需要两条并发 WS 才显形的缺陷。P1-5 就是例子：`Session.outputs` 曾是
**单个** `asyncio.Queue`，每条 WS 的 pump 在同一队列上 `get()` ⇒ 桌面 + 手机同看一条会话时
两个消费者互相偷字节。`verify_p1_backend.py` 对改前/改后两棵影子树的实测：

```
改前 :3197   观看者A 实收 30 / 观看者B 实收 90（共 120 条被劈开，集合互斥）  → FAIL
改后 :3199   观看者A、B 各 120 且 A-B=[] B-A=[]；hb 回执 {"type":"hb","t":…}   → PASS
静态闸门     .bak 200→404；/static/../static_evil/secret.txt 200(CANARY)→404；304 现带 Vary
```

## 前端解析层的缺陷只能拿真浏览器测：`verify_replay_gate.py`

`test_*.py` 跑在 node/venv 里，看不见 xterm 的**自动应答**行为。这类缺陷的真实形状：
回放帧里夹着上一个 TUI 发过的终端查询，xterm 替终端作答，答案顺 `onData` 灌进 pty，
shell 不认就原样回显 —— 用户看到的是「白屏 + 一串数字字母乱码」。

这个探针有三条硬规定，缺一条它就会给出假绿灯：

1. **抽真代码，不抄示例**：用正则从 `static/hub.js` 里原样抽出 `TERM_QUERY_OSC` 与
   `function termWriteReplay(){…}` 拼进测试页。谁把 DCS 那行删了探针就红；
   手抄一份「我以为的实现」则删真代码也照样绿。
   （抽取按**第 0 列的 `}`** 收尾，不做花括号配对 —— 注释里含 `{prefix:"?",final:"c"}`，
   朴素配对会被注释里的 `}` 提前闭合，截到看不见 DCS 那行，第一版就栽在这。）
2. **对照组必须在同一份产物里**：先不设防写一次（期望**有**应答包），再经闸门写一次
   （期望**无**）。只有"红绿同时在场"才排除掉"解析器被别的东西闷掉了"这种假改善。
3. **线序按字节而不是按文档**：DECRQPS 的中间码只有一个 `$`；照文档写成 `DCS $ q`
   （带空格）时 intermediates 变成 `"$ "`，压根匹配不上处理器，
   于是会得到「xterm 不答 DECRQPS」的**假结论** —— 本探针第一版就是这么错的，
   是它自己的 FAIL 把真代码里被覆盖丢失的那行暴露出来。

```
$ venv/bin/python tests/verify_replay_gate.py
  对照组（不设防）xterm 回了：  "P1$r0"q\"  "P1$r1;10r\"  "[?1;1R"
  实验组（经 termWriteReplay）回了： （空）
  PASS  对照组确实会作答（DECRQPS 两类都答）   [实得 2 个 DCS 应答包]
  PASS  对照组 DSR 也作答（证明 onData 通路活着）
  PASS  实验组零应答（闸门真的吞掉了）   [实得 0 包：[]]
  全部通过 ✅   退出码 0
```


## 浏览器探针（四个）：前端解析层的缺陷只有真引擎能作证

共同硬规定（三条都是踩出来的，缺一就变成"自己说好了"）：

1. **被测代码从 `static/hub.js` 原样抽取**（`tests/_hub_extract.py`），不手抄 —— 手抄的
   副本会在重构时静默失联，抽取版则"函数被改名/删行"直接把探针判红。
2. **对照组与实验组必须在同一份产物里**：每个探针都同时跑"改前写法"与"改后写法"，
   并断言改前**确实坏**。只证明改后能用 = 零信息（可能那条件根本不触发）。
3. **测线上字节，不测文档式间距**：`ESC P $ q "q ESC \` 里 `$` 后带空格，intermediates
   就成了 `"$ "` 而非 `"$"` —— 按 README 里的写法造样本会造出一个永不匹配的探针。

| 探针 | 钉住的缺陷 | 对照组实测 | 实验组实测 |
|---|---|---|---|
| `verify_replay_gate.py` | 回放历史时替终端作答查询码：ring 里躺着的 `DECRQPS`（`DCS $ q`）会被 xterm 应答并**注回 pty**，成为可见垃圾。改前的闸门只列 DA1/DA2，**DA3  `{prefix:'?',final:'c'}` 在本机 vendor 版里根本不存在**（grep 实测），真正漏的是 DCS 族 | 直接 write → xterm 回 2 个 DCS 包 + DSR | 经 `termWriteReplay` → **0 包** |
| `verify_term_heal_viewport.py` | 白屏自愈按**缓冲区绝对行**数空行：`getLine(0)` 是 scrollback 最老一行，有历史时读到的是早已滚出屏幕的内容 ⇒ 判"画面不空"⇒ **自愈在长会话里永不触发** | 60 行历史 + ED2 清屏：旧口径 blank=**0**（被误抑制） | 同一现场新口径 blank=10/10（会自愈）；无 scrollback 时两口径一致（防回归） |
| `verify_stream_decode.py` | 每帧 `new TextDecoder()`：UTF-8 多字节被 WS 帧劈开时**必然**吐 U+FFFD；`[?1003h` 跨帧时两帧都匹配不上正则 ⇒ `termMouseLive` 记不下 ⇒ 滚轮上报被闸门吃掉 | '中文终端输出…' 在第 2 字节劈帧 → `'??文终端输出…'`；DECSET 跨帧逐帧扫 → `termMouseLive=false` | 共用解码器 + 24 字符尾巴 → 逐字相等 / `true` |
| `verify_key_focus_guard.py` | 全局 keydown 无 target 守卫：终端里敲 `/` 被 `preventDefault` + 焦点跳搜索框；`term.focus()` 无条件执行 ⇒ 手机自动挂载必弹软键盘 | 真事件冒泡到 document 且 `e.target.closest('.xterm')` 命中（**证明旧写法必劫持**） | `keyTargetIsEditing` 8 个输入位 true / 2 个普通元素 false；`termFocusWanted` 只认 `user:true` |

对应的**结构性**护栏（不需要浏览器、进 L0 常规跑）在 `tests/test_term_focus_policy.py`：
它守的是"位置"而非"存在" —— `term.focus()` 只能出现在 `termFocusWanted(opts)` 之后同一行、
`if (editing) return` 必须排在 `/` 与 Ctrl+K 分支之前、四个用户主动入口与两个自动路径的
`user:true` 数量与身份钉死。变异检验已做：把守卫挪到分支之后 / 给自动挂载补 `user:true`，
两条各自变红。


## 生产体检：`verify_prod_smoke.py`（重启后出结论用的就是它）

09-23 用户裁定「探活最多 2 次即停」之后，探针的设计口径变了：**一轮取齐全部证据**，
而不是一个端点一个端点串行试。这个脚本对真生产做 19 项复合检查（响应码 + 时间戳 +
`/health` 语义字段 + 头大小写 + WS 业务关闭码 + 日志异常计数），**且刻意不建 pty 会话**
—— 重启后跑它不会打断任何在用的终端。

三条实测出来的使用注意（都是本探针第一版自己踩的）：
1. 响应头必须**大小写不敏感**地读：uvicorn 发的是小写 `vary`，`dict(r.headers).get("Vary")`
   恒为 None，会把一条正确实现误判成 FAIL；
2. 两个终端端点外层各包一级（`{"sessions":[…]}` / `{"agents":[…]}`），按裸列表断言必错；
3. WS 关闭码用 `recv()` 超时后读 `ws.close_code`；
4. chat 端点的回复字段是 **`response`**（实测顶层键：`agent_id/session_id/message/timestamp/success/agent/response/model/usage`）—— 按 `reply`/`text` 读会把一条成功的真 LLM 回复判成空字符串（照抄 `verify_p1_backend.py` 的可用取法，
   别在第二个文件里发明第二种）。

`--no-chat` 可跳过那一次真 LLM 调用；不带则该脚本共 19 项。


## `verify_write_gate.py`：一条**只能有一半在生产上跑**的探针

写端点闸门（32 条被拒 / 2 条自带鉴权）的取证有个硬约束：**"带凭据逐条打写端点"这一腿
在生产上做不得**。09-23 就是拿匿名空 body 打生产做红基线，`POST /api/memory/l2/rebuild`
回 200 并把用户手写的 L2 记忆重写了（已按 09-06 在册副本逐字回滚）。所以本探针分两模式：

- `--safe`：只打无凭据那一腿。401 由中间件在 handler **之前**返回 ⇒ 可证明零副作用，生产可跑。
- 默认（影子树）：无凭据 401 + **带凭据非 401** 两腿都打。缺后一腿就是假绿 ——
  一个把所有请求一律 401 的闸门同样能让 `--safe` 全绿。

影子树起法与隔离核验（`fd` 指向自己的 DATA_DIR、指向真库必须 0）：

```bash
mkdir -p /tmp/ah-gate/data /tmp/ah-gate/log && cp -r src static templates /tmp/ah-gate/
cd /tmp/ah-gate && DATA_DIR=... TERM_TOKEN=gate-shadow-token nohup <repo>/venv/bin/python -m uvicorn src.main:app --port 3197 &
```
不复制 `.env` ⇒ 凭据只来自命令行，影子树里根本没有生产口令。

两处期望是**故意放宽**的（放宽方向经过判断，不是将就）：
`/api/settings/term-token` 在未配 `HUB_PASSCODE` 时返 503 —— 那是 fail-closed 的正确行为，
记成失败会诱导后人"为了让探针绿去给影子塞口令"；`/telemetry/events/{source}` 空 body 先撞
Pydantic 校验返 422，其鉴权在 handler 内（`hook.py` 的 Bearer/回环方案），不归本闸门。
