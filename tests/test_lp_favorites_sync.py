#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 静态闸门：项目页收藏/隐藏的同步语义（v0.13.101，用户 2026-10-09 报障）。

用户报障「本地项目之前收藏了，重新启动 agenthub 后又显示没有收藏，之前也修过一次」。
真浏览器取证在 work/probe-lp-sync-once.py（A/B/C/D 四段），本文件钉住**代码形状**
—— 因为那个 bug 的本质是「一次性闸门 + 静默catch」，静态可判，不必每次起浏览器。

**同型代码必须同型修**：本机项目页（09分片）与 GitHub 项目页（10 分片）是两套
各自独立的同步实现 Historically 修09 漏 10、或反之，都是「同型缺陷漏改」的典型。
所以下面每个用例都**对两个分片各判一次**（`for frag in LP_GH`），
少修一个分片这里必红。

为什么必须有这组用例（每条都对应一个真实缺陷形态）：
  R1 `lpPrefSynced = true` 出现在 GET **之前** ⇒ 一次失败终身不再拉（缺陷①）。
     判据不是「有没有 = true」，而是**赋值行号必须晚于 await 行号**。
  R2 catch 里出现空实现（只有注释、没有任何状态记录）⇒ 同步失败对用户不可见。
     静默失败与「真的没有收藏」在屏幕上是同形的，用户分不出就不可能自查。
  R3 同步分支里存在 `lpPushPref()` 且**推的是本地原始集合** ⇒ 过时子集覆盖服务端。
     判据：并集必须经`_lpAdopt(serverSet, localSet)` 这类「以服务端为基准」的函数，
     且 push 的对象必须是合并后的 localSet，不能是 push 前的原始快照。
  R4 并集没有删除墓碑 ⇒「取消收藏」在下次同步时被服务端旧值复活（拿一个bug
     换另一个 bug）。判据：toggle 的删除分支必须写墓碑，且并集函数必须读它。
  R5 lp/gh 两分片必须**都**有上述四件——漏一个即红（防「只修一半」）。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests"))

import tiers  # noqa: E402

_LP = _REPO / "static/hub/09-local-projects.js"
_GH = _REPO / "static/hub/10-github-projects.js"
_BUNDLE = _REPO / "static/hub.js"

#: (分片名, 路径, 同步函数名, 推送函数名, 前缀)
FRAGS = [
    ("lp", _LP, "lpSyncPrefs", "lpPushPref", "lp"),
    ("gh", _GH, "ghSyncPrefs", "ghPushPref", "gh"),
]


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _func_body(src: str, name: str) -> str:
    """取 `async function NAME(...) { ... }` 的函数体（按花括号配平，不靠正则猜）。"""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        raise AssertionError("找不到函数 %s" % name)
    i = src.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
        j += 1
    raise AssertionError("函数 %s 花括号不配平" % name)


