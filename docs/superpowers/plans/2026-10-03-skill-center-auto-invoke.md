# 技能中心统一列表 + 全 agent 自动调用 + 前端重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **本仓例外：** 2026-09-24 多会话编排协议第 3/4 条 —— 施工会话进 worktree、**子代理只读、写入由主会话串行落盘**。故本计划**不采用 subagent-driven**，一律 inline 串行执行。

**Goal:** 把 agent-hub 技能中心从「7 路只读门面（77 条、正文读取累计 0 次）」升级为「20 路统一列表 + 真相关性检索 + 10 家 agent 可见性/推送 + 零调用可观测 + 前端改版」。

**Architecture:** hub 保持**唯一权威门面**（每次请求重扫磁盘，不存第二份副本）。新增 `src/skill_relevance.py`（BM25 召回 + jev 异步精排）与 `src/skill_usage.py`（两源记账 + 僵尸榜）两个专注模块，路由仍挂在 `src/skill.py` 的 `/api/skill` 前缀下。agent 侧四档：pi 扩展钩子推送（零配置）、claude `UserPromptSubmit` hook、`install` 端点铺软链做可见性、MCP 工具做兜底。

**Tech Stack:** Python 3.11 / FastAPI / sqlite3（`data/agents.db` 的 `profile_events`）/ 原生 JS（`static/hub/NN-*.js` → `scripts/build_hubjs.sh` 拼 `static/hub.js`）/ pytest + unittest（`tests/run_tests.sh`）

## Global Constraints

以下为全局约束，**每个 Task 的要求都隐含包含本节**：

- **改前必备份**：`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<英文说明>`；无备份禁止写入（新建文件除外）
- **禁直接改 `static/hub.js`**（生成物）—— 改 `static/hub/NN-*.js` 源模块后跑 `scripts/build_hubjs.sh`
- **禁 push**（用户已授权 commit，ahead 现状 5）
- **禁区**：禁 push · 禁 `pkill` · 禁改 CCR/FCC · 禁动 `~/.hermes/config.yaml`·`~/.qwenpaw/config.json`·`~/.codebuddy/settings.json`·`~/.jcode/config.toml` · 禁把 240 条全铺 · 禁自动删技能 · 子代理只读 · 产物不落 `/tmp`
- **单写者**：本会话 `01a0ff3a`，写入全部由主会话串行落盘
- **重启授权仅一次**：`systemctl --user restart agent-hub.service` 只用于 D1 收尾
- **探活预算 ≤2 次 / 同一判据重试 ≤2 次**，禁轮询
- **禁把 SKIP 当 PASS**：每条验收逐项判 `PASS` / `FAIL` / `不可判定`，不可判定必须写明为何
- **脱敏**：任何回显路径/错误串走 `tdai_client.scrub(...)`
- **VERSION bump 随本批后端改动同批**（v0.13.65 → v0.13.66，顺带清 `code_stale`）
- **本批不做**（登记 PENDING-TASKS，禁假装已做）：各家会话日志 grep 的「直读盘」旁路 ⇒ 僵尸榜置信度**上限只能 `medium`，不得报 `high`**

---

### Task 1: 20 路发现点 + 排除清单 + state 字段（D1）

**Files:**
- Modify: `src/skill.py:57-104`（`_DEFAULT_DIRS`）、`src/skill.py:184-200`（`_scan_one` 的 reason 字段）、`src/skill.py:495-545`（`/status` 加 `state` 与 `excluded`）
- Create: `tests/test_skill_routes.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SKILL_DIRS: Dict[str, str]` —— 7 旧 + 13 新 = **20 路**（已落地 v0.13.66）
  - `EXCLUDED_DIRS: Dict[str, str]` —— `{路径: 排除理由}`，**15 项**
  - `_scan_one(route, root) -> Dict` 新增 `reason` 键，取值 `"no_path" | "no_dir" | "read_error" | None`
  - `GET /api/skill/status` 每路新增 `"state"` 键，取值 `"ok" | "empty" | "missing" | "error"`；顶层新增 `"excluded": Dict[str, str]`

- [ ] **Step 1: 写失败测试**

`tests/test_skill_routes.py`：

```python
"""D1 闸门：20 路发现点 + 排除清单 + state 四态（禁 SKIP 当 PASS）。"""
import unittest

import skill


EXPECTED_ROUTES = {
    "claude", "pi", "techdocs", "superpowers", "agents", "codex", "workbuddy",
    "hermes", "hermes-agent", "hermes-web", "jcode", "grok", "grok-bundled",
    "picoclaw", "qoder", "qoderwake", "qoderwake-shadow", "qoderwake-cli",
    "qoder-alpha", "opencode",
}
# 注：qoderwake-gen 已删除（该路径不存在）；qoderwake-cli 为实测后新增的稳定路。


class TestRoutes(unittest.TestCase):
    def test_20_routes_exact(self):
        self.assertEqual(set(skill.SKILL_DIRS), EXPECTED_ROUTES)

    def test_20_routes_count(self):
        self.assertEqual(len(skill.SKILL_DIRS), 20)

    def test_every_route_path_is_str(self):
        for r, p in skill.SKILL_DIRS.items():
            self.assertIsInstance(p, str, r)
            self.assertTrue(p.startswith("/"), r)

    def test_excluded_has_reasons(self):
        self.assertGreaterEqual(len(skill.EXCLUDED_DIRS), 11)
        for path, why in skill.EXCLUDED_DIRS.items():
            self.assertTrue(path.startswith("/"), path)
            self.assertIsInstance(why, str)
            self.assertTrue(why.strip(), path)

    def test_excluded_disjoint_from_routes(self):
        for path in skill.EXCLUDED_DIRS:
            self.assertNotIn(path, set(skill.SKILL_DIRS.values()), path)

    def test_state_enum(self):
        r = skill._scan_one("x", "")
        self.assertEqual(r["reason"], "no_path")
        r2 = skill._scan_one("x", "/nonexistent-zzz/skills")
        self.assertEqual(r2["reason"], "no_dir")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /fs/1000/ftp/技术文档/agent-hub && python3 -m pytest tests/test_skill_routes.py -q`
