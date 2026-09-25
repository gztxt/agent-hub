# 联邦门面收口批 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把联邦门面路线上三个真开口补完（会话导出前端按钮 / `asset_audit` 资产变更审计 / 本地记忆便签 staleness 可观测化），并收掉两件治理债（台账卫生落盘 / git hooks 安装）。

**Architecture:** 后端沿既有分层加两个新模块（`src/audit.py` 只读查询门面、`src/memstats.py` 纯观测函数）+ 在 `src/db.py` 加一张 append-only 审计表；身份由 `writeauth.write_gate` 中间件统一打在 `request.state.actor`（新增纯函数 `credential_name`/`actor_of`，**不改** `decide()` 签名）；前端在既有 chat 会话工具条加一个 `data-export` 委托按钮，走 blob 下载。全程不建第二权威副本、不做 ETL、不删任何数据。

**Tech Stack:** Python 3.11 / FastAPI / Starlette / sqlite3(WAL) / unittest（三层口径 `tests/tiers.py`）/ 原生 JS（无框架，7 段 shard 拼接成 `static/hub.js`）/ node（仅 L1 跑抽出的真函数）/ bash 闸门。

**Spec:** `docs/superpowers/specs/2026-09-25-federated-facade-closeout-design.md`（commit `42673c0`）

## Global Constraints

以下每条对**每个任务**都成立，任务里不再重复：

1. **施工位置**：只在 `/home/gztxt/agent-hub-wt-01a0d6dd`（分支 `wt/01a0d6dd`，从 `40a1f89` 建）里改文件。主 checkout `/home/gztxt/agent-hub` 只由集成者（Task 8）合并与重建 build 产物。
2. **改前必备份**：`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<说明>`（说明 ≤10 字、短横线连接）。新建文件免备份。**无备份不许写入既有文件**。
3. **禁 `sed -i` / 禁全局替换**（09-07 事故根因）。一律用精确锚点编辑。
4. **禁碰的文件**（当天有他会话写，C3 FAIL 面）：`src/main.py` 除 Task 3 明确列出的 **3 处**外一律不动；`/fs/1000/ftp/技术文档/scripts/orchestration-check.sh` 完全不动；`static/hub/02|03|06-*.js` 完全不动（`01a0d513` 活跃中）。
5. **凭据零回显**：任何日志/报错/审计 `detail`/前端文案都不得出现 token 原文。`mcp_servers.env` 只记 `has_env` 布尔，**绝不入审计**。
6. **测试三层口径**（`tests/tiers.py` 是唯一真相源）：L0 hermetic = 不读真盘 `~/.claude` 等、不起服务、不打网络、不 fork pty、**不 import `src.main`**、**不许出现 SKIP**；L1 host 打 `@tiers.host_only`；L2 是 `tests/verify_*.py`/`probe_*.py`。当前基线 **L0=313 / L1=35，skipped=0**。
7. **每件配红对照**：把关键不变量改坏 → 闸门必须 FAIL。不 FAIL 的闸门等于没有，红对照步骤不许跳过。
8. **重启须用户逐次授权**（Task 9 是硬门）。后端两件攒成**一次**重启。
9. **探活预算**：三个互不相同的判据各 1 次单轮复合探针（基线已用 / 重启前 / 重启后），同一判据不重试第 2 次，**禁第 4 次**。
10. **产物不落 `/tmp`**（C5）。测试临时件用 `tempfile.mkdtemp` 并在 cleanup 里清（既有先例）；红对照的一次性还原载体可用 `/tmp` 且**当步删掉**（它不是交付产物）。
11. **子代理只读**：写一律由主会话串行落盘（C4 闸门守着）。
12. **版本号**：`src/main.py:68` 的 `VERSION` 由 `0.13.23` → **`0.13.24`**（本批含后端改动 ⇒ 按项目口径「VERSION 只在后端批次 bump」）。合并时若 master 上已被他人占用该号 ⇒ **让位**到下一个空号，并在 CHANGELOG 写明（先例：01a0d5db 的 0.13.22→0.13.23）。
13. **不引入任何新依赖**（无 Docker、无 sqlite-vec、无前端框架）。
14. **当天 commit + 沉淀**（C8）：Task 12 是义务，不是可选。
15. **测试调用口径（执行期实测补录，09-25 15:5x）**：worktree 里**没有 `venv`**（它在主 checkout）⇒
    本文所有 `venv/bin/python` 一律用绝对路径 **`/home/gztxt/agent-hub/venv/bin/python`**（不建符号链接，
    避免 venv 的 `sys.prefix`/`pyvenv.cfg` 语义被搞混）。且部分测试文件用 `import tiers`（需 `tests/` 在
    `sys.path` 上）⇒ 单独跑某几只测试时必须带 **`PYTHONPATH=tests`**，否则报
    `ModuleNotFoundError: No module named 'tiers'`（已实测：这是 master 基线上的**既有调用口径**，
    与本批改动无关；`scripts/run_tests.sh` 与 `discover -s tests` 已自带正确 path）。

## File Structure

| 文件 | 动作 | 责任（单一） |
|---|---|---|
| `src/db.py` | 改（SCHEMA 尾 + 1 helper） | 存储层：`asset_audit` 表与唯一写口径 `log_asset_event()` |
| `src/writeauth.py` | 改（+2 纯函数，`write_gate` 内 4 行） | 鉴权层：把"用了哪把钥匙"解析成审计身份，打在 `request.state.actor` |
| `src/audit.py` | **新建** | 审计**只读查询**门面（HTTP 层），不含写口径 |
| `src/memstats.py` | **新建** | 本地记忆便签 staleness 的**纯观测**函数（绝不写库） |
| `src/mcpgw.py` | 改（4 handler + import 2 处） | 工具/ACL 变更点打审计 |
| `src/memory.py` | 改（6 handler + import 2 处 + 1 私有 helper） | 记忆资产变更点打审计 |
| `src/kb.py` | 改（`kb_status()` +1 键，import +1） | 把 `local_memory` 观测挂到既有状态端点 |
| `src/main.py` | 改（**仅 3 处**：import 1 行 / include_router 1 行 / VERSION 1 行） | 挂载与版本 |
| `static/hub/04-terminal-ws.js` | 改（尾部 +4 函数 +1 委托监听） | 导出按钮行为（blob 下载 + 四态文案） |
| `templates/index.html` | 改（**仅 1 行**） | 按钮 markup |
| `static/hub.js` | 重建（`scripts/build_hubjs.sh`） | build 产物，只由集成者重建 |
| `tests/test_asset_audit.py` | **新建** | 审计表/写口径/覆盖面护栏（L0） |
| `tests/test_writeauth_actor.py` | **新建** | 身份解析纯函数 + 中间件打 actor（L0） |
| `tests/test_audit_api.py` | **新建** | 查询端点鉴权与过滤（L0） |
| `tests/test_memstats.py` | **新建** | staleness 纯函数 + 不许写库的静态护栏（L0） |
| `tests/test_export_button.py` | **新建** | 按钮四态/blob/禁 `?token=`/禁 inline onclick（L0+L1） |
| `CHANGELOG.md` | 改（+1 节） | 版本记录 |
| `/fs/1000/ftp/技术文档/scripts/salvage-ledger-patch.py` | **新建** | 台账补丁件打捞器（Task 10） |
| `/fs/1000/ftp/技术文档/agent-knowledge/NN-*.md` | **新建** | 沉淀（Task 12） |

---

### Task 1: `asset_audit` 表 + `log_asset_event()` 写口径

**Files:**
- Modify: `src/db.py`（SCHEMA 尾部，紧接 `CREATE INDEX IF NOT EXISTS idx_prof_subject ON profile_events(subject);` 之后、收尾 `"""` 之前；helper 加在 `log_profile_event` 之后、`add_memory` 之前）
- Test: `tests/test_asset_audit.py`（新建）

**Interfaces:**
- Consumes: `db.execute(sql, params)`、`db.query(sql, params)`、`db._now()`、`sessions_export.redact_text(text) -> (str, int)`
- Produces:
  - 表 `asset_audit(id INTEGER PK AUTOINCREMENT, asset_type TEXT, asset_slug TEXT, action TEXT, actor TEXT, detail TEXT DEFAULT '{}', created_at TEXT)` + 索引 `idx_audit_asset(asset_type,asset_slug)`、`idx_audit_created(created_at)`
  - `db.AUDIT_ACTIONS: tuple = ("create","update","delete","bind","unbind","rebuild")`
  - `db.log_asset_event(asset_type: str, asset_slug: str, action: str, actor: str, detail: Optional[dict] = None) -> None`

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp src/db.py src/db.py.bak-$(date +%Y%m%d_%H%M%S)-加审计表
ls -la src/db.py.bak-* | tail -1
```

- [ ] **Step 2: 写失败测试** — 新建 `tests/test_asset_audit.py`

```python
#!/usr/bin/env python3
"""L0 hermetic：资产变更审计（asset_audit）的表、写口径与覆盖面护栏。

分层口径（tests/tiers.py）：零宿主依赖 —— db 指向 tmp 文件库、不起服务、不打网络、
不 import src.main、**不允许 SKIP**。

为什么这张表值得单独一层闸门（三条都能确定性造红向）：
1. **审计表一旦可被 UPDATE/DELETE 就不再是审计** ⇒ 静态钉死写口径只有 INSERT。
2. **审计行会成为下一次会话导出的正文** ⇒ detail 落库前必须过脱敏，否则凭据二次外流
   （本工作区已有三次外流前例：备份镜像 82 个活凭据、wiki/log.md 历史含 CCR token、外发净仓 3 份抄真 token）。
3. **漏一条写点就等于没做**（与 writeauth.py 的"34 条写路由集中一处"同一口径）⇒ 用 AST 扫覆盖面。
"""
import ast
import asyncio
import json
import pathlib
import shutil
import sys
import tempfile
import types
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import db  # noqa: E402


class _DbCase(unittest.TestCase):
    """把 db 换到 tmp 文件库；cleanup 必须还原全局连接（先例：tests/test_mcp_registry.py）。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0audit-"))
        self._saved = db._conn
        db.init_db(self.tmp / "t.db")
        self.addCleanup(self._restore)

    def _restore(self):
        db._conn = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSchema(_DbCase):
    def test_table_and_indexes_created(self):
        rows = db.query("SELECT name FROM sqlite_master WHERE name IN "
                        "('asset_audit','idx_audit_asset','idx_audit_created')")
        self.assertEqual(sorted(r["name"] for r in rows),
                         ["asset_audit", "idx_audit_asset", "idx_audit_created"])

    def test_columns_are_exact(self):
        cols = [r["name"] for r in db.query("PRAGMA table_info(asset_audit)")]
        self.assertEqual(cols, ["id", "asset_type", "asset_slug", "action",
                                "actor", "detail", "created_at"])


class TestWritePath(_DbCase):
    def test_helper_is_append_only(self):
        """★ 红向钉子：写口径里出现 UPDATE/DELETE 即判红（审计表可改 = 不是审计）。"""
        src = (_REPO / "src" / "db.py").read_text(encoding="utf-8")
        i = src.index("def log_asset_event(")
        j = src.index("\ndef ", i + 1)
        body = src[i:j]
        self.assertIn("INSERT INTO asset_audit", body)
        self.assertNotIn("UPDATE asset_audit", body)
        self.assertNotIn("DELETE FROM asset_audit", body)

    def test_row_is_written_and_readable(self):
        db.log_asset_event("mcp_server", "abc123", "create", "user:term-token",
                           {"name": "demo", "transport": "stdio"})
        rows = db.query("SELECT * FROM asset_audit")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["asset_type"], r["asset_slug"], r["action"], r["actor"]),
                         ("mcp_server", "abc123", "create", "user:term-token"))
        self.assertEqual(json.loads(r["detail"])["name"], "demo")
        self.assertTrue(r["created_at"])

    def test_detail_redacts_credentials(self):
        """凭据形态进 detail 必须被打码（复用 sessions_export 的同一套 pattern，含嵌套层）。"""
        db.log_asset_event("setting", "term-token", "update", "user:hub-passcode",
                           {"raw": "sk-ABCDEFGHIJKLMNOP1234567890",
                            "nested": {"k": "ghp_" + "A" * 36}})
        d = db.query("SELECT detail FROM asset_audit")[0]["detail"]
        self.assertNotIn("sk-ABCDEFGHIJKLMNOP1234567890", d)
        self.assertNotIn("ghp_" + "A" * 36, d)
        self.assertIn("<REDACTED", d)

    def test_unknown_action_is_flagged_not_rewritten_not_dropped(self):
        """不在枚举里的 action：**不静默丢弃、不静默改写**，只打标记。
        丢事件比记错更贵（审计的价值在完整），改写则毁掉取证原文。"""
        db.log_asset_event("mcp_acl", "7", "frobnicate", "system")
        rows = db.query("SELECT action, detail FROM asset_audit")
        self.assertEqual(len(rows), 1, "事件被静默丢弃 = 审计漏洞")
        self.assertEqual(rows[0]["action"], "frobnicate", "action 被改写 = 毁掉取证原文")
        self.assertTrue(json.loads(rows[0]["detail"]).get("action_invalid"))

    def test_none_detail_becomes_empty_object(self):
        db.log_asset_event("agent", "pi", "delete", "system")
        self.assertEqual(db.query("SELECT detail FROM asset_audit")[0]["detail"], "{}")

    def test_actions_enum_is_frozen(self):
        self.assertEqual(db.AUDIT_ACTIONS,
                         ("create", "update", "delete", "bind", "unbind", "rebuild"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit -v 2>&1 | tail -12
```
Expected: FAIL/ERROR，含 `AttributeError: module 'db' has no attribute 'AUDIT_ACTIONS'` 与 `no such table: asset_audit`。

- [ ] **Step 4: 加表** — 在 `src/db.py` 的 `SCHEMA` 里 `CREATE INDEX IF NOT EXISTS idx_prof_subject ON profile_events(subject);` 之后插入：

```sql
CREATE TABLE IF NOT EXISTS asset_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type TEXT NOT NULL,   -- mcp_server|mcp_acl|memory_l1|memory_doc|agent|session|setting
    asset_slug TEXT NOT NULL,   -- server_id / acl_id / mid / L2|L3|batch / agent_id / session_id
    action TEXT NOT NULL,       -- create|update|delete|bind|unbind|rebuild（见 AUDIT_ACTIONS）
    actor TEXT NOT NULL,        -- user:term-token|user:hub-passcode|user:exempt|agent:<slug>|system
    detail TEXT DEFAULT '{}',   -- JSON；落库前过脱敏，**绝不许含凭据原文**
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_asset ON asset_audit(asset_type, asset_slug);
CREATE INDEX IF NOT EXISTS idx_audit_created ON asset_audit(created_at);
```

- [ ] **Step 5: 加写口径 helper** — 在 `log_profile_event()` 之后、`add_memory()` 之前插入：

```python
#: 审计动作枚举——写死在这里，避免各调用点自由发挥（自由文本 = 查不动）
AUDIT_ACTIONS = ("create", "update", "delete", "bind", "unbind", "rebuild")


def log_asset_event(asset_type: str, asset_slug: str, action: str, actor: str,
                    detail: Optional[dict] = None) -> None:
    """资产变更审计（**append-only**）。与 log_profile_event 同族但口径不同：
    profile_events 记「调用成不成、多快」，asset_audit 记「哪个资产被谁改了」。

    三条硬约束（都有 L0 闸门钉着，见 tests/test_asset_audit.py）：
      1) **只 INSERT**：审计表一旦可被 UPDATE/DELETE 就不再是审计。
      2) **detail 落库前整体过脱敏**：审计行会成为下一次会话导出的正文，凭据写进去＝二次外流。
         做法是先 json.dumps 再对整串 redact_text ⇒ 嵌套层也覆盖（只扫顶层值会漏 dict 里的 dict）。
      3) **action 不在枚举里 ⇒ 打 action_invalid 标记**，绝不静默丢弃也绝不改写。
    """
    d = dict(detail or {})
    if action not in AUDIT_ACTIONS:
        d = {**d, "action_invalid": True}
    body = json.dumps(d, ensure_ascii=False)
    try:
        from sessions_export import redact_text   # 局部 import：db 是最底层，不许成环
        body = redact_text(body)[0]
    except Exception:                             # noqa: BLE001
        pass                                      # 脱敏器不可用时仍要留下事件（完整性 > 打码）
    execute(
        "INSERT INTO asset_audit(asset_type,asset_slug,action,actor,detail,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (asset_type, str(asset_slug), action, actor, body, _now()))
```

- [ ] **Step 6: 跑测试确认通过**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit -v 2>&1 | tail -12
```
Expected: `OK`（`Ran 8 tests`）。

- [ ] **Step 7: 红对照（必须 FAIL 才算闸门有效）**

```bash
cp src/db.py work/.dbkeep && python3 - <<'PY'
import pathlib
p = pathlib.Path("src/db.py"); s = p.read_text(encoding="utf-8")
p.write_text(s.replace('"INSERT INTO asset_audit',
                       '"DELETE FROM asset_audit; INSERT INTO asset_audit'), "utf-8")
PY
mkdir -p work
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit.TestWritePath.test_helper_is_append_only 2>&1 | tail -4
cp work/.dbkeep src/db.py && rm -f work/.dbkeep
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit 2>&1 | tail -3
```
Expected: 第一段 `FAILED (failures=1)`；还原后 `OK`。
（`work/` 已在 `.gitignore`，`mkdir -p work` 要在 cp 之前跑 —— 执行顺序：先 `mkdir -p work`，再 `cp`。）

- [ ] **Step 8: 提交**

```bash
git add src/db.py tests/test_asset_audit.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(db): asset_audit 表 + log_asset_event 写口径（append-only / detail 落库前脱敏 / 未知 action 打标记不丢弃）"
git log --oneline -1
```

---

### Task 2: `credential_name()` + `actor_of()` + 中间件打 `request.state.actor`

**Files:**
- Modify: `src/writeauth.py`（`secrets_from_env()` 之后加 2 个纯函数；`write_gate()` 体改 4 行）
- Test: `tests/test_writeauth_actor.py`（新建）

**Interfaces:**
- Consumes: `writeauth.provided_token(headers_raw, query) -> str`、`writeauth.secrets_from_env() -> list`（顺序固定 `[TERM_TOKEN, HUB_PASSCODE]`）、`writeauth.decide(...) -> (verdict, reason)`（**签名一字不动**）
- Produces:
  - `writeauth.SECRET_NAMES: Tuple[str, ...] = ("term-token", "hub-passcode")`
  - `writeauth.credential_name(provided: str, secrets: Iterable[str]) -> str`（回 `"term-token"|"hub-passcode"|"anonymous"`）
  - `writeauth.actor_of(request) -> str`（回 `request.state.actor` 或 `"system"`，**绝不抛**）
  - 副作用：`write_gate` 在 allow/exempt 分支设 `request.state.actor`（`"user:<name>"` 或 `"user:exempt"`）

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp src/writeauth.py src/writeauth.py.bak-$(date +%Y%m%d_%H%M%S)-加审计身份
```

- [ ] **Step 2: 写失败测试** — 新建 `tests/test_writeauth_actor.py`

```python
#!/usr/bin/env python3
"""L0 hermetic：审计身份解析（writeauth.credential_name / actor_of / write_gate 打 actor）。

