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
