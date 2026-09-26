#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：MCP 门面全源默认（v0.13.28 批2）。

钉什么：外部 agent（claude/codex 经 MCP 调 hub）**不传可选参数时**必须拿到全源检索，
不是旧口径的 local,tdai 两路——用户核心诉求「所有 agent 能够加载检索调用」的 MCP 侧落点。
源码文本断言式（仿 test_runlog 的 IntegrationSurface 先例）：MCP SDK 在 venv 可用，
但起真 server 属集成批次；这里钉签名与默认值，直调行为已由实施时冒烟覆盖。
"""
import pathlib
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import inspect  # noqa: E402
import memfed   # noqa: E402
import kb       # noqa: E402
import hubmcp   # noqa: E402


class TestMcpFullSourceDefaults(unittest.TestCase):

    def test_hub_memory_search_has_sources_param(self):
        sig = inspect.signature(hubmcp.hub_memory_search)
        self.assertIn("sources", sig.parameters,
                      "hub_memory_search 必须透传 sources——不透传=外部 agent 拿不到联邦源")

    def test_memory_default_covers_all_fed_sources(self):
        default = inspect.signature(hubmcp.hub_memory_search).parameters["sources"].default
        got = {x.strip() for x in default.split(",") if x.strip()}
        for sid in ("local", "tdai") + tuple(memfed.enabled_ids()):
            self.assertIn(sid, got, f"默认源缺 {sid}——外部 agent 不传参数就查不到它")

    def test_kb_default_covers_all_routes(self):
        default = inspect.signature(hubmcp.hub_kb_search).parameters["routes"].default
        got = {x.strip() for x in default.split(",") if x.strip()}
        self.assertEqual(got, set(kb.ROUTES),
                         "kb 默认路必须= ROUTES 全集（五路），不是旧口径三路")

    def test_defaults_derived_from_registry_not_hardcoded(self):
        """默认值必须从 memfed/kb 动态派生（写死串=新增源时 MCP 又落后一轮）。"""
        src = (_REPO / "src" / "hubmcp.py").read_text(encoding="utf-8")
        self.assertIn('"local,tdai," + ",".join(memfed.enabled_ids())', src)
        self.assertIn('",".join(kb_mod.ROUTES)', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