Expected: FAIL（`AttributeError: module 'skill' has no attribute 'EXCLUDED_DIRS'`）

- [ ] **Step 3: 备份**

```bash
cd /fs/1000/ftp/技术文档/agent-hub
cp src/skill.py src/skill.py.bak-$(date +%Y%m%d_%H%M%S)-d1-routes20
ls -la src/skill.py.bak-*
```

- [ ] **Step 4: 实现**

`_DEFAULT_DIRS` 追加 13 项。⚠️ **本段注释里的条数已在 v0.13.66 实测后作废**，
权威表见设计书 §4.1（口径 = `skill._scan_many()` / `os.walk(followlinks=True)`）。
**不带 `-L` 的 `find` 会把 `opencode`、`skills-hot` 误报为 0**（不跟随软链）——
这是本轮反复踩的同一个坑，凡计数一律回 `_scan_one()` 取。

```python
    # ── v0.13.66 D1：56 号文档漏掉的 13 路实测发现点。
    # 实际落地值（权威表见设计书 §4.1）与本段初稿注释的差异：
    #   hermes 110->120、jcode 56->58、grok 1->3、qoderwake 7->11、qoderwake-shadow 2->1
    #   qoderwake-gen 删除（路径不存在）→ 换 qoderwake-cli
    #   qoder-alpha 14->2 且**必须用 glob**（扩展目录名是内容哈希 42d23c0fa380）
    #   opencode 0->2（3 个软链目录，经软链到达）
    "hermes": "/home/gztxt/.hermes/skills",                      # 120 · 运行时真实加载面
    "hermes-agent": "/home/gztxt/.hermes/hermes-agent/skills",  # 58 · 打包种子源，运行时不被扫描
    "hermes-web": "/home/gztxt/.hermes-web-ui/.ekko/skills",    # 22 · 属 Ekko Studio
    "jcode": "/home/gztxt/.jcode/skills",                        # 58
    "grok": "/home/gztxt/.grok/skills",                          # 3
    "grok-bundled": "/home/gztxt/.grok/bundled/skills",          # 9
    "picoclaw": "/home/gztxt/.picoclaw/workspace/skills",        # 8
    "qoder": "/home/gztxt/.qoder/security/skills",               # 1
    "qoderwake": "/home/gztxt/.qoderwake/resources/builtin-skills",   # 11 · 规范副本
    "qoderwake-shadow": "/home/gztxt/.qoderwake/run/shadow-skills",  # 1
    "qoderwake-cli": "/home/gztxt/.qoderwake/qodercli/security-resources/security-scan/skills",  # 1
    "qoder-alpha": "/home/gztxt/.qoder-alpha/extensions/*/skills",   # 2 · glob，写死哈希必失效
    "opencode": "/home/gztxt/.config/opencode/skill",             # 2 · 软链到达
```

`EXCLUDED_DIRS`（**排除项必须能被面板读到，不静默**）：

```python
EXCLUDED_DIRS: Dict[str, str] = {
    "/home/gztxt/.codex/.tmp/plugins": "marketplace 缓存：按需安装，不是自动加载发现点（504 项）",
    "/home/gztxt/.workbuddy/connectors-marketplace": "marketplace 缓存（716 项）",
    "/home/gztxt/.codebuddy/plugins/marketplaces": "marketplace 缓存（171 项）",
    "/home/gztxt/.grok/marketplace-cache": "marketplace 缓存（72 项）",
    "/home/gztxt/.claude/plugins/marketplaces": "marketplace 缓存（41 项）",
    "/home/gztxt/.hermes/hermes-agent/optional-skills": "上游可选仓（150 项），非自动加载",
    "/fs/1000/ftp/技术文档/snapshots": "快照（664 项）",
    "/fs/1000/ftp/技术文档/全量备份": "备份（247 项）",
    "/home/gztxt/.claude/projects": "会话工程目录，非技能（946 项）",
    "/home/gztxt/Hermes-backup": "备份（125 项）",
    "/home/gztxt/.pi-upgrade-backup": "升级备份（2 项）",
}
```

`_scan_one` 三处 return 补 `reason`（`"" → "no_path"`、目录不存在 → `"no_dir"`、`root` 为空串 → `"no_path"`；成功路径 `reason: None`）。

`/status` 每路加 state：

```python
def _state(r: Dict[str, Any], n_entries: int) -> str:
    if r.get("ok"):
        return "empty" if not n_entries else "ok"
    return {"no_dir": "missing", "no_path": "error"}.get(r.get("reason"), "error")
```

