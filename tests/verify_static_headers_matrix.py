#!/usr/bin/env python3
"""L2：静态资源**响应头矩阵**（起影子实例验，绝不碰生产）。

钉住的两件事（PT-20260924-01 同批，用户 09-24 裁定"记内容指纹"时一并清掉的未闭合 8）：
  1. immutable 分支（URL 的 ?v= == 文件内容哈希）不得带任何校验器：
     `etag`/`last-modified` 都是 **mtime+size** 的函数，与内容哈希提手无关 ⇒
     09-23 自研 APP 事故里实测到 `?v=…22c` 与 `?v=…23a` **共用同一个 ETag**、
     同一 URL 先 200 后 304，端侧因此能继续执行旧字节。
  2. revalidate 分支（提手错/没提手）必须照旧发 ETag 且能回 304 —— 那是"忘了 bump 提手
     也不会把客户端永久钉住"的安全阀，被顺手抹掉就变成每次都全量下载。

为什么必须是 L2 真起服务：本闸门的红臂两次抓出我本人的假动作 ——
  (a) 以 0 缩进往 `if static_path.exists():` 里插类 ⇒ 块被截断、`_GZ_SUFFIX` 与路由掉进
      类的命名空间 ⇒ /static 全 500，而 `ast.parse` **完全通过**；
  (b) 覆写 `FileResponse.set_headers` —— 本 Starlette 版本没有这个钩子（真钩子是
      `set_stat_headers`，在 `__call__` 里 stat 之后才调）⇒ 方法是死代码，头照发。
两类都只有真请求才测得出来。

红基线（自证灵敏度）：在同一进程里用 `starlette.responses.FileResponse` 直接构造一份响应、
把 headers 抓下来，断言它**确实带 etag** ⇒ 证明"没有 etag"这条判据是有力度的，
不是环境凑出来的。不往生产目录写任何文件。
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
PY = os.path.join(REPO, "venv", "bin", "python")
PROD_PORT = 3102

ok = fail = 0


def check(name, cond, got=""):
    global ok, fail
    if cond:
        ok += 1
        print("    PASS %-52s %s" % (name, got))
    else:
        fail += 1
        print("    FAIL %-52s %s" % (name, got))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(port, path, headers=None, method="GET"):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def prod_signature():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PROD_PORT, timeout=5) as r:
            d = json.loads(r.read())
            return (d.get("pid"), d.get("version"))
    except Exception:
        return None


def main():
    prod_before = prod_signature()
    tmp = tempfile.mkdtemp(prefix="hub-static-matrix-")
    port = free_port()
    env = dict(os.environ, PORT=str(port), DATA_DIR=tmp, LOG_DIR=os.path.join(tmp, "logs"),
               HUB_DISABLE_SCHEDULERS="1")
    log = open(os.path.join(tmp, "shadow.log"), "wb")
    p = subprocess.Popen([PY, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1",
                          "--port", str(port)], cwd=REPO, env=env, stdout=log, stderr=log)
    try:
        up = False
        for _ in range(40):
            time.sleep(1)
            try:
                st, _, raw = http(port, "/health")
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
        check("影子实例起来且 /health 200（未占生产端口）", up, "port=%d" % port)
        if not up:
            print(open(os.path.join(tmp, "shadow.log"), encoding="utf-8", errors="replace").read()[-1500:])
            return 1

        rel = "static/hub.js"
        disk = os.path.join(REPO, rel)
        tok = hashlib.md5(open(disk, "rb").read()).hexdigest()[:8]

        print("\n  == ① immutable（提手==内容哈希）：不得有任何校验器 ==")
        st, h, body = http(port, "/%s?v=%s" % (rel, tok))
        check("状态 200", st == 200, str(st))
        check("Cache-Control 是 immutable", "immutable" in (h.get("cache-control") or ""),
              h.get("cache-control", ""))
        check("X-Asset-Token == 内容哈希", h.get("x-asset-token") == tok,
              "%s vs %s" % (h.get("x-asset-token"), tok))
        check("无 etag", "etag" not in h, "etag=%r" % h.get("etag"))
        check("无 last-modified", "last-modified" not in h, "lm=%r" % h.get("last-modified"))
        check("有 content-length（不是 chunked 到底）", (h.get("content-length") or "").isdigit(),
              str(h.get("content-length")))
        check("服务出参与磁盘逐字节一致", hashlib.md5(body).hexdigest()[:8] == tok,
              "出参=%s 磁盘=%s" % (hashlib.md5(body).hexdigest()[:8], tok))

        print("\n  == ② 同 URL 带条件头：必须仍 200，永不 304 ==")
        st2, h2, _ = http(port, "/%s?v=%s" % (rel, tok),
                          headers={"If-None-Match": '"anything"',
                                   "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT"})
        check("带 If-None-Match 仍 200（immutable 不参与协商）", st2 == 200, str(st2))

        print("\n  == ③ revalidate（提手错）：ETag 与 304 必须还在（安全阀不得被抹） ==")
        st3, h3, _ = http(port, "/%s?v=deadbeef" % rel)
        check("状态 200", st3 == 200, str(st3))
        check("Cache-Control 是 no-cache", "no-cache" in (h3.get("cache-control") or ""),
              h3.get("cache-control", ""))
        etag = h3.get("etag") or ""
        check("带 ETag", bool(etag), etag[:20])
        st4, _, _ = http(port, "/%s?v=deadbeef" % rel, headers={"If-None-Match": etag})
        check("用该 ETag 复问 ⇒ 304", st4 == 304, str(st4))

        print("\n  == ④ gzip + immutable：压缩后同样不得带校验器 ==")
        st5, h5, body5 = http(port, "/%s?v=%s" % (rel, tok), headers={"Accept-Encoding": "gzip"})
        check("命中 gzip", (h5.get("content-encoding") or "") == "gzip",
              "%s len=%s" % (h5.get("content-encoding"), len(body5)))
        check("gzip 侧也无 etag", "etag" not in h5, "etag=%r" % h5.get("etag"))
        check("gzip 侧也 immutable", "immutable" in (h5.get("cache-control") or ""),
              h5.get("cache-control", ""))

        print("\n  == ⑤ 红臂自证：直接用 FileResponse 构造，头里必须有 etag ==")
        # 红臂脚本落临时文件（`python -c` 里用分号接 `async def` 是语法错误，09-24 先炸过一次）
        probe = os.path.join(tempfile.gettempdir(), "hub_fileresp_probe.py")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(
                "import asyncio, os, starlette.responses as R\n"
                "os.chdir(%r)\n"
                "resp = R.FileResponse('static/hub.js', headers={'X-T': '1'})\n"
                "async def go():\n"
                "    out = []\n"
                "    async def send(m):\n"
                "        out.append(m)\n"
                "    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',\n"
                "             'method': 'GET', 'path': '/static/hub.js', 'raw_path': b'/static/hub.js',\n"
                "             'query_string': b'', 'headers': [], 'scheme': 'http',\n"
                "             'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 1),\n"
                "             'root_path': ''}\n"
                "    async def receive():\n"
                "        return {'type': 'http.request', 'body': b''}\n"
                "    await resp(scope, receive, send)\n"
                "    print(' '.join(k.decode() + '=' + v.decode() for k, v in out[0]['headers']).lower())\n"
                "asyncio.run(go())\n" % REPO)
        red = subprocess.run([PY, probe], capture_output=True, text=True, timeout=60)
        os.unlink(probe)
        blob = (red.stdout or "") + (red.stderr or "")
        check("红臂：FileResponse 确实自动补 etag（⇒ 本闸门判据有力度）",
              "etag=" in blob, ("抓到 etag=" if "etag=" in blob else "输出=%s" % blob[:120].replace("\n", " ")))
    finally:
        p.terminate()
        try:
            p.wait(timeout=15)
        except Exception:
            p.kill()
        log.close()
        subprocess.run(["rm", "-rf", tmp])
        prod_after = prod_signature()
        print("\n  生产未被影响：%s → %s" % (prod_before, prod_after))
        check("生产 pid/version 未变（本闸门不碰 3102）", prod_before == prod_after,
              "before=%s after=%s" % (prod_before, prod_after))

    print("\n  == 汇总：%d PASS / %d FAIL ==" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
