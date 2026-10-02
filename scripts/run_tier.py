#!/usr/bin/env python3
"""按层跑测试，并把「L0 必须零跳过」做成**可机器判**的闸门。

为什么不用一行 `unittest discover` 就完事：
  discover 会把 host 用例的 SKIP 和"某个 hermetic 用例自己偷偷 skipTest"混在
  同一个 `skipped=N` 里 —— 而后者正是分层放错的信号（干净 runner 上它其实什么
  都没测）。本脚本按 tiers.is_host 把两层拆开，各自给数，并据此判闸。

用法：
  venv/bin/python scripts/run_tier.py hermetic   # 只 L0；出现任何 skip 即 FAIL
  venv/bin/python scripts/run_tier.py host       # 只 L1（换机时应整层显式跳过）
  venv/bin/python scripts/run_tier.py all        # 两层都跑并分别报告
  venv/bin/python scripts/run_tier.py hermetic --fake-home   # 造一个空 HOME，模拟干净 runner
退出码：0 全绿；1 有用例失败；2 分层被放错（L0 里出现 skip）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
import tiers  # noqa: E402


def _resolve(test: unittest.TestCase):
    """从 TestId 反查被装饰的 callable，判它是不是 host 层。"""
    parts = test.id().split(".")          # tests.test_x.TestY.test_z
    obj = None
    holder = None
    try:
        obj = sys.modules[parts[0]]
        for p in parts[1:]:
            holder = obj
            obj = getattr(obj, p, None)
            if obj is None:
                return False
    except Exception:  # noqa: BLE001
        return False
    # obj 现在是绑定方法或函数；holder 是它所属的类
    func = getattr(obj, "__func__", obj)
    if tiers.is_host(func):
        return True
    cls = holder if isinstance(holder, type) else None
    return bool(cls is not None and tiers.is_host(cls))


def _split(pattern: str):
    suite = unittest.TestLoader().discover(str(ROOT / "tests"), pattern=pattern)
    herm, host, other = [], [], []

    def walk(s):
        for x in s:
            if isinstance(x, unittest.TestSuite):
                walk(x)
            else:
                host.append(x) if _resolve(x) else herm.append(x)
    walk(suite)
    return herm, host, other


def _run(tests, label, stream=sys.stderr):
    suite = unittest.TestSuite(tests)
    res = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    skipped = [t for t, _r in res.skipped]
    return res, len(res.skipped), len(tests)


def _parity() -> int:
    """收集器对账：unittest 收进几例 vs pytest 收进几例，必须相等。

    为什么要在**分层跑完之后**再做：`run_tests.sh all` 只跑 L0+L1，而 pytest 一次
    收全标准层 —— 两边例数不等就说明有 test_ 只有某个 runner 收得到（2026-10-02 实测：
    模块级 pytest 风格闸门 14 条 ⇒ 961 vs 975，标准套件压根没跑本批闸门却报全绿）。
    只做 `--collect-only`（约 2s），**不拿 pytest 跑用例** —— 那会同一批跑两遍、
    两个例数并排，入库时反而看不出哪个是真口径。详见 tests/tiers.py 同名段。
    """
    sys.path.insert(0, str(ROOT / "tests"))
    import tiers  # noqa: E402
    bad = tiers.module_level_test_functions()
    if bad:
        print("[tier] ❌ 标准层有 unittest 收不到的 test_（标准套件里它们不会执行）：")
        for b in bad:
            print("        " + b)
        return 2
    n_unit = tiers.unittest_collected_count()
    n_pytest, why = tiers.pytest_collected_count()
    if n_pytest is None:
        print("[tier] ⚠ 收集器对账跳过：%s（unittest=%d 例已跑）" % (why, n_unit))
        return 0
    if n_unit != n_pytest:
        print("[tier] ❌ 收集器对账：unittest=%d ≠ pytest=%d ⇒ 有用例只被一个 runner 收进来"
              % (n_unit, n_pytest))
        return 2
    print("[tier] ✅ 收集器对账：unittest=%d = pytest=%d（%s）" % (n_unit, n_pytest, why))
    return 0


def main(argv):
    tier = (argv[0] if argv else "all").lower()
    fake_home = "--fake-home" in argv
    saved_home = os.environ.get("HOME")
    if fake_home:
        # 假 HOME 必须落在 ~/hub-l0test-fixtures 下、**不能落 /tmp**：
        # v0.13.31 P1 扫描闸把 /tmp 整前缀剔除（EXCLUDE_PATH_PREFIXES），假 HOME
        # 一旦在 /tmp，test_localprojects/test_github_projects 的夹具全被排除
        # ⇒ hermetic-clean 12 例假红（2026-09-26 排障定案：换台机器/换 TMPDIR
        # 就时绿时红，正是这种环境耦合）。与其它 L0 夹具同域即可。
        _base = Path(saved_home or str(Path.home())) / "hub-l0test-fixtures"
        _base.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="hub-clean-home-", dir=str(_base))
        os.environ["HOME"] = tmp
        os.environ["HUB_HOST_TESTS"] = "0"      # 干净 runner 上 host 层必须整层跳过
        print(f"[tier] 已把 HOME 换成空目录 {tmp}（模拟干净 CI runner）")
    try:
        herm, host, other = _split("test_*.py")
        rc = 0
        pairs = {"hermetic": [("L0-hermetic", herm)],
                 "host": [("L1-host", host)],
                 "all": [("L0-hermetic", herm), ("L1-host", host)]}.get(
            tier if tier in ("hermetic", "host", "all") else "all")
        print(f"[tier] L0 hermetic={len(herm)} 例   L1 host={len(host)} 例")
        for label, tests in pairs:
            if not tests:
                print(f"[tier] {label}: 0 例（本层无用例）")
                continue
            stream = sys.stdout
            res, nskip, ntot = _run(tests, label, stream)
            print(f"[tier] {label}: ran={ntot - nskip} skipped={nskip} failures={len(res.failures)} "
                  f"errors={len(res.errors)}")
            if not res.wasSuccessful():
                rc = 1
            if label.startswith("L0") and nskip:
                print(f"[tier] ❌ 分层放错：L0 出现 {nskip} 个 skip —— 这些用例在干净 runner 上"
                      f"什么都没测，请打 @tiers.host_only 或去掉 skipTest：")
                for t, _r in res.skipped:
                    print(f"        {t.id()}")
                rc = max(rc, 2)
        if tier == "all" and host:
            print("[tier] （L1 已随 all 一起跑过；单独看：run_tier.py host）")
        rc = max(rc, _parity())
        return rc
    finally:
        if fake_home and saved_home:
            os.environ["HOME"] = saved_home


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