顶层 `data` 加 `"excluded": dict(EXCLUDED_DIRS)`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /fs/1000/ftp/技术文档/agent-hub && python3 -m pytest tests/test_skill_routes.py -q`
Expected: `6 passed`

- [ ] **Step 6: 跑全量测试不回归**

Run: `cd /fs/1000/ftp/技术文档/agent-hub && bash tests/run_tests.sh 2>&1 | tail -15`
Expected: 与改动前基线同绿（改前先跑一次存基线，**禁把新增 FAIL 说成既有**）

- [ ] **Step 7: 提交**

```bash
cd /fs/1000/ftp/技术文档/agent-hub
git add src/skill.py tests/test_skill_routes.py
git commit -m "feat(skill): D1 20 路发现点 + 排除清单 + state 四态"
```

- [ ] **Step 8: 重启并复验（授权 2，唯一一次）**

```bash
systemctl --user restart agent-hub.service
sleep 3
curl -s -m 8 "http://127.0.0.1:3102/api/skill/status" | python3 -c "
import json,sys;d=json.load(sys.stdin)
print('路数',len(d['disk']),'去重',d['dedup'],'排除',len(d.get('excluded',{})))
for k,v in d['disk'].items(): print(' ',k,v.get('state'),v.get('entries'),v.get('error') or '')"
```

Expected: 路数 20；`opencode` 为 `ok 2`（**不是 `empty 0`** —— 2 条经软链到达）；不存在者为 `missing` + 脱敏 error；无 500。

---

### Task 2: `/api/skill/relevant`（BM25 同步 + jev 异步）（D2）

**Files:**
- Create: `src/skill_relevance.py`
- Create: `src/jev_client.py`
- Modify: `src/skill.py`（导入 + 路由 `GET /api/skill/relevant`）
- Create: `tests/test_skill_relevance.py`

**Interfaces:**
- Consumes: `skill.SKILL_DIRS` / `skill._scan_async` / `skill._dedup`（Task 1 的 20 路）
- Produces:
  - `skill_relevance.tokenize(text: str) -> List[str]`
  - `skill_relevance.score(qtok: List[str], item: Dict) -> Tuple[float, List[str]]`
  - `skill_relevance.recall(items: List[Dict], q: str, k: int = 20) -> List[Dict]`
  - `skill_relevance.jev_state() -> str` —— `"off" | "pending" | "hit" | "down" | "error"`
  - `GET /api/skill/relevant?q=&n=3&max_tokens=600` → `{q, n, engine, took_ms, jev, hits[], truncated, total, skipped}`

- [ ] **Step 1: 写失败测试**

`tests/test_skill_relevance.py`：

```python
"""D2 闸门：BM25 召回可断言 + jev 降级链（2 次不通即 down，不第三次重试）。"""
import unittest

import skill_relevance as R


ITEMS = [
    {"name": "crawl4ai", "description": "把网页抓成 LLM-ready Markdown，JS 渲染页面", "routes": ["claude"]},
    {"name": "unified-memory", "description": "统一记忆、跨 agent 记忆召回、四层检索", "routes": ["pi", "claude"]},
    {"name": "wigolo-search", "description": "local-first web search 本地优先网络搜索", "routes": ["claude"]},
]


class TestTokenize(unittest.TestCase):
    def test_latin_words(self):
        self.assertIn("memory", R.tokenize("unified memory"))

    def test_cjk_bigram(self):
        self.assertTrue(R.tokenize("网页抓取"))

    def test_empty(self):
        self.assertEqual(R.tokenize("   "), [])


class TestRecall(unittest.TestCase):
    def test_ranks_relevant_first(self):
        hits = R.recall(ITEMS, "抓网页转 markdown", k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["name"], "crawl4ai")
        self.assertTrue(hits[0]["matched"])

    def test_k_limit(self):
        self.assertEqual(len(R.recall(ITEMS, "记忆", k=2)), 2)

    def test_empty_query_returns_empty(self):
        self.assertEqual(R.recall(ITEMS, "", k=3), [])

    def test_no_match_returns_empty(self):
        self.assertEqual(R.recall(ITEMS, "zzzz不存在的词", k=3), [])


