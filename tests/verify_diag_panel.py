#!/usr/bin/env python3
"""端侧自检面板 `?diag=1` 的红绿对照闸门。

为什么需要这条：本机对手机/APP 端零探针能力（长期缺口），`?diag=1` 面板是唯一能把
"端侧到底卡在哪一步"变成可复制文本的通道。**通道本身失效 = 永远拿不到事实**，
所以它必须自带红绿对照：

  绿：正常加载 ⇒ 面板存在、`异常清单: 无`、go 与遮罩出口均为 function、navTree 有 3 子项
  红：人为掐断 /static/hub.js（模拟 APP 里 JS 取不到 / 被代理换成 HTML）
      ⇒ 面板必须精确报「资源加载失败」+ go=undefined + navTree子项=0
      ⇒ 且**实测证伪/证实一件事**：JS 不跑时 `侧栏collapsed=false` ⇒ 抽屉停在展开态遮住正文
        （这就是"菜单空白 + 遮挡右侧 + 点不动"的成因之一，与 hub.page 坏值是两回事）
  另：不带 ?diag ⇒ 面板不得存在（不污染正常使用）

CDP 纪律（本轮踩过的真坑）：`Fetch` 域是**按 session 启用**的，所以 `failRequest` 必须
用**同一个连接**发出；另开一条 CDP 去 enable 再拿旧连接去应答 ⇒ "Fetch domain is not enabled"。
"""
import json, re, sys, time
sys.path.insert(0, "/home/gztxt/agent-hub/tests")
from _cdp_min import CDP, launch_chrome, page_target

BASE, PORT = "http://127.0.0.1:3102", int(sys.argv[1]) if len(sys.argv) > 1 else 9392
TXT = "document.getElementById('diagBox') ? document.getElementById('diagBox').textContent : '<<无面板>>'"
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-40s %s" % ("PASS" if ok else "FAIL", name, detail))


def fields(t):
    """按**行首**取键（面板规定一行一键）。用正则而不是 split，避免以后标签里
    出现空格或第二个 '=' 时断言静默失真。"""
    d = {}
    for k in ("origin", "UA", "视口", "窄屏档", "hub.js标签", "go函数",
              "initSidebar跑过(遮罩出口)", "navTree子项", "侧栏可见按钮", "侧栏collapsed",
              "在显示的页", "localStorage键", "就绪"):
        m = re.search(r"(?:^|\n)%s=([^\n]*)" % re.escape(k), t or "")
        d[k] = m.group(1).strip() if m else None
    d["_raw"] = t or ""
    return d


proc = launch_chrome(BASE + "/?diag=1", PORT, "/tmp/hub_diag2", 390, 844)
time.sleep(3.0)

state = {"blocked": 0}


def main_paused(params, cli):
    """同一条连接里既 enable 又应答；只掐 hub.js，其余放行（否则会挂住整个页面）。"""
    rid = params.get("requestId")
    url = (params.get("request") or {}).get("url", "")
    try:
        if "static/hub.js" in url:
            state["blocked"] += 1
            cli.send("Fetch.failRequest", requestId=rid, errorReason="Failed")
        else:
            cli.send("Fetch.continueRequest", requestId=rid)
    except Exception as e:
        print("   拦截应答异常:", e)


c = CDP(page_target(PORT))
c.send("Page.enable"); c.send("Runtime.enable")
c.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)


def nav(url, wait=4.0):
    c.send("Page.navigate", url=url)
    time.sleep(wait)
    return c.eval(TXT) or ""


print("== 绿：正常加载 ==")
t = nav(BASE + "/?diag=1")
f = fields(t)
for ln in t.splitlines():
    print("   | " + ln[:94])
chk("面板存在且可读", t != "<<无面板>>" and len(t) > 80, "长度=%d" % len(t))
chk("报 origin/UA/视口/档", f.get("origin") and f.get("UA") and f.get("视口") == "390x844"
    and f.get("窄屏档") == "true", f.get("窄屏档", ""))
chk("hub.js 标签带版本号", "v=" in f.get("hub.js标签", ""), f.get("hub.js标签", ""))
chk("go 函数存在（已解析）", f.get("go函数") == "function", f.get("go函数", ""))
chk("遮罩出口存在（initSidebar 跑过）", f.get("initSidebar跑过(遮罩出口)") == "function",
    f.get("initSidebar跑过(遮罩出口)", ""))
chk("navTree 有 3 个分组", f.get("navTree子项") == "3", f.get("navTree子项", ""))
chk("侧栏已收起（窄屏默认图标条）", f.get("侧栏collapsed") == "true", f.get("侧栏collapsed", ""))
chk("异常清单为「无」", "异常清单: 无" in f["_raw"], "")

print("\n== 红：同一条连接里启用 Fetch 并掐断 hub.js ==")
c.send("Fetch.enable", patterns=[{"urlPattern": "*static/hub.js*"}])
c.on_paused = lambda p: main_paused(p, c)      # ← 关键：用同一条连接应答
t2 = nav(BASE + "/?diag=1&red=1", wait=5.0)
f2 = fields(t2)
for ln in t2.splitlines():
    print("   | " + ln[:94])
chk("确实拦到并掐断了 hub.js", state["blocked"] >= 1, "拦截次数=%d" % state["blocked"])
joined = t2.replace("\n", " ")
chk("面板报出「资源加载失败 hub.js」", "资源加载失败" in joined and "hub.js" in joined, "")
chk("红况下异常清单不为「无」", "异常清单: 无" not in t2, "")
chk("go 函数消失（=undefined）", f2.get("go函数") == "undefined", f2.get("go函数", ""))
chk("遮罩出口消失", f2.get("initSidebar跑过(遮罩出口)") == "undefined", f2.get("initSidebar跑过(遮罩出口)", ""))
chk("navTree 零子项（=菜单空白）", f2.get("navTree子项") == "0", f2.get("navTree子项", ""))
chk("★JS 不跑时抽屉停在展开态（=遮挡正文）", f2.get("侧栏collapsed") == "false",
    "collapsed=%s 可见按钮=%s" % (f2.get("侧栏collapsed"), f2.get("侧栏可见按钮")))

print("\n== 不带 ?diag：面板不应存在 ==")
c.send("Fetch.disable")
t3 = nav(BASE + "/")
chk("无 diag 参数时面板不存在", t3 == "<<无面板>>", t3[:40])
chk("正常页仍渲染 3 个分组", str(c.eval("document.getElementById('navTree').children.length")) == "3", "")

proc.kill()
bad = res.count(False)
print("\n%s  (%d/%d)" % ("全部 PASS ✅" if not bad else "存在 FAIL ❌", res.count(True), len(res)))
sys.exit(1 if bad else 0)
