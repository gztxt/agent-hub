#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸门：记忆检索与开局注入包不得「全绿而功能层已死」（v0.13.16 / PT-20260924-08）

背景（2026-09-24 实测，不是推断）：
  · `GET /api/memory/search?q=端口` → `{"memories":[],"count":0,"engine":"keyword"}` HTTP 200 / 11ms
  · `GET /api/memory/context?q=agent-hub` → **chars=357**，且整包里**没有「长期 Profile」段**
    （`memory_docs` 只有 L2 一行，L3 为空；`memories` 只有 4 行 09-06 陈旧便签）
  成因：`memory.py` 的 TDAI 透传把**路径/HTTP 方法/两个鉴权头**全写错（`GET /memory/search`
  → 实测 404；真契约是 `POST /v2/atomic/search` + `Authorization: Bearer` + `x-tdai-service-id`），
  再用 `except Exception: pass` 吞掉 ⇒ 对外表现为「服务健康、只是查不到东西」。
  而权威库其实在跑：实测 L1 **233** 条 / L0 **5967** 条。**宣告能力 ≠ 实际能力**。
  这与 ccpocket-bridge「active、端口在听、/health ok，但 Claude 会话起不来，静默 21 天」同形态。

为什么这个闸门必须同时测红绿两向：
  只测「能查到」的那次改动，等价于把老 bug 从「恒返回空」换成「恒返回有」——
  上游一挂仍然会静默。所以**故意把 TDAI 指到死端口、故意抽掉凭据**，
  要求它仍然 200、但必须说得出哪一路死了、为什么死。

隔离性：不 import src.main（那会带起 vitals_loop 去烧 CLI 探活，见 PT-20260923-05）、
不占任何端口（TestClient 走 in-process ASGI）、db 指向临时空库
⇒ 任何命中都只能来自 TDAI，本地便签无法冒充成功。**生产 agents.db 全程不碰。**