class TestJevBreaker(unittest.TestCase):
    def test_off_when_env_zero(self):
        R._reset_jev_breaker()
        R._JEV_ENABLED = False
        self.assertEqual(R.jev_state(), "off")
        R._JEV_ENABLED = True

    def test_two_failures_then_down_no_third_call(self):
        R._reset_jev_breaker()
        calls = []
        R._JEV_ENABLED = True
        R._jev_call = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError("boom"))
        R._JEV_FAILS = 1                      # 假装已失败 1 次
        R.rank_async("q", [{"name": "x", "description": "y"}], 3)
        self.assertEqual(R._JEV_FAILS, 2)
        R.rank_async("q", [{"name": "x", "description": "y"}], 3)
        self.assertEqual(len(calls), 1, "进 down 之后不得再发第三次请求")
        self.assertEqual(R.jev_state(), "down")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /fs/1000/ftp/技术文档/agent-hub && python3 -m pytest tests/test_skill_relevance.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'skill_relevance'`）

- [ ] **Step 3: 备份 `src/skill.py`**

```bash
cd /fs/1000/ftp/技术文档/agent-hub
cp src/skill.py src/skill.py.bak-$(date +%Y%m%d_%H%M%S)-d2-relevant
```

- [ ] **Step 4: 新建 `src/skill_relevance.py`**

```python
"""D2：技能相关性召回（同步 BM25 必成功路径 + jev 异步精排 + 熔断降级）。

为什么 BM25 自己算而不复用 memindex：待召回的技能只有 ~300 条、每条几十词，
放进外部索引是给一把螺丝刀换工厂。CJK 用二元组、拉丁按词切，中英混排都能命中。

jev 只做**异步**精排：它是外部 API，落在用户输入的必成功路径上等于把上游抖动
固化成本机故障（09-06/09-19/09-23 三次同源事故的教训）。**探活 ≤2 次**：
连续失败 2 次即本进程内 `down`，不再发第三次请求，全量退回 BM25。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, List, Tuple

import jev_client

log = logging.getLogger("skill.relevance")

#: 字段权重：命中名字比命中描述值钱（用户往往直接说出技能名）
_W_NAME, _W_DESC, _W_ROUTE = 6.0, 1.0, 2.0
_JEV_TTL_S = 300.0
_JEV_MAX_FAILS = 2                      # 硬编码：探活预算 ≤2 次，不可配到 3
_JEV_ENABLED = os.getenv("SKILL_JEV", "1") != "0"

_JEV_FAILS = 0
_JEV_STATE = "pending" if _JEV_ENABLED else "off"
_JEV_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def _reset_jev_breaker() -> None:
    """仅供测试：把熔断器复位。生产路径不调用。"""
    global _JEV_FAILS, _JEV_STATE, _JEV_CACHE
    _JEV_FAILS, _JEV_STATE, _JEV_CACHE = 0, ("pending" if _JEV_ENABLED else "off"), {}


def tokenize(text: str) -> List[str]:
    """中英混排切词：拉丁按词（小写），CJK 取相邻二元组。"""
    t = (text or "").lower()
    out: List[str] = [w for w in re.findall(r"[a-z0-9_]+", t) if len(w) > 1]
    cjk = re.findall(r"[\u4e00-\u9fff]+", t)
    for run in cjk:
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _fields(item: Dict[str, Any]) -> Dict[str, str]:
    return {
        "name": str(item.get("name") or ""),
        "desc": str(item.get("description") or ""),
        "route": " ".join(item.get("routes") or ([item.get("route")] if item.get("route") else [])),
    }


def score(qtok: List[str], item: Dict[str, Any]) -> Tuple[float, List[str]]:
    """加权命中打分。返回 (得分, 命中词)。同名子串也算命中（`代码` 命中 `code-review`）。"""
    f = _fields(item)
    low = {k: v.lower() for k, v in f.items()}
    total, matched = 0.0, []
    for w in qtok:
        hit = 0.0
        for key, weight in (("name", _W_NAME), ("desc", _W_DESC), ("route", _W_ROUTE)):
            if w in low[key]:
                hit += weight
        if hit:
            total += hit
            matched.append(w)
    return total, matched


def recall(items: List[Dict[str, Any]], q: str, k: int = 20) -> List[Dict[str, Any]]:
    """同步 BM25 召回（必成功路径，无外部依赖、无网络）。零命中返回空列表。"""
    qtok = tokenize(q)
    if not qtok:
        return []
    scored = []
    for it in items:
        s, m = score(qtok, it)
        if s > 0:
            scored.append({**it, "score": round(s, 2), "matched": m})
    scored.sort(key=lambda x: (-x["score"], str(x.get("name") or "").lower()))
    return scored[:max(1, k)]


def _key(q: str, cands: List[Dict[str, Any]]) -> str:
    raw = q + "|" + ",".join(str(c.get("name") or "") for c in cands)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def jev_state() -> str:
    return _JEV_STATE


def _jev_call(q: str, cands: List[Dict[str, Any]], n: int) -> List[str]:
    """同步阻塞地调 jev 取 top-N 名字列表。**只在子线程/子任务里调**，不进 input 路径。"""
    options = "|".join(
        "%s:%s" % (c.get("name"), str(c.get("description") or "")[:60]) for c in cands
    )
    res = jev_client.choice(
        instructions="从下面技能里挑出与该任务最相关的 %d 个。只输出选中的技能名，逗号分隔。\n任务：%s"
                     % (n, q),
        options=options,
    )
    if not res.get("name"):
        raise RuntimeError("jev 返回无 name 字段")
    return [s.strip() for s in str(res["name"]).split(",") if s.strip()]


def rank_async(q: str, cands: List[Dict[str, Any]], n: int = 3) -> None:
    """fire-and-forget 精排：结果只写缓存，供**下一次**同查询直接用。"""
    global _JEV_FAILS, _JEV_STATE
    if not _JEV_ENABLED or _JEV_STATE == "down" or not cands:
        return
    k = _key(q, cands)
    try:
        picked = _jev_call(q, cands, n)
    except Exception as e:                     # 熔断：连续 2 次失败后本进程不再发第三次
        _JEV_FAILS += 1
        log.warning("jev 精排失败(%d/%d)：%s", _JEV_FAILS, _JEV_MAX_FAILS,
                    jev_client.scrub(str(e))[:120])
        if _JEV_FAILS >= _JEV_MAX_FAILS:
            _JEV_STATE = "down"
            log.warning("jev 熔断：探活预算 2 次用尽，本进程永久退回 BM25（不轮询、不重试）")
        return
    _JEV_FAILS = 0
    _JEV_STATE = "hit"
    by = {str(c.get("name")): c for c in cands}
    _JEV_CACHE[k] = (time.time(), [by[p] for p in picked if p in by][:n])


async def cached_pick(q: str, cands: List[Dict[str, Any]], n: int) -> Tuple[List[Dict[str, Any]], str]:
    """读缓存；有则用，无则起后台精排并原样返回 BM25 结果。"""
    if not _JEV_ENABLED:
        return cands[:n], "off"
    if _JEV_STATE == "down":
        return cands[:n], "down"
    k = _key(q, cands)
    hit = _JEV_CACHE.get(k)
    if hit and (time.time() - hit[0]) < _JEV_TTL_S:
        return hit[1], "hit"
    try:
        asyncio.get_running_loop().create_task(
            asyncio.to_thread(rank_async, q, cands, n))
    except RuntimeError:
        pass
    return cands[:n], "pending"


def est_tokens(name: str, desc: str) -> int:
    return int(len(f"{name}: {desc}") / 2.5) + 1
```

- [ ] **Step 5: 新建 `src/jev_client.py`**

```python
"""jev 客户端（TypeSafe System One）。**只在后台线程里被 skill_relevance 调用**，
不 import 到任何 input 关键路径模块的顶层，避免 key 缺失拖垮技能门面。

