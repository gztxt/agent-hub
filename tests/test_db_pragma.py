"""L0 · SQLite PRAGMA 必须写死，不得靠标准库默认值（T05-1，v0.13.16）。

为什么要有这只闸门：改 PRAGMA 是**只在故障发生后才看得见**的那类改动——busy_timeout 被
标准库默认值"意外提供"就是例子（0924 方案 v2 把它列为"缺失"，而实测已回 5000，
原因不在本仓而在 `sqlite3.connect(timeout=5.0)`）。所以断言的对象不是"PRAGMA 有值"，
而是"**由 src/db.py 这一行保证**"。

隔离：init_db 会把模块级 _conn 换指向临时库，故测前存、测后还原，
避免同一次 discover 里后面的用例拿到一个空库。
"""
import pathlib
import sys
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import db  # noqa: E402


class TestPragma(unittest.TestCase):
    def setUp(self):
        self._saved = db._conn
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="hubpragma-")) / "t.db"
        db.init_db(self.tmp)

    def tearDown(self):
        try:
            db._conn.close()
        except Exception:  # noqa: BLE001
            pass
        db._conn = self._saved

    def p(self, name):
        return db._conn.execute(f"PRAGMA {name}").fetchone()[0]

    def test_journal_mode_is_wal(self):
        self.assertEqual(str(self.p("journal_mode")).lower(), "wal")

    def test_busy_timeout_written_not_inherited(self):
        """≥5000 即可。刻意不钉死等于 5000：万一将来调到 8000 也别假红。"""
        self.assertGreaterEqual(int(self.p("busy_timeout")), 5000)

    def test_foreign_keys_on(self):
        """SQLite 默认 OFF；开着是为将来加外键时不静默失效（SCHEMA 实测 0 处 REFERENCES）。"""
        self.assertEqual(int(self.p("foreign_keys")), 1)

    def test_synchronous_normal_under_wal(self):
        """2=FULL 会让每次 commit 等 fsync；WAL 的推荐搭配是 1=NORMAL。"""
        self.assertEqual(int(self.p("synchronous")), 1,
                         "synchronous 必须是 NORMAL(1)：FULL(2) 在 NAS 上会把 fsync 打进写路径")

    def test_wal_checkpoint_does_not_break_startup(self):
        """init_db 里 checkpoint 是 try 包着的；此处只要求它跑完仍可正常读写。
        走仓内既有 add_memory()，不手写 INSERT —— memories.created_at 是 NOT NULL 无默认值。"""
        mid = db.add_memory("探针", "fact", "test", None)
        self.assertTrue(mid)
        rows = db.query("SELECT content FROM memories WHERE source='test'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "探针")

    def test_pragmas_survive_second_init(self):
        """幂等：二次 init_db（重启语义）后 PRAGMA 仍然成立。"""
        db.init_db(self.tmp)
        self.assertEqual(int(self.p("foreign_keys")), 1)
        self.assertEqual(int(self.p("synchronous")), 1)


if __name__ == "__main__":
    unittest.main()
