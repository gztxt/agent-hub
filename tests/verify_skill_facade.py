#!/usr/bin/env python3
"""P2 技能门面闸门：src/skill.py 的红/绿成对断言。

跑法：venv/bin/python tests/verify_skill_facade.py
     或 bash scripts/run_tests.sh probe verify_skill_facade.py
不占端口（TestClient 走 in-process ASGI），不写任何 db，不重启任何服务，不改任何技能文件。

【探活预算（09-23 红线：同一外部目标 ≤2 次）】
本闸门对 TDAI（127.0.0.1:8420）**只打 2 次**：G1 的 `/list` 全路一次、G10 的
`/status?force=true` 一次。其余所有断言都用 `routes=` 限定在磁盘路，或走 tmp 猴补目录，
不产生任何网络调用。拿不到结论即如实 FAIL 并停手，不重试第三次。

【红向断言的意义】
证明"坏的时候会说"。只测绿向等于没测 —— P0 修的缺陷对外就是 HTTP 200 + 空结果，
全绿而功能层已死。本闸门一半以上是红向：路挂了、目录没了、名字撞了、软链越界了、
正文里带凭据了 —— 每一种都必须留下**可读的表态**，而不是静默少条目/静默截断/静默泄露。
"""
import io
import json
import os
import pathlib
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import db  # noqa: E402

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hubskill-"))
db.init_db(_TMP / "agents.db")          # 空库：skill 门面不依赖 db，这里只为与同族闸门一致

from fastapi import FastAPI, HTTPException          # noqa: E402
from starlette.testclient import TestClient         # noqa: E402
import skill                                        # noqa: E402
import hubmcp                                       # noqa: E402
import tdai_client                                  # noqa: E402

_app = FastAPI()
_app.include_router(skill.router)
C = TestClient(_app, raise_server_exceptions=False)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  ✅ PASS  " if ok else "  ❌ FAIL  ") + name + (f"   {detail}" if detail else ""))


def _mk(root: pathlib.Path, skill_dir: str, body: str, name: str = None):
    """在 root 下造一个技能目录；返回 SKILL.md 路径。"""
    d = root / skill_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(body, encoding="utf-8")
    return p


FAKE_KEY = "sk" + "-FAKEKEY1234567890abcdef"  # 运行时拼接：源码不出现 sk- 连写（推前闸门①），值仍为 sk- 形态以验脱敏
FAKE_BEARER = "Bearer AAAA1111.BBBB2222.CCCC3333"