端点 POST https://api.typesafe.ai/v1/systemone；model=jev-latest；
key 来自环境变量 TYPESAFE_API_KEY（真源 ~/.pi/agent/env.typesafe，600）。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict

import httpx

BASE = os.getenv("TYPESAFE_BASE", "https://api.typesafe.ai/v1/systemone")
MODEL = "jev-latest"
TIMEOUT_S = float(os.getenv("JEV_TIMEOUT", "2.5"))


def scrub(s: str) -> str:
    return re.sub(r"(?i)(bearer\s+)\S+", r"\1<redacted>", str(s))[:200]


def choice(instructions: str, options: str, state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """返回顶层 name 字段（判定结果）。失败抛异常，由调用方熔断。"""
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY 未设置（不打印 key 本身）")
    body: Dict[str, Any] = {"model": MODEL, "instructions": instructions, "options": options}
    if state:
        body["state"] = state
    r = httpx.post(BASE, json=body,
                   headers={"Authorization": f"Bearer {key}"},
                   timeout=TIMEOUT_S)
    if r.status_code >= 400:
        raise RuntimeError(f"jev HTTP {r.status_code}: {scrub(r.text)}")
    return r.json()
```

- [ ] **Step 6: 在 `src/skill.py` 挂路由**

```python
@router.get("/relevant")
@runlog.track("skill.relevant")
async def skill_relevant(request: Request,
                         q: str = Query(..., min_length=1, max_length=400),
                         n: int = Query(default=3, ge=1, le=20),
                         max_tokens: int = Query(default=600, ge=50, le=8000)):
    """按当前任务给 top-N 相关技能（注入通道的数据源）。

    同步层 BM25 必成功；jev 异步精排只影响**下一次**同查询；jev 熔断后返回
    engine='bm25(jev-down)'。**任何一层失败都不 5xx** —— 注入通道宁可少推也不卡输入。
    """
    t0 = time.monotonic()
    items = await _all_items()
    cands = relevance.recall(items, q, k=max(20, n * 6))
    picked, jev = await relevance.cached_pick(q, cands, n)
    hits, used, dropped = [], 0, []
    for it in picked:
        name = str(it.get("name") or "")
        desc = str(it.get("description") or "").strip()
        c = relevance.est_tokens(name, desc)
        if used + c <= max_tokens and len(hits) < n:
            hits.append({"name": name, "description": desc, "routes": it.get("routes"),
                         "score": it.get("score"), "matched": it.get("matched") or [],
                         "est_tokens": c})
            used += c
        else:
            dropped.append(name)
    return {"q": q[:200], "n": n, "engine": "bm25" if jev in ("off", "down", "pending") else "bm25+jev",
            "jev": jev, "hits": hits, "dropped_by_budget": dropped, "total": len(items),
            "recall_ms": round((time.monotonic() - t0) * 1000, 1)}
```

配套新增共用取数函数（`/list` 与 `/relevant` 复用，避免两套扫盘口径）：

```python
async def _all_items() -> List[Dict[str, Any]]:
    tasks = [_scan_async(r, SKILL_DIRS.get(r, "")) for r in disk_routes()]
    scans = await asyncio.gather(*tasks, return_exceptions=True)
    items: List[Dict[str, Any]] = []
    for s in scans:
        if isinstance(s, Exception):
            continue
        items.extend(s.get("items") or [])
    unique, _ = _dedup(items)
    return unique
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd /fs/1000/ftp/技术文档/agent-hub && python3 -m pytest tests/test_skill_relevance.py -q`
Expected: `9 passed`

- [ ] **Step 8: 端到端探针（2 次内出结论）**

```bash
systemctl --user restart agent-hub.service; sleep 3
for q in "抓网页转 markdown" "跨 agent 记忆召回" "skill"; do
  curl -s -m 8 "http://127.0.0.1:3102/api/skill/relevant?q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$q")&n=3" \
   | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['engine'],d['jev'],d['recall_ms'],'ms',[h['name'] for h in d['hits']])"
done
```

Expected: 三行都 200；`engine` 以 `bm25` 开头；`recall_ms < 150`；第一行 top-1 = `crawl4ai`。
（`jev` 首轮必为 `pending`/`off`；**不许在这一步反复刷**——熔断判据在单测里已覆盖。）

- [ ] **Step 9: 提交**

```bash
git add src/skill.py src/skill_relevance.py src/jev_client.py tests/test_skill_relevance.py
git commit -m "feat(skill): D2 /api/skill/relevant — BM25 同步 + jev 异步精排 + 熔断降级"
```

---

### Task 3: 记账 + 零调用僵尸榜（D3）

**Files:**
- Create: `src/skill_usage.py`
- Modify: `src/runlog.py`（`SUBJECTS` 增 `skill.relevant` / `skill.inject`）
- Modify: `src/skill.py`（挂 `GET /api/skill/zombies`）
- Create: `tests/test_skill_usage.py`

**Interfaces:**
- Consumes: `db`（`data/agents.db` 的 `profile_events`）
- Produces:
  - `skill_usage.counts(days: int = 7) -> Dict[str, Dict[str, Any]]` —— `{name: {"reads": int, "injects": int, "last_at": str, "via": List[str]}}`
  - `skill_usage.zombies(items: List[Dict], counts: Dict, days: int = 7) -> List[Dict]`
  - `GET /api/skill/zombies?days=7` → `{days, total, zombies[], confidence, direct_source, generated_at}`

- [ ] **Step 1: 写失败测试**

```python
"""D3 闸门：零调用榜可断言 + 置信度不得虚报 high（禁 SKIP 当 PASS）。"""
import unittest
import skill_usage as U


