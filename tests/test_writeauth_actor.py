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
