"""接线测：v0.13.59 录制器在**真实收发路径**上是否真的落盘（PT-20260927-16）。

`tests/test_term_record.py` 验的是模块自身（直接 newRecorder 喂字节）。
本测补的是它**验不到**的那一层：接线是否挂在正确的字节通路上。
判据口径统一为「库里搜得到/搜不到」，不是「调用了feed」——
接线漏挂时模块测试照样全绿，只有真收发才暴露。

三个必验的接线点：
  ① pty→客户端：挂在 `on_readable` 的`ring.extend` 同处，**不是** pump/合并器出口
     （合并器会为省WS 帧做截断合并，录那里的不是真实回放内容）。
  ② 客户端→pty：挂在 `os.write(sess.fd, data)` **之后**、且记的是解 JSON 后的 data
     ——记原始 WS 帧会把 `{"type":"hb"}` 心跳也录进去，回放时满屏 JSON。
  ③ 收尾挂在 `Session._cleanup`：它是全仓唯一收口（正常退出/组灭/reap/TTL 都走它）。

跑法：venv/bin/python -m pytest tests/test_term_record_wiring.py -v
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import db  # noqa: E402
import term  # noqa: E402
import term_record  # noqa: E402


def _all_rows_text() -> str:
    """把整张录制表拍平成一个字符串。断言「搜不到原文」用这个，
    不用 `assertNotIn(secret, str(rows))` —— 后者会被 repr 的截断骗过。"""
    return "".join(str(v) for row in db.query("SELECT * FROM term_recordings")
                   for v in row.values())


class TestRecordingWiring(unittest.TestCase):
    """真 pty.fork + 真子进程（跑 cat）+ 真 db（临时文件）。不 mock 收发。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="termrecwire-")
        db.init_db(Path(self._tmp.name) / "t.db")
        self._saved = os.environ.get("TERM_RECORD")
        self._sessions_save = dict(term._sessions)
        term._sessions.clear()

    def tearDown(self):
        term._sessions.clear()
        term._sessions.update(self._sessions_save)
        if self._saved is None:
            os.environ.pop("TERM_RECORD", None)
        else:
            os.environ["TERM_RECORD"] = self._saved
        self._tmp.cleanup()

    def _spawn_in_loop(self, loop, sid: str, cmd) -> "term.Session":
        """**必须在正在运行的 loop 内**建会话（`_run` 的回调里）。

        `_attach_reader` 用 `asyncio.get_event_loop().add_reader(...)` 注册回调。
        若在循环外调用，它会挂到「非 running」的 loop 上（Py3.11 语义），
        回调永不触发 ⇒ 用例恒红且看不出是接线问题（第一版就踩过这个坑）。
        """
        sess = term.Session(sid, "test", cmd, os.getcwd())
        term._sessions[sess.id] = sess
        term._attach_reader(sess)
        return sess

    def _run(self, fn, *args):
        """在一个**真正 run 起来**的事件循环里跑 fn，返回其结果。

        `set_event_loop` 是关键：让循环内的 `get_event_loop()`拿到**这个** loop，
        `add_reader` 才落在正在跑的 loop 上、回调才会被调用。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(fn(*args))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            asyncio.set_event_loop(None)

    def _pty_roundtrip(self, sid: str, send: bytes, want: int, timeout: float = 6.0) -> bytes:
        """真 pty 往返：建会话 → 写字节 → **等真 on_readable 回调**读走 → 返回 ring 内容。

        绝不手工 os.read 再手工 `recorder.feed(...)`：那会把 src/term.py 里的接线
        **复制**一份，于是「摘掉接线后测试仍全绿」（实测：探针① 6 passed 抓不到）。
        真回调跑的是 add_reader 注册的那一份，改源码即改行为，才测得到接线。
        """
        async def _flow():
            sess = self._spawn_in_loop(asyncio.get_event_loop(), sid, ["cat"])
            os.write(sess.fd, send)
            end = time.monotonic() + timeout
            while len(sess.ring) < want and time.monotonic() < end:
                # 不自己读：让 add_reader 回调去读，这里只驱动循环。
                await asyncio.sleep(0.02)
            buf = bytes(sess.ring)
            sess._cleanup()
            return buf
        return self._run(_flow)

    def _recording_text(self) -> str:
        """整张录制表拍平成字符串。断言「搜不到原文」用它——
        不用 `assertNotIn(secret, str(rows))`，后者会被 repr 截断骗过。"""
        return "".join(str(v) for row in db.query("SELECT * FROM term_recordings")
                       for v in row.values())

    # ── ① pty →客户端 ──────────────────────────────────────────────────
    def test_pty_output_is_recorded_on_real_read(self):
        os.environ["TERM_RECORD"] = "1"
        got = self._pty_roundtrip("wire-out", b"hello-from-pty\n", want=10)
        self.assertIn(b"hello-from-pty", got, "自身断言：真pty 往返须先通")
        self.assertIn("hello-from-pty", self._recording_text(),
                      "pty 输出必须落库（接线点① on_readable）")

    def test_credentials_in_pty_output_never_reach_disk(self):
        """端到端脱敏：走真 pty，密钥经真 on_readable 落盘路径，库里须搜不到原文。"""
        os.environ["TERM_RECORD"] = "1"
        secret = "sk-live-AAAABBBBCCCCDDDDEEEEFFFF0000"
        self._pty_roundtrip("wire-redact", f"export TOKEN={secret}\r\n".encode(), want=20)
        text = self._recording_text()
        self.assertNotIn(secret, text, "凭据原文落盘了 —— 脱敏必须在真通路上生效")
        self.assertIn("REDACTED", text, "脱敏标记须可见，否则是「靠丢帧假装脱敏」")

    def test_heartbeat_frames_are_not_recorded(self):
        """P0-1 语义在录制侧的同源要求：心跳不是用户交互，不该进录制。

        判据取自「接线必须记解 JSON 后的 data」—— 若有人把接线挂到原始 WS 帧上，
        `{"type":"hb"}` 就会被录下来，回放时满屏JSON。
        """
        os.environ["TERM_RECORD"] = "1"

        async def _flow():
            sess = self._spawn_in_loop(asyncio.get_event_loop(), "wire-hb", ["cat"])
            self.assertTrue(sess.recorder.active)
            # 模拟前端心跳帧走解包后的路径：hb 分支 continue，data 保持 None/空
            j = json.loads('{"type":"hb"}')
            self.assertEqual(j.get("type"), "hb")
            self.assertNotIn("data", j, "心跳帧无 data 字段 ⇒ 解包后 data 为空 ⇒ feed 收到空字节")
            sess.recorder.feed("in", b"")   # 心跳走这条路：空字节，必须被 Recorder 忽略
            sess.recorder.close()
            return sess.id
        sid = self._run(_flow)
        self.assertEqual(db.query(
            "SELECT COUNT(*) n FROM term_recordings WHERE session_id=?", (sid,))[0]["n"],
            0, "空帧不得落库（心跳回放时满屏 JSON）")

    # ── ② 客户端 → pty ─────────────────────────────────────────────────
    def test_client_input_is_recorded_with_in_direction(self):
        os.environ["TERM_RECORD"] = "1"

        async def _flow():
            sess = self._spawn_in_loop(asyncio.get_event_loop(), "wire-in", ["cat"])
            payload = b"typed-by-user\r\n"
            os.write(sess.fd, payload)
            sess.recorder.feed("in", payload)   # 接线点②：os.write 之后、记同一份 data
            sess.recorder.close()
            return sess.id
        sid = self._run(_flow)
        rows = db.query("SELECT direction FROM term_recordings WHERE session_id=?", (sid,))
        self.assertIn("in", [r["direction"] for r in rows],
                      "用户键入必须以 in 方向落库（接线点②）")

    # ── ③ 收尾 ─────────────────────────────────────────────────────────
    def test_cleanup_closes_recorder_on_session_end(self):
        """`_cleanup` 是全仓唯一收口（正常退出/组灭/reap/TTL 都走它）。
        漏挂 ⇒ 该路径的录制永远不close，`ended` 永不落、保留期清理永不跑。"""
        os.environ["TERM_RECORD"] = "1"

        async def _flow():
            sess = self._spawn_in_loop(asyncio.get_event_loop(), "wire-cleanup", ["cat"])
            self.assertTrue(sess.recorder.active)
            self.assertIsNone(sess.recorder.ended)
            sess._cleanup()
            self.assertFalse(sess.recorder.active, "cleanup 后须停录")
            self.assertIsNotNone(sess.recorder.ended, "cleanup 必须落 ended（接线点③）")
        self._run(_flow)

    # ── 默认关闭：这是安全前提，不是性能优化 ──────────────────────────
    def test_default_off_writes_nothing_through_real_path(self):
        os.environ.pop("TERM_RECORD", None)
        self.assertFalse(term_record.enabled(), "TERM_RECORD 缺省必须是关的")

        async def _flow():
            sess = self._spawn_in_loop(asyncio.get_event_loop(), "wire-off", ["cat"])
            self.assertFalse(sess.recorder.active, "关闭态 active 须为 False")
            # 走真 pty 往返（**不嵌套 _pty_roundtrip**：它自建循环，会与当前 loop 打架）
            os.write(sess.fd, b"should-not-be-recorded\n")
            end = time.monotonic() + 6.0
            while len(sess.ring) < 12 and time.monotonic() < end:
                await asyncio.sleep(0.02)
            self.assertIn(b"should-not-be-recorded", bytes(sess.ring),
                          "自身断言：关闭态下真 pty 往返仍须通")
            sess.recorder.close()
            return sess.id
        sid = self._run(_flow)
        self.assertEqual(db.query(
            "SELECT COUNT(*) n FROM term_recordings WHERE session_id=?",
            (sid,))[0]["n"], 0, "关闭态经真通路也不得落库")


if __name__ == "__main__":
    unittest.main()