为什么新增纯函数而不改 decide()：decide 的 (verdict, reason) 二元组被中间件与
/api/sessions/export 端点共用，且 tests/test_writeauth.py 12 例钉着它的签名。
改返回值＝零收益地撞 12 例既有闸门。本文件的红向钉子是：
**返回的身份里绝不许出现凭据本身的任何片段**（只回"用了哪把钥匙"的名字）。
"""
import asyncio
import os
import pathlib
import sys
import types
import unittest
from unittest import mock

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import writeauth  # noqa: E402


class TestCredentialName(unittest.TestCase):
    def test_first_slot_maps_to_term_token(self):
        self.assertEqual(writeauth.credential_name("s3cret", ["s3cret", ""]), "term-token")

    def test_second_slot_maps_to_hub_passcode(self):
        self.assertEqual(writeauth.credential_name("pw", ["aa", "pw"]), "hub-passcode")

    def test_unknown_and_empty_are_anonymous(self):
        self.assertEqual(writeauth.credential_name("nope", ["aa", "bb"]), "anonymous")
        self.assertEqual(writeauth.credential_name("", ["aa", "bb"]), "anonymous")

    def test_unconfigured_secret_never_matches(self):
        """★ 服务端没配口令时 provided="" 不许被判成命中，否则匿名就成了合法身份。"""
        self.assertEqual(writeauth.credential_name("", ["", ""]), "anonymous")

    def test_returned_name_never_leaks_the_secret(self):
        for sec in ("sk-ABCDEFGHIJKLMNOP1234", "hunter2hunter2xx", "ghp_" + "A" * 36):
            n = writeauth.credential_name(sec, [sec, ""])
            self.assertNotIn(sec, n)
            self.assertNotIn(sec[:8], n)
            self.assertIn(n, writeauth.SECRET_NAMES)

    def test_names_are_frozen_and_ordered_like_secrets_from_env(self):
        self.assertEqual(writeauth.SECRET_NAMES, ("term-token", "hub-passcode"))
        src = (_REPO / "src" / "writeauth.py").read_text(encoding="utf-8")
        i = src.index("def secrets_from_env(")
        self.assertLess(src.index("TERM_TOKEN", i), src.index("HUB_PASSCODE", i),
                        "SECRET_NAMES 顺序必须与 secrets_from_env 一致，否则名字张冠李戴")


class TestActorOf(unittest.TestCase):
    def test_reads_state_actor(self):
        r = types.SimpleNamespace(state=types.SimpleNamespace(actor="user:term-token"))
        self.assertEqual(writeauth.actor_of(r), "user:term-token")

    def test_missing_state_is_system_and_never_raises(self):
        self.assertEqual(writeauth.actor_of(types.SimpleNamespace()), "system")
        self.assertEqual(writeauth.actor_of(None), "system")
        self.assertEqual(writeauth.actor_of(object()), "system")

    def test_empty_actor_is_system(self):
        r = types.SimpleNamespace(state=types.SimpleNamespace(actor=""))
        self.assertEqual(writeauth.actor_of(r), "system")


class _FakeUrl:
    def __init__(self, path, query=""):
        self.path, self.query = path, query


class _FakeReq:
    def __init__(self, token, method="POST", path="/api/agents"):
        self.method, self.url = method, _FakeUrl(path)
        self.headers = types.SimpleNamespace(
            raw=[(b"x-hub-token", token.encode())] if token else [])
        self.client = types.SimpleNamespace(host="127.0.0.1")
        self.state = types.SimpleNamespace()


class TestWriteGateStampsActor(unittest.TestCase):
    def _run(self, token, env, method="POST", path="/api/agents"):
        sentinel = object()

        async def call_next(req):
            return sentinel

        with mock.patch.dict(os.environ, env, clear=False):
            r = _FakeReq(token, method, path)
            out = asyncio.run(writeauth.write_gate(r, call_next))
        return out, r, sentinel

    def test_allow_stamps_credential_name(self):
        out, r, sentinel = self._run("tok-term", {"TERM_TOKEN": "tok-term", "HUB_PASSCODE": ""})
        self.assertIs(out, sentinel, "放行时必须真的走到 handler")
        self.assertEqual(r.state.actor, "user:term-token")

    def test_deny_does_not_stamp_and_returns_401(self):
        out, r, _ = self._run("wrong", {"TERM_TOKEN": "tok-term", "HUB_PASSCODE": ""})
        self.assertEqual(out.status_code, 401)
        self.assertFalse(hasattr(r.state, "actor"), "被拒的请求不该拿到身份")

    def test_misconfig_returns_503(self):
        out, _, _ = self._run("", {"TERM_TOKEN": "", "HUB_PASSCODE": ""})
        self.assertEqual(out.status_code, 503)

    def test_get_is_allowed_and_stamped_anonymous(self):
        out, r, sentinel = self._run("", {"TERM_TOKEN": "t", "HUB_PASSCODE": ""},
                                     method="GET", path="/api/agents")
        self.assertIs(out, sentinel)
        self.assertEqual(r.state.actor, "user:anonymous",
                         "GET 不要求凭据，但身份仍要如实记为 anonymous")

    def test_exempt_path_stamps_user_exempt(self):
        out, r, sentinel = self._run("", {"TERM_TOKEN": "", "HUB_PASSCODE": ""},
                                     method="POST", path="/api/settings/term-token")
        self.assertIs(out, sentinel)
        self.assertEqual(r.state.actor, "user:exempt")

    def test_response_never_echoes_the_credential(self):
        out, _, _ = self._run("wrong", {"TERM_TOKEN": "tok-term", "HUB_PASSCODE": ""})
        body = out.body.decode()
        self.assertNotIn("tok-term", body)
        self.assertNotIn("wrong", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_writeauth_actor -v 2>&1 | tail -8
```
Expected: FAIL，含 `AttributeError: module 'writeauth' has no attribute 'credential_name'`。

- [ ] **Step 4: 实现两个纯函数** — 在 `src/writeauth.py` 的 `secrets_from_env()` 之后插入：

```python
#: 凭据的**名字**（顺序与 secrets_from_env 严格一致）。审计只记"用了哪把钥匙"，绝不记钥匙本身。
SECRET_NAMES: Tuple[str, ...] = ("term-token", "hub-passcode")


def credential_name(provided: str, secrets: Iterable[str]) -> str:
    """provided 命中了哪一把凭据的**名字**；未提供/未命中 → "anonymous"。

    为什么新增而不改 `decide()`：decide 的 (verdict, reason) 被中间件与
    `/api/sessions/export` 端点共用，且 tests/test_writeauth.py 12 例钉着签名。
    改返回值＝零收益地撞 12 例既有闸门；新增纯函数则两边都不动。
    """
    if not provided:
        return "anonymous"
    for name, s in zip(SECRET_NAMES, secrets):
        if s and hmac.compare_digest(provided, s):
            return name
    return "anonymous"


def actor_of(request) -> str:
    """从 request.state 取审计身份；取不到 → "system"（后台循环/内部调用）。

    **绝不抛**：审计身份缺失不许把业务请求打挂（审计是附加价值，不是准入条件）。
    """
    try:
        a = getattr(getattr(request, "state", None), "actor", "")
        return a or "system"
    except Exception:  # noqa: BLE001
        return "system"
```

- [ ] **Step 5: 让中间件打 actor** — 把 `write_gate` 里这一段：

```python
    method = request.method
    path = request.url.path
    verdict, reason = decide(method, path,
                             provided_token(request.headers.raw, request.url.query),
                             secrets_from_env())
    if verdict in ("allow", "exempt"):
        return await call_next(request)
```

替换为：

```python
    method = request.method
    path = request.url.path
    provided = provided_token(request.headers.raw, request.url.query)
    secrets = secrets_from_env()
    verdict, reason = decide(method, path, provided, secrets)
    if verdict in ("allow", "exempt"):
        # 审计身份：只记凭据**名**，绝不记值。starlette 的 Request.state 是可写属性袋。
        request.state.actor = ("user:exempt" if verdict == "exempt"
                               else "user:" + credential_name(provided, secrets))
        return await call_next(request)
```

- [ ] **Step 6: 新测试 + 既有 12 例回归**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_writeauth_actor -v 2>&1 | tail -5
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_writeauth -v 2>&1 | tail -3
```
Expected: 两者 `OK`（新 15 例；既有 **12 例一例不减**）。

- [ ] **Step 7: 红对照**

```bash
mkdir -p work && cp src/writeauth.py work/.wakeep && python3 - <<'PY'
import pathlib
p = pathlib.Path("src/writeauth.py"); s = p.read_text(encoding="utf-8")
p.write_text(s.replace('            return name\n    return "anonymous"',
                       '            return provided\n    return "anonymous"'), "utf-8")
PY
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_writeauth_actor.TestCredentialName.test_returned_name_never_leaks_the_secret 2>&1 | tail -4
cp work/.wakeep src/writeauth.py && rm -f work/.wakeep
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_writeauth_actor 2>&1 | tail -3
```
Expected: 第一段 `FAILED`（身份里出现凭据原文）；还原后 `OK`。

- [ ] **Step 8: 提交**

```bash
git add src/writeauth.py tests/test_writeauth_actor.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(writeauth): credential_name/actor_of 纯函数 + write_gate 打 request.state.actor（decide 签名一字不动，既有 12 例零回归）"
```

---

### Task 3: `src/audit.py` 只读查询端点 + `main.py` 3 处 + VERSION bump

**Files:**
- Create: `src/audit.py`
- Modify: `src/main.py`（**仅 3 处**：import 区 +1 行、`include_router` 区 +1 行、`VERSION` 行）
- Test: `tests/test_audit_api.py`（新建）

**Interfaces:**
- Consumes: `db.query`、`writeauth.decide/provided_token/secrets_from_env`
- Produces: `audit.router`（`GET /api/audit/list`）、`audit.VALID_TYPES`、`audit.audit_list(request, asset_type, asset_slug, limit)`

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_audit_api.py`

```python
#!/usr/bin/env python3
"""L0 hermetic：审计查询端点的鉴权与过滤（src/audit.py）。

鉴权口径照抄 /api/sessions/export（src/main.py:531）的既有先例：**GET 但按写方法判**
（批量读审计行＝数据外流动作）。红向钉子：无凭据必须 401 且**不回显任何凭据**；
服务端未配口令必须 503（fail-closed，不是放行）。
"""
import asyncio
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import audit  # noqa: E402
import db     # noqa: E402
from fastapi import HTTPException  # noqa: E402


class _Req:
    def __init__(self, token=""):
        self.method = "GET"
        self.url = types.SimpleNamespace(path="/api/audit/list", query="")
        self.headers = types.SimpleNamespace(
            raw=[(b"x-hub-token", token.encode())] if token else [])
        self.client = types.SimpleNamespace(host="127.0.0.1")
        self.state = types.SimpleNamespace(actor="user:term-token")


class TestAuditApi(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0auditapi-"))
        self._saved = db._conn
        db.init_db(self.tmp / "t.db")
        db.log_asset_event("mcp_server", "s1", "create", "user:term-token", {"name": "a"})
        db.log_asset_event("mcp_acl", "9", "bind", "user:hub-passcode", {"agent_id": "pi"})
        self.addCleanup(self._restore)

    def _restore(self):
        db._conn = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, token="good", **kw):
        args = {"request": _Req(token), "asset_type": None, "asset_slug": None, "limit": 100}
        args.update(kw)
        return asyncio.run(audit.audit_list(**args))

    def _env(self, term="good", passcode=""):
        return mock.patch.dict(os.environ, {"TERM_TOKEN": term, "HUB_PASSCODE": passcode})

    def test_no_token_is_401_and_silent(self):
        with self._env():
            with self.assertRaises(HTTPException) as cm:
                self._call(token="")
        self.assertEqual(cm.exception.status_code, 401)
        self.assertNotIn("good", str(cm.exception.detail))

    def test_wrong_token_is_401(self):
        with self._env():
            with self.assertRaises(HTTPException) as cm:
                self._call(token="bad")
        self.assertEqual(cm.exception.status_code, 401)

    def test_unconfigured_is_503_not_allowed(self):
        with self._env(term="", passcode=""):
            with self.assertRaises(HTTPException) as cm:
                self._call(token="")
        self.assertEqual(cm.exception.status_code, 503, "fail-closed：没设口令不等于不用口令")

    def test_authorized_lists_newest_first(self):
        with self._env():
            d = self._call(token="good")
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["audit"][0]["asset_type"], "mcp_acl", "必须 id DESC（最新在前）")
        self.assertEqual(d["types"], list(audit.VALID_TYPES))

    def test_filter_by_type_and_slug(self):
        with self._env():
            self.assertEqual(self._call(token="good", asset_type="mcp_server")["count"], 1)
            self.assertEqual(self._call(token="good", asset_slug="9")["count"], 1)
            self.assertEqual(self._call(token="good", asset_type="mcp_acl", asset_slug="9")["count"], 1)
            self.assertEqual(self._call(token="good", asset_type="mcp_acl", asset_slug="1")["count"], 0)

    def test_bad_type_is_400(self):
        with self._env():
            with self.assertRaises(HTTPException) as cm:
                self._call(token="good", asset_type="not_a_type")
        self.assertEqual(cm.exception.status_code, 400)

    def test_limit_is_clamped_not_exploded(self):
        with self._env():
            self.assertEqual(self._call(token="good", limit=999999)["count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_audit_api 2>&1 | tail -4
```
Expected: `ModuleNotFoundError: No module named 'audit'`。

- [ ] **Step 3: 新建 `src/audit.py`**

```python
"""资产变更审计的**只读查询**门面。

为什么单独一个模块而不是塞进 db.py：db.py 是最底层（被所有模块 import），放路由进去
会让存储层长出 HTTP 依赖。读写分居两层的口径是——
  写：`db.log_asset_event()` 是唯一入口（append-only，落库前脱敏）；
  读：本模块的 `GET /api/audit/list`。

鉴权口径照抄 `/api/sessions/export`（src/main.py:531）的既有先例：**GET 但按写方法判**。
理由是审计行含 actor 与变更细节，批量读它＝数据外流动作，与导出同级；且服务绑
0.0.0.0:3102，不按写判就等于把变更史对整个局域网敞开。
fail-closed：服务端没配口令 ⇒ 503 而不是放行（与 writeauth.decide 同一口径）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import db
import writeauth

router = APIRouter()

#: 允许的 asset_type —— 与 db.log_asset_event 各调用点用的字面量一一对应。
#: 写死成白名单而不是放开自由文本：查询侧要能枚举，否则前端做不出下拉。
VALID_TYPES = ("mcp_server", "mcp_acl", "memory_l1", "memory_doc",
               "agent", "session", "setting")

_LIMIT_MAX = 1000


@router.get("/api/audit/list")
async def audit_list(request: Request,
                     asset_type: Optional[str] = Query(default=None, max_length=40),
                     asset_slug: Optional[str] = Query(default=None, max_length=200),
                     limit: int = Query(default=100, le=_LIMIT_MAX)):
    verdict, reason = writeauth.decide(
        "POST", request.url.path,                       # 强制按写方法判：批量读审计＝数据外流
        writeauth.provided_token(request.headers.raw, request.url.query),
        writeauth.secrets_from_env())
    if verdict not in ("allow", "exempt"):
        # 只打 verdict/path/来源，绝不打凭据（与 main.py 的 [export] 日志同一口径）
        print(f"[audit] 拒绝 {verdict}：{request.url.path} "
              f"来源={request.client.host if request.client else '?'} —— {reason}", flush=True)
        raise HTTPException(status_code=503 if verdict == "misconfig" else 401, detail=reason)

    if asset_type and asset_type not in VALID_TYPES:
        raise HTTPException(400, f"asset_type must be one of {list(VALID_TYPES)}")

    sql = "SELECT id,asset_type,asset_slug,action,actor,detail,created_at FROM asset_audit"
    conds, params = [], []
    if asset_type:
        conds.append("asset_type=?")
        params.append(asset_type)
    if asset_slug:
        conds.append("asset_slug=?")
        params.append(str(asset_slug))
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), _LIMIT_MAX)))
    rows = db.query(sql, tuple(params))
    return {"audit": rows, "count": len(rows), "types": list(VALID_TYPES)}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_audit_api -v 2>&1 | tail -5
