#!/usr/bin/env python3
"""抽屉无障碍真渲染闸门（L2 live）：P1-21。

跑法（先起影子实例，worktree 无 .env，端口/数据与生产隔离）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 HUB_WRITE_TOKEN=probe-token \
      ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 & )
    HUB_PROBE_CDP_PORT=9396 venv/bin/python tests/verify_overlay_a11y.py http://127.0.0.1:3199

为什么必须真渲染（AGENTS.md 铁律「后端体检全绿不能证明前端正常」，且
「elementFromPoint 是静态快照，只回答此刻谁在最上面，测不出动作之后残留了哪一层」）：
P1-21 的三个缺陷静态全绿、语法合法、控制台零报错：
  ① Esc 关不干净（全局出口只调 closeDetail，skillDocDrawer 没接）
  ② 焦点在抽屉内输入位时 Esc 整体失效（editing 早退排在抽屉判定之前）
  ③ 抽屉打开后焦点仍在被遮住的页面上，Tab 跑得出去
这三条只能靠**真键盘事件**（Input.dispatchKeyEvent）暴露：JS 合成 KeyboardEvent
的 isTrusted=false，且不会触发 CDP 层面的默认焦点行为。静态正则锁不住"Esc 到底
有没有生效"，本探针锁。

判据全部是可断言的量：
  O1 抽屉可开（真鼠标点侧栏实体）
  O2 打开后焦点落在抽屉内（document.activeElement 归属抽屉子树）
  O3 正文 inert 生效（mainWrap.inert === true）
  O4 aria-modal=true
  O5 真 Esc 键关掉抽屉（dispatchKeyEvent，不是 JS 合成）
  O6 关闭后焦点归还给开启者（activeElement 回到点开它的那个按钮）
  O7 Tab 循环不逃出抽屉（连按 Shift+Tab 若干次仍在抽屉内）
  O8 焦点在抽屉内输入位时 Esc 仍生效（回归锁：这是改动前失效的那条）
  O9 两个抽屉都验一遍（detailDrawer + skillDocDrawer）
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9396"))
H = 900
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-54s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:170]))


ESC = {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27,
       "nativeVirtualKeyCode": 27}
ESC_UP = dict(ESC, type="keyUp")


def key(c, k):
    c.send("Input.dispatchKeyEvent", **k)
    time.sleep(0.35)


def esc(c):
    key(c, ESC)
    key(c, ESC_UP)


def in_drawer(c, did):
    return c.eval("(function(id){var d=document.getElementById(id);"
                  "return !!(d&&d.classList.contains('on')&&d.contains(document.activeElement));})"
                  "(%s)" % json.dumps(did))


def main():
    profile = tempfile.mkdtemp(prefix="ovl-a11y-")
    proc = launch_chrome(BASE + "/", CDP_PORT, profile, 1280, H)
    try:
        # 点侧栏实体可能触发 termToken() 的原生口令弹窗（alert/prompt）——
        # 原生弹窗会挂住 renderer，之后任何 send 都超时（09-23 探针栽过同一个坑）。
        # on_event 回调里把它关掉，不让探针变成"卡死"而不是"断言失败"。
        def on_dialog(method, params):
            try:
                c.send("Page.handleJavaScriptDialog", accept=True, promptText="")
            except Exception:  # noqa: BLE001
                pass

        c = CDP(page_target(CDP_PORT, tries=50), on_event=on_dialog)
        nav = "location.href=%s" % json.dumps(BASE + "/")
        for _ in range(40):
            try:
                c.eval(nav)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        for _ in range(40):
            try:
                if c.eval("document.readyState") in ("interactive", "complete"):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        time.sleep(3)

        # 开启者取侧栏第一个实体按钮（真鼠标点，焦点才真的落在它身上 ⇒ O6 才有意义）。
        # 注意：点侧栏实体多数走 gotoChat 进工作台而**不开**详情抽屉（见 06 openEntity），
        # 所以抽屉用 showDetail/openOverlay 开启；「开启者」仍是这个真点过的侧栏按钮。
        # 开启者必须是**纯 detail 实体**（无 embed/term/chat）。踩过的坑：点 claude 会
        # 同步挂进 iframe（embed entry），mouseReleased 直接无应答 —— 那是 09-25
        # 已知的 embed 宿主行为，与本闸门无关，但会让探针变成"卡死"而非"断言失败"。
        # 故动态挑一个 entries 只含 detail/open 的实体，找不到才退回 JS 聚焦。
        raw = c.eval("""(() => {
            const pick = AGENTS.find(a => {
                const es = (a.entries || []).map(e => e.type);
                return es.includes('detail') && !es.includes('embed')
                    && !es.includes('term') && !es.includes('chat');
            });
            if (!pick) return 'null';
            const sel = '#navTree .nav-item[data-entity="' + pick.id + '"]';
            let b = document.querySelector(sel);
            if (!b) return 'null';
            /* 折叠组里的行 getBoundingClientRect 全 0 ⇒ 真鼠标点不到（量到的是
               0×0 的折叠容器）。先把所在手风琴展开，再量坐标。 */
            const acc = b.closest('.nav-acc');
            if (acc && !acc.classList.contains('open')) {
              const head = acc.querySelector('.nav-acc-head');
              if (head) head.click();
            }
            b = document.querySelector(sel);
            b.scrollIntoView({block:'center'});
            const r = b.getBoundingClientRect();
            if (!r.width || !r.height) return 'zero-rect';
            return JSON.stringify([b.dataset.entity, Math.round(r.left+r.width/2), Math.round(r.top+r.height/2)]);
        })()""")
        if not raw or raw == "null":
            chk("O0 侧栏可点实体", False, "没找到侧栏实体按钮")
            return 1
        eid, x, y = json.loads(raw)
        # 真鼠标点：按下的瞬间 openEntity 就把抽屉开了（委托走 showDetail），
        # released 必须照发，否则探针量到的是"按下未释放"的半成品。
        c.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1, timeout=8)
        c.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1, timeout=8)
        time.sleep(2.0)
        chk("O0b 真鼠标点已触发抽屉（开启者=" + eid + "）",
            c.eval("document.getElementById('detailDrawer').classList.contains('on')"), "")

        D = "detailDrawer"
        chk("O1b 抽屉内实体名已渲染",
            bool(c.eval("(document.getElementById('detailTitle')||{}).textContent")),
            c.eval("(document.getElementById('detailTitle')||{}).textContent"))

        chk("O2 打开后焦点落在抽屉内", in_drawer(c, D),
            c.eval("document.activeElement && (document.activeElement.id||document.activeElement.tagName)"))
        chk("O3 正文 inert 生效", c.eval("document.getElementById('mainWrap').inert === true"), "")
        chk("O4 aria-modal=true",
            c.eval("document.getElementById('%s').getAttribute('aria-modal')" % D) == "true", "")

        # O7 Tab 循环：连按 Shift+Tab 五次，焦点不许逃出抽屉
        esc_before = in_drawer(c, D)
        for i in range(5):
            key(c, {"type": "rawKeyDown", "key": "Tab", "code": "Tab",
                    "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "modifiers": 8})
            key(c, {"type": "keyUp", "key": "Tab", "code": "Tab",
                    "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "modifiers": 8})
        chk("O7 Shift+Tab×5 不逃出抽屉", esc_before and in_drawer(c, D),
            c.eval("document.activeElement && (document.activeElement.id||document.activeElement.tagName)"))

        # O5 真 Esc 关闭
        esc(c)
        time.sleep(0.8)
        chk("O5 真 Esc 键关闭抽屉",
            not c.eval("document.getElementById('%s').classList.contains('on')" % D), "")
        chk("O6 关闭后焦点归还开启者",
            c.eval("document.activeElement && document.activeElement.closest('#navTree') !== null"),
            c.eval("document.activeElement && (document.activeElement.id||document.activeElement.tagName)"))
        chk("O3b 关闭后正文解除 inert",
            c.eval("document.getElementById('mainWrap').inert === false"), "")

        # ── O8 回归锁：焦点落在抽屉内的输入位时，Esc 仍须生效 ──
        # 改动前 editing 早退排在抽屉判定之前 ⇒ 这条必失败。
        c.eval("openOverlay('skillDocDrawer')")
        time.sleep(1.2)      # 等 openOverlay 的 setTimeout 聚焦落定（见下方注释）
        # 注入输入位后聚焦。**必须等 openOverlay 的 setTimeout(…,0) 落定再聚焦**：
        # 那次聚焦是异步的，先注入先 focus 会被它覆盖回关闭按钮（O8a 曾因此假红，
        # active=BUTTON inside=False）。等 1.2s 后再动焦点即稳定。
        c.eval("""(() => {
            const d = document.getElementById('skillDocDrawer');
            let i = d.querySelector('input,textarea,select');
            if (!i) { i = document.createElement('input'); d.appendChild(i); }
            i.id = 'probeEdit';
        })()""")
        c.eval("document.getElementById('probeEdit').focus()")
        time.sleep(0.5)
        c.eval("document.getElementById('probeEdit').focus()")   # 再确认一次，压掉任何残留异步聚焦
        time.sleep(0.3)
        time.sleep(0.4)
        S = "skillDocDrawer"
        chk("O8a 焦点已在抽屉内输入位", in_drawer(c, S),
            "active=%s inside=%s on=%s" % (
                c.eval("document.activeElement && (document.activeElement.id||document.activeElement.tagName)"),
                c.eval("document.getElementById('%s').contains(document.activeElement)" % S),
                c.eval("document.getElementById('%s').classList.contains('on')" % S)))
        esc(c)
        time.sleep(0.8)
        chk("O8b 输入位状态下 Esc 仍关闭抽屉（改动前失效）",
            not c.eval("document.getElementById('%s').classList.contains('on')" % S), "")

        # ── O9 技能正文抽屉也接 Esc（改动前键盘完全关不掉）──
        c.eval("openOverlay('skillDocDrawer')")
        time.sleep(0.8)
        chk("O9a skillDocDrawer 已开",
            c.eval("document.getElementById('%s').classList.contains('on')" % S), "")
        esc(c)
        time.sleep(0.8)
        chk("O9b skillDocDrawer 可被真 Esc 关闭",
            not c.eval("document.getElementById('%s').classList.contains('on')" % S), "")

        print("\n%d/%d PASS" % (sum(res), len(res)))
        return 0 if all(res) else 1
    finally:
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
