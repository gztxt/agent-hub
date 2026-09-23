"""给 UI 类探针用的极简 CDP（带常驻读线程）。

为什么要有这个文件：第一版探针在 `Page.reload` 后 `time.sleep(3)` 再取结果，
期间到达的 `Fetch.requestPaused` **没人应答** ⇒ 被拦截的资源永远 pending ⇒
页面吊死、量到的是半成品。CDP 的事件和应答必须按"随时会有事件插进来"来写。

分发口径：`id` 响应进 dict 由调用方 wait；`Fetch.requestPaused` 交回调线程处理
（回调里可以再调 send，因为读只在 _loop 一个线程里做）。
"""
import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect as _connect          # noqa: E402


def old_hub_bytes(repo, marker=b"setItem('hub.sidebar',", path="static/hub.js"):
    """按**内容**回溯最近一个仍含 marker 的版本，返回 (bytes, rev)；找不到 (None, None)。

    不钉 HEAD、不读 *.bak-*：HEAD 会被本次修复推前，.bak 被 .gitignore 排除且随时可清。
    红基线只有从 git 取，才在半年后还跑得动。
    """
    r = subprocess.run(["git", "-C", str(repo), "log", "--format=%H", "--", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, None
    for rev in r.stdout.split():
        s = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (rev, path)],
                           capture_output=True)
        if s.returncode == 0 and marker in s.stdout:
            return s.stdout, rev[:8]
    return None, None


def launch_chrome(url, port, profile, width=390, height=844):
    shutil_rmtree = Path(profile)
    import shutil
    shutil.rmtree(shutil_rmtree, ignore_errors=True)
    proc = subprocess.Popen(
        ["/usr/bin/chromium", "--headless=new", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", "--remote-debugging-port=%d" % port,
         "--user-data-dir=%s" % profile, "--window-size=%d,%d" % (width, height), url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def page_target(port, tries=40):
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as r:
                tg = json.load(r)
            p = next((t for t in tg if t.get("type") == "page"), None)
            if p:
                return p["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("chromium devtools 不可达（端口 %d）" % port)


class CDP:
    def __init__(self, ws_url, on_paused=None):
        self.ws = _connect(ws_url, max_size=None)
        self.on_paused = on_paused
        self.n = 0
        self.res = {}
        self.cv = threading.Condition()
        self.tx = threading.Lock()
        self.alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.alive:
            try:
                m = json.loads(self.ws.recv())
            except Exception:
                return
            if "id" in m:
                with self.cv:
                    self.res[m["id"]] = m
                    self.cv.notify_all()
            elif m.get("method") == "Fetch.requestPaused" and self.on_paused:
                threading.Thread(target=self.on_paused, args=(m["params"],), daemon=True).start()

    def send(self, method, timeout=20, **params):
        with self.cv:
            self.n += 1
            mid = self.n
        with self.tx:
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        with self.cv:
            deadline = time.time() + timeout
            while mid not in self.res:
                if not self.cv.wait(max(0.05, deadline - time.time())):
                    raise TimeoutError("%s 无应答（可能有请求停在 paused 状态）" % method)
            m = self.res.pop(mid)
        if "error" in m:
            raise RuntimeError("%s: %s" % (method, m["error"]))
        return m.get("result", {})

    def eval(self, expr, timeout=20):
        return self.send("Runtime.evaluate", timeout=timeout, expression=expr,
                         returnByValue=True).get("result", {}).get("value")

    def close(self):
        self.alive = False
        try:
            self.ws.close()
        except Exception:
            pass