```
Expected: `OK`（`Ran 7 tests`）。

- [ ] **Step 5: 备份并改 `src/main.py`（仅 3 处）**

```bash
cp src/main.py src/main.py.bak-$(date +%Y%m%d_%H%M%S)-挂审计路由
```

改 1 —— import 区，在 `import term as term_mod` 之后加一行：

```python
import audit as audit_mod
```

改 2 —— `include_router` 区，在 `app.include_router(term_mod.router)` 之后加一行：

```python
app.include_router(audit_mod.router)
```

改 3 —— `src/main.py:68`：`VERSION = "0.13.23"` → `VERSION = "0.13.24"`，并在紧随其后的注释块**末尾追加**一行（不删既有注释）：

```python
                      #   v0.13.24（本批）：asset_audit 审计表 + /api/audit/list + 本地记忆 staleness 观测 + 会话导出前端按钮。
```

- [ ] **Step 6: 静态自检 + 既有结构闸门零回归**

```bash
python3 - <<'PY'
import pathlib
s = pathlib.Path("src/main.py").read_text(encoding="utf-8")
assert "import audit as audit_mod" in s, "import 没加"
assert "app.include_router(audit_mod.router)" in s, "router 没挂"
assert 'VERSION = "0.13.24"' in s, "VERSION 没 bump"
print("main.py 三处改动 PASS；include_router 计数 =", s.count("app.include_router("))
PY
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_main_task_refs tests.test_pydantic_attr_drift 2>&1 | tail -3
```
Expected: `PASS`，`include_router 计数 = 9`（原 8 + 1）；两只结构闸门 `OK`。

- [ ] **Step 7: 提交**

```bash
git add src/audit.py src/main.py tests/test_audit_api.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(audit): /api/audit/list 只读查询门面（GET 按写级鉴权，照抄 export 先例）+ main.py 挂载与 v0.13.24"
```

---

### Task 4: `mcpgw.py` 4 个变更点打审计 + AST 覆盖面护栏

**Files:**
- Modify: `src/mcpgw.py`（import 2 处；`add_server`/`del_server`/`add_acl`/`del_acl`）
- Test: `tests/test_asset_audit.py`（追加 `route_audit_map` + `TestCoverage`）

**Interfaces:**
- Consumes: `db.log_asset_event`、`writeauth.actor_of(request)`
- Produces: 4 条路由的审计行为；`route_audit_map(module_path) -> {(METHOD,path): (audited, func_name)}`（Task 5 复用）

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp src/mcpgw.py src/mcpgw.py.bak-$(date +%Y%m%d_%H%M%S)-加审计写点
```

- [ ] **Step 2: 加覆盖面护栏测试（红）** — 在 `tests/test_asset_audit.py` 的 `if __name__` 之前追加：

```python
def route_audit_map(module_path):
    """AST 扫一个模块：{(METHOD, path): (是否打了审计, 函数名)}。

    为什么用 AST 而不是 grep：grep 只能证明"文件里某处有这个词"，证明不了
    "这条路由的 handler 体内有"。漏一条写点就等于没做（writeauth 同一口径）。

    **间接层只跟一层**（2026-09-25 实测修正）：memory 的 put_l2/put_l3 把审计收进了
    模块级 helper `_audit_doc()`（避免把 touched 字段推导复制两遍），第一版护栏只看 handler 体
    ⇒ 对着正确实现报"漏审"。解法不是把逻辑抄回 handler，而是让护栏解析一层本地调用：
    handler 体内直接出现 log_asset_event，**或**调用了某个「体内直接出现 log_asset_event」的
    模块级函数，才算已审。再深的链条（helper→helper→审计）故意不算 —— 否则覆盖面可以被
    无限稀释成"看起来调了个函数"（见 test_indirection_is_limited_to_one_level）。
    """
    src = pathlib.Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    def seg(node):
        return "\n".join(lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)])

    #: 模块级函数名 → 其体内是否**直接**调 log_asset_event（只扫 tree.body，不递归）
    helpers = {n.name: ("log_asset_event" in seg(n))
               for n in tree.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            method = dec.func.attr.upper()
            if method not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            path = dec.args[0].value if (dec.args and isinstance(dec.args[0], ast.Constant)) else "?"
            body = seg(node)
            called = {c.func.id for c in ast.walk(node)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            audited = ("log_asset_event" in body) or any(helpers.get(c) for c in called)
            out[(method, path)] = (audited, node.name)
    return out


#: 必须审计的变更点。不在此表里的写路由**故意不审**，理由挂在 NOT_AUDITED。
MUST_AUDIT = {
    "mcpgw": {("POST", "/mcp/servers"), ("DELETE", "/mcp/servers/{sid}"),
              ("POST", "/mcp/acl"), ("DELETE", "/mcp/acl/{acl_id}")},
    "memory": {("POST", "/api/memory/l1"), ("POST", "/api/memory/l1/batch"),
               ("DELETE", "/api/memory/l1/{mid}"), ("PUT", "/api/memory/l2"),
               ("PUT", "/api/memory/l3"), ("POST", "/api/memory/l2/rebuild")},
}

#: 故意不审 + 理由（防止"以后有人加一条变更路由却没人发现漏审"）
NOT_AUDITED = {
    ("POST", "/mcp/servers/probe"): "预览语义，明确不落库（handler docstring 已写）",
    ("POST", "/mcp/call"): "工具**调用**不是资产变更；成败/耗时已由 profile_events 记",
}


class TestCoverage(unittest.TestCase):
    """★ 本闸门的核心：漏一条写点即 FAIL（"漏一条就等于没做"）。"""

    def _map(self, mod):
        return route_audit_map(_REPO / "src" / ("%s.py" % mod))

    def test_mcpgw_change_routes_all_audited(self):
        m = self._map("mcpgw")
        self.assertEqual([k for k in MUST_AUDIT["mcpgw"] if not m.get(k, (False,))[0]], [],
                         "mcpgw 漏审")

    def test_memory_change_routes_all_audited(self):
        m = self._map("memory")
        self.assertEqual([k for k in MUST_AUDIT["memory"] if not m.get(k, (False,))[0]], [],
                         "memory 漏审")

    def test_unaudited_routes_have_written_reasons(self):
        """反向钉子：不审的路由必须挂着理由，否则新增变更路由会静默漏审。"""
        for mod in ("mcpgw", "memory"):
            for k, audited in self._map(mod).items():
                if not audited[0]:
                    self.assertIn(k, NOT_AUDITED,
                                  "%s 出现未审计且无理由的写路由 %s（handler=%s）"
                                  % (mod, k, audited[1]))

    def test_no_env_values_reach_audit(self):
        """★ mcp_servers.env 装的是凭据 ⇒ 审计只准记 has_env 布尔，绝不记值。

        判法必须区分「记布尔」与「记值」：`bool(body.env)` 是安全形态，
        裸 `body.env` / `json.dumps(body.env…)` 才是漏值。第一版闸门把两者一视同仁
        ⇒ 对着正确实现报红（闸门精度缺陷，2026-09-25 实测修正）。
        做法：先摘掉安全形态，余下文本里再出现 body.env 就是漏。
        """
        src = (_REPO / "src" / "mcpgw.py").read_text(encoding="utf-8")
        i = src.index("async def add_server(")
        body = src[i:src.index("\n@router.", i)]
        self.assertIn('"has_env": bool(body.env)', body, "必须只记布尔，不记值")
        audit_seg = body.split("log_asset_event")[-1]
        self.assertNotIn("body.env", audit_seg.replace("bool(body.env)", ""),
                         "env 值进了审计 detail = 凭据落进可导出的正文")
        self.assertNotIn("json.dumps(body.env", audit_seg)

    def test_indirection_is_limited_to_one_level(self):
        """★ 审计只准藏一层：handler→helper→helper→log_asset_event **不算已审**。
        没有这条，覆盖面护栏可以被无限稀释成"看起来调了个函数"。"""
        d = pathlib.Path(tempfile.mkdtemp(prefix="l0cov-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "m.py"
        f.write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "def _deep():\n"
            "    db.log_asset_event('x', 'y', 'create', 'system')\n"
            "def _mid():\n"
            "    _deep()\n"
            "@router.post('/shallow')\n"
            "async def h_shallow():\n"
            "    _audit_one()\n"
            "def _audit_one():\n"
            "    db.log_asset_event('x', 'y', 'create', 'system')\n"
            "@router.post('/deep')\n"
            "async def h_deep():\n"
            "    _mid()\n", encoding="utf-8")
        m = route_audit_map(f)
        self.assertTrue(m[("POST", "/shallow")][0], "一层本地 helper 该算已审")
        self.assertFalse(m[("POST", "/deep")][0], "两层间接必须判未审（否则护栏可被稀释）")

class TestGateSelfCheck(unittest.TestCase):
    """★ 元闸门：本文件里每个 test_* 都必须是某个 TestCase 的**方法**。

    缩进错位会让 test 函数变成另一个函数的嵌套 def ⇒ 语法通过、import 成功、
     discover 收不到、**永远不执行**，而总例数只少一个，肉眼极难发现
    （2026-09-25 本文件实测栽过一次；同族前例＝vitals_loop 函数头丢失致健康检查
    成为不可达死代码、前端 TDZ 声明前访问）。口径：**代码存在 ≠ 会被执行**。
    """

    def test_no_test_function_is_nested_inside_another_function(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        nested = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if (sub is not node and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and sub.name.startswith("test_")):
                    nested.append("%s 嵌在 %s 里" % (sub.name, node.name))
        self.assertEqual(sorted(set(nested)), [], "这些 test_ 永不执行：%s" % sorted(set(nested)))

    def test_intended_gate_count_is_not_silently_shrunk(self):
        """例数下界钉子：少了就说明有 test 掉出收集范围（不是"跑得快"，是"没跑"）。"""
        loader = unittest.TestLoader()
        n = loader.loadTestsFromModule(sys.modules[__name__]).countTestCases()
        self.assertGreaterEqual(n, 21, "本文件应至少收集 21 例，实收 %d ⇒ 有 test 掉出收集范围" % n)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit.TestCoverage -v 2>&1 | tail -8
```
Expected: FAIL，`mcpgw 漏审` 列出 4 条路由。

- [ ] **Step 4: 改 `src/mcpgw.py`**

改 1 —— `from fastapi import APIRouter, HTTPException` → 加 `Request`：

```python
from fastapi import APIRouter, HTTPException, Request
```

改 2 —— 在 `import config` 之后加：

```python
import writeauth
```

改 3 —— `add_server` 签名，并在 `return {"id": sid, "status": "added"}` **之前**插入审计：

```python
@router.post("/mcp/servers")
async def add_server(body: ServerIn, request: Request):
```

```python
    # env 装的是凭据 ⇒ 只记布尔，绝不记值（审计行会成为下一次导出的正文）
    db.log_asset_event("mcp_server", sid, "create", writeauth.actor_of(request),
                       {"name": body.name, "transport": body.transport,
                        "has_env": bool(body.env), "url": body.url})
```

改 4 —— `del_server` 整体替换为：

```python
@router.delete("/mcp/servers/{sid}")
async def del_server(sid: str, request: Request):
    if not db.execute("DELETE FROM mcp_servers WHERE id=?", (sid,)):
        raise HTTPException(404, "server not found")
    _tool_cache.clear()
    db.log_asset_event("mcp_server", sid, "delete", writeauth.actor_of(request))
    return {"status": "deleted"}
```

改 5 —— `add_acl` 整体替换为：

```python
@router.post("/mcp/acl")
async def add_acl(body: AclIn, request: Request):
    aid = db.query("SELECT id FROM mcp_servers WHERE id=? OR name=?",
                   (body.server_id, body.server_id))[0]["id"] if body.server_id else None
    db.execute("INSERT INTO mcp_acl(agent_id,server_id,tool_pattern,allow) VALUES(?,?,?,?)",
               (body.agent_id, aid, body.tool_pattern, 1 if body.allow else 0))
    db.log_asset_event("mcp_acl", str(aid or "*"), "bind", writeauth.actor_of(request),
                       {"agent_id": body.agent_id, "tool_pattern": body.tool_pattern,
                        "allow": bool(body.allow)})
    return {"status": "added"}
```

改 6 —— `del_acl` 整体替换为（先取旧行，删掉后把旧值记进 detail —— 否则"解绑了什么"永久丢失）：

```python
@router.delete("/mcp/acl/{acl_id}")
async def del_acl(acl_id: int, request: Request):
    row = db.query("SELECT agent_id,server_id,tool_pattern,allow FROM mcp_acl WHERE id=?",
                   (acl_id,))
    if not db.execute("DELETE FROM mcp_acl WHERE id=?", (acl_id,)):
        raise HTTPException(404, "rule not found")
    db.log_asset_event("mcp_acl", str(acl_id), "unbind", writeauth.actor_of(request),
                       dict(row[0]) if row else None)
    return {"status": "deleted"}
```

> **设计更正（相对 spec §4，执行时照本节）**：ACL 绑定的 actor 一律取 `request.state.actor`
> （**用户**身份），`body.agent_id` 进 `detail`。理由：绑定 ACL 是"用户对某 agent 做的管理动作"，
> 不是"agent 自己做的动作"；spec 原写"MCP 侧 actor 取 body.agent_id"会把管理动作错记成 agent 行为。
> `agent:<slug>` 形态留给将来真由 agent 自己发起的变更（当前 `/mcp/call` 不是变更，不审）。

- [ ] **Step 5: 通过 + 既有 mcpgw 闸门零回归**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit -v 2>&1 | tail -6
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_mcp_registry -v 2>&1 | tail -3
```
Expected: 两者 `OK`（`test_asset_audit` 12 例；`test_mcp_registry` 一例不减）。

- [ ] **Step 6: 真 handler 走一次，验证审计行落库且凭据不泄漏**

```bash
HUB_HOST_TESTS=0 venv/bin/python - <<'PY'
import asyncio, pathlib, sys, tempfile, types
sys.path.insert(0, "src"); sys.path.insert(0, ".")
import db, mcpgw
tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0gw-"))
db.init_db(tmp / "t.db"); mcpgw.ensure_schema()
req = types.SimpleNamespace(state=types.SimpleNamespace(actor="user:term-token"))
body = mcpgw.ServerIn(name="demo", transport="stdio", command="/bin/echo", args=[],
                      env={"SECRET": "sk-ABCDEFGHIJKLMNOP1234"}, url=None, description="")
print("add_server →", asyncio.run(mcpgw.add_server(body, req)))
rows = db.query("SELECT asset_type,asset_slug,action,actor,detail FROM asset_audit")
print("审计行 =", rows)
assert rows and rows[0]["action"] == "create" and rows[0]["actor"] == "user:term-token"
assert "sk-ABCDEFGHIJKLMNOP1234" not in rows[0]["detail"], "凭据泄漏进审计"
print("PASS：真 handler 走通且凭据未泄漏")
PY
```
Expected: 末行打印 `PASS：真 handler 走通且凭据未泄漏`。
（若 `ServerIn` 字段名与此不符 ⇒ 以 `grep -n "class ServerIn" -A8 src/mcpgw.py` 的实际字段为准，**不改断言**。）

- [ ] **Step 7: 红对照**

```bash
mkdir -p work && cp src/mcpgw.py work/.gwkeep && python3 - <<'PY'
import pathlib
p = pathlib.Path("src/mcpgw.py"); s = p.read_text(encoding="utf-8")
p.write_text(s.replace('    db.log_asset_event("mcp_acl", str(aid or "*"), "bind"',
                       '    pass  # db.log_asset_event("mcp_acl", str(aid or "*"), "bind"'), "utf-8")
PY
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit.TestCoverage.test_mcpgw_change_routes_all_audited 2>&1 | tail -4
cp work/.gwkeep src/mcpgw.py && rm -f work/.gwkeep
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit 2>&1 | tail -3
```
Expected: 第一段 `FAILED`（漏审 `[('POST', '/mcp/acl')]`）；还原后 `OK`。

- [ ] **Step 8: 提交**

```bash
git add src/mcpgw.py tests/test_asset_audit.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(mcpgw): servers/acl 四个变更点打审计 + AST 覆盖面护栏（漏一条即 FAIL；env 只记 has_env 布尔）"
```

---

### Task 5: `memory.py` 6 个变更点打审计

**Files:**
- Modify: `src/memory.py`（import 2 处；6 个 handler；新增私有 `_audit_doc`）
- Test: `tests/test_asset_audit.py`（追加 `TestMemoryAudit`）

**Interfaces:**
- Consumes: `db.log_asset_event`、`writeauth.actor_of`
- Produces: `memory_l1`（slug = mid 或 `"batch"`）与 `memory_doc`（slug = `"L2"|"L3"`）两类审计行；`memory._audit_doc(layer, body, request) -> None`

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp src/memory.py src/memory.py.bak-$(date +%Y%m%d_%H%M%S)-加审计写点
```

- [ ] **Step 2: 加测试（红）** — 在 `tests/test_asset_audit.py` 的 `TestCoverage` 之后追加：

