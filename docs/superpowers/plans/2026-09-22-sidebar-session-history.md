# 左侧菜单 Agent 历史会话下拉 实施计划（v0.13.0）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 agent-hub 左侧菜单每个 agent 行下方展开「历史会话」下拉（标题＝用户问题原文，中文），点条目即在该 agent 的终端里 `--resume` 续聊；同时消灭顶栏芯片的十六进制字母编号。

**Architecture:** 新增只读适配层 `src/sessions_store.py`（把 6 个 agent 各不相同的磁盘会话仓库归一成统一 Item；sqlite 一律 `mode=ro`）→ `src/term.py` 加 `GET /api/term/history/{agent_id}` 与 `POST /api/term/sessions{agent_id,session_id}`（argv 全部由后端模板拼装，客户端传不进命令）→ `static/hub.js` + `templates/index.html` 加 `.nav-hist` 展开区与芯片中文标题。

**Tech Stack:** Python 3.11 / FastAPI / stdlib `sqlite3`(uri=ro) / **stdlib `unittest`**（venv 实测无 pytest、无 httpx ⇒ 单测走 unittest，HTTP 验证走真起 uvicorn + `urllib` + `websockets`）。

**Spec:** `docs/superpowers/specs/2026-09-22-sidebar-session-history-design.md`

## Global Constraints（每个 Task 都隐含遵守）

- **开工门（D8 / 用户选①）**：`git status --porcelain` 除 `?? docs/` 外必须为空，且用户已点名 pi 为唯一执行者。不为空 → 只取证不动手，报用户。
- **改前必备份**：`cp <f> <f>.bak-$(date +%Y%m%d_%H%M%S)-<≤10字英文>`（新建文件免备份）；无备份禁止写入。
- **禁 `sed -i`、禁全局替换**；改动一律定点替换。
- **禁动生产**：`agent-hub.service`（:3102，`HOST=0.0.0.0`）不得 stop/重启；验证一律 **临时端口 3199 + 只绑 127.0.0.1**。禁 `pkill`/`killall`，只 kill 自己记下的 PID。
- **禁装包**（不加 pytest/httpx）、禁改 `requirements.txt`；`~/.grok ~/.claude ~/.jcode ~/.hermes ~/.codex ~/.qoder` 一律**只读**。
- **凭据脱敏**：token 一律 `TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-)` 现取，值不得进文档/日志/报告。
- **提交拆分**：后端一笔、前端一笔，各自可独立 revert；**子代理一律不执行 `git add`/`git commit`**（防 index 竞争），提交全部由主会话做。
- **汇报口径**：每条结论附真实命令输出 + `PASS`/`FAIL`；无输出不得称 PASS。

## 文件结构（谁负责什么，谁能碰）

| 文件 | 责任 | 动它的 Task |
|---|---|---|
| `src/sessions_store.py` | **新建** 会话仓库适配层（唯一懂各 agent 磁盘格式处） | T1 |
| `tests/test_sessions_store.py` | **新建** 适配层单测（stdlib unittest，真磁盘只读） | T1 |
| `src/term.py` | 改：history 端点 + resume 白名单 + 活会话中文标题 | T2 |
| `tests/verify_term_history.py` | **新建** 端到端验证脚本（真起服务 + WS 回放断言） | T2 写 / T4 跑 |
| `static/hub.js` | 改：`histOpen`/`HIST`/`histHtml`/`termResume`/`termChipHtml` | T3 |
| `templates/index.html` | 改：`.nav-hist` 样式（宽度走 `--sb-w`，不写死 px） | T3 |
| `src/main.py`、`README.md` | 改：VERSION bump + 一行功能说明 | T5 |

**钉死接口（T2/T3 只认这份，不许改名）**

```python
sessions_store.supports(agent_id: str) -> bool
sessions_store.list_history(agent_id: str, cwd: str, limit: int = 3) -> dict
    # {"items":[Item...], "cwd": str, "note": str}   note＝中文说明，可为空串
sessions_store.known_ids(agent_id: str, cwd: str) -> set[str]
sessions_store.resume_argv(agent_id: str, session_id: str, cwd: str) -> list[str]
    # 校验不过抛 ValueError；通过则返回 argv（cmd[0] 为裸命令名，term.py 再 which 解析）
sessions_store.live_titles(agent_id: str) -> dict[int, str]   # pty 子进程 pid -> 中文标题（可空 dict）
sessions_store.mask_title(text: str) -> str

Item = {"agent": str, "id": str, "title": str, "ts": int, "msgs": int|None, "cwd": str}
```

```
GET  /api/term/history/{agent_id}?limit=3   header x-term-token  → {"agent","cwd","note","items":[Item]}
POST /api/term/sessions  body {"agent_id":str,"session_id":str?} → {"session": SessionDict}
SessionDict 新增 "title": str（取不到为空串，前端出兜底文案）
```

## 并行编排

```
主会话 pi ── T0 基线门 + 备份 ─┬─ 子代理 A：T1 sessions_store.py + 单测
     （3 个并发，≤4）           ├─ 子代理 B：T2 term.py + 验证脚本
                                └─ 子代理 C：T3 hub.js + index.html
                ↓ 三方回收，主会话逐行 diff 复验
主会话 pi ── T4 端到端验收（起 3199 → 跑脚本 → 逐 agent 续聊 → 窄屏）── T5 版本/README/两笔提交/切换命令
```
- **文件互不重叠**是并行前提：A 只碰 `src/sessions_store.py` `tests/test_sessions_store.py`；B 只碰 `src/term.py` `tests/verify_term_history.py`；C 只碰 `static/hub.js` `templates/index.html`。越界即 FAIL 退回。
- B/C 只依赖上面钉死的契约，不依赖 A/C 的完成时序。
- 主会话对每份子代理产物 `git diff` 逐行审读后才提交（trust but verify）。

---

## Task 0：基线门与备份（主会话，串行）

- [ ] **Step 1: 确认工作树干净**

```bash
cd ~/agent-hub && date '+NOW %T' && git status --porcelain && git log --oneline -3 && \
ls -la --time-style='+%T' src/main.py static/hub.js templates/index.html src/term.py | awk '{print $6,$7}'
```
Expected：只有 `?? docs/`。**若还有 `M` 行** ⇒ 另一个会话在改：立即停止，把输出原样报用户请裁（禁自行 commit 他人未验收改动）。

- [ ] **Step 2: 逐个时间戳备份**

```bash
cd ~/agent-hub && TS=$(date +%Y%m%d_%H%M%S) && for f in src/term.py static/hub.js templates/index.html src/main.py; do cp "$f" "$f.bak-$TS-v0130-history"; done; ls -l src/*.bak-$TS-* static/*.bak-$TS-* templates/*.bak-$TS-*
```
Expected：4 个 `.bak-<TS>-v0130-history`，大小非零。

- [ ] **Step 3: 抄下当期 CSS 现值与端口占用（T3/T4 要用）**

```bash
cd ~/agent-hub && grep -n -- "--sb-w:" templates/index.html; grep -c "nav-st-w" templates/index.html; systemctl --user is-active agent-hub.service; ss -ltnp 2>/dev/null | grep -E ':3102|:3199'
```
Expected：`--sb-w: <现值>` 记下来；`nav-st-w` 计数为 `0`（v0.12.3 已删）；3102 `active`；3199 无输出（空闲）。

---

## Task 1：会话仓库适配层（子代理 A）

**Files:** Create `src/sessions_store.py`、`tests/test_sessions_store.py`

- [ ] **Step 1: 写失败的单测**

`tests/test_sessions_store.py`：