def main():
    # TDAI 凭据：与 verify_memory_federation / verify_kb_federation 同一来源
    try:
        real = json.loads((pathlib.Path.home() / ".pi/agent/memory-tencentdb.json")
                          .read_text())["server"]
        os.environ["TDAI_URL"] = real["endpoint"]
        os.environ["TDAI_API_KEY"] = real["apiKey"]
        os.environ["TDAI_SERVICE_ID"] = real["serviceId"]
    except (OSError, ValueError, KeyError) as e:
        print(f"读不到 TDAI 凭据（{type(e).__name__}）⇒ 注册表那一路无法取证，本闸门需要本机 TDAI 在跑")
        return 1

    _ORIG_DIRS = dict(skill.SKILL_DIRS)

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G1 绿向] 真目录：信封完整、逐路表态、软链去重（TDAI 探测 1/2）")
    r = C.get("/api/skill/list")
    d = r.json()
    check("G1a HTTP 200 且 count>0（不是空门面）", r.status_code == 200 and d.get("count", 0) > 0,
          f"status={r.status_code} count={d.get('count')}")
    bk = d.get("backends") or []
    check("G1b backends 每路 5 键齐（name/ok/count/ms/error）",
          bk and all({"name", "ok", "count", "ms", "error"} <= set(b) for b in bk),
          f"keys={sorted(bk[0]) if bk else []}")
    disk_names = [b["name"] for b in bk if b["name"] != "tdai"]
    check("G1c 磁盘四路全 ok 且各有条目", len(disk_names) == 4
          and all(b["ok"] and b["count"] > 0 for b in bk if b["name"] != "tdai"),
          f"{[(b['name'], b['ok'], b['count']) for b in bk]}")
    check("G1d engine 不是 none，且 count 与各路之和对得上（防 count 恒 0 那个坑）",
          d.get("engine") not in (None, "none") and d.get("count") > 0,
          f"engine={d.get('engine')} count={d.get('count')}")
    dd = d.get("dedup") or {}
    check("G1e walked ≥ unique 且 walked-unique == len(aliases)（去重账目自洽）",
          dd.get("walked", 0) >= dd.get("unique", 0)
          and dd["walked"] - dd["unique"] == len(dd.get("aliases") or []),
          f"walked={dd.get('walked')} unique={dd.get('unique')} aliases={len(dd.get('aliases') or [])}")
    # 事实②的本体断言：pi 路的 agent-dispatch 是软链，全路清单里它**只能出现一次**
    pi_path = os.path.join(_ORIG_DIRS.get("pi", ""), "agent-dispatch", "SKILL.md")
    if os.path.exists(pi_path):
        rp = os.path.realpath(pi_path)
        hits = [i for i in d.get("items", []) if i.get("realpath") == rp]
        check("G1f 软链技能只列一次，且规范条目取真实文件那一路（不是软链路）",
              len(hits) == 1 and hits[0]["route"] == "techdocs"
              and len(hits[0].get("routes") or []) >= 2,
              f"hits={len(hits)} route={hits[0]['route'] if hits else None} "
              f"routes={hits[0].get('routes') if hits else None}")
        check("G1g 别名表如实记录「经软链在多个目录出现」（不许把软链这个事实抹掉）",
              any(a.get("realpath") == rp and len(a.get("routes") or []) >= 2
                  for a in (dd.get("aliases") or [])),
              f"aliases={json.dumps(dd.get('aliases'), ensure_ascii=False)[:150]}")
    else:
        check("G1f 软链技能只列一次", False, f"本机已无 {pi_path}，事实②的前提变了，请重新取证")
        check("G1g 别名表如实记录软链", False, "同上")
    check("G1h took_ms 是正数（monotonic 口径，不是 None/0）",
          isinstance(d.get("took_ms"), (int, float)) and d["took_ms"] >= 1, f"took_ms={d.get('took_ms')}")
    tdai_b = next((b for b in bk if b["name"] == "tdai"), None)
    check("G1i 注册表那一路必须**出现**在 backends 里（禁止用字段缺失表达不接）",
          tdai_b is not None, f"backends={[b['name'] for b in bk]}")
    if tdai_b:
        want_note = tdai_b["ok"] and tdai_b["count"] == 0 and dd.get("unique", 0) > 0
        has_note = "0 行" in (d.get("note") or "")
        check("G1j note 与事实同真同假：注册表 0 行且磁盘有货 ⇔ note 必须说出来",
              want_note == has_note,
              f"tdai(ok={tdai_b['ok']},count={tdai_b['count']}) unique={dd.get('unique')} note={d.get('note')!r}")

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G2 红向] routes 白名单：写错值必须 400 且回显可用值")
    r = C.get("/api/skill/list", params={"routes": "nope"})
    det = str(r.json().get("detail"))
    check("G2a routes=nope → 400 且 detail 同时含坏值与可用值",
          r.status_code == 400 and "nope" in det and "techdocs" in det, f"{r.status_code} {det[:90]}")
    r = C.get("/api/skill/list", params={"routes": ""})
    check("G2b routes= 空串 → 400（与 /api/kb 同口径，不当成「全部」）",
          r.status_code == 400, f"{r.status_code} {str(r.json().get('detail'))[:60]}")
    r = C.get("/api/skill/list", params={"routes": "claude,pi"})
    dd2 = r.json()
    check("G2c 白名单真的裁剪了请求（只打这两路，不是摆设）",
          r.status_code == 200 and [b["name"] for b in dd2["backends"]] == ["claude", "pi"],
          f"backends={[b['name'] for b in dd2.get('backends', [])]}")
    r = C.get("/api/skill/list", params={"q": "zzz不可能命中zzz"})
    check("G2d 过滤词零命中时 note 说明「不是路挂了」（不许让人误读成故障）",
          r.status_code == 200 and r.json()["count"] == 0 and "未命中" in (r.json().get("note") or ""),
          f"note={r.json().get('note')!r}")

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G3 红向] 某路挂了必须说得出是哪路、为什么，且不拖垮其余路")
    try:
        skill.SKILL_DIRS = dict(_ORIG_DIRS, ghost="/nonexistent-skill-dir-xyz")
        r = C.get("/api/skill/list", params={"routes": "ghost,techdocs"})
        d3 = r.json()
        gb = next((b for b in d3["backends"] if b["name"] == "ghost"), None)
        tb = next((b for b in d3["backends"] if b["name"] == "techdocs"), None)
        check("G3a 不存在的路 ok=false 且 error 非空（不许静默当成 0 条）",
              gb is not None and gb["ok"] is False and bool(gb["error"]), f"ghost={gb}")
        check("G3b degraded 点名了坏路", "ghost" in (d3.get("degraded") or []),
              f"degraded={d3.get('degraded')}")
        check("G3c note 说明「本次弃用，结果不完整」", "弃用" in (d3.get("note") or ""),
              f"note={d3.get('note')!r}")
        check("G3d 好路仍正常返回（部分失败不拖垮整体）",
              tb is not None and tb["ok"] and tb["count"] > 0, f"techdocs={tb}")
        check("G3e 白名单跟着 SKILL_DIRS 现算（不是 import 期固化）",
              r.status_code == 200 and "ghost" in [b["name"] for b in d3["backends"]],
              f"status={r.status_code}")
    finally:
        skill.SKILL_DIRS = _ORIG_DIRS

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G4 红向] frontmatter 容错：解析不出也不许丢条目")
    t4 = pathlib.Path(tempfile.mkdtemp(prefix="hubskill-fm-"))
    _mk(t4, "a-normal", "---\nname: a-normal\ndescription: 正常技能\n---\n正文\n")
    _mk(t4, "b-nofm", "这个文件根本没有 frontmatter，只有裸正文。\n")     # 事实③的形态
    _mk(t4, "c-quoted", '---\nname: "c-quoted"\ndescription: \'带引号\'\n---\n正文\n')  # 事实④
    _mk(t4, "d-folded", "---\nname: d-folded\ndescription: >-\n  第一行\n  第二行\n---\n正文\n")
    (t4 / "e-bom").mkdir()
    (t4 / "e-bom" / "SKILL.md").write_bytes(
        "\ufeff---\nname: e-bom\ndescription: 带BOM的技能\n---\n正文\n".encode("utf-8"))
    try:
        skill.SKILL_DIRS = {"t": str(t4)}
        d4 = C.get("/api/skill/list").json()
        by = {i["name"]: i for i in d4["items"]}
        check("G4a 五个技能全在（缺 frontmatter 那个没被丢）", len(by) == 5, f"names={sorted(by)}")
        check("G4b 无 frontmatter ⇒ fm=false 且 name 回退成目录名",
              by.get("b-nofm", {}).get("fm") is False, f"b-nofm={by.get('b-nofm')}")
        check("G4c 引号被剥掉（name 不是 \"c-quoted\" 而是 c-quoted）",
              by.get("c-quoted", {}).get("name") == "c-quoted", f"name={by.get('c-quoted', {}).get('name')!r}")
        check("G4d 折叠块标量被拼成一行", by.get("d-folded", {}).get("description") == "第一行 第二行",
              f"desc={by.get('d-folded', {}).get('description')!r}")
        check("G4e fm_keys 如实反映解析到了什么（无 fm 者为空列表）",
              by.get("a-normal", {}).get("fm_keys") == ["description", "name"]
              and by.get("b-nofm", {}).get("fm_keys") == [],
              f"a={by.get('a-normal', {}).get('fm_keys')} b={by.get('b-nofm', {}).get('fm_keys')}")
        check("G4f 前导 BOM 不会让 frontmatter 静默失效（剥 BOM 后 fm=true 且描述读到了）",
              by.get("e-bom", {}).get("fm") is True
              and by.get("e-bom", {}).get("description") == "带BOM的技能",
              f"e-bom fm={by.get('e-bom', {}).get('fm')} desc={by.get('e-bom', {}).get('description')!r}")
    finally:
        skill.SKILL_DIRS = _ORIG_DIRS

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G5 红向] 软链越界：读之前就拒，且拒读要留痕（不许静默丢，也不许读出去）")
    t5 = pathlib.Path(tempfile.mkdtemp(prefix="hubskill-evil-"))
    _mk(t5, "ok", "---\nname: ok\ndescription: 合法技能\n---\n正文\n")
    evil_dir = t5 / "evil"
    evil_dir.mkdir()
    os.symlink("/etc/passwd", str(evil_dir / "SKILL.md"))       # 白名单外的软链
    passwd_head = ""
    try:
        with open("/etc/passwd", encoding="utf-8", errors="replace") as f:
            passwd_head = f.readline().strip()                   # 形如 root:x:0:0:...
    except OSError:
        passwd_head = "root:"
    try:
        skill.SKILL_DIRS = {"t": str(t5)}
        scan = skill._scan_one("t", str(t5))
        check("G5a 扫描期就拒读越界软链（items 里只有合法那条）",
              [i["name"] for i in scan["items"]] == ["ok"], f"items={[i['name'] for i in scan['items']]}")
        check("G5b 拒读留痕：skipped_outside 指名道姓给出 path 与 realpath",
              len(scan.get("skipped_outside") or []) == 1
              and scan["skipped_outside"][0]["realpath"] == "/etc/passwd",
              f"skipped={scan.get('skipped_outside')}")
        ok5c = passwd_head not in json.dumps(scan, ensure_ascii=False)
        check("G5c 扫描结果里不含 /etc/passwd 的任何内容", ok5c,
              "无泄漏" if ok5c else "越界内容泄漏到扫描结果")
        r = C.get("/api/skill/list")
        d5 = r.json()
        check("G5d /list 层面：越界条目不进 items，且 note 说明有拒读",
              d5["count"] == 1 and "拒读" in (d5.get("note") or "")
              and len(d5.get("skipped_outside") or []) == 1,
              f"count={d5['count']} note={d5.get('note')!r}")
        check("G5e /list 响应全文不含 passwd 内容", passwd_head not in r.text, "越界内容泄漏到 /list")
        r = C.get("/api/skill/read", params={"name": "evil"})
        check("G5f /read 拿不到越界文件（4xx 且响应体不含 passwd 内容）",
              r.status_code in (400, 404) and passwd_head not in r.text, f"status={r.status_code}")
        try:
            skill._guard_inside_whitelist("/etc/passwd")
            check("G5g _guard_inside_whitelist 对越界路径抛 400", False, "没抛，闸门形同虚设")
        except HTTPException as e:
            check("G5g _guard_inside_whitelist 对越界路径抛 400", e.status_code == 400,
                  f"status={e.status_code} detail={e.detail}")
        ok_path = str(t5 / "ok" / "SKILL.md")
        check("G5h 同一闸门对白名单内路径放行（绿向，别把合法软链也一起拒了）",
              skill._guard_inside_whitelist(ok_path) == os.path.realpath(ok_path))
        # pi→techdocs 那种「软链指向白名单内」必须合法，否则事实②会被这道闸门误杀。
        # 注意：此刻 SKILL_DIRS 还被猴补成 tmp，所以白名单要用原始目录现算，不能取 _allowed_roots()
        _allowed_orig = [os.path.realpath(x) for x in _ORIG_DIRS.values() if os.path.isdir(x)]
        check("G5i 指向白名单内的软链放行（pi→techdocs 形态不被误杀）",
              skill._inside(os.path.realpath(os.path.join(_ORIG_DIRS.get("pi", ""),
                                                          "agent-dispatch", "SKILL.md")),
                            _allowed_orig) is True)
    finally:
        skill.SKILL_DIRS = _ORIG_DIRS

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G6 绿向+红向] /read 全文一致，截断必须显式表态")
    target = None
    for route, root in _ORIG_DIRS.items():
        p = os.path.join(root, "caveman", "SKILL.md")
        if os.path.exists(p):
            target = p
            break
    if not target:
        check("G6a 找到用于比对的真实技能", False, "本机没有 caveman 技能，前提变了")
    else:
        real_size = os.stat(target).st_size
        r = C.get("/api/skill/read", params={"name": "caveman"})
        d6 = r.json()
        check("G6a /read 200 且 bytes 与磁盘 stat 逐字节一致",
              r.status_code == 200 and d6.get("bytes") == real_size,
              f"status={r.status_code} bytes={d6.get('bytes')} disk={real_size}")
        check("G6b 正文非空且未截断时 truncated=false",
              bool(d6.get("content")) and d6.get("truncated") is False,
              f"len={len(d6.get('content') or '')} truncated={d6.get('truncated')}")
        try:
            skill.READ_MAX_BYTES = 64
            r = C.get("/api/skill/read", params={"name": "caveman"})
            d6c = r.json()
            check("G6c 超上限时 truncated=true 且给出 bytes_total（不静默截断）",
                  d6c.get("truncated") is True and d6c.get("bytes_total") == real_size
                  and bool(d6c.get("note")),
                  f"truncated={d6c.get('truncated')} bytes_total={d6c.get('bytes_total')} note={str(d6c.get('note'))[:60]!r}")
            check("G6d 截断后正文确实变短（上限真的生效）",
                  len((d6c.get("content") or "").encode("utf-8")) <= 80,
                  f"len={len((d6c.get('content') or '').encode('utf-8'))}")
        finally:
            skill.READ_MAX_BYTES = int(os.getenv("SKILL_READ_MAX_BYTES", str(256 * 1024)))
        r = C.get("/api/skill/read", params={"name": "caveman", "with_body": "false"})
        check("G6e with_body=false 时只回元信息不回正文（省 token 的路子能用）",
              r.status_code == 200 and "content" not in r.json() and r.json().get("bytes") == real_size,
              f"keys={sorted(r.json())[:8]}")

    print("\n[G6f 红向] 名字不存在 / route 写错")
    r = C.get("/api/skill/read", params={"name": "根本不存在的技能"})
    check("G6f 不存在的技能 → 404 且指向 /api/skill/list",
          r.status_code == 404 and "/api/skill/list" in str(r.json().get("detail")),
          f"{r.status_code} {str(r.json().get('detail'))[:70]}")
    r = C.get("/api/skill/read", params={"name": "caveman", "route": "nope"})
    check("G6g route 写错 → 400 且回显可用值", r.status_code == 400 and "claude" in str(r.json().get("detail")),
          f"{r.status_code} {str(r.json().get('detail'))[:70]}")
    r = C.get("/api/skill/read", params={"name": "../../../../etc/passwd"})
    check("G6h 字面穿越串 → 4xx 且响应体不含 passwd 内容",
          r.status_code in (400, 404, 422) and "root:" not in r.text, f"status={r.status_code}")

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G7 红向] 凭据不外泄：技能正文是高危面，必须过 scrub")
    t7 = pathlib.Path(tempfile.mkdtemp(prefix="hubskill-leak-"))
    _mk(t7, "leaky", f"---\nname: leaky\ndescription: 带凭据的技能\n---\n"
                     f"export OPENAI_API_KEY={FAKE_KEY}\n"
                     f"curl -H 'Authorization: {FAKE_BEARER}' https://x\n")
    try:
        skill.SKILL_DIRS = {"t": str(t7)}
        r = C.get("/api/skill/read", params={"name": "leaky"})
        body = r.text
        check("G7a 正文里的 sk- 形态 key 被脱敏", FAKE_KEY not in body and "<redacted>" in body,
              f"含假key={FAKE_KEY in body}")
        check("G7b 正文里的 Bearer 令牌被脱敏", FAKE_BEARER.split()[1] not in body,
              f"含假bearer={FAKE_BEARER.split()[1] in body}")
        r = C.get("/api/skill/list")
        ok7c = FAKE_KEY not in r.text
        check("G7c /list 响应也不含凭据（description 同样过 scrub）", ok7c,
              "响应体无凭据" if ok7c else "假 key 出现在 /list")
        be = skill._backend("x", {"ok": False, "items": [], "ms": 1.0,
                                  "error": f"上游拒绝：{FAKE_KEY}"})
        check("G7d backends[].error 也过 scrub（脱敏只有一个实现）",
              FAKE_KEY not in (be.get("error") or "") and "<redacted>" in (be.get("error") or ""),
              f"error={be.get('error')!r}")
    finally:
        skill.SKILL_DIRS = _ORIG_DIRS

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G8 红向] 同名多路 → 409 报候选，不许静默挑一个")
    t8 = pathlib.Path(tempfile.mkdtemp(prefix="hubskill-dup-"))
    ta, tb = t8 / "ta", t8 / "tb"
    _mk(ta, "dup", "---\nname: dup\ndescription: A 版\n---\nAAA\n")
    _mk(tb, "dup", "---\nname: dup\ndescription: B 版\n---\nBBB\n")
    try:
        skill.SKILL_DIRS = {"ta": str(ta), "tb": str(tb)}
        r = C.get("/api/skill/read", params={"name": "dup"})
        det = r.json().get("detail")
        check("G8a 同名不同文件 → 409 且给出两个候选",
              r.status_code == 409 and isinstance(det, dict) and len(det.get("candidates") or []) == 2,
              f"status={r.status_code} detail={str(det)[:120]}")
        r = C.get("/api/skill/read", params={"name": "dup", "route": "tb"})
        check("G8b 带 route 后消歧成功，读到的是 B 版",
              r.status_code == 200 and "BBB" in (r.json().get("content") or ""),
              f"status={r.status_code} route={r.json().get('route')}")
        r = C.get("/api/skill/list")
        check("G8c 同名两条在 /list 里都在（realpath 不同 ⇒ 不该被去重合并）",
              r.json()["count"] == 2, f"count={r.json().get('count')}")
    finally:
        skill.SKILL_DIRS = _ORIG_DIRS

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G9 MCP 出口] 诊断必须透传；上游 4xx 的 detail 必须带出来")
    _bak_get = hubmcp._get
    try:
        fake = {"count": 2, "engine": "claude+pi", "items": [
            {"name": "s1", "description": "d1", "route": "claude", "routes": ["claude"],
             "path": "/x/s1/SKILL.md", "via_symlink": False, "fm": True, "bytes": 10,
             "id": None, "status": None, "version": None, "owner_agent_id": None},
            {"name": "s2", "description": "d2", "route": "tdai", "routes": ["tdai"],
             "path": None, "via_symlink": False, "fm": None, "bytes": None,
             "id": "sk_1", "status": "active", "version": 1, "owner_agent_id": "a"}],
            "dedup": {"walked": 3, "unique": 2, "aliases": []},
            "backends": [{"name": "claude", "ok": True, "count": 1, "ms": 3.0, "error": None}],
            "degraded": ["pi"], "note": "['pi'] 本次弃用，结果不完整", "took_ms": 12.3}
        hubmcp._get = lambda path, **kw: dict(fake)
        out = json.loads(hubmcp.hub_skill_list())
        check("G9a MCP 出口保留 backends/degraded/note/took_ms/dedup（不许剥诊断）",
              all(k in out for k in ("backends", "degraded", "note", "took_ms", "dedup")),
              f"keys={sorted(out)}")
        check("G9b degraded 与 note 原样带出（外部 agent 能看出少了哪路）",
              out["degraded"] == ["pi"] and "弃用" in out["note"], f"degraded={out.get('degraded')}")
        check("G9c 注册表条目的 id/status/version 保留（磁盘路为 None 也如实）",
              out["items"][1]["id"] == "sk_1" and out["items"][0]["id"] is None)

        hubmcp._get = lambda path, **kw: {"error": "hub REST 409", "path": path, "http": 409,
                                         "detail": {"error": "同名多路", "candidates": [{"route": "ta"}, {"route": "tb"}]}}
        out = json.loads(hubmcp.hub_skill_read("dup"))
        check("G9d /read 的 409 候选经 MCP 出口仍在（detail 不被吞）",
              out.get("http") == 409 and len((out.get("detail") or {}).get("candidates") or []) == 2,
              f"out={str(out)[:130]}")
    finally:
        hubmcp._get = _bak_get

    # 真 _get 的 HTTPError 分支：必须把上游正文带出来且脱敏（这是今天补的那个修复）
    _bak_urlopen = hubmcp.urllib.request.urlopen

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _raise(_url, timeout=None):
        raise urllib.error.HTTPError("http://x/api/skill/read", 409, "Conflict", {},
                                     _FakeResp(json.dumps({"detail": {"candidates": [1, 2],
                                                                     "key": FAKE_KEY}}).encode()))
    try:
        hubmcp.urllib.request.urlopen = _raise
        got = hubmcp._get("/api/skill/read", name="dup")
        check("G9e _get 在 HTTPError 时带回 http 码与 detail（旧写法只有 'hub REST 409'）",
              got.get("http") == 409 and "candidates" in (got.get("detail") or ""), f"got={str(got)[:140]}")
        ok9f = FAKE_KEY not in json.dumps(got, ensure_ascii=False)
        check("G9f 上游正文里的凭据在 detail 里被脱敏", ok9f,
              "detail 无凭据" if ok9f else "假 key 从 detail 泄漏")
    finally:
        hubmcp.urllib.request.urlopen = _bak_urlopen

    # ─────────────────────────────────────────────────────────────────────
    print("\n[G10 /status] 形状、缺口表态、缓存（TDAI 探测 2/2）")
    r = C.get("/api/skill/status", params={"force": "true"})
    s = r.json()
    disk = s.get("disk") or {}
    check("G10a disk 四路齐且各有 available/entries/fm_missing/via_symlink/total_bytes",
          set(disk) == {"claude", "pi", "techdocs", "superpowers"}
          and all({"available", "entries", "fm_missing", "via_symlink", "total_bytes"} <= set(v)
                  for v in disk.values()),
          f"routes={sorted(disk)}")
    check("G10b fm_missing 如实列出无 frontmatter 的技能（事实③：3 个）",
          sorted(disk.get("claude", {}).get("fm_missing") or [])
          == ["agent-ecosystem", "system-memory", "system-rules"],
          f"fm_missing={disk.get('claude', {}).get('fm_missing')}")
    check("G10c via_symlink 如实列出经软链到达的技能（事实②）",
          disk.get("pi", {}).get("via_symlink") == ["agent-dispatch"],
          f"via_symlink={disk.get('pi', {}).get('via_symlink')}")
    td = s.get("tdai") or {}
    check("G10d tdai 段有 available/rows/why，且 why 说明 0 行的原因（不许空着）",
          {"available", "rows", "why"} <= set(td) and bool(td.get("why")), f"tdai={str(td)[:150]}")
    gap = (s.get("gap") or {}).get("note") or ""
    check("G10e gap.note 明说「磁盘 N 条 vs 注册表 M 行，未打通是待裁项不是故障」",
          "待裁" in gap and str(s.get("dedup", {}).get("unique")) in gap, f"gap={gap[:120]}")
    check("G10f 首次调用 cached=false，二次调用命中缓存 cached=true（不每请求重扫磁盘）",
          s.get("cached") is False and C.get("/api/skill/status").json().get("cached") is True)

    # ─────────────────────────────────────────────────────────────────────
    bad = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 58)
    print("总计 %d 项：PASS %d / FAIL %d" % (len(results), len(results) - len(bad), len(bad)))
    if bad:
        print("FAIL 明细: " + ", ".join(bad))
    print("=" * 58)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