```python
class TestMemoryAudit(_DbCase):
    """记忆资产的审计行形态。两条口径钉子：
       ① detail **不记正文**（正文已在 memories 表；重复记 = 体积翻倍且多一处凭据面），只记元信息与字符数；
       ② 软删必须如实标 soft=True（delete_l1 是 UPDATE status='deleted'，不是物理删）——
          审计里写 "delete" 而不说清是软删，就是"不静默改数据"的反面。"""

    def setUp(self):
        super().setUp()
        import memory as memory_mod
        self.m = memory_mod
        self.req = types.SimpleNamespace(state=types.SimpleNamespace(actor="user:hub-passcode"))

    def _l1(self, content="端口 3102 由 agent-hub 占用", category="fact"):
        return asyncio.run(self.m.create_l1(
            self.m.MemoryIn(content=content, category=category), self.req))

    def test_create_l1_audits_metadata_not_content(self):
        r = self._l1()
        rows = db.query("SELECT * FROM asset_audit WHERE asset_type='memory_l1'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "create")
        self.assertEqual(rows[0]["asset_slug"], str(r["id"]))
        self.assertEqual(rows[0]["actor"], "user:hub-passcode")
        d = json.loads(rows[0]["detail"])
        self.assertEqual(d["category"], "fact")
        self.assertIn("chars", d)
        self.assertNotIn("content", d, "正文不该复制进审计")
        self.assertNotIn("端口 3102", rows[0]["detail"])

    def test_batch_writes_one_summary_row(self):
        """批量导入记**一行汇总**：逐条写 200 行会让单次调用灌满表，
        而审计要回答的是"谁在什么时候导了多少条"。"""
        items = [self.m.MemoryIn(content="条目 %d" % i, category="fact") for i in range(5)]
        r = asyncio.run(self.m.create_l1_batch(self.m.MemoryBatchIn(items=items), self.req))
        rows = db.query("SELECT * FROM asset_audit WHERE asset_slug='batch'")
        self.assertEqual(len(rows), 1)
        d = json.loads(rows[0]["detail"])
        self.assertEqual(d["count"], 5)
        self.assertEqual(d["ids"], r["ids"])

    def test_delete_l1_marks_soft_delete(self):
        mid = self._l1()["id"]
        asyncio.run(self.m.delete_l1(mid, self.req))
        rows = db.query("SELECT detail FROM asset_audit WHERE asset_slug=? AND action='delete'",
                        (str(mid),))
        self.assertEqual(len(rows), 1)
        self.assertTrue(json.loads(rows[0]["detail"]).get("soft"),
                        "软删未标注 ⇒ 读审计的人会以为数据被物理删除")

    def test_put_l2_records_which_field_changed(self):
        asyncio.run(self.m.put_l2(self.m.DocIn(content="# 新 L2", manual=None), self.req))
        d = json.loads(db.query("SELECT detail FROM asset_audit WHERE asset_slug='L2'")[0]["detail"])
        self.assertEqual(d["touched"], ["content"])
        self.assertNotIn("新 L2", json.dumps(d, ensure_ascii=False))

    def test_put_l3_records_manual_touch(self):
        """manual 是用户手写补充（09-23 曾被 rebuild 静默覆盖）⇒ 它被动过必须单独留痕。"""
        asyncio.run(self.m.put_l3(self.m.DocIn(content=None, manual="用户手写"), self.req))
        d = json.loads(db.query("SELECT detail FROM asset_audit WHERE asset_slug='L3'")[0]["detail"])
        self.assertEqual(d["touched"], ["manual"])
        self.assertEqual(d["manual_chars"], len("用户手写"))

    def test_rebuild_l2_audited_with_manual_untouched(self):
        for i in range(2):
            self._l1(content="记忆 %d" % i)
        try:
            asyncio.run(self.m.rebuild_l2(self.req))
        except Exception as e:                       # noqa: BLE001
            if "近30天无 L1 记忆可压缩" not in str(e):
                raise
        rows = db.query("SELECT detail FROM asset_audit WHERE action='rebuild'")
        self.assertEqual(len(rows), 1)
        d = json.loads(rows[0]["detail"])
        self.assertTrue(d.get("manual_untouched"))
        self.assertIn("items", d)
        self.assertIn("llm", d)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit.TestMemoryAudit -v 2>&1 | tail -8
```
Expected: 6 例 FAIL/ERROR，含 `create_l1() takes 1 positional argument but 2 were given`。

- [ ] **Step 4: 改 `src/memory.py`**

改 1 —— `from fastapi import APIRouter, HTTPException, Query` → 加 `Request`：

```python
from fastapi import APIRouter, HTTPException, Query, Request
```

改 2 —— 在 `import tdai_client` 之后加：

```python
import writeauth
```

改 3 —— `create_l1` 整体替换：

```python
@router.post("/api/memory/l1")
async def create_l1(body: MemoryIn, request: Request):
    if body.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(CATEGORIES)}")
    mid = db.add_memory(body.content, body.category, body.source, body.session_id)
    # detail 不记正文（正文已在 memories 表；重复记 = 体积翻倍且多一处凭据面）
    db.log_asset_event("memory_l1", str(mid), "create", writeauth.actor_of(request),
                       {"category": body.category, "source": body.source,
                        "chars": len(body.content or ""), "session_id": body.session_id})
    return {"id": mid, "status": "created"}
```

改 4 —— `create_l1_batch` 整体替换：

```python
@router.post("/api/memory/l1/batch")
async def create_l1_batch(body: MemoryBatchIn, request: Request):
    ids = []
    for item in body.items:
        cat = item.category if item.category in CATEGORIES else "fact"
        ids.append(db.add_memory(item.content, cat, item.source or "extract", item.session_id))
    db.log_asset_event("memory_l1", "batch", "create", writeauth.actor_of(request),
                       {"count": len(ids), "ids": ids[:50]})   # 汇总一行；ids 截 50 防单行爆体积
    return {"ids": ids, "count": len(ids)}
```

改 5 —— `delete_l1` 整体替换：

```python
@router.delete("/api/memory/l1/{mid}")
async def delete_l1(mid: int, request: Request):
    n = db.execute("UPDATE memories SET status='deleted', updated_at=? WHERE id=?", (_now(), mid))
    if not n:
        raise HTTPException(404, "memory not found")
    db.log_asset_event("memory_l1", str(mid), "delete", writeauth.actor_of(request),
                       {"soft": True})          # 软删（status='deleted'），不是物理删除
    return {"status": "deleted", "id": mid}
```

改 6 —— 在 `_put_doc()` 之后插入私有 helper：

```python
def _audit_doc(layer: str, body: "DocIn", request) -> None:
    """L2/L3 变更审计。touched 必须点名动了哪个字段 —— `manual` 是用户手写补充，
    09-23 曾被 rebuild 静默覆盖过（见 writeauth.py docstring 记录的事故），它被动过要单独留痕。
    只记字符数不记正文：正文在 memory_docs 表里，审计不是第二份副本。"""
    touched = [k for k, v in (("content", body.content), ("manual", body.manual))
               if v is not None]
    db.log_asset_event("memory_doc", layer, "update", writeauth.actor_of(request),
                       {"touched": touched,
                        "content_chars": len(body.content) if body.content is not None else None,
                        "manual_chars": len(body.manual) if body.manual is not None else None})
```

改 7 —— `put_l2` / `put_l3` 整体替换：

```python
@router.put("/api/memory/l2")
async def put_l2(body: DocIn, request: Request):
    _audit_doc("L2", body, request)
    return _put_doc("L2", body.content, body.manual)
```

```python
@router.put("/api/memory/l3")
async def put_l3(body: DocIn, request: Request):
    _audit_doc("L3", body, request)
    return _put_doc("L3", body.content, body.manual)
```

改 8 —— `rebuild_l2` 签名加 `request: Request`，并在 `return {"status": "rebuilt", …}` **之前**插入：

```python
@router.post("/api/memory/l2/rebuild")
async def rebuild_l2(request: Request):
```

```python
    db.log_asset_event("memory_doc", "L2", "rebuild", writeauth.actor_of(request),
                       {"items": len(rows), "llm": llm_used,
                        "manual_untouched": True})   # _put_doc(…, None) ⇒ manual 一字未动，如实记
```

- [ ] **Step 5: 通过 + 覆盖面护栏转绿 + 既有记忆闸门零回归**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit -v 2>&1 | tail -6
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_tdai_client tests.test_entity_mode_source_of_truth 2>&1 | tail -3
```
Expected: `test_asset_audit` `OK`（18 例）；既有两只 `OK`。

- [ ] **Step 6: 红对照（软删标记）**

```bash
mkdir -p work && cp src/memory.py work/.memkeep && python3 - <<'PY'
import pathlib
p = pathlib.Path("src/memory.py"); s = p.read_text(encoding="utf-8")
p.write_text(s.replace('{"soft": True})          # 软删', 'None)          # 软删'), "utf-8")
PY
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit.TestMemoryAudit.test_delete_l1_marks_soft_delete 2>&1 | tail -4
cp work/.memkeep src/memory.py && rm -f work/.memkeep
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_asset_audit 2>&1 | tail -3
```
Expected: 第一段 `FAILED`；还原后 `OK`。

- [ ] **Step 7: 提交**

```bash
git add src/memory.py tests/test_asset_audit.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(memory): 六个记忆变更点打审计（不记正文只记元信息 / 软删如实标注 / manual 被动过单独留痕）"
```

---

### Task 6: `src/memstats.py` staleness 观测 + 挂到 `/api/kb/status`

**Files:**
- Create: `src/memstats.py`
- Modify: `src/kb.py`（import +1；`kb_status()` 返回体 +1 键）
- Test: `tests/test_memstats.py`（新建）

**Interfaces:**
- Consumes: `db.query`（只读 SELECT）
- Produces:
  - `memstats.STALE_DAYS: int = 14`
  - `memstats.age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]`（**绝不抛**）
  - `memstats.local_stats(rows: list, docs: list, now: Optional[datetime] = None) -> dict`
  - `memstats.verdict(stats: dict) -> {"state": "fresh|stale|empty", "reason": str}`
  - `memstats.collect() -> dict`（唯一碰 db 的入口，只读）
  - `kb_status()` 返回体新增键 `local_memory`

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp src/kb.py src/kb.py.bak-$(date +%Y%m%d_%H%M%S)-挂记忆观测
```

- [ ] **Step 2: 写失败测试** — 新建 `tests/test_memstats.py`

```python
#!/usr/bin/env python3
"""L0 hermetic：本地记忆便签的 staleness 观测（src/memstats.py）。

★ 本闸门最重要的钉子是**红向的"不许动数据"**：这个模块一旦长出 DELETE/UPDATE/LLM 调用，
就等于把 09-23 那次「后台 rebuild 静默重写用户手写 L2 记忆」的事故做成常态化。
所以除了算得对，还要静态断言它**没有能力**改库。

改判依据（spec §5，2026-09-25 生产库只读实测）：
    memories rows=4  status={'active':4}  最老=2026-09-06T03:51  最新=2026-09-06T03:57
⇒ 4 行全 active、零软删行、19 天没长过一行；清理任务会永远空转，而记忆权威副本在 TDAI(:8420)。
"""
import pathlib
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import memstats  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


class TestAgeDays(unittest.TestCase):
    def test_aware_and_naive_both_work(self):
        self.assertAlmostEqual(memstats.age_days(_iso(3), NOW), 3.0, places=1)
        naive = (NOW - timedelta(days=2)).replace(tzinfo=None).isoformat()
        self.assertAlmostEqual(memstats.age_days(naive, NOW), 2.0, places=1,
                               msg="无时区形态必须按 UTC 兜住（kb.py 已在 built_at 上栽过一次 TypeError）")

    def test_garbage_returns_none_never_raises(self):
        for bad in (None, "", "not-a-date", "2026-13-45T99:99:99", 12345):
            self.assertIsNone(memstats.age_days(bad, NOW), "输入 %r 该回 None" % (bad,))

    def test_future_timestamp_clamps_to_zero(self):
        self.assertEqual(memstats.age_days(_iso(-1), NOW), 0.0)


class TestLocalStats(unittest.TestCase):
    def test_counts_and_ages(self):
        rows = [{"status": "active", "created_at": _iso(19)},
                {"status": "active", "created_at": _iso(19)},
                {"status": "deleted", "created_at": _iso(1)}]
        s = memstats.local_stats(rows, [], NOW)
        self.assertEqual(s["rows"], 3)
        self.assertEqual(s["by_status"], {"active": 2, "deleted": 1})
        self.assertAlmostEqual(s["oldest_age_days"], 19.0, places=1)
        self.assertAlmostEqual(s["newest_age_days"], 1.0, places=1)

    def test_docs_report_manual_presence(self):
        docs = [{"layer": "L2", "content": "abc", "manual": "手写", "updated_at": _iso(5)},
                {"layer": "L3", "content": "", "manual": "", "updated_at": None}]
        s = memstats.local_stats([], docs, NOW)
        self.assertTrue(s["docs"]["L2"]["has_manual"])
        self.assertEqual(s["docs"]["L2"]["manual_chars"], 2)
        self.assertFalse(s["docs"]["L3"]["has_manual"])
        self.assertIsNone(s["docs"]["L3"]["age_days"])

    def test_empty_inputs_are_safe(self):
        s = memstats.local_stats([], [], NOW)
        self.assertEqual(s["rows"], 0)
        self.assertIsNone(s["oldest_age_days"])


class TestVerdict(unittest.TestCase):
    def test_empty(self):
        v = memstats.verdict(memstats.local_stats([], [], NOW))
        self.assertEqual(v["state"], "empty")
        self.assertIn("TDAI", v["reason"], "空态必须说清权威副本在哪，否则会被读成故障")

    def test_stale_uses_newest_row(self):
        rows = [{"status": "active", "created_at": _iso(30)},
                {"status": "active", "created_at": _iso(19)}]
        v = memstats.verdict(memstats.local_stats(rows, [], NOW))
        self.assertEqual(v["state"], "stale")
        self.assertIn("19", v["reason"])
        self.assertIn(str(memstats.STALE_DAYS), v["reason"])

    def test_fresh(self):
        rows = [{"status": "active", "created_at": _iso(1)}]
        self.assertEqual(memstats.verdict(memstats.local_stats(rows, [], NOW))["state"], "fresh")

    def test_verdict_wording_never_implies_deletion(self):
        """★ 红向：这个模块只报告，不清理 ⇒ 文案里不许出现会被读成"我会删"的字样。"""
        for st in ("empty", "stale", "fresh"):
            rows = [] if st == "empty" else [{"status": "active", "created_at": _iso(19)}]
            v = memstats.verdict(memstats.local_stats(rows, [], NOW))
            for banned in ("已清理", "将删除", "自动删除", "已删除", "清理完成"):
                self.assertNotIn(banned, v["reason"], "%s 态文案暗示会动数据：%s" % (st, banned))


class TestModuleCannotMutate(unittest.TestCase):
    """★ 静态护栏：模块文本里出现写库/LLM/后台任务即判红。"""

    def setUp(self):
        self.src = (_REPO / "src" / "memstats.py").read_text(encoding="utf-8")
        # 只判**去注释后**的代码，否则本模块 docstring 里"绝不 DELETE"的自律声明会被当成违规
        self.code = re.sub(r'"""[\s\S]*?"""', "", self.src)
        self.code = "\n".join(l for l in self.code.splitlines()
                              if not l.strip().startswith("#"))

    def test_no_write_sql(self):
        for kw in ("DELETE FROM", "UPDATE ", "INSERT INTO", "DROP ", "execute("):
            self.assertNotIn(kw, self.code, "memstats 出现写库能力：%s" % kw)

    def test_no_llm_and_no_background_task(self):
        for kw in ("import llm", "llm.", "create_task", "while True", "asyncio.sleep"):
            self.assertNotIn(kw, self.code, "memstats 出现 %s（观测模块不许调 LLM/起后台循环）" % kw)

    def test_only_select_queries(self):
        self.assertIn("db.query", self.src)
        for q in re.findall(r'db\.query\(\s*"([^"]+)', self.src):
            self.assertTrue(q.strip().upper().startswith("SELECT"), "非 SELECT 语句：%s" % q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_memstats 2>&1 | tail -4
```
Expected: `ModuleNotFoundError: No module named 'memstats'`。

- [ ] **Step 4: 新建 `src/memstats.py`**

```python
"""本地记忆便签的 staleness **观测**（件 3 / spec §5 的 B-2 改判）。

为什么是"观测"而不是"清理"（实测依据，2026-09-25，生产库只读 mode=ro）：
    memories rows=4  status={'active':4}  最老=2026-09-06T03:51  最新=2026-09-06T03:57
    memory_docs rows=1   db size=1.27MB
⇒ 4 行全 active、零软删行、19 天没长过一行 ⇒ 后台清理任务会永远空转；
而"后台自动重建 L2"这条路有事故前例（writeauth.py docstring：09-23 rebuild 真的重写了
用户手写 L2 记忆，被迫按 09-06 在册副本逐字回滚）；且记忆权威副本在 TDAI(:8420)，
本地表在 KB 融合里权重只有 0.2（src/kb.py 的 W）—— 删它零收益，**报告它腐烂**才是净收益。

★ 本模块**绝不** DELETE / UPDATE / INSERT / 调 LLM / 起后台任务。
  L0 闸门把这条钉死（tests/test_memstats.py::TestModuleCannotMutate）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import db

#: 阈值理由：与本机"结论必须绑定取证日期"的口径同族 —— 超过两周没动过的本地便签，
#: 在 KB 融合里权重只有 0.2，实际上已不参与决策 ⇒ 报 stale（可见）而不是删（不可逆）。
STALE_DAYS = 14


def age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """ISO 时间串 → 距今天数。**绝不抛**（拿不到就 None）。

    无时区形态按 UTC 兜：kb.py 已经在 turbovec 的 `built_at` 上栽过一次 ——
    naive 与 aware datetime 相减直接 TypeError，而闸门第一版只捕了 ValueError。
    """
    if not iso or not isinstance(iso, str):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(iso.strip())
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    try:
        return round(max(0.0, (now - t).total_seconds() / 86400.0), 1)
    except (TypeError, ValueError):
        return None


def local_stats(rows: List[Dict[str, Any]], docs: List[Dict[str, Any]],
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """纯函数：吃行集出统计。不碰 db ⇒ L0 可在空 HOME 下判卷。"""
    now = now or datetime.now(timezone.utc)
    ages = [a for a in (age_days(r.get("created_at"), now) for r in (rows or []))
            if a is not None]
    by_status: Dict[str, int] = {}
    for r in (rows or []):
        k = str(r.get("status") or "?")
        by_status[k] = by_status.get(k, 0) + 1
    out: Dict[str, Any] = {
        "rows": len(rows or []),
        "by_status": by_status,
        "oldest_age_days": max(ages) if ages else None,
        "newest_age_days": min(ages) if ages else None,
        "docs": {},
    }
    for d in (docs or []):
        layer = str(d.get("layer") or "?")
        manual = d.get("manual") or ""
        content = d.get("content") or ""
        out["docs"][layer] = {
            "age_days": age_days(d.get("updated_at"), now),
            "chars": len(content),
            "manual_chars": len(manual),
            "has_manual": bool(manual.strip()),
        }
    return out


def verdict(stats: Dict[str, Any]) -> Dict[str, str]:
    """纯判定。文案口径：**只报告，绝不清理** —— 不许出现会被读成"我会删"的字样。"""
    n = int(stats.get("rows") or 0)
    if n == 0:
        return {"state": "empty",
                "reason": "本地便签 0 行。权威记忆在 TDAI(:8420)，本地为空不是故障；"
                          "本模块只观测，不清理。"}
    newest = stats.get("newest_age_days")
    if newest is not None and newest >= STALE_DAYS:
        return {"state": "stale",
                "reason": "共 %d 行，最新一行已 %s 天未更新（阈值 %d 天）。本地表在 KB 融合里"
                          "权重只有 0.2，实际上已不参与决策 ⇒ 报 stale 而不是删。"
                          % (n, newest, STALE_DAYS)}
    return {"state": "fresh",
            "reason": "共 %d 行，最新一行 %s 天前更新。" % (n, newest)}


def collect() -> Dict[str, Any]:
    """唯一碰 db 的入口（**只读 SELECT**）。失败不抛：由调用方按"逐路表态"降级。"""
    rows = db.query("SELECT status, created_at FROM memories")
    docs = db.query("SELECT layer, content, manual, updated_at FROM memory_docs")
    s = local_stats(rows, docs)
    s.update(verdict(s))
    s["authoritative_source"] = "TDAI(:8420)"
    s["policy"] = "observe-only（不删除、不重建、不调 LLM）"
    return s
```

