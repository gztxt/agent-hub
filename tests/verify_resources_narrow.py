#!/usr/bin/env python3
"""资源页真渲染闸门（L2 live）：P1-14/15/16 的窄屏取证。

跑法（先起影子实例，worktree 里没有 .env，端口/数据与生产隔离）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 HUB_WRITE_TOKEN=probe-token \
      ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 & )
    HUB_PROBE_CDP_PORT=9395 venv/bin/python tests/verify_resources_narrow.py http://127.0.0.1:3199

为什么必须是**真渲染**（AGENTS.md：「后端体检全绿不能证明前端布局正常」）：
P1-14/15/16 三个缺陷的共同特征是「语法合法 + 页能开 + 控制台零报错 + 后端全绿」，
只有真把页面摆到 390px 视口里量 DOM 才会暴露：
  · var(--primary) 之类未定义变量 ⇒ 徽章底色解析失败回退透明（截图能看出来，断言看不出来）
  · .btn.xs 不存在 ⇒ 按钮无样式（要断言 computed style，不是看有没有这个 class）
  · max-width:400px ⇒ 390px 视口 scrollWidth > clientWidth（横向溢出）

判据全部是可断言的量，不以截图交差：
  R1 资源页能进、卡片渲染出来
  R2 徽章 computed background-color 不是透明（alpha=0）
  R3 「结束/强杀」按钮 computed 有可见底色/边框，不是裸文字
  R4 五档视口（320/390/768/1280/1920）断言 scrollWidth == clientWidth（无横向溢出）
  R5 视口中心点 elementFromPoint 归属在页面内（没有被遮罩整块盖住）
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) >1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9395"))
WIDTHS = [320, 390, 768, 1280, 1920]
H = 900
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:170]))


def main():
    profile = tempfile.mkdtemp(prefix="res-narrow-")
    proc = launch_chrome(BASE + "/", CDP_PORT, profile, 1280, H)
    try:
        c = CDP(page_target(CDP_PORT, tries=50))
        # 首次导航：默认执行上下文在页面 commit 前不存在，eval 会报
        # "Cannot find default execution context" —— 重试到上下文就绪为止。
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
        time.sleep(2)

        # 进资源页：侧栏点「资源」（真鼠标）
        hit = None
        raw = c.eval("""(() => {
            const b = document.getElementById('btnNavResources');
            if (!b) return 'null';
            const r = b.getBoundingClientRect();
            return JSON.stringify([Math.round(r.left+r.width/2),
                                   Math.round(r.top+r.height/2)]);
        })()""")
        if raw and raw != "null":
            x, y = json.loads(raw)
            c.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            c.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
            hit = (x, y)
        time.sleep(4)

        on = c.eval("document.getElementById('page-resources') ? "
                    "getComputedStyle(document.getElementById('page-resources')).display : 'none'")
        chk("R1a 资源页已激活", on not in ("none", "null"), "display=%s" % on)

        ncard = c.eval("document.querySelectorAll('#resList .agent-card').length")
        chk("R1b 资源卡片渲染", ncard > 0, "cards=%s" % ncard)

        # R2 徽章底色非透明：逐个徽章量 computed
        badge = c.eval("""(() => {
            const bs = [...document.querySelectorAll('#resList .s-badge')];
            if (!bs.length) return JSON.stringify({n:0});
            const bad = [];
            bs.forEach(b => {
                const bg = getComputedStyle(b).backgroundColor;
                const m = bg.match(/rgba?\\(([^)]+)\\)/);
                const parts = m ? m[1].split(',').map(s => parseFloat(s)) : [];
                const alpha = parts.length === 4 ? parts[3] : 1;
                if (!alpha) bad.push(bg);
            });
            return JSON.stringify({n: bs.length, bad: bad.slice(0,3)});
        })()""")
        b = json.loads(badge or "{}")
        chk("R2 徽章底色非透明", b.get("n", 0) > 0 and not b.get("bad"),
            "bad=%s n=%s" % (b.get("bad"), b.get("n")))

        # R3 前置：真鼠标点第一张卡片头展开进程列表。
        # 折叠态下按钮在 display:none 的容器里，宽高恒为 0 —— 那不是缺陷，
        # 直接量会得到「按钮零尺寸」的假结论（我第一版就踩了这个）。
        hdr = c.eval("""(() => {
            const e = document.querySelector('#resList .agent-header');
            if (!e) return 'null';
            const r = e.getBoundingClientRect();
            return JSON.stringify([Math.round(r.left+r.width/2), Math.round(r.top+r.height/2)]);
        })()""")
        if hdr and hdr != "null":
            hx, hy = json.loads(hdr)
            for t in ("mousePressed", "mouseReleased"):
                c.send("Input.dispatchMouseEvent", type=t, x=hx, y=hy, button="left", clickCount=1)
            time.sleep(2)

        # R3 按钮有可见样式 **且有实尺寸**（不是裸文字，也不是折叠态的 0×0）
        btn = c.eval("""(() => {
            const e = document.querySelector('#resList .btn.sm.danger');
            if (!e) return JSON.stringify({found:false});
            const s = getComputedStyle(e);
            const bg = s.backgroundColor, bd = s.borderTopWidth;
            const rc = e.getBoundingClientRect();
            return JSON.stringify({found:true, bg:bg, border:bd,
                                   w:Math.round(rc.width), h:Math.round(rc.height)});
        })()""")
        d = json.loads(btn or "{}")
        # 底色透明且无边框 = 无样式（裸文字）；.btn.sm 应有可见描边或底色
        styled = d.get("found") and d.get("w", 0) > 0 and d.get("h", 0) > 0 and (
            d.get("border", "0px") != "0px" or "rgba(0, 0, 0, 0)" != d.get("bg", ""))
        chk("R3 结束按钮有可见样式且有实尺寸", styled, json.dumps(d, ensure_ascii=False))

        # R4/R5 五档视口：横向溢出 + 中心点归属
        for w in WIDTHS:
            # 真视口必须走 CDP 设备模拟：JS 赋值 window.innerWidth 改不了
            # 视口宽度（读回来仍是旧值），那种"测过了"是假绿。
            c.send("Emulation.setDeviceMetricsOverride", width=w, height=H,
                   deviceScaleFactor=1, mobile=(w < 768))
            time.sleep(1.0)
            real = c.eval("String(window.innerWidth)")
            if int(real or 0) != w:
                chk("视口 %dpx 真生效" % w, False, "innerWidth=%s" % real)
                continue
            m = c.eval("""(() => {
                const de = document.documentElement;
                const e = document.elementFromPoint(Math.floor(window.innerWidth/2),
                                                    Math.floor(window.innerHeight/2));
                return JSON.stringify({
                    sw: de.scrollWidth, cw: de.clientWidth,
                    hit: e ? (e.id || e.className || e.tagName) : 'null'
                });
            })()""")
            d = json.loads(m or "{}")
            overflow = d.get("sw", 0) - d.get("cw", 1)
            # 允许 1px 取整误差
            chk("R4 %dpx 无横向溢出" % w, overflow <= 1,
                "scrollW=%s clientW=%s 溢出=%s" % (d.get("sw"), d.get("cw"), overflow))
            chk("R5 %dpx 中心点归属正常" % w,
                d.get("hit") not in (None, "null", "html", "body"),
                "hit=%s" % d.get("hit"))
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    ok = sum(res)
    print("\n资源页窄屏取证: %d/%d PASS" % (ok, len(res)))
    return 0 if ok == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