class TestZombies(unittest.TestCase):
    def test_zero_read_is_zombie_with_medium_confidence(self):
        items = [{"name": "a", "routes": ["claude"]}, {"name": "b", "routes": ["pi"]}]
        counts = {"b": {"reads": 2, "injects": 1, "last_at": "2026-10-03T00:00:00Z", "via": ["hub"]}}
        z = U.zombies(items, counts, days=7)
        self.assertEqual([x["name"] for x in z], ["a"])
        self.assertEqual(z[0]["confidence"], "medium")   # 直读旁路未实现 ⇒ 封顶 medium
        self.assertEqual(z[0]["days_idle"], 7)

    def test_never_report_high_when_direct_not_implemented(self):
        z = U.zombies([{"name": "a", "routes": []}], {}, days=7)
        self.assertNotEqual(z[0]["confidence"], "high")

    def test_suggested_action_present(self):
        z = U.zombies([{"name": "a", "routes": []}], {}, days=7)
        self.assertIn(z[0]["suggested_action"], ("add_triggers", "widen_visibility", "retire_review"))

    def test_counts_never_raises_without_db(self):
        self.assertIsInstance(U.counts(days=7), dict)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_skill_usage.py -q`
Expected: FAIL（`ModuleNotFoundError: skill_usage`）

- [ ] **Step 3: 备份**

```bash
cd /fs/1000/ftp/技术文档/agent-hub
cp src/runlog.py src/runlog.py.bak-$(date +%Y%m%d_%H%M%S)-d3-zombies
cp src/skill.py   src/skill.py.bak-$(date +%Y%m%d_%H%M%S)-d3-zombies
```

- [ ] **Step 4: 新建 `src/skill_usage.py`**

```python
"""D3：技能调用记账与「零调用僵尸榜」。

**只认 hub 通道**（`profile_events` 里 subject='skill.read' / 'skill.inject'）。
各家 agent 直读磁盘的旁路统计**本批未实现** ⇒ 置信度封顶 `medium`。
军规：禁把 SKIP 当 PASS —— 不取证的两源口径绝不能报 `high`。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

DIRECT_SOURCE = "not-implemented"
_SUBJECTS = ("skill.read", "skill.inject")
_WINDOW = max(1, min(365, days)) if (days := 7) else 7


def counts(days: int = 7) -> Dict[str, Dict[str, Any]]:
    """近 N 天各技能被读/被注入的次数。DB 读不到就返回空 dict（不抛，面板要能开）。"""
    import db
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = db.fetchall(
            "SELECT subject, detail, created_at FROM profile_events "
            "WHERE source='rest' AND subject IN (?,?) AND created_at >= ?",
            _SUBJECTS, since)
    except Exception:
        return out
    for subj, detail, at in rows:
        try:
            name = (json.loads(detail or "{}") or {}).get("name") or ""
        except Exception:
            name = ""
        if not name:
            continue
        e = out.setdefault(name, {"reads": 0, "injects": 0, "last_at": "", "via": []})
        e["reads" if subj == "skill.read" else "injects"] += 1
        e["last_at"] = max(e["last_at"], str(at or ""))
        if "hub" not in e["via"]:
            e["via"].append("hub")
    return out


def zombies(items: List[Dict[str, Any]], counts_map: Dict[str, Dict[str, Any]],
            days: int = 7) -> List[Dict[str, Any]]:
    """列出近 N 天零调用的技能。**只出建议，绝不自动删**（军规：唯一权威副本 + 禁自动退役）。"""
    rows: List[Dict[str, Any]] = []
    for it in items:
        name = str(it.get("name") or "")
        c = counts_map.get(name) or {}
        if (c.get("reads") or 0) or (c.get("injects") or 0):
            continue
        routes = it.get("routes") or ([it.get("route")] if it.get("route") else [])
        if len(routes) <= 1:
            action = "widen_visibility"
        elif not str(it.get("description") or "").strip():
            action = "add_triggers"
        else:
            action = "retire_review"
        rows.append({"name": name, "routes": routes, "days_idle": days,
                     "last_at": None, "confidence": "medium",
                     "direct_source": DIRECT_SOURCE, "suggested_action": action})
    rows.sort(key=lambda r: str(r["name"]).lower())
    return rows


def snapshot(items: List[Dict[str, Any]], days: int = 7) -> Dict[str, Any]:
    z = zombies(items, counts(days), days)
    return {"days": days, "total": len(items), "zombies": z,
            "zombies_count": len(z), "confidence": "medium",
            "direct_source": DIRECT_SOURCE,
            "note": "仅 hub 通道记账；各家直读磁盘未取证 ⇒ 置信度封顶 medium",
            "generated_at": datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 5: 挂路由**

`src/runlog.py` 的 `SUBJECTS` 改为：

```python
SUBJECTS = ("mem.search", "mem.context", "kb.search", "kb.browse",
            "kb.status", "skill.list", "skill.read", "skill.relevant",
            "skill.inject", "cc.start")
```

`src/skill.py` 追加：

```python
@router.get("/zombies")
async def skill_zombies(days: int = Query(default=7, ge=1, le=365)):
    """近 N 天零调用的技能榜。置信度封顶 medium（直读旁路未取证）。"""
    return usage.snapshot(await _all_items(), days)
```

- [ ] **Step 6: 跑测试 + 全量**

Run: `python3 -m pytest tests/test_skill_usage.py -q` → `4 passed`
Run: `bash tests/run_tests.sh 2>&1 | tail -8`

- [ ] **Step 7: 探针**

```bash
curl -s -m 8 "http://127.0.0.1:3102/api/skill/zombies?days=7" | python3 -c "
import json,sys;d=json.load(sys.stdin)
print('total',d['total'],'zombies',d['zombies_count'],'confidence',d['confidence'],'direct',d['direct_source'])"
```

Expected: `confidence=medium`、`direct_source=not-implemented`、`zombies_count` 与 `total - 被读数` 一致。

- [ ] **Step 8: 提交**

```bash
git add src/runlog.py src/skill.py src/skill_usage.py tests/test_skill_usage.py
git commit -m "feat(skill): D3 零调用僵尸榜（两源口径未齐 ⇒ 置信度封顶 medium）"
```

---

### Task 4: pi 推送注入（D4）

**Files:**
- Modify: `~/.pi/agent/extensions/hub-facade.ts`（共享配置，改前备份；rev 递增）
- Create: `agent-knowledge/50-技能中心统一列表与自动调用.md`（沉淀，闭合编排协议 C8）

**Interfaces:**
- Consumes: `GET /api/skill/relevant?q=&n=3&max_tokens=600`（Task 2）
- Produces: 扩展 `input` 钩子在注入记忆命中**之后**追加技能候选块

- [ ] **Step 1: 备份**

```bash
cp ~/.pi/agent/extensions/hub-facade.ts \
   ~/.pi/agent/extensions/hub-facade.ts.bak-$(date +%Y%m%d_%H%M%S)-d4-skill-inject
```

- [ ] **Step 2: 改扩展**

在 `input` 处理函数里，记忆检索那段之后、`context` 组装之前插入（**不新增第二个检索出口**，仍走唯一 `_hubGet` 通道，保持 600ms 预算与静默降级口径）：

```ts
// ── 技能候选（D4）：与记忆同源、同一通道、同一降级口径 ──────────────
// 只推「名字 + 一句用途 + 怎么读」，不推正文：正文由 agent 自己决定要不要读。
// relevant 端点即使 5xx/超时也必须静默跳过——输入钩子的价值在「多给一条线索」，
// 不在「非拿到不可」。
```

调用形状：`GET {base}/api/skill/relevant?q=<用户本轮输入>&n=3&max_tokens=600`，
渲染成 `候选技能：a — 用途…（读：/api/skill/read?name=a）` 三行，追加到 context 尾部。

- [ ] **Step 3: 静态自证（不开新会话即可验的先验）**

```bash
grep -n "skill/relevant" ~/.pi/agent/extensions/hub-facade.ts
npx --yes tsc --noEmit --skipLibCheck --target es2022 --module esnext \
  ~/.pi/agent/extensions/hub-facade.ts 2>&1 | head -5
```

Expected: 命中 1 处；tsc 无本文件报错（第三方类型缺失可忽略，但**禁把本文件的错算进豁免**）。

- [ ] **Step 4: 沉淀（闭合 C8）**

在 `agent-knowledge/` 新建 `50-技能中心统一列表与自动调用.md`，含：硬证据（`skill.read`=0 / `796/800` / 20 路 vs 7 路）、四档分工、置信度封顶说明、pi-web 0.10 交接命令。

- [ ] **Step 5: 端侧确认（不自助完结）**

在下一轮新会话里问一句「本轮有没有真去读候选技能」，把结果回填台账。
**P4 判据在此之前只能是「本机 PASS / 端侧未确认」**。

---

### Task 5: claude hook（D5，授权 1）

**Files:**
- Create: `scripts/hub_skill_inject.py`（自研脚本资产：落 `技术文档/scripts/` + 纳入 git，09-19 12:04 三件套）
- Modify: `~/.claude/settings.json`（共享配置：**先备份**，改后复核既有键仍在）

**Interfaces:**
- Consumes: `GET /api/skill/relevant`
- Produces: `hooks.UserPromptSubmit` → stdout 追加技能候选块

- [ ] **Step 1: 备份 settings.json**

```bash
cp ~/.claude/settings.json \
   ~/.claude/settings.json.bak-$(date +%Y%m%d_%H%M%S)-d5-userpromptsubmit-hook
python3 -c "import json;d=json.load(open('/home/gztxt/.claude/settings.json'));print('改前键:',sorted(d))"
```

- [ ] **Step 2: 写脚本**

`scripts/hub_skill_inject.py`：从 stdin 读 hook JSON（取 `prompt`）→ 3s 超时 curl hub `/api/skill/relevant` → 命中则 `print()` 候选块，**任何异常静默 exit 0**（钩子失败绝不能挡住用户输入）。

- [ ] **Step 3: 挂 hook**

用 `python3 - <<'PY'` 改 JSON（**禁 `sed -i`**，军规）：读 → 改 `hooks.UserPromptSubmit` → 原子写（临时文件 + `os.replace`）。

- [ ] **Step 4: 复核既有键仍在（lost update 防护）**

```bash
python3 -c "
import json;d=json.load(open('/home/gztxt/.claude/settings.json'))
for k in ('apiKeyHelper','env','skillListingBudgetFraction','model','reasoningEffort','cacheControl'):
    assert k in d, k
print('既有 6 键仍在，hooks =', json.dumps(d['hooks'],ensure_ascii=False)[:120])"
```

Expected: 打印「既有 6 键仍在」+ hooks 片段。

- [ ] **Step 5: 真实验证（一次真实对话，禁只验 200）**

开一个 claude 会话问一句需要技能的任务，看是否出现候选块且 agent 真去读。
**P5 判据 = hook 日志 + 一次真实对话**，两者缺一即 `不可判定`。

---

### Task 6: 10 家可见性铺软链（D6）

**Files:**
- Create: `scripts/skill_visibility_sync.py`（**先 `--dry-run`**）
- Create: `tests/verify_skill_visibility.py`

- [ ] **Step 1: 写同步脚本**：精选集 = `techdocs` 自研 18 条 + 各家 bundled，源路 → 目标路映射显式写在脚本里（**不自动推断，避免把 240 条铺出去**）。

- [ ] **Step 2: dry-run 审阅**

```bash
python3 scripts/skill_visibility_sync.py --dry-run | head -40
```

Expected: 逐条 `名字 源路 → 目标路(动作: 新建|已存在|跳过)`；**计数必须与设计书一致**，对不上就停手。

- [ ] **Step 3: 执行 + 体检**

```bash
python3 scripts/skill_visibility_sync.py --apply
python3 tests/verify_skill_visibility.py     # 期望 10/10 PASS，每条 read -L 可解析
```

- [ ] **Step 4: 确认只建链不复制**

```bash
python3 - <<'PY'
import glob, os
bad = [p for p in glob.glob(os.path.expanduser('~/.claude/skills/*'))
       if not os.path.islink(p) and 'bak' not in p]
print('非软链条目:', len(bad)); print(bad[:5])
PY
```

Expected: 新铺的均为软链；实体目录只限本来就有的本机技能。

- [ ] **Step 5: 提交**：`git add scripts/ tests/ && git commit -m "feat(skill): D6 10 家可见性软链同步 + 体检闸门"`

---

### Task 7: 技能中心前端改版（D7）

**Files:**
- Modify: `templates/index.html:1635-1690`（技能页：删静态「7 路发现点」文案、加 3 张诊断卡）
- Modify: `static/hub/04-terminal-ws.js:586-680`（`loadSkills` / `renderSkillList` / `loadSkillBudget` + 新增三个渲染函数）
- Create: `tests/verify_skill_center_ui.py`
- Modify: `src/main.py`（`VERSION` → v0.13.66）

**Interfaces:**
- Consumes: `/api/skill/status`（Task 1）、`/api/skill/relevant`（Task 2）、`/api/skill/zombies`（Task 3）
- Produces: 静态产物 `static/hub.js`（由 `scripts/build_hubjs.sh` 生成，**禁手改**）

- [ ] **Step 1: 写闸门测试**（6 条断言：①无静态路数文案 ②「从未调用」徽章字段 ③自检卡覆盖全部 backends + 排除段 ④相关实验室打 `/api/skill/relevant` ⑤预算卡三档分区 ⑥断点唯一）

- [ ] **Step 2: 备份**

```bash
cp templates/index.html templates/index.html.bak-$(date +%Y%m%d_%H%M%S)-d7-skillcenter-ui
cp static/hub/04-terminal-ws.js static/hub/04-terminal-ws.js.bak-$(date +%Y%m%d_%H%M%S)-d7-skillcenter-ui
cp src/main.py src/main.py.bak-$(date +%Y%m%d_%H%M%S)-d7-version-bump
```

- [ ] **Step 3: 改 HTML** —— 删掉写死的「7 路发现点」，三张诊断卡：`发现点自检`（含排除段）、`相关性实验室`（输入任务 → 显示 top-N + 命中词 + jev 状态 + 耗时 + 「本次注入会用哪 3 条、会不会被预算裁掉」）、`注入预算`（进度条 + 三档分区 + 一句「被裁 = 注入时 agent 看不到它」）。

- [ ] **Step 4: 改 JS 源模块** —— 主列表项加「末次调用时间 / 从未调用徽章」、加「只看零调用」筛选芯片、按发现点分组折叠（组头显示 路名 + 条数 + 状态点）。**数字全部来自后端字段**，页面不再有第二个真相。

- [ ] **Step 5: 重建 + 逐字节校验**

```bash
bash scripts/build_hubjs.sh
python3 -m pytest tests/test_hubjs_split.py -q     # 必须 PASS（拼接结果 == 仓库 hub.js）
```

- [ ] **Step 6: 窄屏自检**：宽/窄两档各截图/断言一次 —— 断点只允许 `HUB_NARROW_MQ`（`static/hub/01-core-boot.js:6`）；**禁写第二处 `innerWidth < 768`**；首屏只读不写 localStorage；键名带档位。

- [ ] **Step 7: 跑全量 + 提交**

```bash
bash tests/run_tests.sh 2>&1 | tail -10
git add -A && git commit -m "feat(skill-ui): D7 技能中心改版（统一列表/自检/相关实验室/预算可视化）+ v0.13.66"
```

---

## 收尾（Task 8：交接与结项）

- [ ] 在 `PENDING-TASKS.md` 登记：`PT-20261003-xx` 记录本批 + **未闭合项**：直读盘旁路未实现（置信度封顶 medium）、pi/claude 端侧未确认、pi-web 0.10 待用户升级重启
- [ ] 写 `agent-hub/logs/pi-skill-center-handoff.md`：pi-web 0.9.3→0.10.0 升级命令 + 升级后三项自证
- [ ] 跑 `bash scripts/orchestration-check.sh`（期望 C8 转为 PASS）
- [ ] 端侧由用户确认后，才把整体判为「闭合」；否则一律记「本机 PASS / 端侧未确认」

## 自审（对照 spec 的三问）

1. **Spec 覆盖**：R1→T1；R2→T7 预算可视化 + T5 预算；R3→T4/T5；R4→T2；R5→T1（state 四态 + 待重验登记）；R6→T3。前端 F1~F8→T7 全部。pi-web 升级→Task 8 交接件。**无遗漏。**
2. **占位符**：无 TBD/TODO/「类似 Task N」；每步含实际代码或实际命令。
3. **类型一致**：`recall/tokenize/score/rank_async/cached_pick/jev_state` 在 T2 定义，T6 不用；`counts/zombies/snapshot` 在 T3 定义，T7 用 `/zombies` 端点；`_all_items()` 在 T2 定义并被 T3、T7 复用 —— 命名全程一致。