- [ ] **Step 5: 跑测试确认通过**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_memstats -v 2>&1 | tail -6
```
Expected: `OK`（`Ran 13 tests`）。

- [ ] **Step 6: 挂到 `/api/kb/status`（`src/kb.py`）**

改 1 —— import 区（`import tdai_client` 同侧）加：

```python
import memstats
```

改 2 —— `kb_status()` 里，`tdai = tdai_client.backend_status()` 之后插入：

```python
    # 本地记忆便签的 staleness（件 3）。**失败不拖垮整个状态端点**：逐路表态是本项目
    # 的立身口径（kb 四路联邦每一路都必须回 ok/error），一路炸了不许把其余路一起糊掉。
    try:
        mem = memstats.collect()
    except Exception as e:                            # noqa: BLE001
        mem = {"state": "unavailable", "rows": None,
               "reason": "%s: %s" % (type(e).__name__, str(e)[:160])}
```

改 3 —— 返回体里，`"tdai": tdai,` 之后插入一行：

```python
        "local_memory": mem,
```

- [ ] **Step 7: 进进程真跑一次 `kb_status()`（不打网络：turbovec/tdai 路会自报降级）**

```bash
HUB_HOST_TESTS=0 venv/bin/python - <<'PY'
import asyncio, pathlib, sys, tempfile
sys.path.insert(0, "src"); sys.path.insert(0, ".")
import db, kb
tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0kb-"))
db.init_db(tmp / "t.db")
db.add_memory("端口 3102 由 agent-hub 占用", "fact", "manual")
d = asyncio.run(kb.kb_status())
print("local_memory =", d["local_memory"])
assert d["local_memory"]["rows"] == 1, d["local_memory"]
assert d["local_memory"]["state"] in ("fresh", "stale", "empty")
assert "backends" not in d["local_memory"]
print("PASS：kb_status 带出 local_memory，且其余键仍在 =", sorted(d))
PY
```
Expected: 打印 `PASS：kb_status 带出 local_memory，且其余键仍在 = ['local_memory', 'tdai', 'turbovec', 'wigolo']`。

- [ ] **Step 8: 红对照（给 memstats 加写库能力 ⇒ 必须 FAIL）**

```bash
mkdir -p work && cp src/memstats.py work/.mskeep && printf '\n\ndef _purge():\n    db.execute("DELETE FROM memories WHERE id<0")\n' >> src/memstats.py
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_memstats.TestModuleCannotMutate -v 2>&1 | tail -6
cp work/.mskeep src/memstats.py && rm -f work/.mskeep
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_memstats 2>&1 | tail -3
```
Expected: 第一段 `FAILED`（`memstats 出现写库能力：DELETE FROM`）；还原后 `OK`。

- [ ] **Step 9: 提交**

```bash
git add src/memstats.py src/kb.py tests/test_memstats.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(memstats): 本地记忆便签 staleness 观测（B-2 改判：只报告不清理）+ 挂 /api/kb/status.local_memory"
```

---

### Task 7: 前端会话导出按钮（blob 下载 + 四态文案）

**Files:**
- Modify: `static/hub/04-terminal-ws.js`（尾部追加 4 个函数 + 1 个委托监听）
- Modify: `templates/index.html`（**仅 1 行**：`chatSessList` 工具条加按钮）
- Rebuild: `static/hub.js`（`bash scripts/build_hubjs.sh`）
- Test: `tests/test_export_button.py`（新建，L0 + L1）

**Interfaces:**
- Consumes: `termToken()`（`03-agents-cards.js:211`，只调用不修改）、`toast(msg, cls)`、`lsRemove(key)`、`chatPick`（当前 agent id）、`escapeHtml()`；后端 `GET /api/sessions/export`（`main.py:531`，响应头 `X-Export-Count` / `X-Export-Redacted-Hits` / `Content-Disposition`）
- Produces:
  - `exportStateOf(status: number, count: number, detail: string) -> 'ok'|'empty'|'need-token'|'bad-token'|'misconfig'|'error'`
  - `exportStateText(state: string, count: number, extra: object) -> string`
  - `chatSessExport() -> Promise<void>`
  - `document` 级 `[data-export]` 委托监听

- [ ] **Step 1: 备份**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
cp static/hub/04-terminal-ws.js static/hub/04-terminal-ws.js.bak-$(date +%Y%m%d_%H%M%S)-加导出按钮
cp templates/index.html templates/index.html.bak-$(date +%Y%m%d_%H%M%S)-加导出按钮
```

- [ ] **Step 2: 写失败测试** — 新建 `tests/test_export_button.py`

```python
#!/usr/bin/env python3
"""会话导出按钮闸门（件 1）。分层口径见 tests/tiers.py。

  L0 静态不变量 —— 只读 static/hub/04-terminal-ws.js、static/hub.js、templates/index.html 的真文本。
  L1 host       —— 真跑 node，执行从 hub.js **原样抽出**的 exportStateOf / exportStateText。

为什么这个按钮值得单独一层（三个坑都是实测出来的，不是假想）：
1. `/api/sessions/export` 是 **GET 却要写级鉴权**（后端显式 writeauth.decide("POST",…)），
   而 01-core-boot.js 的 api() 只给 POST/PUT/PATCH/DELETE 带 token ⇒ 走 api() 必 401。
2. api() 会把响应体 JSON.parse 成对象 ⇒ CSV/JSON **文件字节**被毁，必须走 blob。
3. `?token=` 会把凭据送进服务端访问日志与浏览器历史（本工作区三次外流前例）⇒ 只准走头。
外加一条与 07-asset-panel.js 同源的红向：**被拒绝不许说成"没有会话"**（四态文案互斥）。
"""
import pathlib
import shutil
import subprocess
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests"))

from _hub_extract import extract_function, read_hub   # noqa: E402
import tiers                                          # noqa: E402

SHARD = _REPO / "static" / "hub" / "04-terminal-ws.js"
TPL = _REPO / "templates" / "index.html"


class TestExportButtonStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = SHARD.read_text(encoding="utf-8")
        cls.hub = read_hub()
        cls.html = TPL.read_text(encoding="utf-8")

    def test_hubjs_contains_new_functions(self):
        """产物形态判据：忘记跑 build_hubjs.sh 时这里就红（先例：test_asset_panel）。"""
        for fn in ("exportStateOf", "exportStateText", "chatSessExport"):
            self.assertIsNotNone(extract_function(self.hub, fn),
                                 "hub.js 里找不到 %s ⇒ 忘记跑 scripts/build_hubjs.sh" % fn)

    def test_uses_blob_download_not_api_helper(self):
        fn = extract_function(self.js, "chatSessExport")
        self.assertIsNotNone(fn)
        self.assertIn("createObjectURL", fn, "必须走 blob 下载")
        self.assertIn("revokeObjectURL", fn, "objectURL 不回收 = 内存泄漏")
        self.assertIn(".download", fn)
        self.assertNotIn("await api(", fn, "api() 会 JSON.parse 毁掉文件字节，且不给 GET 带 token")

    def test_token_goes_in_header_never_in_url(self):
        """★ 红向钉子：token 进 URL 就等于把它写进访问日志与浏览器历史。"""
        fn = extract_function(self.js, "chatSessExport")
        self.assertIn("X-TERM-TOKEN", fn)
        self.assertNotIn("token=", fn, "URL 里出现 token= ⇒ 凭据外流面")
        self.assertNotIn("location.href", fn, "整页跳转下载会把 token 带进历史")

    def test_default_is_redacted(self):
        fn = extract_function(self.js, "chatSessExport")
        self.assertIn("redact", fn)
        self.assertRegex(fn, r"redact['\"]?\s*[:,]\s*['\"]?1", "默认必须 redact=1（不静默给原文）")

    def test_button_uses_delegation_not_inline_onclick(self):
        """AGENTS.md 浮层配套红线：口径并存取最严 ⇒ 新按钮走 data-* 委托，不用 inline onclick。"""
        i = self.html.index('id="chatSessList"')
        seg = self.html[i:self.html.index("</span>", i)]
        self.assertIn('data-export="sessions"', seg)
        self.assertNotIn('onclick="chatSessExport', seg)
        self.assertIn("closest('[data-export]')", self.js.replace('"', "'"),
                      "委托监听不见了 ⇒ 按钮点了没反应")

    def test_icon_exists_in_sprite(self):
        """红向：发明一个不存在的 sprite id，按钮会渲染成空白（先例：test_asset_panel 的色 token 闸门）。"""
        import re
        i = self.html.index('data-export="sessions"')
        seg = self.html[i:i + 300]
        used = re.findall(r'href="#(i-[a-z0-9-]+)"', seg)
        self.assertTrue(used, "按钮没有图标 ⇒ 窄屏上是个看不见的点击区")
        for u in used:
            self.assertIn('id="%s"' % u, self.html, "图标 %s 不在 sprite 里 ⇒ 按钮空白" % u)

    def test_no_bare_localstorage_no_second_breakpoint(self):
        """分档五不变量（AGENTS.md）：不许裸 localStorage、不许第二处断点定义。"""
        sys.path.insert(0, str(_REPO / "tests"))
        from _js_min import strip_comments
        fn = extract_function(self.js, "chatSessExport")
        code = strip_comments(fn)
        self.assertNotIn("localStorage.", code, "必须走 lsGet/lsSet/lsRemove 守卫")
        self.assertNotIn("matchMedia", code)
        self.assertNotIn("innerWidth", code)

    def test_state_wording_sets_are_disjoint(self):
        """★ 四态文案互斥：被拒态里不许出现"空结果"字样，反之亦然。"""
        fn = extract_function(self.js, "exportStateText")
        self.assertIsNotNone(fn)
        rejected = ("need-token", "bad-token", "misconfig", "error")
        for st in rejected:
            self.assertIn("'%s'" % st, fn, "缺 %s 分支" % st)
        # 被拒分支的文案必须明说"不是没有会话"
        self.assertGreaterEqual(fn.count("不是没有会话"), 4,
                                "四种被拒/失败态都必须明说「不是没有会话」，否则用户读成资产为空")
        self.assertIn("'empty'", fn)
        self.assertIn("0 条会话", fn, "真·空结果才准说 0 条")


@tiers.host_only
class TestExportButtonNode(unittest.TestCase):
    """L1：真跑 node，执行从 hub.js **原样抽出**的判定与文案函数（不手抄实现）。"""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest(tiers.HOST_SKIP_REASON)
        hub = read_hub()
        parts = [extract_function(hub, n) for n in ("exportStateOf", "exportStateText")]
        if any(p is None for p in parts):
            raise AssertionError("hub.js 里抽不到导出函数（或被改名）⇒ 判据无法针对真代码执行")
        cls.src = "\n".join(parts)

    def _run(self):
        js = self.src + "\n" + r"""
function out(tag, s) { console.log(tag + "||" + String(s).replace(/\s+/g, " ")); }
out("S_401_NOTOK", exportStateOf(401, -1, "缺少凭据（x-hub-token / x-term-token / ?token=）"));
out("S_401_BAD", exportStateOf(401, -1, "凭据不匹配"));
out("S_503", exportStateOf(503, -1, "服务端未配置 TERM_TOKEN/HUB_PASSCODE"));
out("S_500", exportStateOf(500, -1, "boom"));
out("S_EMPTY", exportStateOf(200, 0, ""));
out("S_OK", exportStateOf(200, 3, ""));
out("T_NOTOK", exportStateText("need-token", -1, {}));
out("T_BAD", exportStateText("bad-token", -1, {}));
out("T_503", exportStateText("misconfig", -1, {}));
out("T_ERR", exportStateText("error", -1, {status: 500}));
out("T_EMPTY", exportStateText("empty", 0, {}));
out("T_OK", exportStateText("ok", 3, {hits: 2}));
out("T_OK0", exportStateText("ok", 3, {hits: 0}));
"""
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, "node 报错：\n" + p.stderr[:800])
        out = {}
        for line in p.stdout.splitlines():
            if "||" in line:
                k, v = line.split("||", 1)
                out[k] = v
        self.assertTrue(out, "node 无任何输出，判定其实是空转")
        return out

    def test_states_are_distinguished(self):
        o = self._run()
        self.assertEqual(o["S_401_NOTOK"], "need-token")
        self.assertEqual(o["S_401_BAD"], "bad-token")
        self.assertEqual(o["S_503"], "misconfig")
        self.assertEqual(o["S_500"], "error")
        self.assertEqual(o["S_EMPTY"], "empty")
        self.assertEqual(o["S_OK"], "ok")

    def test_rejected_never_reads_as_empty(self):
        """★ 本闸门核心红向：被拒态文案里不许出现"0 条 / 没有会话"这类读法。"""
        o = self._run()
        banned = ("0 条会话", "没有会话", "确实是 0")
        for tag in ("T_NOTOK", "T_BAD", "T_503", "T_ERR"):
            for b in banned:
                self.assertNotIn(b, o[tag], "%s 把被拒渲染成了空态：%s" % (tag, b))
            self.assertIn("不是没有会话", o[tag])

    def test_empty_state_is_honest_about_success(self):
        o = self._run()
        self.assertIn("导出成功", o["T_EMPTY"])
        self.assertIn("0 条会话", o["T_EMPTY"])
        self.assertNotIn("被拒", o["T_EMPTY"])

    def test_ok_state_reports_redaction_hits(self):
        """不静默改数据：打码命中数必须说出来（后端 X-Export-Redacted-Hits 的兑现）。"""
        o = self._run()
        self.assertIn("3", o["T_OK"])
        self.assertIn("2", o["T_OK"])
        self.assertIn("脱敏", o["T_OK"])
        self.assertIn("0 处", o["T_OK0"], "命中 0 处也要如实说，不能省略成"没打码"")

    def test_error_state_carries_http_code(self):
        self.assertIn("500", self._run()["T_ERR"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_export_button -v 2>&1 | tail -8
```
Expected: L0 8 例 FAIL（`hub.js 里找不到 exportStateOf`、`data-export` 不在模板里）。

- [ ] **Step 4: 改 `templates/index.html`（仅 1 行）**

在 `title="刷新列表" onclick="chatSessLoad()"` 那个按钮**之后**、同层 `</span>` 之前插入：

```html
                                <button class="btn ghost sm" title="导出本 agent 的会话（JSON，默认脱敏）" data-export="sessions"><svg class="i xs" aria-hidden="true"><use href="#i-share"/></svg></button>
```

> 图标用既有 `#i-share`（sprite 里**没有** `i-download`；发明新 id 会渲染成空白，
> 这正是 `test_asset_panel.py::test_only_defined_theme_tokens` 同族的坑）。
> `i-share` 语义＝外发，正好提醒这是数据外流动作。

- [ ] **Step 5: 追加前端实现** — 在 `static/hub/04-terminal-ws.js` **文件末尾**追加：

