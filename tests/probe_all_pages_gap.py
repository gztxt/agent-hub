#!/usr/bin/env python3
"""全站留白一致性探针（v0.13.97 追加）。

【为什么必须逐页量】用户否掉第一版的原话：「点左边菜单栏的其他菜单时右边页面边距
又变了，应该是全部统一才对」。第一版用 `main:has(> #page-chat.on)` 限定在终端页——
只在终端页量是**测不出**这个问题的，静态闸门也只查「规则存不存在」。
本探针**逐页量 main 的 computed padding + 实际内容盒位置**，判据是
「所有页的净留白完全相同」，这才能证伪「有的页留白没跟上」。

【怎么逐页】用页面自带的三件套，不自己发明：
  ① `lsSet('hub.page', id)` 写盘后 `location.reload()` —— 与 go() 同一真相源
     （go() 末尾就是 lsSet('hub.page', page)，启动时回读），避免手点侧栏漏项；
  ② 逐个 section.page 依次点亮，量**同一种容器**在各页的净留白。
     量的是 main 的 padding（全局）与「页内首个可见块到 main 内容盒边缘」的距离，
     后者才是用户眼里的「页面边距」。
"""
import json
import sys
import time

sys.path.insert(0, "/fs/1000/ftp/技术文档/agenthub/tests")
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = "http://127.0.0.1:3102"
CDP_PORT = 9395
WIDTHS = [390, 1440]

# 全部 section.page 的 id（运行时从 DOM 取，不在脚本里硬编一份——那是第二真相源）
LIST_PAGES = r"""
JSON.stringify([...document.querySelectorAll('section.page')].map(s => s.id))
"""

# 逐页量：main padding + 页内首个可见块的净留白
MEAS = r"""
JSON.stringify((() => {
  const px = v => Math.round(parseFloat(v) * 100) / 100;
  const mw = document.getElementById('mainWrap');
  const cs = getComputedStyle(mw);
  const mr = mw.getBoundingClientRect();
  const page = document.querySelector('section.page.on');
  if (!page) return JSON.stringify({err: 'no page.on'});
  // 页内「首个可见叶子块」：用户眼里的内容起点（跳过 .page 自身这层壳）
  const vis = el => { const b = el.getBoundingClientRect();
                      return b.width > 0 && b.height > 0; };
  let first = null;
  for (const el of page.querySelectorAll('*')) {
    if (!vis(el)) continue;
    if (el.children.length && [...el.children].some(vis)) continue;  // 有可见子 ⇒ 不是叶子
    first = el; break;
  }
  const fb = first ? first.getBoundingClientRect() : null;
  return JSON.stringify({
    page: page.id,
    padMain: {t: px(cs.paddingTop), r: px(cs.paddingRight),
              b: px(cs.paddingBottom), l: px(cs.paddingLeft)},
    firstVisible: first ? (first.id || first.className || first.tagName).toString().slice(0, 28) : null,
    gapTop: fb ? Math.round(fb.top - mr.top) : -1,
    gapLeft: fb ? Math.round(fb.left - mr.left) : -1,
    gapRight: fb ? Math.round(mr.right - fb.right) : -1
  });
})())
"""

EXPECT_WIDE = (4.8, 6.4)      # --main-pad-y / --main-pad-x
EXPECT_NARROW = (3.2, 3.2)


def _unpack(v):
    """把 CDP `returnByValue` 的返回值解成Python 结构。

    坑（两个方向都踩过，别再试）：
      · 页面里写 `JSON.stringify(obj)` ⇒ JS 返回**字符串** `"{\\"a\\":1}"`，
        CDP 对它再序列化一层 ⇒ Python 侧是 str 包 str，要 loads **两次**。
      · 页面里写 `JSON.stringify([...])` ⇒ JS 返回**字符串** `'["a","b"]'`，
        CDP 同样再包一层 ⇒ loads 一次得到 list，再 loads 就炸
        （'list' object has no attribute 'find'/'get'）。
    所以**循环 loads 直到结果不是 str**，两种形态一次认全。
    """
    for _ in range(4):
        if not isinstance(v, str):
            return v
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def run(w, port):
    """一档：起独立实例 → 逐页点亮并量。"""
    proc = launch_chrome("about:blank", port, "/vol1/tmp/hub_allpg_%d" % port, 1280, 900)
    cdp = CDP(page_target(port))
    try:
        cdp.send("Page.enable"); cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=900,
                 deviceScaleFactor=1, mobile=(w < 768))
        cdp.send("Page.navigate", url=BASE + "/")
        for _ in range(200):
            if cdp.eval("document.readyState") == "complete":
                break
            time.sleep(0.05)
        time.sleep(3.0)
        pages = _unpack(cdp.eval(LIST_PAGES))
        out = []
        for pid in pages:
            # 纯 DOM 点亮：只改 section.page 的 .on，不调 go()（go 会牵出侧栏/WS 等副作用）
            cdp.eval("(()=>{document.querySelectorAll('section.page')"
                     ".forEach(s=>s.classList.toggle('on', s.id==='%s'));return 1})()" % pid)
            time.sleep(0.35)
            out.append(_unpack(cdp.eval(MEAS)))
        return out
    finally:
        cdp.close()
        proc.terminate()


def main():
    port = CDP_PORT
    fails = []
    print("全站留白一致性 · base=%s" % BASE)
    for w in WIDTHS:
        port += 1
        exp = EXPECT_NARROW if w < 768 else EXPECT_WIDE
        print("\n== 视口宽 %d（期望 main padding %s/%s）==" % (w, exp[0], exp[1]))
        print("%-26s %-18s %-24s %s" % ("page", "main pad Y/X", "首个可见块", "净留白 T/L/R"))
        rows = run(w, port)
        pads = set()
        for d in rows:
            if d.get("err"):
                fails.append("vw=%d %s" % (w, d["err"]))
                continue
            p = d["padMain"]
            pads.add((p["t"], p["l"]))
            print("%-26s %-18s %-24s %d/%d/%d"
                  % (d["page"], "%s/%s" % (p["t"], p["l"]),
                     d["firstVisible"], d["gapTop"], d["gapLeft"], d["gapRight"]))
            if (p["t"], p["l"]) != (exp[0], exp[1]):
                fails.append("vw=%d %s main padding=%s/%s，应为 %s/%s"
                             % (w, d["page"], p["t"], p["l"], exp[0], exp[1]))
        if len(pads) > 1:
            fails.append("vw=%d 各页 main padding 不一致：%s ← 正是用户报的「边距又变了」"
                         % (w, sorted(pads)))
        print("  → 本档 %d 页，main padding 唯一取值：%s"
              % (len(rows), sorted(pads) or "无"))
    print("\n%s" % ("FAIL:\n  " + "\n  ".join(fails) if fails
                    else "PASS 所有页面 main padding 完全一致，且等于收窄后的基准"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())