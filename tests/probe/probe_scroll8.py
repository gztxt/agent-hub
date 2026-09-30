#!/usr/bin/env python3
"""第八轮：A/B 对照 —— 同一页面分别加载 xterm 5.5.0 与 6.0.0，触摸滚动是否行为不同。
这是判定「升级引入」还是「一直如此」的唯一干净实验。"""
import json, os, sys, tempfile, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass

# 取 5.5.0 版 xterm.js（从 git 对象取出，不动工作树）
import subprocess
old = subprocess.run(["git", "-C", REPO, "show", "a32c8fa^:static/vendor/xterm.js"],
                     capture_output=True).stdout
open(os.path.join(REPO, "work", "probe", "xterm-5.5.0.js"), "wb").write(old)
print("5.5.0 xterm.js 取出 %d 字节" % len(old))

def run(tag, port):
    prof = tempfile.mkdtemp(prefix="ab")
    proc = launch_chrome(BASE + "/", port, prof, 430, 900)
    cdp = CDP(page_target(port), on_event=lambda m, p: dismiss(cdp))
    try:
        cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
        cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
        cdp.eval("ensureTerm()"); time.sleep(2.0)
        cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
        time.sleep(2.5)
        cdp.eval("""(function(){ window.__ev=[];
          ['touchstart','touchmove','touchend'].forEach(function(t){
            document.addEventListener(t, function(){ window.__ev.push(t); }, true); }); return 1; })()""")
        cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
        vy = cdp.eval("(function(){var b=term.buffer.active; return (typeof b.viewportY==='number')?b.viewportY:b.ydisp;})()")
        cdp.send("Input.synthesizeScrollGesture", x=265, y=391, xDistance=0, yDistance=250, speed=800, gestureSourceType="touch")
        time.sleep(1.2)
        vy2 = cdp.eval("(function(){var b=term.buffer.active; return (typeof b.viewportY==='number')?b.viewportY:b.ydisp;})()")
        api = cdp.eval("""(function(){ var b=term.buffer.active;
            var y = function(){ return (typeof b.viewportY==='number')?b.viewportY:b.ydisp; };
            term.scrollToTop(); var top=y(); term.scrollToBottom();
            return {top:top, ydisp:String(b.ydisp), viewportY:String(b.viewportY)}; })()""")
        print("  %-10s 触摸位移=%-4s 事件=[%s] | scrollToTop→%s  ydisp=%s viewportY=%s" % (
            tag, (vy-vy2) if isinstance(vy,int) and isinstance(vy2,int) else '?',
            cdp.eval("(window.__ev||[]).join(',')"), api["top"], api["ydisp"], api["viewportY"]))
    finally:
        try: proc.terminate()
        except Exception: pass

print("A 组：当前 vendor（6.0.0）")
run("6.0.0", 9460)
print("B 组：把 vendor 换成 5.5.0")
os.replace(os.path.join(REPO, "static", "vendor", "xterm.js"),
           os.path.join(REPO, "static", "vendor", "xterm.js.bak-ab"))
shutil_src = os.path.join(REPO, "work", "probe", "xterm-5.5.0.js")
import shutil; shutil.copy(shutil_src, os.path.join(REPO, "static", "vendor", "xterm.js"))
try:
    run("5.5.0", 9461)
finally:
    os.replace(os.path.join(REPO, "static", "vendor", "xterm.js.bak-ab"),
               os.path.join(REPO, "static", "vendor", "xterm.js"))
    print("（已还原 vendor）")