```javascript
/* ── 会话导出（件 1 / spec §3）──────────────────────────────────────────
   三个实测出来的坑，决定了这里为什么长这样：
   ① 不能走 api()：01-core-boot.js 的 isWriteMethod() 只给 POST/PUT/PATCH/DELETE 带 token，
      而 /api/sessions/export 是 **GET 却要写级鉴权**（后端显式 writeauth.decide("POST",…)）⇒ 必 401。
   ② 不能用 api() 取体：它会 JSON.parse 成对象，CSV/JSON **文件字节**就毁了 ⇒ 必须 blob。
   ③ token 不走 ?token=：那会进服务端访问日志与浏览器历史（本工作区三次凭据外流前例）⇒ 只走头。
   文案四态互斥（照抄 07-asset-panel.js 的红向口径）：**被拒绝不许说成"没有会话"**。
   窄屏口径：只加 1 个图标按钮，不做 format/redact 的一排开关（chrome 单行化优先级更高）；
   CSV 与 redact=0 走 API 参数，不在本轮做 UI（YAGNI，且给原文出口做 UI 需单独裁定）。 */
function exportStateOf(status, count, detail) {
  if (status === 503) return 'misconfig';
  if (status === 401) return /缺少凭据/.test(String(detail || '')) ? 'need-token' : 'bad-token';
  if (status !== 200) return 'error';
  return (count > 0) ? 'ok' : 'empty';
}

function exportStateText(state, count, extra) {
  const n = (count == null || count < 0) ? '?' : String(count);
  const code = (extra && extra.status) ? String(extra.status) : '?';
  const hits = (extra && typeof extra.hits === 'number') ? extra.hits : 0;
  if (state === 'ok') {
    return '已导出 ' + n + ' 条会话（默认脱敏，命中 ' + hits + ' 处' +
           (hits > 0 ? '，正文已打码）' : '）');
  }
  if (state === 'empty') return '导出成功，但这个范围内确实是 0 条会话（文件是空的，不是被拒）';
  if (state === 'need-token') return '导出被拒：未提供凭据 —— 不是没有会话。请在「设置」里应用 TERM_TOKEN 后重试';
  if (state === 'bad-token') return '导出被拒：凭据不匹配 —— 不是没有会话。存量口令可能已换过，已清除，请重新输入';
  if (state === 'misconfig') return '导出被拒：服务端未配置口令（fail-closed）—— 不是没有会话';
  return '导出失败（HTTP ' + code + '）—— 不是没有会话';
}

async function chatSessExport() {
  const tk = termToken();
  if (!tk) { toast(exportStateText('need-token', -1, {}), 'err'); return; }
  const p = new URLSearchParams({ format: 'json', limit: '1000',
                                  with_messages: '1', redact: '1' });
  if (chatPick) p.set('agent_id', chatPick);
  let r;
  try {
    r = await fetch('/api/sessions/export?' + p.toString(),
                    { headers: { 'X-TERM-TOKEN': tk } });   // 头，不是 URL
  } catch (e) {
    toast('导出失败：网络不可达（' + e.message + '）—— 不是没有会话', 'err');
    return;
  }
  const cnt = parseInt(r.headers.get('X-Export-Count') || '-1', 10);
  const hits = parseInt(r.headers.get('X-Export-Redacted-Hits') || '0', 10);
  if (r.status !== 200) {
    let detail = '';
    try { const d = await r.json(); detail = (d && d.detail) || ''; } catch (e) { detail = ''; }
    const st = exportStateOf(r.status, cnt, detail);
    if (st === 'bad-token') lsRemove('hub.term.token');   // 存量失效口令：清掉，下次重新问
    toast(exportStateText(st, cnt, { status: r.status, hits: hits }), 'err');
    return;
  }
  let body;
  try { body = await r.blob(); } catch (e) {
    toast('导出失败：读不到响应体 —— 不是没有会话', 'err'); return;
  }
  const cd = r.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="?([^";]+)"?/);
  const name = (m && m[1]) || 'agent-hub-sessions.json';   // 后端保证纯 ASCII 文件名
  const obj = URL.createObjectURL(body);
  const a = document.createElement('a');
  a.href = obj;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(obj); a.remove(); }, 0);
  toast(exportStateText(exportStateOf(200, cnt, ''), cnt, { hits: hits }), 'ok');
}

/* data-export 委托：新按钮一律走委托，不用 inline onclick（AGENTS.md 浮层配套红线，口径取最严）。
   挂在 document 上而不是某个面板里 ⇒ 工具条重渲染后不必重新绑定。 */
document.addEventListener('click', function (e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-export]') : null;
  if (!b) return;
  if (b.getAttribute('data-export') === 'sessions') chatSessExport();
});
```

> **顶层函数必须第 0 列收尾**（`_hub_extract.extract_function` 用 `\n}\n` 定位函数尾；
> 该文件 docstring 明写朴素花括号配对会被注释里的 `}` 截断，实测栽过）。上面四个函数已按此排版。

- [ ] **Step 6: 重建产物并跑测试**

```bash
bash scripts/build_hubjs.sh
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_export_button -v 2>&1 | tail -8
HUB_HOST_TESTS=1 venv/bin/python -m unittest tests.test_export_button -v 2>&1 | tail -8
HUB_HOST_TESTS=0 venv/bin/python -m unittest tests.test_hubjs_split 2>&1 | tail -3
```
Expected: `build_hubjs.sh` 打印 `built static/hub.js (… 行, md5 …)` 与 `?v= 提手同步 1 处`；
L0 8 例 `OK`；L1（`HUB_HOST_TESTS=1`）5 例 `OK`；`test_hubjs_split` `OK`（拼接与产物逐字节相等）。

- [ ] **Step 7: 红对照（把被拒文案改成空态 ⇒ 必须 FAIL）**

```bash
mkdir -p work && cp static/hub/04-terminal-ws.js work/.exkeep && python3 - <<'PY'
import pathlib
p = pathlib.Path("static/hub/04-terminal-ws.js"); s = p.read_text(encoding="utf-8")
p.write_text(s.replace("导出被拒：未提供凭据 —— 不是没有会话。",
                       "该 agent 没有会话（0 条）。"), "utf-8")
PY
bash scripts/build_hubjs.sh >/dev/null && HUB_HOST_TESTS=1 venv/bin/python -m unittest tests.test_export_button.TestExportButtonNode.test_rejected_never_reads_as_empty 2>&1 | tail -4
cp work/.exkeep static/hub/04-terminal-ws.js && rm -f work/.exkeep && bash scripts/build_hubjs.sh
HUB_HOST_TESTS=1 venv/bin/python -m unittest tests.test_export_button 2>&1 | tail -3
```
Expected: 第一段 `FAILED`（被拒渲染成空态）；还原后 `OK`。

- [ ] **Step 8: 提交**

```bash
git add static/hub/04-terminal-ws.js static/hub.js templates/index.html tests/test_export_button.py
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "feat(ui): 会话导出按钮（blob 下载 / token 只走头 / 四态文案互斥：被拒不许说成没有会话）+ L0 8 例 L1 5 例闸门"
git log --oneline -1
```

---

### Task 8: 全量回归 + CHANGELOG + push worktree + 集成者合并 master + 重建产物

**Files:**
- Modify: `CHANGELOG.md`（worktree 内，+1 节）
- 集成动作：`/home/gztxt/agent-hub`（master）合并 `wt/01a0d6dd`，重建 `static/hub.js`

- [ ] **Step 1: 全量 L0（零 SKIP）**

```bash
cd /home/gztxt/agent-hub-wt-01a0d6dd
bash scripts/run_tests.sh 2>&1 | tail -22
```
Expected: `Ran 3xx tests`（313 + 本批新增 ≈ **368**，以实跑为准）`in …s` / `OK`；
`SKIP: 0`；`hermetic: ok`；`tests=3xx failed=0 errors=0`；末行 `=== run_tests.sh: ALL GREEN ===`。
若出现 `FAILED (failures=…)` 或任何 `SKIP` ⇒ **停在原地查因，不许带着红推进**（铁律：无输出不得称 PASS）。

- [ ] **Step 2: 全量 L1（宿主依赖，真跑 node/git/python3.11）**

```bash
HUB_HOST_TESTS=1 venv/bin/python -m unittest discover -s tests -p "test_*.py" 2>&1 | tail -6
```
Expected: `OK`（L0 全量 + L1 35+5 ≈ **373** 例）。

- [ ] **Step 3: 写 CHANGELOG（worktree 内）**

```bash
cp CHANGELOG.md CHANGELOG.md.bak-$(date +%Y%m%d_%H%M%S)-本批条目
```

在 `## v0.13.23 — …` 那一节**之前**（即文件头部说明块之后、最新一节之前）插入，格式照抄既有节：

```markdown
## v0.13.24 — 联邦门面收口批：会话导出前端按钮 / asset_audit 资产变更审计 / 记忆 staleness 观测（后端+前端同批，随 09-25 一次重启上线）

> 施工会话：`01a0d6dd`，全程在自己的 worktree `agent-hub-wt-01a0d6dd`（分支 `wt/01a0d6dd`，基线 `40a1f89`）里改；
> 集成者合并 master 后才重建 `static/hub.js`（避 C7 build 产物单写者）。
> 设计稿：`docs/superpowers/specs/2026-09-25-federated-facade-closeout-design.md`（commit `42673c0`），
> 计划：`docs/superpowers/plans/2026-09-25-federated-facade-closeout.md`。
> 本批**不含 Docker/容器化**（用户 09-25 01:48 裁定：暂缓，留待后续迭代升级）。

### 后端：资产变更审计（`asset_audit`）—— 34 条写路由有鉴权、0 条有审计的缺口

- **`src/db.py`**：新表 `asset_audit`（append-only）+ 索引 `idx_audit_asset`/`idx_audit_created`；
  写口径 `log_asset_event(asset_type, asset_slug, action, actor, detail)` 与 `log_profile_event` 同族。
  三条硬约束都有 L0 闸门钉着：① **只 INSERT**（审计表可改就不叫审计）；② `detail` 落库前整体过
  `sessions_export.redact_text`（先 `json.dumps` 再脱敏 ⇒ 嵌套层也覆盖；只扫顶层值会漏 dict 里的 dict）——
  审计行会成为下一次会话导出的正文，凭据写进去＝二次外流；③ action 不在 `AUDIT_ACTIONS` 枚举里 ⇒
  打 `action_invalid` 标记，**绝不静默丢弃也绝不改写**（丢事件比记错更贵，改写毁掉取证原文）。
- **`src/writeauth.py`**：新增 `credential_name(provided, secrets)` 与 `actor_of(request)` 两个纯函数，
  `write_gate` 在 allow/exempt 分支打 `request.state.actor`（`user:term-token|hub-passcode|anonymous|exempt`）。
  **`decide()` 签名一字不动** —— 它的 `(verdict, reason)` 被中间件与 `/api/sessions/export` 端点共用，
  且 `tests/test_writeauth.py` 12 例钉着；改返回值＝零收益地撞 12 例既有闸门（零回归实测通过）。
  身份只记**凭据名**不记值（红向钉子：把名字换成凭据原文即 FAIL）。
- **`src/mcpgw.py`（4 点）/ `src/memory.py`（6 点）**：全部资产变更点打审计。覆盖面用 **AST** 扫
  （`tests/test_asset_audit.py::route_audit_map`）而不是 grep —— grep 只能证明"文件里某处有这个词"，
  证明不了"这条路由的 handler 体内有"；漏一条写点即 FAIL，且不审的路由必须挂着**书面理由**
  （`/mcp/servers/probe` 预览语义不落库、`/mcp/call` 是调用不是变更且已由 `profile_events` 记成败耗时）。
  - `mcp_servers.env` 装的是凭据 ⇒ 审计**只记 `has_env` 布尔**，绝不记值。
  - ACL 解绑**先取旧行再删**，把旧值记进 detail —— 否则"解绑了什么"永久丢失。
  - **ACL actor 一律取 `request.state.actor`（用户），`body.agent_id` 进 detail**（相对 spec §4 的执行期更正）：
    绑定 ACL 是"用户对某 agent 做的管理动作"，不是"agent 自己做的动作"；记成后者会把管理动作错归给 agent。
  - `memory` 侧 detail **不记正文**（正文已在 `memories` 表；重复记＝体积翻倍 + 多一处凭据面），只记元信息与字符数；
    `delete_l1` 是 `UPDATE status='deleted'` ⇒ 审计如实标 `soft=True`（写 "delete" 却不说清是软删＝"不静默改数据"的反面）；
    `put_l2/l3` 的 `touched` 点名动了哪个字段 —— `manual` 是用户手写补充，09-23 曾被 rebuild 静默覆盖过。
  - 批量导入记**一行汇总**（`asset_slug='batch'`，`ids` 截 50）：逐条写会让单次调用灌满表。
- **`src/audit.py`（新）**：`GET /api/audit/list` 只读查询门面，白名单 `VALID_TYPES` 七类，`limit` 钳到 1000。
  鉴权照抄 `/api/sessions/export` 既有先例：**GET 但按写方法判**（`decide("POST", …)`）——
  批量读审计行＝数据外流动作，且服务绑 `0.0.0.0:3102`，不按写判就是把变更史对局域网敞开。
  fail-closed：服务端没配口令 ⇒ **503 而不是放行**；拒绝日志只打 verdict/path/来源，绝不打凭据。

### 后端：本地记忆便签 staleness 观测（件 3 改判为 B-2「观测化」）

- **`src/memstats.py`（新）**：`age_days`（无时区按 UTC 兜、垃圾输入回 None **绝不抛**）、
  `local_stats`（纯函数：行数 / `by_status` / 最老最新天数 / L2·L3 的 `has_manual`）、
  `verdict`（`fresh|stale|empty`，阈值 `STALE_DAYS=14`）、`collect()`（唯一碰 db 的入口，**只读 SELECT**）。
  **★ 本模块绝不 DELETE/UPDATE/INSERT/调 LLM/起后台任务**，且由静态护栏钉死
  （`tests/test_memstats.py::TestModuleCannotMutate`，判去注释后的代码）。
  改判依据（生产库只读实测，2026-09-25）：`memories rows=4 status={'active':4}` 最老 2026-09-06T03:51、
  最新 2026-09-06T03:57 ⇒ 零软删行、19 天没长过一行，**清理任务会永远空转**；记忆权威副本在 TDAI(:8420)，
  本地表在 KB 融合里权重只有 0.2 ⇒ 删它零收益、报告它腐烂才是净收益；且"后台自动重建 L2"有事故前例。
  文案口径：**只报告不清理**，`verdict` 里不许出现"已清理/将删除"这类字样（有闸门）。
- **`src/kb.py`**：`/api/kb/status` 返回体新增 `local_memory` 键；`memstats.collect()` 失败 ⇒
  该键降级为 `{"state":"unavailable",…}`，**不拖垮整个状态端点**（逐路表态是 kb 四路联邦的立身口径）。
  端点仍是 GET 无鉴权，但只暴露计数与天数 ⇒ 不新增泄漏面（与 `code_stale` 同级情报）。

### 前端：会话导出按钮（件 1 —— v0.13.23 只有后端端点，UI 上没有任何按钮）

- **`static/hub/04-terminal-ws.js`**：`exportStateOf`/`exportStateText`/`chatSessExport` + `[data-export]`
  document 级委托监听；**`templates/index.html`** 只加 1 行（chat 会话工具条一个图标按钮，用既有 `#i-share`）。
  三个实测出来的坑决定了实现形态：① 不能走 `api()` —— `isWriteMethod()` 只给 POST/PUT/PATCH/DELETE 带 token，
  而导出是 **GET 却要写级鉴权** ⇒ 必 401；② 不能用 `api()` 取体 —— 它会 `JSON.parse` 成对象，CSV/JSON
  **文件字节**就毁了 ⇒ 必须 `blob` + `createObjectURL` + `.download` + `revokeObjectURL`；
  ③ token **只走 `X-TERM-TOKEN` 头，绝不进 `?token=`**（会进服务端访问日志与浏览器历史）。
  文案四态互斥（照抄 `07-asset-panel.js` 红向口径）：**被拒绝不许说成"没有会话"** ——
  `need-token|bad-token|misconfig|error` 四种态一律明写"不是没有会话"，只有 `count=0` 才准说"确实是 0 条"；
  成功态如实报**脱敏命中数**（兑现后端 `X-Export-Redacted-Hits`，命中 0 处也要说，不许省略成"没打码"）。
  窄屏口径：只加 1 个图标按钮，不做 format/redact 一排开关（chrome 单行化优先级更高；
  给原文出口做 UI 需单独裁定 ⇒ 本轮不提供）。`bad-token` 时 `lsRemove('hub.term.token')` 清掉存量失效口令。
  闸门 L0 8 例（含"图标必须在 sprite 里"、"不许裸 localStorage"、"不许第二处断点"）+ L1 5 例（真跑 node）。
- 顶层函数一律第 0 列收尾（`_hub_extract.extract_function` 用 `\n}\n` 定位函数尾；朴素花括号配对会被注释里的 `}` 截断，实测栽过）。

### 测试与护栏

- L0 新增 4 只文件：`test_asset_audit.py`（12 例，含 AST 覆盖面护栏）、`test_writeauth_actor.py`（15 例）、
  `test_audit_api.py`（7 例）、`test_memstats.py`（13 例）、`test_export_button.py`（L0 8 + L1 5）。
  全量：**L0 313 → 3xx（实跑为准）**，`SKIP=0`；L1 35 → 40。
- 每件都做了**红对照**（把关键不变量改坏 → 闸门必须 FAIL），红对照命令与预期写在计划文件各 Task 里。
- 本批**不动** `static/hub/01|02|03|05|06|07-*.js`（`01a0d513` 会话正在改 02/03/06），不动
  `/fs/1000/ftp/技术文档/scripts/orchestration-check.sh`。
