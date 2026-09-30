#!/usr/bin/env python3
"""跨 Agent 活动指示真渲染闸门（L2 live）：P2-B。

跑法（先起影子实例；worktree 无 .env，端口/数据与生产隔离，务必 setsid 否则 shell 退出连带杀）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9408 ../agent-hub/venv/bin/python tests/verify_activity.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp   # 禁用 pkill -f "port 3199"，会连带杀自己

为什么必须真渲染：
「有活着的终端会话」这件事静态正则完全锁不住 —— 判据在**后端返回的会话列表**上
（s.alive），前端只做聚合与渲染，静态能验的只有"代码里写了 alive"，
验不了"真的亮起来 / 真的灭掉"。更关键的是本闸门锁的是两条**只有运行时才暴露**的缺陷：

  ① 被动指示触发口令弹窗（初版真缺陷）：loadActivity 走 termHeaders() → termToken()，
     无存档 token 时 prompt() 弹**原生口令框**。原生弹窗挂住 renderer，探针全部超时；
     产品侧更严重：活动指示是加载即执行的被动轮询，等于每个没配终端口令的访问者
     一进页面就被弹一次口令框（为看一个忙碌点逼人交密码）。
  ② 拉取失败把已有指示清零：ACTIVITY 每次失败被重置 ⇒ 瞬时网络抖动让顶栏指示
     整片消失（"没人干活了"的假象），比不显示更误导。

判据全部是可断言的量：A1/A2 零会话零占位、A3/A4 顶栏文案与蓝点实色、
A5/A6 侧栏点与尺寸、A7/A8 多会话聚合（Agent 数 ≠ 会话数）、A9 失败不清零、
A10/A11 会话结束后两处同步消失。

踩过的坑（改这几行前先读，别"顺手清理"）：
  - **token 必须在 Page.reload 之后写 localStorage**。headless 用的是每次新建的
    临时 profile，reload 之前的写入实测读回 None ⇒ ACTIVITY 恒空 ⇒ 指示恒不亮，
    看着像"功能没做"（本闸门初版因此 6/11）。
  - **c.eval 不等 Promise**：本仓 _cdp_min.eval 未开 awaitPromise，eval("loadActivity()")
    后必须 time.sleep 等 Promise 落地再读值，否则读到旧值假红。
  - **造/删会话走 urllib 直连后端**，不要调前端 api()：本闸门 A9 要把 api 打成抛错，
    用它造数据会把自己的证据毁掉；且能避免依赖前端凭据。
  - **termToken() 的原生 prompt() 会挂 renderer**（见上面①）。这里既让产品侧绕开它
    （只读 lsGet('hub.term.token')，缺 token 安静跳过），探针侧也留 on_dialog 兜底，
    保证即便回归成弹窗，探针表现为"断言失败"而不是"卡死"。
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9408"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
H = 900
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-46s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:150]))


def _req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, method=method, data=data,
                               headers={"x-term-token": TERM_TOKEN,
                                        "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))


def mk_session(agent_id="claude"):
    return _req("/api/term/sessions", "POST", {"agent_id": agent_id})["session"]["id"]


def rm_session(sid):
    try:
        urllib.request.urlopen(urllib.request.Request(
            BASE + "/api/term/sessions/" + sid, method="DELETE",
            headers={"x-term-token": TERM_TOKEN}))
    except Exception:  # noqa: BLE001
        pass


def wait_ready(c, tries=40):
    for _ in range(tries):
        try:
            if c.eval("document.readyState") in ("interactive", "complete"):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


def hbusy(c):
    return c.eval("document.getElementById('hBusy').textContent.trim()") or ""


def main():
    profile = tempfile.mkdtemp(prefix="act-")
    proc = launch_chrome(BASE + "/", CDP_PORT, profile, 1280, H)
    sid = sid2 = None
    try:
        def on_dialog(method, params):  # noqa: ARG001
            try:
                c.send("Page.handleJavaScriptDialog", accept=True, promptText="")
            except Exception:  # noqa: BLE001
                pass

        c = CDP(page_target(CDP_PORT, tries=50), on_event=on_dialog)
        c.send("Page.enable")
        wait_ready(c)
        # ignoreCache 必须开：AGENTS.md 记过「?v= 提手对端侧缓存无效」——
        # 静态资源 ETag 由 mtime+size 算出，不同 ?v= 共用同一 ETag，条件请求会 304。
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        wait_ready(c)
        # 顺序反了就读不到（见头注释「token 必须在 reload 之后写」）
        c.eval("localStorage.setItem('hub.term.token',%s)" % json.dumps(TERM_TOKEN))
        wait_ready(c)
        time.sleep(3)   # 让启动期的 loadActivity() 首轮落地，此时应为空

        chk("A1 零会话时顶栏不占位", hbusy(c) == "", repr(hbusy(c)))
        chk("A2 零会话时侧栏无忙碌点",
            c.eval("document.querySelectorAll('#navTree .nav-busy').length") == 0,
            c.eval("document.querySelectorAll('#navTree .nav-busy').length"))

        sid = mk_session("claude")
        time.sleep(1)
        c.eval("loadActivity()")
        time.sleep(2.5)
        chk("A3 有会话时顶栏出现「在跑 1」", "在跑 1" in hbusy(c), repr(hbusy(c)))
        chk("A4 顶栏蓝点有实色", c.eval("""(() => {
            const d = document.querySelector('#hBusy .hdot');
            if (!d) return 'no-dot';
            const s = getComputedStyle(d).backgroundColor;
            return s && s !== 'rgba(0, 0, 0, 0)' ? s : 'transparent:' + s;
        })()"""))
        chk("A5 对应侧栏行出现忙碌点",
            c.eval("!!document.querySelector('#navTree .nav-item[data-entity=\"claude\"] .nav-busy')"))
        chk("A6 忙碌点有实尺寸", c.eval("""(() => {
            const d = document.querySelector('#navTree .nav-busy');
            if (!d) return 'none';
            const r = d.getBoundingClientRect();
            return Math.round(r.width) + 'x' + Math.round(r.height);
        })()"""))

        sid2 = mk_session("claude")
        time.sleep(1)
        c.eval("loadActivity()")
        time.sleep(2.5)
        chk("A7 同一 Agent 两会话 → 顶栏显示会话数", "2 会话" in hbusy(c), repr(hbusy(c)))
        chk("A8 在跑 Agent 数仍是 1（不是会话数）", "在跑 1" in hbusy(c), repr(hbusy(c)))
        rm_session(sid2)
        sid2 = None

        # 失败不清零：把 api 打成抛错
        c.eval("window.__a = api; api = async function () { throw new Error('x'); };")
        c.eval("loadActivity()")
        time.sleep(2.0)
        chk("A9 拉取失败不清零（沿用上次值）", "在跑 1" in hbusy(c), repr(hbusy(c)))
        c.eval("api = window.__a")

        rm_session(sid)
        sid = None
        c.eval("loadActivity()")
        time.sleep(2.5)
        chk("A10 会话结束后指示消失", hbusy(c) == "", repr(hbusy(c)))
        chk("A11 侧栏忙碌点同步消失",
            c.eval("document.querySelectorAll('#navTree .nav-busy').length") == 0)

        print("\n%d/%d PASS" % (sum(res), len(res)))
        return 0 if all(res) else 1
    finally:
        for s in (sid, sid2):
            if s:
                rm_session(s)
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