```python
"""sessions_store 单测：真磁盘只读断言（无网络、无进程、无 mock）。
   跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_sessions_store -v"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import sessions_store as ss      # noqa: E402

CWD = "/fs/1000/ftp/技术文档"


class TestMask(unittest.TestCase):
    def test_kv_secret_masked(self):
        out = ss.mask_title("入口 ?ccr_web_token=ccr-web-fixed-token-gztxt-2026 打不开")
        self.assertIn("<masked>", out)
        self.assertNotIn("ccr-web-fixed-token-gztxt-2026", out)

    def test_uuid_title_not_mangled(self):
        t = "排查 session 01a0c91d-eb84-7130-9767-479821ef336c 无响应"
        self.assertNotIn("<masked>", ss.mask_title(t))      # 含 '-' 的 UUID 刻意不糊（避免误伤）

    def test_plain_chinese_untouched(self):
        t = "agent hub 宽屏 左侧菜单 1.不要有滚动条"
        self.assertEqual(ss.mask_title(t), t)

    def test_title_length_capped(self):
        self.assertLessEqual(len(ss.mask_title("长" * 400)), 120)


class TestTable(unittest.TestCase):
    def test_only_six_agents(self):
        self.assertEqual(set(ss.SESSION_STORES), {"grok", "claude", "qoder", "jcode", "hermes", "codex"})

    def test_supports_negative(self):
        for a in ("pi", "shell", "qwenpaw", "ccr"):
            self.assertFalse(ss.supports(a))

    def test_id_regexes(self):
        st = ss.SESSION_STORES
        self.assertTrue(st["jcode"]["id_re"].match("session_seedling_1790072132294_70768ccd3fd9f542"))
        self.assertFalse(st["jcode"]["id_re"].match("sheep"))            # 动物名不唯一，禁用
        self.assertTrue(st["hermes"]["id_re"].match("20260921_221812_fa76f1"))
        self.assertTrue(st["grok"]["id_re"].match("01a0c91d-eb84-7130-9767-479821ef336c"))
        self.assertFalse(st["grok"]["id_re"].match("01a0c91d-eb84-7130-9767-479821ef336c; rm -rf /"))


class TestResumeArgv(unittest.TestCase):
    def test_good_ids(self):
        self.assertEqual(ss.resume_argv("grok", "01a0c91d-eb84-7130-9767-479821ef336c", CWD),
                         ["grok", "--resume", "01a0c91d-eb84-7130-9767-479821ef336c"])
        self.assertEqual(ss.resume_argv("qoder", "7491a32f-ccfb-4602-bd84-22c521fd45ee", CWD),
                         ["qodercli", "-w", CWD, "-r", "7491a32f-ccfb-4602-bd84-22c521fd45ee"])
        self.assertEqual(ss.resume_argv("codex", "01a0bffd-7df4-7692-953d-210230d73610", CWD),
                         ["codex", "resume", "01a0bffd-7df4-7692-953d-210230d73610"])

    def test_injection_rejected(self):
        for bad in ["x; rm -rf /", "--resume=evil", "$(id)", "../etc/passwd", "a" * 300, ""]:
            with self.assertRaises(ValueError):
                ss.resume_argv("grok", bad, CWD)

    def test_unsupported_agent(self):
        with self.assertRaises(ValueError):
            ss.resume_argv("pi", "01a0c91d-eb84-7130-9767-479821ef336c", CWD)


class TestRealStores(unittest.TestCase):
    """把 spec §2 取证矩阵的数字当断言：磁盘形态变了就 FAIL，这正是我们要的信号。"""

    def test_grok_history_has_cn_title(self):
        d = ss.list_history("grok", CWD, 3)
        self.assertTrue(d["items"], "grok 在 技术文档 实测 88 条，不应为空")
        it = d["items"][0]
        self.assertLessEqual(len(d["items"]), 3)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", it["title"]), f"标题应含中文：{it}")
        self.assertNotRegex(it["title"], r"\A[0-9a-f]{4}", "标题不得是字母编号")
        self.assertIsInstance(it["ts"], int)
        self.assertTrue(it["id"])

    def test_claude_jcode_hermes_nonempty(self):
        for agent, cwd in (("claude", CWD), ("jcode", CWD), ("hermes", "/home/gztxt")):
            with self.subTest(agent=agent):
                self.assertTrue(ss.list_history(agent, cwd, 3)["items"])

    def test_sorted_desc_and_capped(self):
        items = ss.list_history("grok", CWD, 3)["items"]
        ts = [i["ts"] for i in items]
        self.assertEqual(ts, sorted(ts, reverse=True))

    def test_codex_cli_only(self):
        d = ss.list_history("codex", CWD, 3)
        self.assertLessEqual(len(d["items"]), 3)            # 实测 1 条；0 也合法但须给 note
        if not d["items"]:
            self.assertTrue(d["note"])

    def test_hermes_note_declares_scope(self):
        d = ss.list_history("hermes", "/home/gztxt", 3)
        self.assertTrue(d["note"], "hermes 不按 cwd 过滤，note 必须写明口径")

    def test_qoder_empty_state_has_note(self):
        d = ss.list_history("qoder", CWD, 3)
        self.assertEqual(d["items"], [], "qoder 该目录实测 0 条可续")
        self.assertTrue(d["note"], "空态必须给中文说明，不得留白")

    def test_missing_dir_degrades_not_raises(self):
        d = ss.list_history("grok", "/no/such/dir", 3)
        self.assertEqual(d["items"], [])
        self.assertTrue(d["note"])

    def test_unknown_agent_degrades(self):
        self.assertEqual(ss.list_history("pi", CWD, 3)["items"], [])

    def test_sqlite_readonly_mtime_unchanged(self):
        for agent, p in (("hermes", Path.home() / ".hermes/state.db"),
                         ("codex", Path.home() / ".codex/state_5.sqlite")):
            if not p.exists():
                self.skipTest(f"{p} 不存在")
            before = p.stat().st_mtime_ns
            ss.list_history(agent, CWD, 3)
            self.assertEqual(before, p.stat().st_mtime_ns)

    def test_known_ids_covers_history(self):
        ids = ss.known_ids("grok", CWD)
        for it in ss.list_history("grok", CWD, 3)["items"]:
            self.assertIn(it["id"], ids)

    def test_cache_returns_same_object(self):
        self.assertIs(ss.list_history("grok", CWD, 3), ss.list_history("grok", CWD, 3))


class TestLiveTitles(unittest.TestCase):
    def test_shape(self):
        for agent in ("grok", "claude", "hermes", "jcode", "codex", "qoder", "pi"):
            with self.subTest(agent=agent):
                m = ss.live_titles(agent)
                self.assertIsInstance(m, dict)
                for k, v in m.items():
                    self.assertIsInstance(k, int)
                    self.assertIsInstance(v, str)
```

- [ ] **Step 2: 跑到 FAIL（模块不存在）**

```bash
cd ~/agent-hub && venv/bin/python -m unittest tests.test_sessions_store 2>&1 | tail -4
```
Expected：`ModuleNotFoundError: No module named 'src.sessions_store'`。

- [ ] **Step 3: 写实现**

`src/sessions_store.py`：

