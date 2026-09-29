"""测试分层（tier）唯一真相源 —— 约定见 tests/README.md。

三层：
  L0 hermetic  tests/test_*.py 里没打 @host_only 的用例。零宿主依赖：不读真盘 ~/.claude、
               不起服务、不打网络、不 fork pty、不 import src.main。换任何机器（含干净 CI
               runner）结论必须一模一样，且**不许出现 SKIP**（出现即分层放错，闸门判 FAIL）。
  L1 host      tests/test_*.py 里打了 @host_only 的用例。断言依赖本机真实仓库形态
               （~/.grok/sessions、~/.claude/projects、~/.jcode/sessions、~/.qoder/projects、
               ~/.hermes/state.db、~/.codex/state_5.sqlite，以及 /fs 下的真目录）。
               换机时给**显式 SKIP + 理由**，绝不静默通过。
  L2 live      tests/verify_*.py 与 tests/probe_*.py —— 需要服务在跑，天然不被
               `unittest discover -p "test_*.py"` 收进来。单跑方式见 README。

跑法（仓根）：
  HUB_HOST_TESTS=0 venv/bin/python -m unittest discover -s tests -p "test_*.py" -v   # L0
  HUB_HOST_TESTS=1 venv/bin/python -m unittest discover -s tests -p "test_*.py" -v   # L0+L1
  bash scripts/run_tests.sh hermetic|all|probe <file>                                # 同一套口径的封装
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

#: L1 判据：这六个都在，才算「像本机」——缺任何一个，对应那家仓库的断言就不可判定
HOST_REPOS = (".grok/sessions", ".claude/projects", ".jcode/sessions",
              ".qoder/projects", ".hermes/state.db", ".codex/state_5.sqlite")

#: SKIP 理由必须自带因果与解法（-v 下逐条打印，闸门汇总里也报数）
HOST_SKIP_REASON = (
    "SKIP(host-dependent): 本用例断言本机真实仓库形态（~/.grok/sessions ~/.claude/projects "
    "~/.jcode/sessions ~/.qoder/projects ~/.hermes/state.db ~/.codex/state_5.sqlite 及 /fs 真目录），"
    "换机（干净 runner）上既不能算通过也不能算失败 ⇒ 显式跳过。"
    "要在本机强制运行：HUB_HOST_TESTS=1；要跳过：HUB_HOST_TESTS=0。")


def host_like() -> bool:
    """当前环境是否具备跑 L1 的条件。HUB_HOST_TESTS 显式覆盖自动探测。"""
    flag = (os.getenv("HUB_HOST_TESTS") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    home = Path.home()
    return all((home / p).exists() for p in HOST_REPOS)


#: 打在 class 或 method 上：@tiers.host_only
#: 除 unittest 的 skip 外，再打一个自己的小旗 —— 否则“哪些用例是 host”这件事
#: 只能从 skip 输出里猜，而 L0 零跳过这道闸就没法机器判。
def host_only(obj):
    obj = unittest.skipUnless(host_like(), HOST_SKIP_REASON)(obj)
    try:
        obj.__hub_host_only__ = True
    except (AttributeError, TypeError):        # 不可写属性的对象（少见）：宁可不 tagging 也不报错
        pass
    return obj


def is_host(obj) -> bool:
    return bool(getattr(obj, "__hub_host_only__", False))


def missing_host_paths() -> list:
    """给闸门脚本报告「为什么跳过 L1」用，只报路径存在性，不读内容。"""
    home = Path.home()
    return [str(home / p) for p in HOST_REPOS if not (home / p).exists()]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ── L0 夹具收尾（v0.13.58）───────────────────────────────────────────────────
#: 为什么要它：L0 夹具根默认在 ~ 下（不能用 /tmp —— /tmp 是精度闸的排除路径前缀，
#: 夹具若住那儿会被先杀光，测不到排除逻辑本身）。但**造完从不删**，实测累积到
#: 230MB / 2402 个目录（tests/test_ghsettings、test_github_projects、test_hublog、
#: test_localprojects、test_modelcfg、test_prefs 六个文件各自造）。后果不是"占磁盘"，
#: 而是每个夹具都带 .git ⇒ 项目扫描把它们当真实项目 ⇒ 本机项目数 176（应 ≈44）⇒
#: L1 精度闸与 prepush 闸门一起变红。**闸门红是真信号，不该靠改断言消掉。**
def l0_fixture_root() -> Path:
    """夹具根（与各测试文件的 _L0_TMP 同一口径，单一真相源）。"""
    return Path(os.getenv("HUB_L0_TMP", str(Path.home() / "hub-l0test-fixtures")))


def l0_fixture_sweep(testcase: unittest.TestCase) -> Path:
    """造一个夹具根下的唯一子目录，并登记「用例结束即删」。

    用 ``testcase.addCleanup`` 而不是 atexit / 模块级 tearDown：进程被杀（闸门超时、
    Ctrl-C、OOM）时 atexit 不跑，而 ``addCleanup`` 在用例正常结束时一定会跑。
    只删自己造的那个子目录，不碰别人的（并发跑多个测试文件时互不干扰）。
    """
    import shutil
    import tempfile
    root = l0_fixture_root()
    root.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="", dir=str(root)))
    testcase.addCleanup(shutil.rmtree, str(d), True)
    return d


def l0_fixture_register(d) -> Path:
    """登记一个刚造好的夹具目录，进程退出时统一删（见 l0_fixture_cleanup）。

    为什么不直接在造的地方 rmtree：这些测试的 ``_mktmp(prefix)`` 是模块级函数，
    拿不到 ``self.addCleanup``（夹具在函数里造、用例还在跑）。折中：登记 + atexit。
    进程被 kill -9 时残留最多一份 —— 比修复前「每次运行都留、无限累积到 2402 个」
    已经收敛；且残留只影响磁盘，不影响任何断言（扫描侧另有 EXCLUDE_DIR_NAMES 兜底）。
    """
    _L0_CREATED.append(Path(d))
    return Path(d)


def l0_fixture_cleanup() -> None:
    """删掉本进程造出的全部夹具目录；根目录空了也一并收掉。"""
    import shutil
    for d in list(_L0_CREATED):
        shutil.rmtree(str(d), ignore_errors=True)
    _L0_CREATED.clear()
    root = l0_fixture_root()
    try:
        next(root.iterdir())
    except StopIteration:
        try:
            root.rmdir()
        except OSError:
            pass
    except OSError:
        pass


#: 本进程造出的夹具目录（l0_fixture_register 追加）
_L0_CREATED = []

import atexit  # noqa: E402
atexit.register(l0_fixture_cleanup)
