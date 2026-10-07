#!/usr/bin/env python3
"""端到端**稳态 / 不回归**闸门（L2 live，真 hub 页面 + 真 jcode）。

⚠️ 先读这条，别把它当本 bug 的红绿判据：
  本探针跑的是**稳态**（新建会话 + 点芯片重连后等 4s 再 dump）。jcode v0.91.0 在客户端
  下发 resize 之后会自己发一次 `\\x1b[2J` 整屏重画（实测：全量 ring 3824B 里 `2J` ×1），
  ⇒ **修复前的稳态也是干净的**（本轮实测 7/7 PASS 在 pre-fix 影子上同样成立）。
  所以本探针**不能**区分修复前后；它守的是「修复没把真实终端路径搞坏」——
  真进程、真 WS、真 ring 回放、真 chromium 下 TUI 仍在、仍在主屏、无稳态残影。

  真正的红绿判据是 `tests/verify_term_altclear.py`：它喂**客户端 resize 之前**那段
  真实回放前缀（本轮实测 245B，含 hub 头部 + `Connecting to server...` + `?1049h`），
  在 pre-fix 下屏上留 0-2 行残影、在 fix 下清空 —— 那正是用户报障的**启动窗口**。

判据（可断言）：
  E1 页面里真有 jcode 的 TUI（buffer 含 `jcode · client` 或 `server:`）
  E2 hub 自己写的头部**不在**屏上（`提示：点「新会话」` 那两行）
  E3 jcode 进备用屏前的 `Connecting to server...` **不在**屏上
  E4 仍在主屏（备用屏永不出现）
  E5-E7 点当前会话芯片重连（keepScreen=true 的 ring 回放路径）后仍主屏 / TUI 仍在 / 无残影

跑法：
    ( cd <worktree-or-repo-without-.env> && DATA_DIR=$PWD/work/probe/data PORT=3199 \
        HOST=127.0.0.1 HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 & )
    HUB_PROBE_BASE=http://127.0.0.1:3199 HUB_PROBE_TERM_TOKEN=probe-term-token \
      HUB_PROBE_CDP_PORT=9455 python tests/verify_term_altclear_live.py
  ⚠️ 影子必须从**没有 .env 的目录**起：主 checkout 的 `.env` 是覆盖模式加载，
  会把 `TERM_TOKEN` 覆盖成生产值 ⇒ 探针的 token 对不上（本轮踩过，症状是 401 一片）。
  收尾：fuser -k 3199/tcp（禁 pkill -f）。
退出码：0 全绿 / 1 有 FAIL / 2 环境不满足（影子未起）。
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = os.environ.get("HUB_PROBE_BASE", "http://127.0.0.1:3199")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9454"))
TOKEN = os.environ.get("HUB_PROBE_TERM_TOKEN", "probe-term-token")

AGENT = os.environ.get("HUB_PROBE_AGENT", "jcode")
#: TUI 存在的标记（不同 agent 不同）：jcode 有「已登录的 TUI」与「首启 onboarding」两态，
#: 都可作为"TUI 渲染出来了"的判据；claude 用 "Claude Code"。
_JCODE_MARKS = ("jcode · client", "server:", "Welcome to jcode onboarding", "Log in to get started")
_DEFAULT_MARKS = "|".join(_JCODE_MARKS if AGENT == "jcode" else ("Claude Code",))
TUIMARKS = tuple(x for x in os.environ.get("HUB_PROBE_TUIMARKS", _DEFAULT_MARKS).split("|") if x)
RES = []
MADE = []


def chk(name, ok, detail=""):
    RES.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:  # noqa: BLE001
        pass


def ev(cdp, expr):
    return cdp.eval(expr)


DUMP = """(function(){
  var t=term; if(!t||!t.buffer) return {type:null,line:[]};
  var b=t.buffer.active, out=[];
  for(var i=0;i<b.length;i++){var l=b.getLine(i); out.push(l?l.translateToString(true):'');}
  return {type:b.type, text:out.join('\\n')};})()"""


def main():
    # 影子实例必须已经在跑（见文件头跑法）；先确认可达，否则报环境不满足而不是假红。
    try:
        urllib.request.urlopen(BASE + "/health", timeout=5).read()
    except Exception as e:  # noqa: BLE001
        print("  环境不满足：影子实例 %s 不可达（%s）" % (BASE, e))
        return 2

    prof = tempfile.mkdtemp(prefix="altclear-live")
    proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
    try:
        cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
        cdp.send("Page.enable")
        cdp.send("Page.navigate", url=BASE + "/")
        time.sleep(1.5)
        ev(cdp, "localStorage.setItem('hub.term.token', %s)" % json.dumps(TOKEN))
        cdp.send("Page.reload", ignoreCache=True)
        time.sleep(3.0)

        ev(cdp, "go('chat')")
        time.sleep(0.5)
        # 走真实路径：选实体 → 自动挂载（写 hub 头部）→ 新建 jcode 会话（真进程）
        ev(cdp, "chatPick=%s" % json.dumps(AGENT))
        ev(cdp, "termAutoAttach()")
        time.sleep(2.0)
        ev(cdp, "termNew()")
        # jcode 要起进程、连服务、画首帧；给足时间（真机实测 ~3s）
        for _ in range(40):
            time.sleep(0.5)
            d = ev(cdp, DUMP) or {}
            if any(mk in (d.get("text") or "") for mk in TUIMARKS):
                break
        d = ev(cdp, DUMP) or {}
        text = d.get("text") or ""
        print("  —— 屏面（去空行）——")
        for ln in [x for x in text.split("\n") if x.strip()]:
            print("     | " + ln)

        chk("E4 仍在主屏（备用屏永不出现）", d.get("type") == "normal", "type=%s" % d.get("type"))
        chk("E1 页面里真有 %s 的 TUI" % AGENT, any(mk in text for mk in TUIMARKS))
        chk("E2 hub 头部不在屏上（无 终端 / 提示：点「新会话」残影）",
            ("提示：点「新会话」" not in text))
        chk("E3 jcode 的 Connecting to server... 不在屏上",
            "Connecting to server..." not in text)

        # ── 第二段：点芯片重连（keepScreen=true 的 ring 回放路径）──────────────
        # 这才是报障的真实入口：新建会话走 termConnect 的 `!keepScreen ⇒ term.clear()`，
        # 头部本就被清掉，测不出差异；而**点当前会话芯片**（termOpenChip）重连时
        # keepScreen=true、不清屏，只回放 ring —— 修复前 ring 里的 `?1049h` 被吞且不清，
        # 于是 `Connecting to server...` 叠进已在屏上的 TUI。修复后回放里的 `?1049h`
        # 触发同步清屏，再按 ring 重画。
        sid = ev(cdp, "(typeof termSid!=='undefined'&&termSid)||''")
        if sid:
            MADE.append(sid)
            rec = ev(cdp, "(function(){try{termOpenChip(%s,'%s');return 'ok';}catch(e){return 'ERR:'+e;}})()"
                     % (json.dumps(sid), AGENT))
            time.sleep(4.0)
            d2 = ev(cdp, DUMP) or {}
            t2 = d2.get("text") or ""
            print("  —— 芯片重连后的屏面（去空行）——")
            for ln in [x for x in t2.split("\n") if x.strip()]:
                print("     | " + ln)
            chk("E5 芯片重连后仍在主屏", d2.get("type") == "normal", "type=%s" % d2.get("type"))
            chk("E6 芯片重连后 TUI 仍在", any(mk in t2 for mk in TUIMARKS),
                "reconnect=%s" % rec)
            chk("E7 芯片重连**不回放残影**（无 Connecting / 无 hub 头部）",
                ("Connecting to server..." not in t2)
                and ("提示：点「新会话」" not in t2))
        else:
            chk("E5-E7 取到会话 id（否则芯片路径测不了）", False)
        return finish(cdp, proc)
    except Exception as e:  # noqa: BLE001
        print("  探针异常：%s: %s" % (type(e).__name__, e))
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        return 1


def finish(cdp, proc):
    ok = sum(1 for x in RES if x)
    print("\n%d/%d PASS" % (ok, len(RES)))
    for s in MADE:
        try:
            rq = urllib.request.Request(BASE + "/api/term/sessions/" + s, method="DELETE",
                                        headers={"x-term-token": TOKEN})
            urllib.request.urlopen(rq, timeout=10).read()
        except Exception:  # noqa: BLE001
            pass
    try:
        cdp.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    return 0 if (RES and ok == len(RES)) else 1


if __name__ == "__main__":
    sys.exit(main())