```python
"""会话仓库适配层（v0.13.0）：只读把各 agent 自己在磁盘上的历史会话归一成 Item。

安全模型（依据 spec §2 的实测事实）：
- 只读：文件 open(..., errors='ignore')；sqlite 一律 "file:<p>?mode=ro" + uri=True。不写、不删、不 chmod。
- 标题＝用户问题原文（中文），过 mask_title() 遮蔽口令后外发；字母 id 一律不进标题。
- resume argv 由本模块模板拼装：客户端只能提供 session_id，必须过 id_re + 实盘存在双校验。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

HOME = Path.home()
CACHE_TTL_S = 15          # 菜单 30s 轮询，15s 缓存足够去重
SCAN_CANDIDATES = 60      # 每 agent 最多扫多少候选（mtime 倒序）：防 grok 88 / jcode 117 扫穿
SCAN_CANDIDATES_JSON = 20  # jcode 整份 JSON 较大，候选数另设小上限
HEAD_CHARS = 65536        # 大 jsonl 只读头部这么多字符找标题
HARD_BUDGET_S = 1.5       # 单次 list_history 硬预算，超时返回已读到的 + note

UUID_RE = re.compile(r"\A[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.I)
JCODE_RE = re.compile(r"\Asession_[a-z]+_\d{13}_[0-9a-f]{6,16}\Z")
HERMES_RE = re.compile(r"\A\d{8}_\d{6}_[0-9a-f]{6}\Z")

# ── 口令遮蔽 ────────────────────────────────────────────────────────────────
# ① KV 形：允许 key 带前缀（实测本机 CCR 入口是 `?ccr_web_token=<值>`，\b 在 `_` 处不成立，
#    所以不能只写 \btoken\b）；② 28+ 位 base64/hex 裸串且含数字。
# 刻意**不**糊含 '-'/'_' 的长串：那会把 UUID、时间戳备份名一并糊掉（单测 test_uuid_title_not_mangled 锁死）。
_KV = re.compile(r"(?i)([A-Za-z0-9_\-]*(?:token|api[_-]?key|apikey|secret|passwd|password|authorization|bearer))"
                 r"\s*[=:]\s*[^\s,;'\"）)]+")
_BLOB = re.compile(r"\b(?=[A-Za-z0-9+/=]{28,}\b)[A-Za-z0-9+/=]*[0-9][A-Za-z0-9+/=]*\b")


def mask_title(text: Optional[str]) -> str:
    t = _KV.sub(lambda m: m.group(1) + "=<masked>", text or "")
    t = _BLOB.sub("<masked>", t)
    return re.sub(r"\s+", " ", t).strip()[:120]


def _dash_slug(cwd: str) -> str:
    """claude / qoder 目录名：逐字符把非 [A-Za-z0-9] 换成 '-'
       （实测 /fs/1000/ftp/技术文档 → -fs-1000-ftp-----；只正向生成，不逆向反解——反解有歧义）"""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _iso(ts: Optional[str]) -> int:
    """ISO8601（grok/jcode 混用 秒/微秒/纳秒 + Z）→ epoch 秒；解析失败返回 0。"""
    if not ts:
        return 0
    s = str(ts).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)          # 纳秒截到微秒：fromisoformat 不吞 9 位小数
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _head_lines(p: Path, n: int = 400) -> List[dict]:
    """只读文件头 HEAD_CHARS，返回解析成功的 JSON 行（大 jsonl 不整读）"""
    try:
        with open(p, "r", errors="ignore") as f:
            blob = f.read(HEAD_CHARS)
    except Exception:  # noqa: BLE001
        return []
    out: List[dict] = []
    for line in blob.splitlines()[:n]:
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def _first_user_text(objs: List[dict], *, jcode: bool = False) -> str:
    """取「用户第一条问题原文」。claude/qoder：content 以 '<' 开头的是命令回显/meta，跳过；
       jcode：`display_role == 'system'` 是注入（实测不过滤就会命中 system prompt）。"""
    for d in objs:
        if jcode:
            if d.get("role") == "user" and d.get("display_role") != "system":
                c = d.get("content")
                txt = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                if txt.strip():
                    return txt.strip()
            continue
        if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta"):
            continue
        c = (d.get("message") or {}).get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
            return c.strip()
    return ""


# ── 各仓库适配器：统一返回 (items, note)。note 为中文，供前端空态/降级直显 ──────
def _t_grok(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    root = HOME / ".grok" / "sessions" / quote(cwd, safe="")
    if not root.is_dir():
        return [], f"grok 在该目录无会话仓库（{root.name}）"
    dirs = [p for p in root.iterdir() if (p / "summary.json").exists()]
    dirs.sort(key=lambda p: (p / "summary.json").stat().st_mtime, reverse=True)
    items: List[dict] = []
    for p in dirs[:SCAN_CANDIDATES]:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        try:
            d = json.load(open(p / "summary.json", errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
        info = d.get("info") or {}
        title = (d.get("session_summary") or "").strip() or \
            _first_user_text(_head_lines(p / "chat_history.jsonl", 20))
        items.append({"agent": "grok", "id": info.get("id") or p.name,
                      "title": mask_title(title) or "未命名会话", "ts": _iso(d.get("updated_at")),
                      "msgs": d.get("num_messages"), "cwd": info.get("cwd") or cwd})
        if len(items) >= limit:
            break
    return items, ""


def _t_jsonl_dir(kind: str, root_dir: Path, cwd: str, limit: int, t0: float, qoder: bool = False) -> Tuple[List[dict], str]:
    if not root_dir.is_dir():
        return [], f"{kind} 在该目录无会话仓库"
    files = sorted(root_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        skeletons = sum(1 for p in root_dir.iterdir() if p.is_dir())
        return [], (f"{kind} 该目录 0 条可续会话（仅 {skeletons} 个无正文骨架）" if skeletons
                    else f"{kind} 该目录暂无历史")
    items: List[dict] = []
    for p in files[:SCAN_CANDIDATES]:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        objs = _head_lines(p)
        title = ""
        if qoder:                       # qoder 实测有 last-prompt 行（取的就是用户自己那句）
            title = next((d.get("lastPrompt", "") for d in objs if d.get("type") == "last-prompt"), "")
        title = title or _first_user_text(objs)
        items.append({"agent": kind, "id": p.stem, "title": mask_title(title) or "未命名会话",
                      "ts": int(p.stat().st_mtime), "msgs": None, "cwd": cwd})
        if len(items) >= limit:
            break
    return items, ""


def _t_claude(cwd: str, limit: int, t0: float):
    return _t_jsonl_dir("claude", HOME / ".claude" / "projects" / _dash_slug(cwd), cwd, limit, t0)


def _t_qoder(cwd: str, limit: int, t0: float):
    return _t_jsonl_dir("qoder", HOME / ".qoder" / "projects" / _dash_slug(cwd), cwd, limit, t0, qoder=True)


def _t_jcode(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """仓库以 ~/.jcode/sessions/*.json 为准（sqlite 的 recent_sessions 会被 prune，只作辅助）。
       .bak 文件名以 .json.bak 结尾，glob('session_*.json') 天然不会命中。"""
    root = HOME / ".jcode" / "sessions"
    if not root.is_dir():
        return [], "jcode 无会话仓库"
    files = sorted(root.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], "jcode 无会话仓库"
    items: List[dict] = []
    for p in files[:SCAN_CANDIDATES_JSON]:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        try:
            d = json.load(open(p, errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
        if d.get("working_dir") != cwd:
            continue
        msgs = d.get("messages") or []
        title = (d.get("title") or "").strip() or _first_user_text(msgs, jcode=True)
        items.append({"agent": "jcode", "id": d.get("id") or p.stem, "title": mask_title(title) or "未命名会话",
                      "ts": _iso(d.get("last_active_at") or d.get("updated_at")),
                      "msgs": len(msgs) or None, "cwd": cwd})
        if len(items) >= limit:
            break
    return items, ""


def _ro(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _t_hermes(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """权威仓库是 state.db（~/.hermes/sessions/ 已于 08-05 停写）。152/209 条 cwd 为 NULL ⇒
       不按 cwd 过滤、按 source='cli' 全量（D7），并在 note 里写明口径。"""
    db = HOME / ".hermes" / "state.db"
    if not db.exists():
        return [], "hermes 无 state.db"
    sql = ("select s.id as id, s.title as title, s.last_activity_at as ts, "
           "(select m.content from messages m where m.session_id=s.id and m.role='user' and m.active=1 "
           " order by m.id limit 1) as first_u "
           "from sessions s where s.source='cli' order by s.last_activity_at desc limit ?")
    try:
        with _ro(db) as c:
            rows = c.execute(sql, (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [], f"hermes 读取失败：{type(e).__name__}"
    items = [{"agent": "hermes", "id": r["id"], "title": mask_title(r["title"] or r["first_u"] or "") or "未命名会话",
              "ts": int(r["ts"] or 0), "msgs": None, "cwd": cwd} for r in rows]
    return items, "口径：hermes 按 source=cli 全量（历史多数条目未记 cwd）"


def _t_codex(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """只列 source='cli'（D5，否则把 codex exec 探针当历史）。实测 updated_at/created_at 为 epoch 秒；
       `has_user_event` 实测在唯一真会话上为 0 ⇒ 不可当过滤条件。"""
    db = HOME / ".codex" / "state_5.sqlite"
    if not db.exists():
        return [], "codex 无 state_5.sqlite"
    try:
        with _ro(db) as c:
            rows = c.execute("select id, title, cwd, updated_at from threads "
                             "where source='cli' and cwd=? and archived=0 order by updated_at desc limit ?",
                             (cwd, limit)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [], f"codex 读取失败：{type(e).__name__}"
    items = [{"agent": "codex", "id": r["id"], "title": mask_title(r["title"] or "") or "未命名会话",
              "ts": int(r["updated_at"] or 0), "msgs": None, "cwd": r["cwd"]} for r in rows]
    return items, ("" if items else "codex 该目录无交互式历史（exec 探针不计）")


SESSION_STORES: Dict[str, dict] = {
    "grok":   {"kind": "grok_dir",      "id_re": UUID_RE,    "resume": ["grok", "--resume", "{id}"],            "fn": _t_grok},
    "claude": {"kind": "claude_dir",    "id_re": UUID_RE,    "resume": ["claude", "--resume", "{id}"],          "fn": _t_claude},
    "qoder":  {"kind": "qoder_dir",     "id_re": UUID_RE,    "resume": ["qodercli", "-w", "{cwd}", "-r", "{id}"], "fn": _t_qoder},
    "jcode":  {"kind": "jcode_json",    "id_re": JCODE_RE,   "resume": ["jcode", "--resume", "{id}"],           "fn": _t_jcode},
    "hermes": {"kind": "hermes_sqlite", "id_re": HERMES_RE,  "resume": ["hermes", "--resume", "{id}"],          "fn": _t_hermes},
    "codex":  {"kind": "codex_sqlite",  "id_re": UUID_RE,    "resume": ["codex", "resume", "{id}"],             "fn": _t_codex},
}

_CACHE: Dict[tuple, Tuple[float, dict]] = {}


def supports(agent_id: str) -> bool:
    return agent_id in SESSION_STORES


def list_history(agent_id: str, cwd: str, limit: int = 3) -> dict:
    st = SESSION_STORES.get(agent_id)
    if not st:
        return {"items": [], "cwd": cwd, "note": f"{agent_id} 无历史会话仓库"}
    limit = max(1, min(int(limit or 3), 20))
    key = (agent_id, cwd, limit)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        items, note = st["fn"](cwd, limit, time.time())
    except Exception as e:  # noqa: BLE001
        items, note = [], f"历史读取失败：{type(e).__name__}"
    items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    out = {"items": items[:limit], "cwd": cwd, "note": note}
    _CACHE[key] = (time.time(), out)
    return out


def known_ids(agent_id: str, cwd: str) -> set:
    return {i["id"] for i in list_history(agent_id, cwd, 20)["items"]}


def resume_argv(agent_id: str, session_id: str, cwd: str) -> List[str]:
    st = SESSION_STORES.get(agent_id)
    if not st:
        raise ValueError(f"{agent_id} 不支持历史续聊")
    sid = str(session_id or "")
    if len(sid) > 128 or not st["id_re"].match(sid):
        raise ValueError("session_id 形状非法")
    if sid not in known_ids(agent_id, cwd):
        raise ValueError("session_id 不在实盘清单内")
    argv = [t.replace("{id}", sid).replace("{cwd}", cwd) for t in st["resume"]]
    for t in argv:                                  # 兜底栅栏：id 已过 ^…\Z，这里护住 cwd
        if any(c in t for c in "\x00\n;|&$`"):
            raise ValueError("拼装结果含可疑字符")
    return argv