用法：cd /home/gztxt/agent-hub && venv/bin/python tests/verify_memory_federation.py
退出码 0 = 全绿；非 0 = 有 FAIL。
"""
import json
import inspect
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import db  # noqa: E402

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hubgate-"))
db.init_db(_TMP / "agents.db")          # 空库：本地 0 行

from fastapi import FastAPI                      # noqa: E402
from starlette.testclient import TestClient      # noqa: E402
import memory                                    # noqa: E402
import tdai_client                               # noqa: E402

_app = FastAPI()
_app.include_router(memory.router)
C = TestClient(_app)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  ✅ PASS  " if ok else "  ❌ FAIL  ") + name + (f"   {detail}" if detail else ""))


def main():
    # 真凭据只在进程内使用，绝不打印
    try:
        real = json.loads((pathlib.Path.home() / ".pi/agent/memory-tencentdb.json")
                          .read_text())["server"]
    except (OSError, ValueError) as e:
        print(f"无法读取 TDAI 凭据（{type(e).__name__}）⇒ 本闸门需要本机 TDAI 在跑")
        return 1
    os.environ["TDAI_URL"] = real["endpoint"]
    os.environ["TDAI_API_KEY"] = real["apiKey"]
    os.environ["TDAI_SERVICE_ID"] = real["serviceId"]
    KEY = real["apiKey"]

    print("\n[A 绿向] 权威库真的被查到（本地库为空，命中不可能来自本地）")
    d = C.get("/api/memory/search", params={"q": "端口", "episodic": 3, "limit": 5}).json()
    check("A1 count > 0", d.get("count", 0) > 0, f"count={d.get('count')}")
    check("A2 engine 含 tdai（不再是 keyword 单打）", "tdai" in (d.get("engine") or ""),
          f"engine={d.get('engine')}")
    b = {x["name"]: x for x in d.get("backends", [])}
    check("A3 backends 逐路可诊断", "tdai_l1" in b and b["tdai_l1"]["ok"] is True,
          f"backends={list(b)}")
    check("A4 融合条目带 source 与 rrf",
          all("source" in m and "rrf" in m for m in d.get("memories", [])))
    check("A5 本地路如实报 0（不伪装成参与命中）",
          b.get("local", {}).get("ok") is True and b.get("local", {}).get("count") == 0)

    print("\n[B 绿向] 开局注入包拿到权威 L3（改造前实测 chars=357 且无 Profile 段）")
    d = C.get("/api/memory/context", params={"q": "agent-hub", "max_chars": 8000}).json()
    ctx = d.get("context") or ""
    check("B1 chars 显著超过旧实测 357", d.get("chars", 0) > 357, f"chars={d.get('chars')}")
    check("B2 出现「长期 Profile」段", "长期 Profile" in ctx)
    check("B3 degraded 为空（本向要求全路通）", d.get("degraded") == [], f"{d.get('degraded')}")
    check("B4 起始链路没被卡住（<1500ms）", d.get("took_ms", 9e9) < 1500,
          f"took_ms={d.get('took_ms')}")

    print("\n[C 红向] TDAI 不可达：仍须 200，但必须说得出哪一路死了")
    os.environ["TDAI_URL"] = "http://127.0.0.1:9"        # discard 端口，必然拒连
    r = C.get("/api/memory/search", params={"q": "端口", "limit": 5})
    d = r.json()
    check("C1 HTTP 仍 200（上游故障不打断调用方）", r.status_code == 200)
    check("C2 degraded 非空", bool(d.get("degraded")), f"{d.get('degraded')}")
    dead = [x for x in d.get("backends", []) if not x["ok"]]
    check("C3 error 有具体原因（不是空串/不是 'error'）",
          bool(dead) and bool(dead[0].get("error")) and len(dead[0]["error"]) > 8,
          f"error={dead[0].get('error') if dead else '<无死路可报>'}")
    check("C4 engine 如实降为 none（不谎报 keyword+tdai）", d.get("engine") == "none",
          f"engine={d.get('engine')}")

    print("\n[D 红向] 无凭据：必须区分「我没拿到 key」与「服务挂了」")
    os.environ.pop("TDAI_API_KEY", None)
    os.environ.pop("TDAI_SERVICE_ID", None)
    _bak = tdai_client.CREDS_PATH
    tdai_client.CREDS_PATH = pathlib.Path("/nonexistent/memory-tencentdb.json")
    d = C.get("/api/memory/search", params={"q": "端口", "limit": 5}).json()
    dead = [x for x in d.get("backends", []) if not x["ok"]]
    err = dead[0]["error"] if dead else ""
    check("D1 报「凭据缺失」而非 HTTP 错误", "凭据缺失" in err, f"error={err}")
    check("D2 http=None（根本没发出去，不许谎报状态码）",
          bool(dead) and dead[0].get("http") is None,
          f"http={dead[0].get('http') if dead else '<无死路>'}")
    tdai_client.CREDS_PATH = _bak

    print("\n[E 不变式] 凭据永不进响应")
    os.environ["TDAI_API_KEY"] = KEY
    for qs in ({"q": "端口"}, {"q": "端口", "episodic": 5}):
        t = C.get("/api/memory/search", params=qs).text
        check(f"E1 响应不含 apiKey（sources={list(qs)}）", KEY not in t)
    odd = "TqVhbGFpckFSMjMyMw=="                 # 非 sk- 形态，只能靠精确替换兜住
    os.environ["TDAI_API_KEY"] = odd
    t = C.get("/api/memory/search", params={"q": "端口"}).text
    check("E2 非标准形态 key 也被打码", odd not in t)

    # ── F 组：09-24 独立复核回补项 ──────────────────────────────
    # 背景：外派 claude（只读，无 Bash）复核 P0，命中 8 处真缺陷（已逐条自验）。
    # 本组把每条变成**可断言闸门**，并尽量配红/绿对照：绿向证明修对了，
    # 红向证明旧写法**确实会坏**——没有红向的闸门只能证明代码写了，不能证明它在防什么。
    print("\n[F 组] 独立复核回补：RRF 权重 / 去重键 / sources 白名单 / 脱敏边界 / stale 位")
    local3 = [{"source": "local", "id": i, "content": f"本地便签{i}"} for i in range(3)]
    l1_3 = [{"source": "tdai_l1", "id": f"m{i}", "content": f"权威条目{i}"} for i in range(3)]
    uni = memory._rrf_fuse([local3, l1_3], limit=3)
    wgt = memory._rrf_fuse([local3, l1_3],
                           weights=[memory.W_LOCAL, memory.W_TDAI_L1], limit=3)
    check("F1a 红向：同权时稳定排序确实让 4 行陈旧便签压过 TDAI 第一名",
          uni and uni[0]["source"] == "local", f"uni[0]={uni[0]['source'] if uni else '<空>'}")
    check("F1b 绿向：加权后权威 L1 回第一",
          wgt and wgt[0]["source"] == "tdai_l1", f"wgt[0]={wgt[0]['source'] if wgt else '<空>'}")

    same_id = memory._rrf_fuse([[{"source": "tdai_l1", "id": 7, "content": "A"}],
                                [{"source": "tdai_l0", "id": 7, "content": "B"}]], limit=5)
    check("F2 同 id 跨路不合并（旧实现两路都叫 tdai 时会合并加分，偽装成双路证实）",
          len(same_id) == 2, f"得 {len(same_id)} 条")

    r = C.get("/api/memory/search", params={"q": "端口", "sources": "tdaii"})
    check("F3a 未知 sources 打 400（不返回静默零结果的 200）",
          r.status_code == 400, f"HTTP {r.status_code}")
    check("F3b 400 里点名坏值与可用值", "tdaii" in r.text and "tdai" in r.text)
    check("F3c sources 空串也拒（不开「全部关掉」的后门）",
          C.get("/api/memory/search", params={"q": "端口", "sources": ""}).status_code == 400)

    import time as _t
    # 这个测试改了两次，过称写在这里以免后人又不当回事推掉：
    # 09-24 独立复核说「先截断后脱敏会在 key 跨 200 边界时漏半个 key」——**方向对、机制说错**。
    # 实测：`sk-` 形态呌08字符合法尾巴）会被正则 `sk-[A-Za-z0-9_-]{8,}` 连残片一并兜住，
    # 按 `sk-` 造的样本**泄不出来**（第一版 F4a 就是这么红的）；
    # 而**本机真实 TDAI key 是 `TqVhbGFp…==` 这种非 sk- 形态**，只能靠精确替换兜——
    # 精确替换的前提是「key 完整地出现在文本里」，截断正好破坏这个前提。所以缺陷为真，
    # 且恰好命中我们在用的那一种。样本必须按真实形态造，不是按想得通的形态造。
    _odd = "TqVhbGFpckFSMjMyMw=="                # 与本机 key 同形态（非 sk- 前缀）
    _txt = "y" * (200 - 8) + _odd + "zzz"        # 前 8 字符恰好跨在 200 边界上
    _old = tdai_client.scrub(_txt[:200], _odd)   # 旧写法：先截断后脱敏
    _new = tdai_client.scrub(_txt, _odd)[:200]   # 现写法：先脱敏后截断
    check("F4a 红向：旧写法对**非 sk- 形态** key 会泄出可见残片",
          _odd[:6] in _old, f"泄出={_old[-20:]!r}")
    check("F4b 绿向：先脱敏后截断不漏任何 ≥6 连字符残片",
          not any(_odd[i:i + 6] in _new for i in range(len(_odd) - 5))
          and _odd not in _new and "<redact" in _new,
          # 注：不要求字面量 `<redacted>` 完整出现——[:200] 会把占位符本身截成
          # `…<redacte`。那是**外观问题不是泄露问题**，拿它当断言会得到假红。
          f"截断尾={_new[-14:]!r}")
    _sk = "sk-" + "A" * 40
    check("F4c sk- 形态残片由正则兜住（顺序修在此是冗余防御，不是唯一防线）",
          "AAAA" not in tdai_client.scrub(("x" * 185 + _sk)[:200], _sk))

    _bak_probe = dict(tdai_client._PROBE)
    try:
        tdai_client._PROBE.update({"data": {"state": "ok"}, "ts": _t.monotonic() - 9999})
        st_old = tdai_client.backend_status()
        check("F5a 陈旧缓存必须自己标 stale（不能只靠下游读 age）",
              st_old.get("stale") is True and st_old.get("state") == "ok",
              f"stale={st_old.get('stale')}")
        tdai_client._PROBE["ts"] = _t.monotonic()
        st_new = tdai_client.backend_status()
        check("F5b 新鲜缓存 stale=False（否则监控会永远告警）", st_new.get("stale") is False)
        check("F5c ttl_s 随包给出（下游可不理解 age 也能算过期）",
              isinstance(st_new.get("ttl_s"), (int, float)))
    finally:
        tdai_client._PROBE.clear()
        tdai_client._PROBE.update(_bak_probe)

    _src_tc = inspect.getsource(tdai_client._schedule_probe) + inspect.getsource(
        tdai_client.backend_status)
    check("F6 探针时间轴不残 time.time()（NTP 拨回会让负 age 致缓存永不过期）",
          "time.time()" not in _src_tc)
    check("F7 _local_l1 带 layer='L1'（与 list_l1 口径一致，不把全表混入融合）",
          "layer='L1'" in inspect.getsource(memory._local_l1))

    import hubmcp
    _bak_get = hubmcp._get
    try:
        hubmcp._get = lambda p, **kw: {
            "memories": [{"id": "m_1", "source": "tdai_l1", "type": "fact",
                          "content": "端口基线", "score": 0.9}],
            "count": 1, "engine": "tdai_l1|rrf", "tdai": True,
            "backends": [{"name": "tdai_l1", "ok": True, "count": 1, "ms": 3.1}],
            "degraded": ["local"], "took_ms": 4.0}
        out = json.loads(hubmcp.hub_memory_search("端口"))
        check("F8a MCP 层透传 degraded（否则外部 agent 只看到 count:0 的 200）",
              out.get("degraded") == ["local"], f"degraded={out.get('degraded')}")
        check("F8b MCP 层透传 backends 逐路诊断",
              bool(out.get("backends")) and out["backends"][0]["name"] == "tdai_l1")
        check("F8c MCP 条目带 source（不带给调用方就分不出权威库与陈旧便签）",
              out["memories"][0].get("source") == "tdai_l1")
        check("F8d TDAI 的 type 兼容到 category 位（不留 null 丢语义）",
              out["memories"][0].get("category") == "fact")
        hubmcp._get = lambda p, **kw: {"context": "x" * 9000, "chars": 9000,
                                       "degraded": ["tdai_l1"], "backends": []}
        raw = hubmcp.hub_memory_context()
        try:
            back = json.loads(raw)
            ok_json = True
        except ValueError:
            back, ok_json = None, False
        check("F8e context 返回仍是合法 JSON（旧写法硬截 [:6000] 会截出非法 JSON）",
              ok_json and back.get("degraded") == ["tdai_l1"], f"合法={ok_json}")
        check("F8f max_chars 已下推到服务端（截断在正文层，不在结构层）",
              "max_chars" in inspect.getsource(hubmcp.hub_memory_context))
    finally:
        hubmcp._get = _bak_get

    bad = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 58)
    print("总计 %d 项：PASS %d / FAIL %d" % (len(results), len(results) - len(bad), len(bad)))
    if bad:
        print("FAIL 明细: " + ", ".join(bad))
    print("=" * 58)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