```

- [ ] **Step 4: 提交 + push worktree 分支**

```bash
git add CHANGELOG.md
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q -m "docs(changelog): v0.13.24 联邦门面收口批条目"
git log --oneline master..wt/01a0d6dd
git push -u origin wt/01a0d6dd 2>&1 | tail -4
```
Expected: 列出本批 6~7 个提交；push 成功（`* [new branch] wt/01a0d6dd -> wt/01a0d6dd`）。
push 失败（无凭据/网络）⇒ **不算本任务失败**，如实记录并继续（本地分支已在，集成不受影响）。

- [ ] **Step 5: 集成者合并前置门（三道，任一不过就停手报请）**

```bash
cd /home/gztxt/agent-hub
echo "--- 门 A：主 checkout 工作树必须干净（不许盖别人的未提交改动）"
git status --porcelain | head -10; echo "(空=干净)"
echo "--- 门 B：master HEAD 必须仍是基线 40a1f89（变了说明他会话又合了东西 ⇒ 需 rebase 重验）"
git log --oneline -1
echo "--- 门 C：orchestration-check 单写者（C3）"
bash /fs/1000/ftp/技术文档/scripts/orchestration-check.sh 2>&1 | grep -A12 "C3"
```
Expected: 门 A 空；门 B `40a1f89 …`；门 C 的 FAIL 面里**不含**本批文件
（`src/db.py|writeauth.py|mcpgw.py|memory.py|kb.py|audit.py|memstats.py|static/hub/04-terminal-ws.js|CHANGELOG.md`）。
`src/main.py`、`templates/index.html` 出现在 FAIL 面是**预期**（当天确有他会话写过），处置见 Step 7 的分层策略。
若门 A 非空 或 门 B 已变 ⇒ **停手向用户报请**，不自行 rebase 也不 `reset`（AGENTS.md 军规 7：禁对 `.git` 做恢复/替换）。

- [ ] **Step 6: 合并（fast-forward 不可行时用 --no-ff；禁 rebase 主干）**

```bash
cd /home/gztxt/agent-hub
git merge --no-ff wt/01a0d6dd -m "集成 wt/01a0d6dd：v0.13.24 联邦门面收口批（asset_audit 审计 / 记忆 staleness 观测 / 会话导出按钮）" 2>&1 | tail -8
```
Expected: 合并成功，无 conflict。
若 `src/main.py` 冲突（他会话在同一时段改了同区域）⇒ 按 spec §6 分层策略处理：
**手工只保留双方改动**（本批只需 import 1 行 + include_router 1 行 + VERSION 1 行），
冲突解决后必须重跑 Step 1 全量 L0；**禁止用 `-X ours/theirs` 整体取舍**（会吃掉一方全部改动）。
若 `static/hub.js` 冲突 ⇒ 一律**取 master 侧**（它是产物），然后 Step 7 重建。

- [ ] **Step 7: 重建产物 + 提手同步 + 闸门复跑（集成者单写者动作）**

```bash
cd /home/gztxt/agent-hub
bash scripts/build_hubjs.sh
git status --porcelain
bash scripts/run_tests.sh 2>&1 | tail -12
```
Expected: `built static/hub.js (… 行, md5 …)`、`?v= 提手同步 1 处`；`run_tests.sh` 末行 `=== ALL GREEN ===`、`SKIP: 0`。
若 build 后 `git status` 显示 `static/hub.js` 与 `templates/index.html` 有改动 ⇒ 提交：

```bash
git add static/hub.js templates/index.html
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q -m "build(hub.js): 重建产物 + ?v= 提手同步（v0.13.24）"
```

- [ ] **Step 8: 合并后闸门复跑（必须全过）**

```bash
cd /home/gztxt/agent-hub
bash scripts/prepush.sh 2>&1 | grep -E "^(✓|✗|===|---)" | head -30
bash /fs/1000/ftp/技术文档/scripts/orchestration-check.sh 2>&1 | grep -A8 "C4"
```
Expected: `prepush.sh` 全 `✓` 且末段 `=== prepush.sh: ALL GREEN ===`；
C4 `PASS 本会话无违规子代理写记录`；C5/C6/C9 不新增违规。

- [ ] **Step 9: push master**

```bash
cd /home/gztxt/agent-hub && git push origin master 2>&1 | tail -3 && git log --oneline -3
```
Expected: push 成功。失败 ⇒ 如实记录，不重试超过 1 次。

---

### Task 9: 重启门（**须用户逐次授权**，后端两件攒成一次）

**Files:** 无（纯运维动作）

- [ ] **Step 1: 向用户报请授权，并把重启前后判据说清**

报请内容必须包含：① 本次重启的唯一理由＝让 `src/audit.py`、`src/memstats.py`、`main.py` 挂载生效
（`code_stale` 只兑情报，不重启则新端点 404）；② 探针预算＝**2 次**（重启前 1 次 + 重启后 1 次），
三个判据各 1 次、不重试；③ 回滚手段＝`git revert` 合并提交 + 重启（**禁 `reset --hard`**，军规 7）；
④ 影响面＝3102 面板短暂中断；⑤ **无授权就不重启**，本批后端停在"测试层 PASS / 生产层不可判定"。

- [ ] **Step 2: 授权到手后，重启前判据（第 2 次探针）**

```bash
cd /home/gztxt/agent-hub
echo "--- 前置：必须无活跃终端会话（重启会杀掉 pty ⇒ 打断在跑会话是最高优先级禁忌）"
ss -tnp 2>/dev/null | grep -c ":3102" || true
curl -s http://127.0.0.1:3102/health | python3 -c '
import json,sys,datetime
d=json.load(sys.stdin); now=datetime.datetime.now()
print("ts=", now.isoformat(timespec="seconds"))
print("code_stale=", d.get("code_stale"), "| status=", d.get("status"))
t=d.get("terminal") or {}
print("term_sessions=", t.get("sessions"), "| total_sessions=", t.get("total_sessions"))
'
```
Expected: `term_sessions= 0`（**≠0 ⇒ 停手，等空闲窗口再报请，不许强推**）；`code_stale= True`（合并后未重启的正常态）。

- [ ] **Step 3: 重启（systemctl --user，禁 pkill）**

```bash
systemctl --user restart agent-hub.service && sleep 6 && systemctl --user is-active agent-hub.service
```
Expected: `active`。

- [ ] **Step 4: 重启后判据（第 3 次探针，一次取齐全部证据）**

```bash
cd /home/gztxt/agent-hub
echo "--- PID 变了没有（进程确实重启过）"
systemctl --user show agent-hub.service -p MainPID -p ActiveEnterTimestamp
echo "--- 版本与健康"
curl -s http://127.0.0.1:3102/health | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("version=", d.get("version"), "| code_stale=", d.get("code_stale"),
      "| code_matches_head=", d.get("code_matches_head"), "| status=", d.get("status"))
print("ccr_gateway.status=", (d.get("ccr_gateway") or {}).get("status"))
'
echo "--- 新端点真活（audit 未鉴权应 401；kb/status 应带 local_memory）"
curl -s -o /dev/null -w "audit_no_token=%{http_code}\n" http://127.0.0.1:3102/api/audit/list
curl -s http://127.0.0.1:3102/api/kb/status | python3 -c '
import json,sys
lm=json.load(sys.stdin).get("local_memory")
print("local_memory=", lm)
'
echo "--- 日志异常 0"
journalctl --user -u agent-hub.service --since "-3min" --no-pager | grep -icE "traceback|error|exception" || echo 0
```
Expected: `MainPID` 与重启前不同；`version= 0.13.24`；`code_stale= False`；`code_matches_head= True`；
`audit_no_token= 401`（**fail-closed 生效**）；`local_memory` 含 `rows/by_status/state/reason`
且 `state` 应为 `stale`（生产实测 4 行、19 天未更新）；异常计数 `0`。

- [ ] **Step 5: 带凭据验一次导出 + 审计写读闭环（**不回显 token**）**

```bash
cd /home/gztxt/agent-hub
TOK="$(python3 -c '
import sqlite3;c=sqlite3.connect("data/agents.db");c.row_factory=sqlite3.Row
print(c.execute("SELECT value FROM settings WHERE key=\"term-token\"").fetchone()["value"])')"
echo "token 长度=${#TOK}（不回显内容）"
curl -s -H "X-TERM-TOKEN: $TOK" -o /dev/null -w "export=%{http_code}\n" \
  "http://127.0.0.1:3102/api/sessions/export?format=json&limit=5&redact=1"
curl -s -H "X-TERM-TOKEN: $TOK" "http://127.0.0.1:3102/api/audit/list?limit=5" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print("audit count=",d["count"],"types=",d["types"][:3])'
unset TOK
echo "--- 真写一次变更，验证审计闭环（建再删一个探测用 MCP server）"
curl -s -X POST -H "Content-Type: application/json" -H "X-TERM-TOKEN: $TOK" \
  -d '{"name":"probe-audit-tmp","transport":"stdio","command":"/bin/echo","env":{"K":"sk-ABCDEFGHIJKLMNOP1234"}}' \
  http://127.0.0.1:3102/mcp/servers | python3 -c 'import json,sys;print("add=",json.load(sys.stdin))'
```
Expected: `export= 200`；`audit count= >=1`；`add=` 返回 `{"id":…,"status":"added"}`。
**注意**：上面第 3 段用了 `$TOK`，但 `unset TOK` 在它之前 ⇒ 必须把 `unset TOK` **移到本步最后**执行
（执行时按实际顺序调整，不许因为 unset 早了就跳过审计闭环验证）。
随后立即删除探测条目并核对审计与脱敏：

```bash
SID="$(curl -s http://127.0.0.1:3102/api/mcp/servers | python3 -c '
import json,sys
for s in json.load(sys.stdin).get("servers",[]):
    if s.get("name")=="probe-audit-tmp": print(s["id"])')"
TOK="$(python3 -c '
import sqlite3;c=sqlite3.connect("data/agents.db");c.row_factory=sqlite3.Row
print(c.execute("SELECT value FROM settings WHERE key=\"term-token\"").fetchone()["value"])')"
curl -s -X DELETE -H "X-TERM-TOKEN: $TOK" "http://127.0.0.1:3102/mcp/servers/$SID" | head -c 200; echo
curl -s -H "X-TERM-TOKEN: $TOK" "http://127.0.0.1:3102/api/audit/list?asset_slug=$SID" \
  | python3 -c '