# ── 活会话 pid → 中文标题（顶栏芯片去字母用；各 agent 登记表结构均实测）────────
def live_titles(agent_id: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    try:
        if agent_id == "grok":            # [{"session_id","pid","cwd","opened_at"}]
            for e in json.load(open(HOME / ".grok" / "active_sessions.json", errors="ignore")):
                t = _title_of_session("grok", e.get("session_id"))
                if e.get("pid") and t:
                    out[int(e["pid"])] = t
        elif agent_id == "claude":        # ~/.claude/sessions/<pid>.json
            for p in (HOME / ".claude" / "sessions").glob("[0-9]*.json"):
                try:
                    d = json.load(open(p, errors="ignore"))
                except Exception:  # noqa: BLE001
                    continue
                t = _title_of_session("claude", d.get("sessionId"), d.get("cwd"))
                if d.get("pid") and t:
                    out[int(d["pid"])] = t
        elif agent_id == "hermes":        # {"entries":[{"pid","session_id",...}]}
            db = HOME / ".hermes" / "state.db"
            try:
                entries = json.load(open(HOME / ".hermes" / "runtime" / "active_sessions.json",
                                         errors="ignore")).get("entries", [])
            except Exception:  # noqa: BLE001
                entries = []
            if db.exists():
                for e in entries:
                    if not (e.get("pid") and e.get("session_id")):
                        continue
                    try:
                        with _ro(db) as c:
                            r = c.execute("select title from sessions where id=?", (e["session_id"],)).fetchone()
                    except Exception:  # noqa: BLE001
                        continue
                    if r and r["title"]:
                        out[int(e["pid"])] = mask_title(r["title"])
        elif agent_id == "jcode":         # session json 的 last_pid（实测字段存在）
            for p in (HOME / ".jcode" / "sessions").glob("session_*.json"):
                try:
                    d = json.load(open(p, errors="ignore"))
                except Exception:  # noqa: BLE001
                    continue
                t = (d.get("title") or "").strip() or _first_user_text(d.get("messages") or [], jcode=True)
                if d.get("last_pid") and t:
                    out[int(d["last_pid"])] = mask_title(t)
    except Exception:  # noqa: BLE001
        return out
    return out


def _title_of_session(agent: str, sid: Optional[str], cwd: Optional[str] = None) -> str:
    if not sid:
        return ""
    if agent == "grok":
        hit = next(iter((HOME / ".grok" / "sessions").glob(f"*/{sid}/summary.json")), None)
        if not hit:
            return ""
        try:
            d = json.load(open(hit, errors="ignore"))
        except Exception:  # noqa: BLE001
            return ""
        return mask_title((d.get("session_summary") or "").strip() or
                          _first_user_text(_head_lines(hit.parent / "chat_history.jsonl", 20)))
    if agent == "claude":
        roots = [HOME / ".claude" / "projects" / _dash_slug(cwd)] if cwd else \
            [x for x in (HOME / ".claude" / "projects").iterdir() if x.is_dir()]
        for r in roots:
            f = r / f"{sid}.jsonl"
            if f.exists():
                return mask_title(_first_user_text(_head_lines(f, 60)))
    return ""
```

- [ ] **Step 4: 跑到全绿**

```bash
cd ~/agent-hub && venv/bin/python -m unittest tests.test_sessions_store -v 2>&1 | tail -20
```
Expected：`Ran 22 tests ... OK`。若 hermes/codex 用例 FAIL，**先核列名再改 SQL，不许猜**：

```bash
python3 -c "import sqlite3;c=sqlite3.connect('file:/home/gztxt/.hermes/state.db?mode=ro',uri=True);print('sessions:',[d[1] for d in c.execute('pragma table_info(sessions)') if d[1] in ('id','title','source','cwd','last_activity_at')]);print('messages:',[d[1] for d in c.execute('pragma table_info(messages)')])"
```
Expected：sessions 侧出现 `id,title,source,cwd,last_activity_at`；messages 侧出现 `session_id,role,content,active,id`。以这条输出为准修 SQL。

- [ ] **Step 5: 只读自证（没碰任何 agent 仓库）**

```bash
cd ~/agent-hub && for d in ~/.grok/sessions ~/.claude/projects ~/.jcode/sessions ~/.qoder/projects ~/.hermes/state.db ~/.codex/state_5.sqlite; do echo "$(basename $d) 近10分钟被改条目数=$(find -L "$d" -newermt '-10 minutes' 2>/dev/null | wc -l)"; done
```
Expected：`grok/claude/jcode` 可能因**您自己在用**而非 0；判定标准是「本次测试期间 mtime 未变」，故须改用 Step 4 前后各跑一次并比对。若某条目数 >0 且时间戳落在测试窗口内，FAIL 并停手报用户。

- [ ] **Step 6: 交回主会话（不 commit）**

```bash
cd ~/agent-hub && git status --porcelain && wc -l src/sessions_store.py tests/test_sessions_store.py && \
ls -l src/sessions_store.py | awk '{print $1,$3,$4}' && \
venv/bin/python -m unittest tests.test_sessions_store 2>&1 | tail -3
```
Expected：仅新增两文件（+ `.gitignore` 已排除的 pycache/bak）；属主 `gztxt Users`；`OK`。

---

## Task 2：term.py —— history 端点 / resume 白名单 / 中文标题（子代理 B）

**Files:** Modify `src/term.py`（现网关键位置：`Session.to_dict` ≈:68、`CreateIn` ≈:132、`create_session` ≈:141、`list_sessions` ≈:163）；Create `tests/verify_term_history.py`

- [ ] **Step 1: 先写端到端验证脚本（真服务，stdlib only）**

`tests/verify_term_history.py`：

```python
"""v0.13.0 历史下拉 / 续聊 端到端验证。对已运行实例跑（默认 :3199）。
   跑法：cd ~/agent-hub && TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) \
         venv/bin/python tests/verify_term_history.py
   逐条打 PASS/FAIL；退出码非 0 = 有 FAIL。真输入（发 prompt）不在此脚本内，留 T4 手测。"""
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.getenv("HUB_BASE", "http://127.0.0.1:3199")
TOKEN = ***"TOKEN"]
HDR = {"x-term-token": TOKEN}
FAILS = []