class TestPrefsSync(unittest.TestCase):
    """收藏同步语义 —— 09/10 两分片同型同判。

    L0 hermetic：不碰网络、不碰真库，只读仓内静态源码。判据全部写成
    「赋值/调用在源码中的相对位置」，不依赖运行时状态。
    """

    def test_R1_synced_flag_set_only_after_success(self):
        """R1：`lpPrefSynced/ghPrefSynced = true` 必须在 await **之后**。"""
        for tag, path, sync, _push, pfx in FRAGS:
            with self.subTest(frag=tag):
                body = _func_body(_read(path), sync)
                assign = re.search(r"\b" + pfx + r"PrefSynced\s*=\s*true", body)
                self.assertIsNotNone(assign, "%s: 同步函数里没有 %sPrefSynced = true" % (tag, pfx))
                await_at = body.find("await ")
                self.assertNotEqual(await_at, -1, "%s: 同步函数里没有 await" % tag)
                self.assertGreater(
                    assign.start(), await_at,
                    "%s: %sPrefSynced=true 出现在 await 之前 ⇒ 一次失败后本页终身不重拉"
                    % (tag, pfx))

    def test_R2_catch_records_failure_visibly(self):
        """R2：catch 必须留下可显示的失败痕迹，不能是空实现。"""
        for tag, path, sync, _push, pfx in FRAGS:
            with self.subTest(frag=tag):
                src = _read(path)
                body = _func_body(src, sync)
                m = re.search(r"catch\s*\([^)]*\)\s*\{", body)
                self.assertIsNotNone(m, "%s: 同步函数没有 catch" % tag)
                catch_body = body[m.end():]
                self.assertIn(
                    pfx + "PrefFail", catch_body,
                    "%s: catch 里没有 %sPrefFail(...) ⇒ 同步失败对用户不可见，"
                    "而「同步失败」与「真的没有收藏」在屏幕上同形" % (tag, pfx))

    def test_R3_sync_merges_from_server_baseline(self):
        """R3：并集必须以服务端为基准，且 push 的是合并后的集合。"""
        for tag, path, sync, push, pfx in FRAGS:
            with self.subTest(frag=tag):
                src = _read(path)
                body = _func_body(src, sync)
                self.assertIn(
                    "_%sAdopt(" % pfx, body,
                    "%s: 同步里没有 _%sAdopt(serverSet, localSet) ⇒ "
                    "仍在用「本地非空就整份推上去」的覆盖语义" % (tag, pfx))
                # push 必须在 adopt 之后：合并先发生，再推
                self.assertLess(body.find("_%sAdopt(" % pfx), body.find(push + "("),
                                "%s: 先 push 再 adopt ⇒ 推出去的是未合并的旧集合" % tag)
                # 「以本地为准」的两个旧判据不许复活
                self.assertNotIn(
                    "%sStars.size === 0 && %sHiddenSet.size === 0" % (pfx, pfx),
                    body, "%s: 旧的「本地为空才接收」分支复活了" % tag)

    def test_R4_delete_tombstone_present_and_used(self):
        """R4：取消收藏必须记墓碑，且并集必须读墓碑（否则取消会被复活）。"""
        for tag, path, _sync, _push, pfx in FRAGS:
            with self.subTest(frag=tag):
                src = _read(path)
                self.assertIn(
                    "function _%sTombAdd(" % pfx, src,
                    "%s: 没有 _%sTombAdd ⇒ 并集会把「取消收藏」复活" % (tag, pfx))
                adopt = _func_body(src, "_%sAdopt" % pfx)
                self.assertIn(
                    "%sTomb.has(" % pfx, adopt,
                    "%s: _%sAdopt 不读墓碑 ⇒ 墓碑形同虚设" % (tag, pfx))
                # toggle 的删除分支要写墓碑、添加分支要撤墓碑
                tog = _func_body(src, "%sToggleStar" % pfx)
                self.assertIn("%sTombAdd(" % pfx, tog,
                              "%s: %sToggleStar 删除分支不写墓碑" % (tag, pfx))
                self.assertIn("%sTombDrop(" % pfx, tog,
                              "%s: %sToggleStar 添加分支不撤墓碑 ⇒ 重新收藏也进不来"
                              % (tag, pfx))

    def test_R5_ls_keys_went_through_guard(self):
        """R5：墓碑存取必须走 lsSet守卫（裸 localStorage 会被 test_ls_guard 判红，
        且真机上无守卫的写入在隐私模式下静默失败）。"""
        for tag, path, _sync, _push, pfx in FRAGS:
            with self.subTest(frag=tag):
                src = _read(path)
                for fn in ("_%sTombAdd" % pfx, "_%sTombDrop" % pfx, "_%sAdopt" % pfx):
                    body = _func_body(src, fn)
                    self.assertNotIn(
                        "localStorage.setItem", body,
                        "%s: %s 里有裸 localStorage.setItem" % (tag, fn))
                    if fn != "_%sAdopt" % pfx:
                        self.assertIn("lsSet(", body,
                                      "%s: %s 没有走 lsSet 守卫" % (tag, fn))

    def test_R6_bundle_is_rebuilt(self):
        """R6：产物 static/hub.js 必须含新形态——改了分片不重建产物，
        页面加载的仍是旧代码，所有断言在源码上全绿而用户那边毫无变化。
        （P1-18「写端删了产物没重建 ⇒ 每秒报错页面却看着正常」同型。）"""
        b = _read(_BUNDLE)
        self.assertIn("lpPrefFail", b, "产物 hub.js 没有 lpPrefFail ⇒ 没重建")
        self.assertIn("ghPrefFail", b, "产物 hub.js 没有 ghPrefFail ⇒ 没重建")
        self.assertIn("hub.lp.tomb", b, "产物 hub.js 没有墓碑键 ⇒ 没重建")
        self.assertIn("hub.gh.tomb", b, "产物 hub.js 没有 gh 墓碑键 ⇒ 没重建")


if __name__ == "__main__":
    unittest.main(verbosity=2)