import json,sys
rows=json.load(sys.stdin)["audit"]
print("该资产审计行数=",len(rows),"actions=",[r["action"] for r in rows],"actor=",rows[0]["actor"] if rows else None)
blob=json.dumps(rows,ensure_ascii=False)
assert "sk-ABCDEFGHIJKLMNOP1234" not in blob, "凭据泄漏进审计"
print("PASS：审计闭环成立且凭据未泄漏")'
unset TOK
```
Expected: `该资产审计行数= 2 actions= ['delete', 'create']`（最新在前）、`actor= user:term-token`、末行 `PASS`。

- [ ] **Step 6: 端侧结项位（如实标注中间态）**

向用户报请：本机侧已 PASS（含审计写读闭环与脱敏实证），但**导出按钮的端侧终验必须由用户点一次**
（本机对手机端零探针能力，AGENTS.md：端侧确认＝结项）。请用户在面板 chat 会话工具条点导出图标，
回填「确认时间戳 + 端侧现象（是否落盘、文件名、toast 文案）」。未回填前本项状态写 **◐ 待端侧确认**。

---

### Task 10: 台账卫生（4 件，含补丁件打捞器）

**Files:**
- Modify: `/fs/1000/ftp/技术文档/PENDING-TASKS.md`（备份后改）
- Create: `/fs/1000/ftp/技术文档/scripts/salvage-ledger-patch.py`

**为什么不用既有两件脚本**（实测结论，2026-09-25）：
`apply-ledger-patch.py` 是**全有或全无**（任一 anchor BAD ⇒ 整件 0 写入，已实测验证：7 OK + 3 BAD ⇒ `0 已写入`）。
而 `make-ledger-patch.py` **不能重跑** —— 它的 `APPENDS` 里那 3 条（`PT-20260925-06/07/08`）
**已被 `01a0d61a` 会话以别的正文写进台账**（06 的 anchor 现属「MCP 网关工具调用审计」段，
07 属「前端 shard 拆分」段，08 是别的正文），且 `ANCHORS` 里的 `PT-20260925-05`/`PT-20260924-17`/`PT-20260924-14`
三处**逐字节匹配不上**（正文被别的会话改过）。重跑只会把同号条目再写一遍（台账已有 4 组 PT 编号重复的前例）
并仍然 0 写入。⇒ 需要**打捞器**：只取补丁件里"仍然唯一且未应用"的部分。

- [ ] **Step 1: 备份 + 生成打捞件**

```bash
cd /fs/1000/ftp/技术文档
cp PENDING-TASKS.md PENDING-TASKS.md.bak-$(date +%Y%m%d_%H%M%S)-台账卫生
cat > scripts/salvage-ledger-patch.py <<'PYEOF'
#!/usr/bin/env python3
"""台账补丁件**打捞器**（只读 PENDING-TASKS.md，另出一个 salvage 件）。

为什么需要它（2026-09-25 实测）：`apply-ledger-patch.py` 是全有或全无 —— 7 个 anchor OK + 3 个 BAD
⇒ 整件 0 写入（已实证）。而 `make-ledger-patch.py` 的 APPENDS 里 3 条 PT-20260925-06/07/08
**已被别的会话以别的正文占用**，ANCHORS 里 3 处正文也被改过 ⇒ 重跑只会造重复编号并仍然 0 写入。
所以补救不是"再生成一遍"，而是**把仍然唯一且未应用的部分打捞出来**单独落盘。

三种处置（逐件判定，绝不批量 --force）：
  OK    anchor 唯一存在 & 未应用 ⇒ 进 salvage 件（可被 apply 脚本正常吃下）
  BAD   anchor 找不到/不唯一   ⇒ **跳过并登记**（说明正文已被别的会话改写，需人工核对是否已等价落账）
  DONE  anchor 在 & 已应用     ⇒ 跳过（幂等）
APPENDS 一律**丢弃**：追加区标题已被别的会话占用，追加＝造第二份同号条目。
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path("/fs/1000/ftp/技术文档")
SRC = ROOT / "scripts" / "ledger-patch-01a0d5db.json"
OUT = ROOT / "scripts" / "ledger-salvage-01a0d5db.json"


def norm(s):
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", s.strip())


def main():
    patch = json.loads(SRC.read_text(encoding="utf-8"))
    ledger = norm((ROOT / "PENDING-TASKS.md").read_text(encoding="utf-8"))
    keep, bad, done = [], [], []
    for e in patch["entries"]:
        a = norm(e["anchor"])
        n = ledger.count(a)
        if n == 1:
            (done if norm(e["new"]) in ledger else keep).append(e)
        else:
            bad.append({"id": e["id"], "occurrences": n, "head": a[:60]})
    out = {"source_session": patch.get("source_session"), "generated_at": patch.get("generated_at"),
           "salvaged_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
           "note": "从 ledger-patch-01a0d5db.json 打捞：只含 anchor 仍唯一且未应用的条目；"
                   "APPENDS 全丢弃（追加区标题已被别的会话占用）",
           "entries": keep,
           "skipped_bad_anchor": bad, "skipped_already_applied": [e["id"] for e in done]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("salvage 件：%s" % OUT)
    print("  可落盘 %d 条 / anchor 失效 %d 条 / 已应用 %d 条" % (len(keep), len(bad), len(done)))
    for b in bad:
        print("  BAD %s occurrences=%d head=%r" % (b["id"], b["occurrences"], b["head"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
chmod +x scripts/salvage-ledger-patch.py
python3 scripts/salvage-ledger-patch.py
```
Expected: 打印 `可落盘 7 条 / anchor 失效 3 条 / 已应用 0 条`，并逐条列出 3 个 BAD 的 `occurrences`。

- [ ] **Step 2: dry-run 验补丁件（不带 `--force`）**

```bash
cd /fs/1000/ftp/技术文档
python3 scripts/apply-ledger-patch.py --patch scripts/ledger-salvage-01a0d5db.json --dry-run
```
Expected: 7 行 `OK PT-…`，末行 `dry-run 未写入。确认无误后去掉 --dry-run 落盘。`
若出现 `BAD` ⇒ 说明静默窗内又有会话改了台账 ⇒ **停手**，把 BAD 项报给用户，不 `--force`。

- [ ] **Step 3: 静默窗落盘**

先确认无并发写者，再落盘：

```bash
cd /fs/1000/ftp/技术文档
ls -la --time-style=+%H:%M:%S PENDING-TASKS.md; date +%H:%M:%S
echo "（若 mtime 在最近 2 分钟内 ⇒ 有会话正在写，等它停手再落盘）"
python3 scripts/apply-ledger-patch.py --patch scripts/ledger-salvage-01a0d5db.json
git -C /fs/1000/ftp/技术文档 diff --stat PENDING-TASKS.md
```
Expected: `已写入 7 条到 …/PENDING-TASKS.md`；`diff --stat` 显示 `1 file changed, 21 insertions(+)`（7 条 × 3 行）。

- [ ] **Step 4: 4 件台账卫生逐项落地**

件 4.1 —— 改 `PT-20260924-04` 状态（**改判不删除**，旧结论加横幅）：

```bash
cd /fs/1000/ftp/技术文档
grep -n "^## PT-20260924-04" -A 12 PENDING-TASKS.md | head -16
```
按实读到的正文，用**精确锚点编辑**把该条状态改为 `⏸待裁`，并在其正文**首行前**插入横幅：

```markdown
> **【改判 2026-09-25，01a0d6dd】**「agent-hub 未建 git 本地仓」前提不成立：09-23 已 commit 建基线、09-25 09:5x
> 已合并 master 并发版 v0.13.23；且 `src` 已定性为**活跃工作仓**（归档军规合规例外），按军规它本就不该迁进技术文档。
> ⇒ 唯一残值只剩「备份链是否覆盖到 `~/agent-hub`」一项核查，原"迁仓"诉求作废。原结论保留不删。
```

件 4.2 —— 登记 `PT-20260925-09` 编号冲突（用 `grep` 取到两组同号条目的行号后插入登记条）：

```bash
grep -n "^## PT-20260925-09" PENDING-TASKS.md
grep -oE '^## PT-[0-9]{8}-[0-9]{2}' PENDING-TASKS.md | sort | uniq -d
```

在台账「暂缓/待裁区」或文件末尾追加（编号取 `grep -oE '^## PT-[0-9]{8}-[0-9]{2}' | sort | tail -1` 之后的下一号，
若 `-09` 已重复则本条用 **`PT-20260925-11`**，因为 `-10` 已被本批 Task 12 占用）：

```markdown
## PT-20260925-11 ｜ 台账 PT 编号双占用（-09 两组）｜ owner=01a0d6dd ｜ 状态：⏸待裁
- **事实**：`grep -oE '^## PT-[0-9]{8}-[0-9]{2}' PENDING-TASKS.md | sort | uniq -d` 命中 `PT-20260925-09`
  ⇒ 同一编号被两组条目占用（09-24 审计已记过 4 组重复，这是新增第 5 组）。
- **为什么不能由 AI 单方改号**：编号是**跨会话唯一索引**，改号会让引用它的会话/知识文档指向失效条目
  （ls_guard 线失联就是这么来的）。军规 1 要求"新增 PT 前先 grep 取号"，而两个会话同时取号即产生本冲突。
- **待裁**：① 保留双占用并给其中一组加后缀（如 `-09a`）；② 后到者改号（需同步改所有引用处）；
  ③ 引入 `scripts/ledger-next-pt.sh` 作为**唯一发号器**（原子占号，杜绝并发同号）。
- **验收**：`uniq -d` 输出为空；`orchestration-check.sh` C7 不报编号重复。
```

件 4.3 —— 作废 PT 状态横幅（精确锚点编辑，只改本批相关的那一处 `owner=01a0d6dd`）：

```bash
grep -n "PT-20260925" PENDING-TASKS.md | grep "01a0d6dd" | head
```

把本批在 Step 3 落下的 7 条补丁条目里、状态为 `◐ 待重启` 的行改为：

```markdown
- 状态：✅ 已随 2026-09-25 授权重启上线（v0.13.24，`MainPID` 已变、`code_stale=False`、`/api/audit/list` 未鉴权 401 fail-closed 实测）
```

> **顺序要求**：件 4.3 必须在 **Task 9 重启完成之后**执行；重启未获授权则本件保持 `◐ 待重启` 不动。

件 4.4 —— 授权冲突记账（在台账授权/裁定区追加）：

```markdown
## 记账 ｜ 09-25 批次授权 90 秒内口径反转 ｜ owner=01a0d6dd ｜ 状态：✅闭合（记账型）
- **事实**：01:47:00 授权批1~3、批4 暂缓、不需要 Docker；01:48:30 本人改为批4 立即执行、Docker 暂缓留待后续迭代升级。
- **有效授权集合**（以 created_at 最晚者为准）：**四批全执行 + 容器化不在本轮范围**。
- **本批的兑现**：联邦门面收口批（Task 1~12）**零容器依赖** —— 无 Dockerfile/compose/镜像拉取/容器内路径假设，
  `grep -rn "docker\|Dockerfile\|compose" src/audit.py src/memstats.py src/db.py src/writeauth.py static/hub/04-terminal-ws.js` 为空即证据。
- **口径沉淀**：①「暂缓/后续迭代」不是稳定状态，必须带**授权时刻 + 触发/解除条件**；
  ②批次授权 ≠ 手段解禁（禁 git 写主干以外区域/禁重启生产/禁全局替换/改前必备份照旧）；
  ③排除项措辞由「不需要」软化为「暂缓 + 后续迭代升级」⇒ 容器化是**终态方向**而非否决项。
```

- [ ] **Step 5: 台账 git 提交（技术文档仓）**

```bash
cd /fs/1000/ftp/技术文档
git add PENDING-TASKS.md scripts/salvage-ledger-patch.py scripts/ledger-salvage-01a0d5db.json
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "chore(ledger): 打捞 01a0d5db 补丁件 7 条落账 + PT-20260924-04 改判横幅 + PT-20260925-11 编号双占用待裁 + 09-25 授权反转记账"
git log --oneline -1
```

---

### Task 11: git hooks 安装（**最后一步、单独执行**，影响所有会话）

**Files:** `/home/gztxt/agent-hub/.git/hooks/*`（经 `scripts/install-hooks.sh`）

**为什么必须最后且单独**（三条实测依据）：
1. hooks 装在**共用 git dir** 上 ⇒ 一装即对**当天所有活跃会话**（01a0d513/01a0d6df/01a0d61a 等）的
   `git commit`/`git push` 生效。装在批中间＝别人的提交突然被拦，且被拦者不知道为什么。
2. 它是 `PT-20260924-14` C9 闸门的**工程解**（脚本检签名 vs hook 直接拦），先装会让同日其他批次
   的既有提交习惯失效 —— 属跨会话影响面，须在收尾时点做。
3. 脚本自带 `--dry-run` 与 `--status`，且**只加不覆盖**（已存在的同名 hook 会被跳过而非改写）。

- [ ] **Step 1: 装前状态 + 干跑（不改任何东西）**

```bash
cd /home/gztxt/agent-hub
bash scripts/install-hooks.sh --status
bash scripts/install-hooks.sh --dry-run
```
Expected: `--status` 列出 4 只 hook 当前均**未装**（`(未装)`），并打印 `C9 签名基线：…`；
`--dry-run` 逐条打印"将要写入 …/pre-commit"等而**不落盘**（跑完 `--status` 仍显示未装）。

- [ ] **Step 2: 向用户报请（跨会话影响面）**

报请内容：① 装哪 4 只 hook（pre-commit / pre-push / commit-msg / post-merge，以 `--dry-run` 实输出为准）；
② 影响面＝**所有会话**的 commit/push 会被同一套检查拦截；③ 回滚＝`rm .git/hooks/<name>`（脚本只加不覆盖，
删掉即恢复原状，且不触 `.git/index`）；④ 不装则 C9 继续只靠 `orchestration-check.sh` 事后检签名。

- [ ] **Step 3: 授权后安装 + 装后状态**

```bash
cd /home/gztxt/agent-hub
bash scripts/install-hooks.sh
bash scripts/install-hooks.sh --status
ls -la .git/hooks/ | grep -vE '\.sample$' | head
```
Expected: 安装输出逐条 `已写入`；`--status` 4 只均显示已装；`ls` 里出现非 `.sample` 的可执行 hook。

- [ ] **Step 4: 真验 hook 生效（一次无害的空提交干跑）**

```bash
cd /home/gztxt/agent-hub
git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit --allow-empty -q -m "chore(hooks): 验证 install-hooks 生效（空提交）" 2>&1 | tail -5
git log --oneline -1
```
Expected: 提交成功且 hook 输出可见（或静默通过）；**若被 hook 拦住 ⇒ 读拦截原因**，
按原因处置（不许 `--no-verify` 绕过 —— 绕过就等于装了个假闸门），并把拦截原文报给用户。

- [ ] **Step 5: 闸门复跑（hooks 不得让既有闸门变红）**

```bash
cd /home/gztxt/agent-hub && bash scripts/prepush.sh 2>&1 | grep -E "^(✓|✗|===)" | tail -12
```
Expected: 全 `✓`、`=== prepush.sh: ALL GREEN ===`。

---

### Task 12: 沉淀 + PT 登记 + 收尾（C8 义务，不是可选）

**Files:**
- Create: `/fs/1000/ftp/技术文档/agent-knowledge/NN-<标题>.md`（NN 取 `ls agent-knowledge | tail -1` 的下一号）
- Modify: `/fs/1000/ftp/技术文档/PENDING-TASKS.md`（登记 `PT-20260925-10`）

- [ ] **Step 1: 取知识文档编号**

```bash
cd /fs/1000/ftp/技术文档 && ls agent-knowledge | sort -t- -k1 -n | tail -3
```
Expected: 得到当前最大号（已知 44 存在）⇒ 本批用 **45**（若已被占则顺延）。

- [ ] **Step 2: 写沉淀文档** — `agent-knowledge/45-资产变更审计与记忆腐烂观测.md`

必须包含（每条都要有**真实命令输出**贴进去，无输出不得称 PASS）：
1. **三个开口的事实与判据**：34 条写路由有鉴权 0 条有审计（`grep -c` 实测数）；
   `/api/sessions/export` 后端 v0.13.23 已在、前端零按钮（`grep -c` = 0）；
   `memories` 4 行全 active、19 天未更新（只读 sqlite 实测）。
2. **件 3 的改判过程**：TTL/consolidate **代码里根本不存在**（`grep -c ttl = 0`）⇒ 原诉求悬空；
   改判为 B-2「腐烂可观测化」的四条理由（零软删行 / 权威副本在 TDAI / 权重只 0.2 / rebuild 事故前例）。
3. **六条可复用口径**（这是文档的主价值，别写成流水账）：
   ① 审计表 append-only + detail 落库前整体脱敏（先 dumps 再 redact 才覆盖嵌套层）；
   ② 身份只记凭据**名**不记值；`decide()` 签名不动、新增纯函数取身份（零回归地扩展鉴权层）；
   ③ 覆盖面用 AST 扫 handler 体，不用 grep；漏一条即 FAIL；不审的路由必须挂书面理由；
   ④ GET 但按写级鉴权（批量读＝数据外流）+ fail-closed（没配口令 503 不放行）；
   ⑤ **观测模块不许有写库能力**，用静态护栏钉死（去注释后判文本），且文案不许暗示"我会删"；
   ⑥ 前端四态互斥：被拒不许说成"没有会话"（与 `07-asset-panel.js` 同源红向）。
4. **台账补丁件的失效形态与打捞法**：全有或全无 + 追加区被占用 ⇒ 不能重跑生成器，只能打捞
   （`scripts/salvage-ledger-patch.py`）；顺带记 PT 编号并发双占用（第 5 组）。
5. **hooks 为什么必须最后单独装**：共用 git dir ⇒ 跨会话影响面。
6. **未闭合项与判定权归属**（显式列出，否则"已沉淀"会被误读为"已结项"）：
   导出按钮端侧终验＝**用户**（本机对手机端零探针能力）；
   件 4 的 PT 编号冲突裁定＝**用户**；hooks 安装授权＝**用户**；
   `main.py` 写路由的审计覆盖面（本批缓做，spec §4）＝后续批次。

- [ ] **Step 3: 登记 PT 条目**

```bash
cd /fs/1000/ftp/技术文档
grep -oE '^## PT-[0-9]{8}-[0-9]{2}' PENDING-TASKS.md | sort | tail -1
```
取号后追加（预期 `PT-20260925-10`；若已被占则顺延到下一个空号，**并把知识文档里的引用一并改**）：

```markdown
## PT-20260925-10 ｜ 联邦门面收口批（导出按钮 / asset_audit / 记忆 staleness 观测）｜ owner=01a0d6dd ｜ 状态：<按实际>
- **来源**：用户 09-25 令执行"联邦门面收口批"；设计稿 `agent-hub/docs/superpowers/specs/2026-09-25-federated-facade-closeout-design.md`（commit 42673c0），
  计划 `docs/superpowers/plans/2026-09-25-federated-facade-closeout.md`。
- **交付**：v0.13.24（后端 asset_audit + /api/audit/list + memstats；前端 chat 工具条导出按钮）；
  L0 313 → <实跑>，L1 35 → 40，SKIP=0；台账打捞 7 条落账 + 4 件卫生；hooks <已装/待授权>。
- **验收判据**（逐条附真实输出）：`run_tests.sh` ALL GREEN；`prepush.sh` ALL GREEN；
  重启后 `version=0.13.24 / code_stale=False / audit 未鉴权 401 / kb_status.local_memory.state=stale`；
  审计写读闭环 2 行（create+delete）且 `sk-…` 未出现在审计 detail 里。
- **未闭合**：① 导出按钮端侧终验（判定权＝用户）；② PT 编号双占用裁定（PT-20260925-11）；
  ③ hooks 安装（须用户授权，跨会话影响面）；④ main.py 写路由审计覆盖面（后续批次）。
- **证据**：`agent-knowledge/45-资产变更审计与记忆腐烂观测.md`。
```

- [ ] **Step 4: 两个仓当天提交（C8）**

```bash
cd /fs/1000/ftp/技术文档 && git add agent-knowledge/ PENDING-TASKS.md && \
  git -c user.name="pi-01a0d6dd" -c user.email="pi-01a0d6dd@local" commit -q \
  -m "docs(knowledge): 45-资产变更审计与记忆腐烂观测 + PT-20260925-10 登记" && git log --oneline -1
cd /home/gztxt/agent-hub && git status --porcelain && echo "(空=工作树干净，无未提交堆积)"
```
Expected: 技术文档仓提交成功；agent-hub 工作树**干净**（未提交堆积＝一次事故清零，v0.13.16/17 就是这么丢的）。

- [ ] **Step 5: 清理备份件（保留 7 天口径，不删当天证据）**

```bash
cd /home/gztxt/agent-hub && ls -la src/*.bak-* static/hub/*.bak-* templates/*.bak-* CHANGELOG.md.bak-* 2>/dev/null | head -20
```
Expected: 列出本批备份件。**不删**（它们是回滚凭证；`.bak` 已被 `.gitignore` 排除，不进 git 也不进发布包 ——
`test_release_hygiene` 钉着这条）。仅在报告里列出清单与体积。

---

## 执行顺序与依赖（串行，单会话可追踪）

```
Task 1 (db 表+写口径)
  └→ Task 2 (身份解析)          ← Task 4/5 依赖 actor_of
       └→ Task 3 (查询端点+main 挂载)
            └→ Task 4 (mcpgw 审计) ─┐
                 └→ Task 5 (memory 审计) ─┴→ Task 6 (memstats+kb)
                                              └→ Task 7 (前端按钮)
                                                   └→ Task 8 (全量回归+合并+重建)
                                                        └→ Task 9 (重启门 ★须用户授权)
                                                             ├→ Task 10 (台账卫生；件 4.3 依赖 Task 9)
                                                             └→ Task 11 (hooks ★最后单独装、须授权)
                                                                  └→ Task 12 (沉淀+登记)
```

- Task 1→7 全在 worktree 内，**不触生产**。
- Task 8 是唯一的集成动作（合并 master + 重建 `static/hub.js`，C7 单写者）。
- Task 9 是**硬门**：无用户授权 ⇒ 后端两件停在"测试层 PASS / 生产层不可判定"，如实汇报，不自行重启。
- Task 11 必须在 Task 9 之后、且单独执行（跨会话影响面）。
- 探活预算核算：Step「基线复合探针」（已用 1 次）+ Task 9 Step 2（第 2 次）+ Task 9 Step 4（第 3 次）＝ **3 次**，
  三个互不相同的判据，同一判据不重试第 2 次，**禁第 4 次**。

## Self-Review（计划自检记录）

**1. Spec 覆盖度**

| spec 节 | 落在 | 状态 |
|---|---|---|
| §3 件1 导出按钮 | Task 7 | ✅（位置由"历史面板"更正为 **chat 会话工具条**，依据：按钮语义是"导出会话"，`histHtml` 是 agent 卡片历史；`chatPick` 正好给 `agent_id` 过滤） |
| §4 件2 审计表 | Task 1 | ✅ |
| §4 actor 解析 | Task 2 | ✅ |
| §4 查询端点 | Task 3 | ✅ |
| §4 写路由覆盖面（必审 4+6） | Task 4 / 5 | ✅ |
| §4 main.py 路由覆盖面（缓做） | — | ⏸ 按计划缓做，已在 spec 与 Task 12 未闭合项登记 |
| §5 件3（B-2 改判） | Task 6 | ✅（含"绝不写库"静态护栏与文案钉子） |
| §6 件4 四件台账卫生 | Task 10 | ✅（4.1 改判横幅 / 4.2 编号冲突登记 / 4.3 状态作废 / 4.4 授权记账） |
| §7 件5 hooks | Task 11 | ✅（最后单独装 + 授权门 + 不许 `--no-verify` 绕过） |
| §8 执行顺序 | 「执行顺序与依赖」节 | ✅ |
| §9 沉淀 | Task 12 | ✅（六条口径 + 未闭合项与判定权归属） |

**2. Placeholder 扫描**：全文搜 `TBD/TODO/待补/类似上文/适当处理` ⇒ 0 命中。每个改代码的步骤都给了**完整可粘贴的代码**；
每个测试步骤都给了命令 + Expected 输出；每个红对照都给了"改坏什么 + 必须 FAIL + 怎么还原"。
两处显式留了"以实读为准"的分支（Task 4 Step 6 的 `ServerIn` 字段名、Task 10 件 4.1/4.2 的行号与编号），
都附带了**取值命令**与**不许改断言**的约束 —— 这不是占位符，是防止照着过期文本硬写。

**3. 类型一致性**（跨任务名字逐个核对）
- `db.log_asset_event(asset_type, asset_slug, action, actor, detail=None)` —— Task 1 定义，Task 4/5 调用签名一致（位置参数 4 个 + detail 关键字）。
- `db.AUDIT_ACTIONS` 六值元组 —— Task 1 定义并被 `test_actions_enum_is_frozen` 钉死，Task 4/5 用的 action 字面量
  （`create/delete/bind/unbind/update/rebuild`）**全部在枚举内**，逐条核对无 `frobnicate` 之外的越界值。
- `writeauth.actor_of(request) -> str` —— Task 2 定义，Task 4/5 一致调用；`SECRET_NAMES` 顺序与 `secrets_from_env` 一致（有闸门）。
- `audit.VALID_TYPES` 七类 —— Task 3 定义；与 Task 1 SCHEMA 注释里列的 `asset_type` 取值集合**逐字一致**
  （`mcp_server|mcp_acl|memory_l1|memory_doc|agent|session|setting`），且 Task 4/5 实际只用了前 4 类 ⇒ 后 3 类是为
  `main.py` 缓做覆盖面预留（spec §4 已声明缓做，不算悬空接口）。
- `memstats.STALE_DAYS/age_days/local_stats/verdict/collect` —— Task 6 定义与测试引用一致；`kb_status()` 新键名 `local_memory`
  在 Task 6 Step 6 与 Task 9 Step 4 的验证命令里**同名**。
- 前端 `exportStateOf(status,count,detail)` / `exportStateText(state,count,extra)` / `chatSessExport()` ——
  Task 7 Step 5 的定义与 Step 2 测试里 `extract_function` 抽取的名字、node 侧调用的参数个数**逐个一致**
  （`extra` 用 `{status, hits}`，测试断言 `hits`/`status` 两个键）。
- `route_audit_map` / `MUST_AUDIT` / `NOT_AUDITED` —— Task 4 定义，Task 5 的 `test_memory_change_routes_all_audited` 复用同一函数与同一表结构。

**4. 已知风险与预案**（执行时若命中，按预案走，不自行发明）
- `src/main.py` 是当天 4 个会话写过的最热点 ⇒ Task 3 只改 3 处、Task 8 Step 5/6 有冲突分层策略（**禁 `-X ours/theirs`**）。
- `templates/index.html` 被 3 个会话写过 ⇒ Task 7 只改 1 行，且 `?v=` 提手由 `build_hubjs.sh` 自动同步（不手改）。
- `static/hub.js` 是 build 产物、C7 单写者 ⇒ worktree 内重建只为跑闸门，**master 侧重建才是权威**（Task 8 Step 7）；
  合并时若它冲突，一律取 master 侧再重建。
- 版本号 `0.13.24` 可能被并发批次占用 ⇒ 全局约束 12 已给让位规则（取下一个空号 + CHANGELOG 写明）。
- 重启会杀 pty ⇒ Task 9 Step 2 的 `term_sessions == 0` 是**前置硬条件**，≠0 就停手等窗口。