def call(path, *, method="GET", body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or HDR)})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {"detail": raw.decode("utf8", "ignore")[:120]}


def chk(name, ok, evidence=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{evidence}]" if evidence else ""))
    if not ok:
        FAILS.append(name)


async def replay(sid, want):
    import websockets
    url = BASE.replace("http", "ws") + f"/ws/term/{sid}?token={TOKEN}"
    got = ""
    async with websockets.connect(url, max_size=None) as ws:
        try:
            for _ in range(10):
                m = await asyncio.wait_for(ws.recv(), timeout=4)
                got += m.decode("utf8", "ignore") if isinstance(m, bytes) else m
                if want and want in re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\a]*\a", "", got):
                    break
        except Exception:  # noqa: BLE001
            pass
    return got


def main():
    # 1) 旧行为不降级
    code, d = call("/api/term/sessions", headers={})
    chk("旧行为：GET /api/term/sessions 免 token 仍 200", code == 200, f"HTTP {code}")
    chk("活会话条目带 title 字段（可为空串）", all("title" in s for s in d.get("sessions", [])),
        f"n={len(d.get('sessions', []))}")

    # 2) history 端点
    code, d = call("/api/term/history/grok?limit=3")
    items = d.get("items") or []
    chk("history/grok 有中文条目", code == 200 and len(items) > 0, f"HTTP {code} n={len(items)}")
    if items:
        t = items[0]["title"]
        chk("标题含中文、非字母编号", bool(re.search(r"[\u4e00-\u9fff]", t)) and not re.match(r"^[0-9a-f]{4}$", t), t[:24])
    chk("limit=3 生效", len(items) <= 3, f"n={len(items)}")
    chk("history 缺 token → 401", call("/api/term/history/grok", headers={})[0] == 401)
    code, d = call("/api/term/history/qoder?limit=3")
    chk("qoder 空态有中文 note", code == 200 and bool(d.get("note")), (d.get("note") or "")[:30])
    chk("无仓库 agent(pi) → 400", call("/api/term/history/pi")[0] == 400)
    chk("非法 limit → 422", call("/api/term/history/grok?limit=999")[0] == 422)

    # 3) resume 白名单
    code, d = call("/api/term/sessions", method="POST", body={"agent_id": "grok", "session_id": "x; rm -rf /"})
    chk("注入型 session_id → 400", code == 400, f"HTTP {code}")
    code, d = call("/api/term/sessions", method="POST",
                   body={"agent_id": "grok", "session_id": "00000000-0000-4000-8000-000000000000"})
    chk("形状合法但不存在的 id → 404", code == 404, f"HTTP {code}")

    # 4) 逐 agent：列历史 → 续聊起 pty → WS 回放到旧内容 → 销毁
    for agent in ["grok", "claude", "jcode", "hermes", "codex", "qoder"]:
        his = call(f"/api/term/history/{agent}?limit=1")[1].get("items") or []
        if not his:
            chk(f"{agent} 无历史可续（空态可接受）", True, "note 已给")
            continue
        code, d = call("/api/term/sessions", method="POST",
                       body={"agent_id": agent, "session_id": his[0]["id"]})
        s = d.get("session") or {}
        chk(f"{agent} 续聊起会话 200", code == 200 and bool(s.get("id")),
            f"HTTP {code} cmd={str(s.get('cmd',''))[:34]}")
        if s.get("id"):
            frag = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", his[0]["title"])[:6]
            got = asyncio.new_event_loop().run_until_complete(replay(s["id"], frag)) if frag else ""
            chk(f"{agent} WS 回放含旧会话片段", bool(frag) and frag in re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", got or ""),
                f"回放 {len(got or '')}B 期望片段 {frag}")
            chk(f"{agent} 销毁生效", call(f"/api/term/sessions/{s['id']}", method="DELETE")[0] == 200)

    print("\nALL GREEN" if not FAILS else f"\n{len(FAILS)} FAIL: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `src/term.py` 顶部导入适配层**（在 `import profiles` 之后）

```python
import sessions_store
```

- [ ] **Step 3: `Session` 增加 `resume_of`，`to_dict` 带上**

`__init__` 里（`self._cleaned = False` 附近）加：

```python
        self.resume_of = ""     # v0.13.0：非空 = 由某条磁盘历史续聊而来
```

`to_dict` 整块替换为：

```python
    def to_dict(self):
        return {"id": self.id, "agent_id": self.agent_id, "cmd": " ".join(self.cmd),
                "cwd": self.cwd, "alive": self.alive,
                "created": self.created, "resume_of": self.resume_of,
                "idle_s": round(time.time() - self.last_io)}
```

- [ ] **Step 4: `CreateIn` 加可选 `session_id`**

```python
class CreateIn(BaseModel):
    agent_id: str
    session_id: Optional[str] = None    # v0.13.0：续聊某条历史；仅接受形状合法且实盘存在的 id
```

- [ ] **Step 5: `create_session` 走 resume 模板**（整函数替换；命令仍由后端拼装，客户端传不进命令）

```python
@router.post("/api/term/sessions")
async def create_session(body: CreateIn, request: Request):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "POST /api/term/sessions")
    prof = profiles.get_profile(body.agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{body.agent_id} 无终端入口（仅画像白名单可拉起）")
    if len([s for s in _sessions.values() if s.alive]) >= MAX_SESSIONS:
        raise HTTPException(429, f"终端会话数达上限 {MAX_SESSIONS}")
    cwd = prof["terminal"].get("cwd") or os.path.expanduser("~")
    if body.session_id:
        # 客户端只能给 id：命令仍由后端模板拼装，id 必须过形状正则 + 实盘存在双校验
        try:
            cmd = sessions_store.resume_argv(prof["id"], body.session_id, cwd)
        except ValueError as e:
            if "不在实盘清单" in str(e):
                raise HTTPException(404, str(e)) from e
            raise HTTPException(400, str(e)) from e
    else:
        cmd = shlex.split(prof["terminal"]["cmd"])
    resolved = profiles.which(cmd[0])
    if not resolved:
        raise HTTPException(400, f"命令 {cmd[0]} 未在本机找到")
    cmd[0] = resolved
    sid = uuid.uuid4().hex[:10]
    sess = Session(sid, prof["id"], cmd, cwd)
    sess.resume_of = body.session_id or ""
    _sessions[sid] = sess
    _attach_reader(sess)
    title = (sessions_store.live_titles(prof["id"]) or {}).get(sess.pid, "")
    return {"session": dict(sess.to_dict(), title=title)}
```

- [ ] **Step 6: `list_sessions` 合并中文标题**（整函数替换）

```python
@router.get("/api/term/sessions")
async def list_sessions():
    _reap()
    # 只展示活会话：已退出记录不再以"僵尸条目"出现（历史改由 /api/term/history 从磁盘直读）
    titles: Dict[str, Dict[int, str]] = {}
    out = []
    for s in _sessions.values():
        if not s.alive:
            continue
        if s.agent_id not in titles:
            titles[s.agent_id] = sessions_store.live_titles(s.agent_id) or {}
        out.append(dict(s.to_dict(), title=titles[s.agent_id].get(s.pid, "")))
    return {"sessions": out}
```

- [ ] **Step 7: 新增 history 端点**（放在 `list_sessions` 之后。**要 token**——条目是用户原话，比免鉴权的活会话列表更严）

```python
@router.get("/api/term/history/{agent_id}")
async def agent_history(agent_id: str, request: Request, limit: int = Query(default=3, ge=1, le=20)):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "GET /api/term/history")
    prof = profiles.get_profile(agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{agent_id} 无终端入口，谈不上续聊历史")
    if not sessions_store.supports(prof["id"]):
        raise HTTPException(400, f"{agent_id} 无历史会话仓库")
    cwd = prof["terminal"].get("cwd") or os.path.expanduser("~")
    return dict(sessions_store.list_history(prof["id"], cwd, limit), agent=prof["id"])
```

- [ ] **Step 8: 静态自检（不起服务也不许报错）**

```bash
cd ~/agent-hub && venv/bin/python -c "import sys;sys.path.insert(0,'src');import term,sessions_store;print('import OK',len(sessions_store.SESSION_STORES),'stores')" && venv/bin/python -m py_compile src/term.py src/sessions_store.py tests/verify_term_history.py && echo "py_compile OK"
```
Expected：`import OK 6 stores` 然后 `py_compile OK`。

- [ ] **Step 9: 交回主会话（不 commit）**

```bash
cd ~/agent-hub && git diff --stat src/term.py && git status --porcelain
```

---

## Task 3：前端 —— 左侧历史下拉 + 芯片去字母（子代理 C）

**Files:** Modify `static/hub.js`（关键位置：`termChipHtml` ≈:641、`termNew` ≈:627、`navItemHtml` ≈:1311、`renderNav` ≈:1366、侧栏点击委托 ≈:1487-1502）、`templates/index.html`（`.nav-hist` 样式段）

- [ ] **Step 1: `index.html` 样式（宽度只走 `--sb-w` 与既有 token，不写死 px）**

接在 `.nav-item` 相关段之后（v0.12.5 那批之后）：

```css
        /* ── v0.13.0 左侧历史下拉：agent 行下方展开，点条目 = 终端里续聊该历史 ──
           行高/字号与 .nav-item 同级；左内缩一个图标位，与名称起点对齐。
           刻意不加滚动条与边框盒：侧栏 09-22 已定为「不出滚动条」且宽度收到 --sb-w。 */
        .nav-hist { display: flex; flex-direction: column; gap: 1px; margin: 1px 0 4px;
                    padding-left: calc(var(--ico-md) + 6px); }
        .nav-hist .hh-hd { font-size: var(--fs-xs); color: var(--muted); padding: 2px 6px; letter-spacing: .02em; }
        .nav-hist .hh-row { display: flex; align-items: baseline; gap: 6px; padding: 4px 6px; cursor: pointer;
                            border-radius: 8px; font-size: var(--fs-xs); color: var(--text-2); min-width: 0; }
        .nav-hist .hh-row:hover { background: var(--surface-2); color: var(--text-1); }
        .nav-hist .hh-t { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .nav-hist .hh-ts { flex: none; color: var(--muted); font-family: var(--font-mono); }
        .nav-hist .hh-note { padding: 4px 6px; font-size: var(--fs-xs); color: var(--muted); }
        .sidebar.collapsed .nav-hist { display: none; }   /* 收起态是 48px 图标条，放不下 */
        /* 顶栏芯片：字母 sid 换成中文标题后需要一个截断窗（max-width 用 em，不写死 px） */
        .sess-item .s-t { max-width: 12em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                          display: inline-block; vertical-align: bottom; }
```

- [ ] **Step 2: `hub.js` 历史状态下拉（插在 `let _navHtml = ''` 之前）**

```javascript
/* ── v0.13.0 左侧历史下拉：同一时刻只展开一个 agent（与 navOpen 手风琴同构，D3）。
   histOpen 进 localStorage；数据缓存在 HIST —— 30s loadAgents 重绘时不闪空白。 ── */
const HIST_LIMIT = 3;                                   // D6：每 agent 3 条，不做「显示全部」
const TERM_HIST_AGENTS = ['grok', 'claude', 'jcode', 'hermes', 'codex', 'qoder'];   // 与后端 SESSION_STORES 同集合
let histOpen = localStorage.getItem('hub.hist') || '';
const HIST = {};                                        // agent_id -> {items,note,loading,err}

function hhTime(ts) {                                   // 绝对时间：相对时间每轮变化会破 DOM diff
  if (!ts) return '';
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0');
  return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function histHtml(aid) {
  const h = HIST[aid];
  if (!h || h.loading) return '<div class="nav-hist"><div class="hh-note">读取历史…</div></div>';
  if (h.err) return '<div class="nav-hist"><div class="hh-note">历史读取失败：' + escapeHtml(h.err) + '</div></div>';
  const rows = (h.items || []).map(it =>
    '<div class="hh-row" data-agent="' + escapeHtml(aid) + '" data-sid="' + escapeHtml(it.id) + '"' +
    ' title="' + escapeHtml(it.title) + '"><span class="hh-t">' + escapeHtml(it.title) + '</span>' +
    '<span class="hh-ts">' + hhTime(it.ts) + '</span></div>').join('');
  const hd = '<div class="hh-hd">历史会话' + (rows ? '（' + h.items.length + '）' : '') + '</div>';
  return '<div class="nav-hist">' + hd +
         (rows || '<div class="hh-note">' + escapeHtml(h.note || '该目录暂无可续会话') + '</div>') + '</div>';
}

function histLoad(aid) {
  HIST[aid] = { items: [], note: '', loading: true, err: '' };
  renderNav();
  api('/api/term/history/' + encodeURIComponent(aid) + '?limit=' + HIST_LIMIT, { headers: termHeaders() })
    .then(d => { HIST[aid] = { items: d.items || [], note: d.note || '', loading: false, err: '' }; })
    .catch(e => { HIST[aid] = { items: [], note: '', loading: false, err: String((e && e.message) || e) }; })
    .then(() => renderNav());                            // 无 finally 依赖：老 Safari 也走得到
}

async function termResume(agentId, sid) {
  try {
    const d = await api('/api/term/sessions', { method: 'POST',
      headers: termHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ agent_id: agentId, session_id: sid }) });
    toast('已在终端里续聊该历史会话', 'ok');
    gotoChat(agentId, 'term');
    termConnect(d.session.id, agentId);
    termRefreshList();
  } catch (e) { toast('续聊失败：' + e.message, 'err'); }
}
```

- [ ] **Step 3: `renderNav` 里挂上历史块**（agents 与 infra 两处 map 统一走 `navRow`）

```javascript
function navRow(a) { return navItemHtml(a) + (a.id === histOpen ? histHtml(a.id) : ''); }
```

```javascript
      if (sub.length) body += '<div class="nav-sub">' + label + '</div>' + sub.map(navRow).join('');
      ...
      if (rest.length) body += '<div class="nav-sub">其他</div>' + rest.map(navRow).join('');
      ...
      body = list.map(navRow).join('');
```

- [ ] **Step 4: 点击委托改成「展开 + 进工作台」（D3/A 方案）**

把 `el = e.target.closest('button[data-entity]')` 那三行整体替换：

```javascript
    el = e.target.closest('.hh-row');                     // 历史条目：续聊，窄屏顺手收抽屉
    if (el) { termResume(el.dataset.agent, el.dataset.sid); if (narrow()) apply(true); return; }
    el = e.target.closest('button[data-entity]');
    if (el) {
      const aid = el.dataset.entity;
      if (TERM_HIST_AGENTS.includes(aid)) {
        if (histOpen === aid) histOpen = '';              // 再点当前行 = 只收起，不离开页面
        else { histOpen = aid; histLoad(aid); }           // 展开新的（自动收起上一个）
        localStorage.setItem('hub.hist', histOpen);
        renderNav();
      }
      openEntity(aid);                                    // 进工作台照旧（A：两件事一次点击）
      if (narrow() && !histOpen) apply(true);
      return;
    }
```

- [ ] **Step 5: `termChipHtml` 去字母（D2）**

```javascript
/* 芯片模板：行1 内联会话项。v0.13.0 起标签＝历史会话的问题原文（中文），
   取不到标题（刚新建、agent 还没落摘要 / 无 pid 登记表）退显「新会话 MM-DD」。
   字母 sid 只留在 data-sid 里作 DOM 键，用户可见处一律不再出现。 */
function termChipHtml(s) {
  const label = (s.title && s.title.trim()) ? s.title.trim() : ('新会话 ' + hhTime(Math.floor(s.created)));
  return '<span class="sess-item' + (s.id === termSid ? ' cur' : '') + '" data-sid="' + s.id + '">' +
         '<a href="javascript:void(0)" title="' + escapeHtml(label) + '"' +
         ' onclick="termConnect(\'' + s.id + '\',\'' + s.agent_id + '\')"><span class="s-t">' +
         escapeHtml(label) + '</span></a>' +
         '<button class="sess-x" title="关闭此会话" aria-label="关闭此会话" ' +
         'onclick="termKillOne(\'' + s.id + '\')">' + ico('x', 'xs') + '</button></span>';
}
```

- [ ] **Step 6: `termNew` 本地插入的芯片补 `title` 字段**

```javascript
    if (el && !el.querySelector('.sess-item[data-sid="' + d.session.id + '"]'))
      el.insertAdjacentHTML('beforeend', termChipHtml(Object.assign({ alive: true, title: '' }, d.session)));
```

- [ ] **Step 7: 语法自检 + 不许残留字母标签**

```bash
cd ~/agent-hub && node --check static/hub.js && echo "JS OK" ; grep -n "slice(0, 4)\|slice(0,4)" static/hub.js ; grep -n "sid4" static/hub.js | head
```
Expected：`JS OK`；`slice(0,4)` **无命中**（旧的 `sid4` 标签来源已拔）；`sid4` 无命中。若 node 不在 PATH，用 `~/.nvm/versions/node/v24.18.0/bin/node --check`。

- [ ] **Step 8: 交回主会话（不 commit）**

```bash
cd ~/agent-hub && git diff --stat static/hub.js templates/index.html && git status --porcelain
```

---

## Task 4：端到端验收（主会话，串行；三方产物必须先逐行 diff 过）

**Files:** 无新增修改（发现问题回相应 Task 修，不在验收里顺手改）

- [ ] **Step 0: 主会话复验三方 diff（trust but verify）**

```bash
cd ~/agent-hub && git status --porcelain && git diff --stat && \
git diff src/term.py | head -80 && wc -l src/sessions_store.py tests/test_sessions_store.py tests/verify_term_history.py
```
Expected：只出现点名的 6 个文件（3 改 3 新）；无 `venv/`、无 `.env`、无 `data/`。出现别的文件即退回对应子代理。

- [ ] **Step 1: 起临时实例（3199，只绑 127.0.0.1；生产 3102 不动）**

```bash
cd ~/agent-hub && nohup venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 > /tmp/hub3199.log 2>&1 & echo "PID=$!" > /tmp/hub3199.pid; sleep 6; cat /tmp/hub3199.pid; curl -s -o /dev/null -w 'health=%{http_code}\n' http://127.0.0.1:3199/health; ss -ltn | grep -E ':3199|:3102'
```
Expected：`health=200`；3199 与 3102 **同时**在听（证明没动生产）。若 `health` 非 200：读 `/tmp/hub3199.log` 尾部归因，不许继续。

- [ ] **Step 2: 单测 + 后端到端脚本**

```bash
cd ~/agent-hub && venv/bin/python -m unittest tests.test_sessions_store 2>&1 | tail -3 && \
TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) venv/bin/python tests/verify_term_history.py; echo "退出码=$?"
```
Expected：`OK`；脚本逐行 PASS，末行 `ALL GREEN`，`退出码=0`。**任一 FAIL**：记下条目名 → 回相应 Task 修 → 全脚本重跑（不得只跑失败项了事）。

- [ ] **Step 3: 真工具环（军规：文本通 ≠ 配置对）**

只挑**探针历史**做真输入（标题含「只回复一个字」的那条），避免污染真历史：

```bash
cd ~/agent-hub && TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) venv/bin/python - <<'PY'
import asyncio, json, os, re, urllib.request
B="http://127.0.0.1:3199"; T=os.environ["TOKEN"]; H={"x-term-token":T,"Content-Type":"application/json"}
def call(p, m="GET", b=None):
    r=urllib.request.Request(B+p, method=m, data=json.dumps(b).encode() if b else None, headers=H)
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"{}")
its=[i for i in call("/api/term/history/grok?limit=20")["items"] if "只回复" in i["title"]]
assert its, "没找到探针历史，改用手工确认（不许拿真历史做输入测试）"
s=call("/api/term/sessions","POST",{"agent_id":"grok","session_id":its[0]["id"]})["session"]
print("resume cmd:", s["cmd"], "| title:", s.get("title","")[:24])
import websockets
async def go():
    async with websockets.connect(B.replace("http","ws")+f"/ws/term/{s['id']}?token={T}", max_size=None) as ws:
        buf=""
        for _ in range(8):
            try: buf += (await asyncio.wait_for(ws.recv(),timeout=4)).decode("utf8","ignore")
            except Exception: break
        await ws.send(json.dumps({"data":"只回复一个字：好\r"}))
        for _ in range(30):
            try: buf += (await asyncio.wait_for(ws.recv(),timeout=10)).decode("utf8","ignore")
            except Exception: break
        return buf
buf=asyncio.new_event_loop().run_until_complete(go())
plain=re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\a]*\a","",buf)
print("PASS 输入后 TUI 有响应" if len(plain)>len(re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\a]*\a","",buf[:4000])) else "FAIL 无响应")
call("/api/term/sessions/"+s["id"],"DELETE",None) if False else None
PY
```
Expected：打出 `resume cmd: …grok --resume <uuid>`、中文 title、`PASS 输入后 TUI 有响应`。**跑完手工销毁**：
`curl -s -X DELETE -H "x-term-token: $TOKEN" http://127.0.0.1:3199/api/term/sessions/<id>`（id 取自上面打印）。

- [ ] **Step 4: 肉眼验收（宽屏 + 窄屏）**

```bash
cd ~/agent-hub && echo "宽屏： http://127.0.0.1:3199  （临时实例）" && ss -ltn | grep 3199
```
逐项判 PASS/FAIL：① 点 grok 行 → 行下出 3 条中文标题 + 时间，工作台同时切换；② 再点 grok 行 → 只收起；③ 点 claude 行 → claude 展开、grok 收起；④ 点一条历史 → 终端里出现该会话旧内容；⑤ 顶栏芯片是中文/「新会话 HH:MM」，**画面里没有 a3f9 这类字母**；⑥ qoder 行展开是灰字中文说明而非空白；⑦ 手机（<768px 抽屉）重复 ①②④，无横向滚动条。

- [ ] **Step 5: 回归（老行为不许变）**

```bash
cd ~/agent-hub && TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) && \
ID=$(curl -s -X POST -H "x-term-token: $TOKEN" -H 'Content-Type: application/json' -d '{"agent_id":"shell"}' http://127.0.0.1:3199/api/term/sessions | python3 -c 'import json,sys;print(json.load(sys.stdin)["session"]["id"])') && \
echo "无 session_id 新建=$ID" && curl -s -H "x-term-token: $TOKEN" http://127.0.0.1:3199/api/term/sessions | head -c 300 && echo && \
curl -s -X DELETE -H "x-term-token: $TOKEN" http://127.0.0.1:3199/api/term/sessions/$ID && echo
```
Expected：不带 `session_id` 的 POST 仍 200 起会话；列表含该会话且带 `title` 字段；DELETE 返回 `{"status":"killed"}`。

- [ ] **Step 6: 收掉临时实例（只 kill 自己记的 PID）**

```bash
cd ~/agent-hub && PID=$(sed -n 's/PID=//p' /tmp/hub3199.pid) && kill "$PID" && sleep 2 && \
ss -ltn | grep -c ':3199' ; systemctl --user is-active agent-hub.service ; curl -s -o /dev/null -w '3102=%{http_code}\n' http://127.0.0.1:3102/health
```
Expected：3199 计数 `0`；3102 仍 `active` 且 `health=200`（**生产全程未动**）。

---

## Task 5：版本号、归档与提交（主会话，串行）

- [ ] **Step 1: VERSION bump（读现值再改，别假设）**

```bash
cd ~/agent-hub && grep -n 'VERSION = ' src/main.py
```
把该行从当期值改到 `0.13.0`（**只改这一行**，禁全局替换）：

```bash
cd ~/agent-hub && venv/bin/python - <<'PY'
import re,pathlib
p=pathlib.Path('src/main.py'); s=p.read_text()
n=re.sub(r'VERSION = "[\d.]+"', 'VERSION = "0.13.0"', s, count=1)
assert n!=s, "没命中 VERSION 行，停手"
p.write_text(n); print("bumped:", [l for l in n.splitlines() if 'VERSION =' in l])
PY
```

- [ ] **Step 2: README 一行**

在 `## 功能` 段末追加：

```markdown
- **左侧菜单历史会话下拉（v0.13.0）**：点 agent 行 = 进工作台 + 在该行下方展开最近 3 条历史会话（标题＝用户问题原文，磁盘直读，口令自动 `<masked>`）；点条目在终端里 `<cli> --resume <id>` 续聊。顶栏会话芯片由十六进制 sid 改中文标题。新增只读适配层 `src/sessions_store.py`（grok/claude/jcode/hermes/codex/qoder 六种仓库），`GET /api/term/history/{agent_id}` 要 TERM_TOKEN。
```

- [ ] **Step 3: 两笔提交（各自可独立 revert）**

```bash
cd ~/agent-hub && git add src/sessions_store.py tests/test_sessions_store.py src/term.py tests/verify_term_history.py && \
git commit -m "v0.13.0 后端：会话仓库适配层 + history/resume API + 活会话中文标题" && \
git add static/hub.js templates/index.html && \
git commit -m "v0.13.0 前端：左侧历史会话下拉 + 顶栏芯片去字母（问题原文当标题）" && \
git add src/main.py README.md docs/ && git commit -m "v0.13.0 版本号 + spec/plan 归档" && git log --oneline -4 && git status --porcelain
```
Expected：3 笔提交；末次 `git status --porcelain` 只剩 `.bak-*`（已被 .gitignore 排除 ⇒ 应为空）。

- [ ] **Step 4: 生产切换（交给用户，AI 不重启）**

```bash
# 用户自行执行（本设计全程不 stop/restart agent-hub.service）：
systemctl --user restart agent-hub.service && sleep 3 && curl -s http://127.0.0.1:3102/health
```

---

## 回滚

| 层级 | 命令 |
|---|---|
| 前端笔 | `cd ~/agent-hub && git revert --no-edit HEAD~1`（下拉消失，终端与后端不受影响） |
| 后端笔 | `cd ~/agent-hub && git revert --no-edit HEAD~2`（history/resume 端点与适配层整体摘除） |
| 单文件急退 | `cp src/term.py.bak-<TS>-v0130-history src/term.py`（其余三个同理）；`rm src/sessions_store.py tests/*.py` 新文件即可整体摘除 |
| 判据 | 回滚后 `curl -s http://127.0.0.1:3199/api/term/sessions`（临时实例）与 `/health` 双 200，且用户 3102 全程未被打断 |

## 自审（写完后用新眼睛过一遍的结果）

- **spec 覆盖**：D1→T2S5+T3S2；D2→T3S5+T2S6；D3→T3S3/S4；D4（六 agent）→T1S3 表 + T4S2；D5→`_t_codex` 的 `source='cli'`；D6→`HIST_LIMIT`/`limit` 钳位；D7→`_t_hermes` 口径 + note；§5 隐私→`mask_title` + T1 三条 mask 单测；§6 九项验收→T4 Step1-6 与 T2 脚本逐条对应。无遗漏。
- **占位符扫描**：全文无 TBD/TODO；两处「实现时核对」已换成可跑的 `pragma table_info` 取证命令并给出 Expected。
- **类型一致性**：`Item` 六字段在 T1 六个适配器构造处逐一对齐；`title`/`resume_of` 在 T2 `to_dict`、T3 `termChipHtml`/`termNew` 三处引用同名；`histHtml/histLoad/termResume/navRow` 命名在 T3 内部自洽；`TERM_HIST_AGENTS` 与 `SESSION_STORES` 集合同为 6 项（T1 单测 `test_only_six_agents` 锁死）。

---

## 执行偏差记录（2026-09-22 22:55 结项时回写，以实测为准）

计划里以下 7 条与实盘不符，**实现按实测改判，未反向放宽校验**：

1. **grok 标题字段位置**：正文在 jsonl 顶层 `content`（非 `message.content`），且用户真问题被包在
   `<user_query>…</user_query>` 内（实测第 4 行）。原逻辑「content 以 `<` 开头即跳过」会把真问题丢掉，
   退化成 agent 自生成的**英文**摘要（`Agent Hub sidebar menu name…`）。改：先拆 `user_query`，
   拆不到再按注入块前缀表跳过。
2. **`HEAD_CHARS` 64KB 不够**：grok 单是 `<user_info>` 一行就 33KB。改 256KB。
3. **jcode `content` 是 text 块列表**：原样 `json.dumps` 会让标题长成 `[{"type": "text", …`。
   改：统一 `_text_of()` 拉平（str / 块列表两种形态都吃）。
4. **hermes 标题优先级反转**：`sessions.title` 是 LLM 生成的「友好问候 #6」这类，按 D2「问题原文」
   应 first_u 优先、LLM 标题兼底。jcode 同理（其 `title` 实测恒为 None）。
5. **qoder 单测正例的 cwd 写错**（计划把正例也写成技术文档目录，而该目录 qoder 实测 0 条）：
   按真实 cwd `/home/gztxt/agent-hub` 断言，不放宽 `id_re + 实盘存在` 这道安全闸。
6. **顶栏芯片 pid 反查不足以覆盖六家**：jcode 只在退出时写 `last_pid`，codex/qoder 根本没有
   pid→会话 登记表 ⇒ 新增 `title_for()` 走 `resume_of` 直查盘上标题兜底。
7. **计划漏了刷新态**：`histOpen` 进 localStorage 但 `HIST` 不进 ⇒ 刷新后下拉永远「读取历史…」。
   补 `histBootstrap()`；并补「新前端 + 未重启旧后端」404 时说人话的降级话术。

验证手段的两条坑（写给下一次执行者）：

- **无头 WS 客户端必须替 TUI 答终端查询**：grok/jcode 发 `CSI > 0 q`、hermes 发 `CSI c` + `OSC 11;?`、
  codex 发 kitty `CSI > 1 u`；不答就只回 11~200 字节，看着完全像「没恢复/白屏」。此外 hermes 起手
  静默加载约 25s，可见内容不足要**重连**（ring 会把旧帧补给新连接）。
- **「重启了」不等于重启**：按 `pgrep -f <pattern>` 取首条 PID 会杀错（uvicorn 父/子并存），
  新实例端口被占即静默退出，而健康检查仍 200 —— 本轮因此有整整一版结果打在旧代码上。
  口径改为：`ss -ltnp | grep :PORT` 反查真实监听 PID，重启后比对进程 `lstart` 晚于文件 mtime。
- **obscura `--eval` 只支持同步表达式**，不 await Promise，且不与 `--dump` 组异步等待；
  `--storage-dir` 实测不持久化 localStorage ⇒ 多步交互（点击→等异步→读 DOM）不能可靠编排。
  本轮改用「同步注入 HIST + renderNav()」取得下拉真实渲染文本，真实点击链路留给端侧眼校。
