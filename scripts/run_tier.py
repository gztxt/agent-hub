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


def main(argv):
    tier = (argv[0] if argv else "all").lower()
    fake_home = "--fake-home" in argv
    saved_home = os.environ.get("HOME")
    if fake_home:
        tmp = tempfile.mkdtemp(prefix="hub-clean-home-")
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
        return rc
    finally:
        if fake_home and saved_home:
            os.environ["HOME"] = saved_home


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
