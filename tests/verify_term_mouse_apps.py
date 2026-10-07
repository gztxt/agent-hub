#!/usr/bin/env python3
"""L2 live：鼠标模式分层闸门（v0.13.84）。

用户报障（2026-10-07）：嵌入式终端修完一轮后，codex/cursor 这类 agent 正常了，
claude 与 opencode 仍「不能向上翻看内容、输入框点不进」。机制（/tmp/mouse_probe3~7
真 PTY 取证，dump 在 /tmp/dump_*.bin）：
  - codex：DECSET 清单里没有鼠标（只有 2004/1004/2026），输出主屏线性追加
    ⇒ 滚轮天然走 xterm scrollback，v0.13.81/82/83 三层机制对它零摩擦；
  - claude / opencode：开机声明 `?1000;1002;1003;1006h`，全屏绝对定位重画。
    它们的「向上翻看」只存在于**应用内滚动**（把滚轮 SGR 上报喂回 pty）；
    而 v0.13.81 看门狗在 wheel 到达瞬间把 activeProtocol 切 NONE（上报不发）、
    v0.13.83 又吞 ?1049h（scrollback 里只有重画碎片）⇒ 两条滚动路径全断。

v0.13.84 分层修法（本探针的靶子）：
  ① app 声明了鼠标（termMouseLive）⇒ wheel 不复位、上报放行到 pty ⇒ 应用内滚动；
  ② mousedown 仍复位（点击/拖选归浏览器：复制与「输入框去鼠标」保留），
     mouseup 后延迟回装跟踪 ⇒ 松手之后 wheel 立即恢复应用内滚动；
  ③ 会话结束（4404/4410）⇒ 清 wanted/live ⇒ wheel 回原生 scrollback（防死轮）。

判据（每条可证伪；红基线 = v0.13.83 代码下 A1/A2/A3 必红）：
  A1  真 claude / opencode 会话：wheel-up ×20 ⇒ 到达 pty 的 SGR 轮上报 ≥18 条
  A2  同屏出现会话开头（用户问句原文可见 ⇒ 真翻到了 transcript 顶部）
  A3  viewportY 纹丝不动（滚动发生在 app 内部，不是 scrollback）
  A4  wheel-down ×25 回到底部（尾行 '60' 重现）
  B1  拖选仍能选中文字（v0.13.81 语义不回退）
  C1  bash（线性对照组）：wheel-up ⇒ viewportY 真变小，且一条轮上报都不发
  D1  `printf '\\e[?1002h'` 声明鼠标的 bash 被服务端 kill（4410）后：
      termMouseLive=false、viewportY 恢复可动、且不再产生轮上报

跑法（先起影子实例，worktree 无 .env，务必 setsid 否则 shell 退出连带杀）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9414 ../agent-hub/venv/bin/python tests/verify_term_mouse_apps.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp    # 禁用 pkill -f "3199"，会连带杀自己

踩坑留痕：
  - 上报计数必须拦 `termWs.send` 的**已 JSON.stringify 载荷**再 parse 回 data 比对，
    直接 indexOf('\\u001b') 会因 stringify 把 ESC 变成字面量 "\\u001b" 而永不命中。
  - `term` / `termMouseLive` 是顶层词法绑定，CDP eval 用裸名，不能 window. 前缀。
  - claude 每次重画都会重新断言 `?1000h...?1006h`（实测箭头键 79B 帧里四连发），
    opencode 只开机断言一次 ⇒ 回装逻辑不能依赖 app 自己重发，必须 hub 侧补写。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9414"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PROMPT = "从1数到60，每行一个数字，不要任何解释"
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))
    return bool(ok)


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def boot():
    prof = tempfile.mkdtemp(prefix="mouseapps")
    proc = launch_chrome(BASE + "/", CDP_PORT, prof, 1280, 900)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
    cdp.send("Page.enable")
    cdp.send("Page.navigate", url=BASE + "/")
    time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(2.5)
    cdp.eval("go('chat')")
    time.sleep(0.6)
    cdp.eval("switchMode('term')")
    time.sleep(0.6)
    cdp.eval("ensureTerm()")
    time.sleep(2.0)
    # 关键：stub 掉后台自动挂载。termRefreshList 轮询回来的 termAutoAttach 会对
    # 「最新会话」再发一次 termConnect ⇒ 把探针刚连的 ws 顶掉（实测 1005 秒断，
    # 误诊成「opencode 会话挂了」，其实服务端 alive=true）。
    cdp.eval("termAutoAttach = function(){ return Promise.resolve([]); };")
    return proc, cdp


JS_TEXT = ("(function(){var b=term.buffer.active,out=[],y=b.viewportY;"
           "for(var i=0;i<term.rows;i++){var l=b.getLine(y+i);out.push(l?l.translateToString(true):'');}"
           "return out.join('\\n');})()")


def dump(cdp, tag, txt=None):
    """失败必 dump 整屏——「红要能自证，不许让人再跑一遍猜」。"""
    txt = txt if txt is not None else cdp.eval(JS_TEXT)
    lines = [l for l in txt.splitlines() if l.strip()]
    print("  ~~ [%s] 屏(%d 非空行): %s" % (
        tag, len(lines), ' ⏎ '.join(l.strip()[:56] for l in lines[:10])[:420]))


def viewport(cdp):
    return cdp.eval("term.buffer.active.viewportY")


def box(cdp):
    return cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")


def wheel(cdp, bx, n, dy):
    for _ in range(n):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=bx["x"], y=bx["y"], deltaX=0, deltaY=dy)
        time.sleep(0.12)
    time.sleep(1.2)


def spy(cdp):
    """计轮上报：包**当前 termWs 的 send 方法**（只读旁路，原样透传）。
    ⚠️ 两个历史坑：
      ① 别换 ws 对象/只挂 window.WebSocket 构造钩子 —— hub 的 onopen/onmessage
         在原始对象上，探针读的是另一份状态，D1/kill 判定全失真；
      ② 别用 addEventListener('send') —— **Chrome 不派发 WebSocket 的 send 事件**
         （实测计数恒 0，A1 假红；老 wrapper 版实测能数到 20）。"""
    cdp.eval("(function(){if(!window.__rep)window.__rep=0;window.__rep=0;"
             "if(termWs && !termWs.__spy){termWs.__spy=1;var o=termWs.send.bind(termWs);"
             "termWs.send=function(x){try{var j=JSON.parse(x);var d=(j&&j.data)||'';"
             "if(d.indexOf('\\u001b[<64;')>=0||d.indexOf('\\u001b[<65;')>=0)window.__rep++;}catch(e){}"
             "return o(x);};}return 1;})()")
    time.sleep(0.2)


def open_session(cdp, agent, cwd=None):
    body = "{agent_id:%s}" % json.dumps(agent)
    if cwd:
        body = body[:-1] + ", 'cwd':%s}" % json.dumps(cwd)
    cdp.eval("window.__mk=null;(async()=>{try{const r=await fetch('/api/term/sessions',"
             "{method:'POST',headers:{'X-TERM-TOKEN':termToken(),'Content-Type':'application/json'},"
             "body:JSON.stringify(%s)});window.__mk=await r.json();}catch(e){window.__mk={err:String(e)};}})()"
             % body)
    for _ in range(40):
        d = cdp.eval("window.__mk && window.__mk.session && window.__mk.session.id ? window.__mk.session.id : ''")
        if d:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("会话创建失败: %r" % (cdp.eval("window.__mk"),))
    # chatPick 必须先对上 agent：termRefreshList 的轮询按 chatPick 过滤清单，
    # 连着「不属于当前实体」的会话会被下一轮轮询直接 termDetach（实测：连上 shell
    # 几秒后画面被写 [当前会话已结束]，误诊成服务端杀会话）。
    cdp.eval("chatPick = %s" % json.dumps(agent))
    # 先摘掉自动挂载已接的会话。ensureTerm/termAutoAttach 可能已经 termConnect 到
    # 别的 sid，探针自连时若不先 detach，两次 close/detach 的收尾会把刚建的
    # 连接一并置 null（实测：POST 成功、服务 alive=true、客户端却永远 CONNECTING→null）。
    cdp.eval("(termWs && termWs.readyState===1) && termWs.close()")
    time.sleep(0.3)
    cdp.eval("termDetach()")
    time.sleep(0.3)
    cdp.eval("termConnect(%s,%s,{user:true})" % (json.dumps(d), json.dumps(agent)))
    for _ in range(40):
        if cdp.eval("termWs && termWs.readyState===1"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("ws 未就绪 agent=%s" % agent)
    time.sleep(2.0)   # 回放帧落定
    # 再等一帧稳定：opencode 的输入框在首屏画完才接管键盘 —— 太早 termSend 会被吞
    # （实测 2s 即发问句：屏上永远停在 Ask anything 占位）。
    prev, same = None, 0
    t0 = time.time()
    while time.time() - t0 < 30:
        txt = cdp.eval(JS_TEXT)
        same = same + 1 if txt == prev else 0
        prev = txt
        if same >= 2 and txt.strip():
            break
        time.sleep(1.5)
    spy(cdp)
    return d


def kill_session(cdp, sid):
    cdp.eval("window.__del=null;(async()=>{try{const r=await fetch('/api/term/sessions/'+%s,"
             "{method:'DELETE',headers:{'X-TERM-TOKEN':termToken()}});window.__del=r.status;}catch(e){window.__del=String(e);}})()"
             % json.dumps(sid))
    for _ in range(30):
        if cdp.eval("!termWs || termWs.readyState>1"):
            break
        time.sleep(0.5)


def settle(cdp, expect_tail, timeout):
    """等回答稳定：连续 4 拍画面不变；若 expect_tail 出现再至少稳 2 拍。"""
    prev, same, t0 = None, 0, time.time()
    while time.time() - t0 < timeout:
        txt = cdp.eval(JS_TEXT)
        if txt == prev:
            same += 1
        else:
            same = 0
        prev = txt
        if same >= 4 and (not expect_tail or expect_tail in txt):
            return prev
        time.sleep(1.5)
    return prev


def main():
    proc, cdp = boot()
    bx = box(cdp)
    try:
        # ── A 组：真 TUI 应用内滚动（claude / opencode 各一遍）──
        for agent, cwd in (("claude", None), ("opencode", "/tmp")):
            # opencode 用 /tmp：技术文档根的 CLAUDE.md/AGENTS.md 会污染 agent 行为
            # （实测它收到 hello 后去 cat 会话快照并弹 Allow once 权限框，转录永远出不来）
            sid = open_session(cdp, agent, cwd)
            # 打字→回显确认→重试：hub 路径下 TUI 何时接管输入不可预测（实测裸 PTY 12s 可打字，
            # hub 里同拍打进去却不显示 —— 发早了被开机初始化吞）。问句在屏 = 真的进去了。
            sent_ok = False
            for attempt in range(3):
                cdp.eval("termSend(%s)" % json.dumps(PROMPT + "\r"))
                te = time.time()
                while time.time() - te < 15:
                    if PROMPT[:4] in (cdp.eval(JS_TEXT) or ""):
                        sent_ok = True
                        break
                    time.sleep(1.5)
                if sent_ok:
                    break
                time.sleep(4)
            if not sent_ok:
                print("  ~~ [%s] 问句三次都没回显，A2 之前先 dump 整屏" % agent)
                dump(cdp, "send-fail[%s]" % agent)
            t0 = settle(cdp, "60", 150)
            if "60" not in t0:
                print("  ~~ [%s] settle 超时，尾屏: %r" % (agent, t0[-200:]))
            y0 = viewport(cdp)
            wheel(cdp, bx, 20, -120)
            rep = cdp.eval("window.__rep")
            t1 = cdp.eval(JS_TEXT)
            y1 = viewport(cdp)
            chk("A1[%s] wheel-up 转发 SGR 轮上报 ≥18（实测 %d）" % (agent, rep), rep >= 18, rep)
            # A2 扫法：上滚 20 格可能越过 transcript 顶（opencode 顶部有大片留白），
            # 再逐格下滚找问句 —— 用户实际就是「滚到看见为止」，几格内必须见得到头。
            found = PROMPT[:5] in t1
            steps = 0
            while not found and steps < 6:
                wheel(cdp, bx, 1, 120)
                steps += 1
                found = PROMPT[:5] in cdp.eval(JS_TEXT)
            ok2 = chk("A2[%s] 屏上出现 transcript 开头（问句原文，下滚%s格后）" % (agent, steps), found)
            if not found:
                dump(cdp, "A2[%s]失败时屏" % agent)
            chk("A3[%s] viewportY 不动（滚动在 app 内：%s→%s）" % (agent, y0, y1), y0 == y1)
            wheel(cdp, bx, 30, 120)
            t2 = cdp.eval(JS_TEXT)
            ok4 = chk("A4[%s] 下滚回到底部（尾行可见 '60'）" % agent, "60" in t2)
            if not ok4:
                dump(cdp, "A4[%s]失败时屏" % agent, t2)
            chk("B1[%s] 拖选仍能选中文字" % agent, drag_select(cdp, bx))
            kill_session(cdp, sid)
            time.sleep(2.0)

        # ── C 组：bash 线性对照组，旧路径不许被改坏 ──
        sid = open_session(cdp, "shell")
        cdp.eval("termSend('clear; for i in $(seq 1 300); do echo LINE$i; done\\r')")
        time.sleep(3.0)
        cdp.eval("term.scrollToBottom()")
        time.sleep(0.5)
        y0 = viewport(cdp)
        wheel(cdp, bx, 5, -120)
        y1 = viewport(cdp)
        chk("C1 bash wheel-up ⇒ viewportY 变小且零轮上报", y1 < y0 and cdp.eval("window.__rep") == 0,
            "%s→%s rep=%s" % (y0, y1, cdp.eval("window.__rep")))

        # ── D 组：声明鼠标但被 kill 的会话 ⇒ 4410 后自愈 ──
        sid2 = open_session(cdp, "shell")
        # 先铺 300 行 scrollback：kill 后要还能滚（printf 的输出也在这条流里）
        cdp.eval("termSend('for i in $(seq 1 300); do echo LINE$i; done\\r')")
        time.sleep(2.5)
        # raw ESC 直接进 bash 行编辑器会被当按键序列吞掉（实测 live 恒 False 的根因）；
        # 必须让 printf 自己解释字面 \e。
        cdp.eval("termSend(%s)" % json.dumps("printf '\\e[?1002h\\e[?1006h'\r"))
        time.sleep(1.5)
        live = cdp.eval("termMouseLive")
        cdp.eval("(function(){var w=termWs;window.__cc=[];if(w)w.addEventListener('close',"
                 "function(e){window.__cc.push(e.code)});return 1})()")
        cdp.eval("term.scrollToBottom()")
        y0 = viewport(cdp)
        wheel(cdp, bx, 3, -120)
        y_app = viewport(cdp)     # 应用声明鼠标期间：轮上报被吃掉，viewport 不动（设计内）
        kill_session(cdp, sid2)
        # kill 的 close 可能分两段：先传输级 1005/1006，hub 自动重连再拿 4404 对账
        # —— 归零发生在 4404 那一刀。所以判据是「轮询到 live=false」，不是「睡 2.5s」。
        for _ in range(24):
            if cdp.eval("termMouseLive") is False:
                break
            time.sleep(0.5)
        live_after = cdp.eval("termMouseLive")
        rep_before = cdp.eval("window.__rep")
        cdp.eval("term.scrollToBottom()")
        time.sleep(0.5)
        y0b = viewport(cdp)
        wheel(cdp, bx, 3, -120)
        y1b = viewport(cdp)
        chk("D1 会话被 kill 后：live=false、viewport 恢复可动、无新轮上报",
            (live is True) and (live_after is False) and (y1b < y0b) and (cdp.eval("window.__rep") == rep_before),
            "live %s→%s y %s→%s close=%s del=%s" % (
                live, live_after, y0b, y1b,
                cdp.eval("JSON.stringify(window.__cc||[])"), cdp.eval("String(window.__del)")))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    print("\n%d/%d PASS" % (sum(res), len(res)))
    return 0 if all(res) else 1


def drag_select(cdp, bx):
    x, y = bx["x"], bx["y"]
    cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1, buttons=1)
    time.sleep(0.1)
    for dy in (12, 24, 36):
        # mouseMoved 必须带 buttons:1（CDP 的 button 只表“变化的键”，xterm 拖选看 event.buttons）
        cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y + dy, button="left", buttons=1)
        time.sleep(0.1)
    mid = cdp.eval("(term.hasSelection()||0) + '|' + (term.getSelection()||'').length")
    cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y + 36, button="left", clickCount=1)
    time.sleep(0.2)
    sel = cdp.eval("term.getSelection()") or ""
    if len(sel.strip()) > 0:
        time.sleep(0.6)                              # arm 后选区不许被回装的跟踪态清掉
        sel = cdp.eval("term.getSelection()") or ""
    return len(sel.strip()) > 0, "拖动中=%s 终=%r" % (mid, sel[:24])


if __name__ == "__main__":
    sys.exit(main())
