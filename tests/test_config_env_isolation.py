#!/usr/bin/env python3
"""L0 hermetic 测试：`.env` 覆盖模式致影子实例隔离失效（判例 105 坑2 的修复闸门）。

**事故**：2026-10-09 跑 L2 验证时给影子实例传 `DATA_DIR=work/shadow PORT=3205`，
**完全没生效** ——影子照样写生产 `data/agents.db`，测试账进了生产库。
根因：`src/config.py` 顶部 `load_dotenv(env_path, override=True)` **无条件覆盖**
⇒ `.env` 里的值优先于调用方传进来的环境变量。
而 `run_dualstack.py` 的 `_port()` 早就做对了（`os.getenv("PORT")` 优先），
**只有config.py 反着来** ⇒ 同一进程里端口能覆盖、数据目录不能。

**修法**：`ENV_OVERRIDABLE` 白名单（`DATA_DIR`/`LOG_DIR`/`PORT`/`HOST`）优先于 `.env`，
**其余键（全部凭据）仍由 `.env` 兜底**，生产行为逐字不变。

**为什么是纯静态断言而不跑真进程**：真跑要import `config` 从而加载真实 `.env`
（含真凭据），测试就不hermetic 了。本文件改为**在子进程里用 `env -i` 清空环境**
跑一段最小片段，验证「快照取样在 load_dotenv 之前」这个承重顺序 ——
顺序错了静态就能看出来（`_env_override` 若出现在 `load_dotenv` 之后即判红）。

**这个坑的教训形态**（本文件第一版就中过）：修复第一版把快照写在 `load_dotenv` **之后**，
语法正确、日志照打、看着像生效了，实际读的全是 `.env` 的值。
⇒ 「看起来生效」必须用**反例**证明：清空环境时快照必须为空。
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests"))

import tiers  # noqa: E402

CONFIG = _REPO / "src" / "config.py"
SRC = CONFIG.read_text(encoding="utf-8")

#: 白名单成员。**只增不改**，且每加一个都要先回答「误设它会不会写坏生产」。
EXPECTED = {"DATA_DIR", "LOG_DIR", "PORT", "HOST"}


def _tuple_literal_after(src: str, name: str) -> str:
    m = re.search(rf"{name}\s*=\s*\(([^)]*)\)", src)
    return m.group(1) if m else ""


class TestWhitelistShape(unittest.TestCase):
    """白名单的形状：成员、命名、注释。"""

    def test_whitelist_exists_and_is_tuple(self):
        self.assertRegex(SRC, r"ENV_OVERRIDABLE\s*=\s*\(",
                         "缺 ENV_OVERRIDABLE 白名单 ⇒ 隔离开关不存在")

    def test_members_exact(self):
        got = set(re.findall(r'"([A-Z_]+)"', _tuple_literal_after(SRC, "ENV_OVERRIDABLE")))
        self.assertEqual(got, EXPECTED,
                         f"白名单成员变了：{sorted(got)} ≠ {sorted(EXPECTED)}。"
                         "增删成员必须先确认「误设它会不会写坏生产」")

    def test_no_credential_in_whitelist(self):
        """**凭据类绝不能进白名单** —— 一旦可被环境变量覆盖，
        任何 `FOO=xxx venv/bin/python` 都能夺权改掉生产凭据来源。"""
        cred_markers = ("TOKEN", "KEY", "PASSCODE", "SECRET", "AUTH")
        for m in re.findall(r'"([A-Z_]+)"', _tuple_literal_after(SRC, "ENV_OVERRIDABLE")):
            for bad in cred_markers:
                self.assertNotIn(bad, m, f"白名单含凭据类键 {m} —— 越权")

    def test_override_true_still_present(self):
        """`.env` 的 `override=True` **必须保留**。

        反面：有人可能顺手改成 `override=False`「修好隔离」——
        那会让所有「不起 systemd 就裸跑」的场景丢掉全部凭据，
        从「隔离不了」变成「全挂」。白名单是唯一正确解法。
        """
        self.assertRegex(SRC, r"load_dotenv\(env_path,\s*override=True\)",
                         "override=True 被改掉了？凭据会失去 .env 兜底，这是更大的事故")


class TestSnapshotOrder(unittest.TestCase):
    """**本修复的承重点**：快照必须在 `load_dotenv` 之前取。"""

    def test_snapshot_before_load_dotenv(self):
        """快照必须在**真实调用**的 `load_dotenv` 之前取。

        ⚠️定位必须用「行首无缩进的真实调用」，不能用 `SRC.find("override=True")`——
        注释里第一次出现这个词的位置比代码早（第一版断言就是这么写的，
        于是把正确的代码判成红）。这与判例 105 记的「断言取样范围比被测对象窄/宽
        都会假红」是同一族：**定位锚点必须锚在代码上，不是锚在提到它的文字上**。
        """
        # 真实调用：行首可能有缩进，但前面不能是 '#'
        m_call = re.search(r"^\s*load_dotenv\(", SRC, re.M)
        self.assertIsNotNone(m_call, "找不到真实 load_dotenv 调用行")
        m_snap = re.search(r"^_env_override: Dict", SRC, re.M)
        self.assertIsNotNone(m_snap, "找不到 _env_override 快照定义行")
        self.assertLess(m_snap.start(), m_call.start(),
                        "❌ 快照在 load_dotenv **之后** ⇒ 读到的全是 .env 的值，"
                        "隔离开关看似存在实则一行都没生效（第一版就中过）")

    def test_reads_from_os_environ_not_after(self):
        """快照必须读 `os.environ`（调用方真传进来的值），不是从 `.env` 文件解析。"""
        self.assertRegex(SRC, r"_env_override[^=]*=\s*\{[^}]*os\.environ",
                         "快照必须从 os.environ 取调用方传的值")

    def test_config_reads_snapshot_first(self):
        """Config.__init__ 里 4 个隔离旋钮必须**先查快照**再退回 os.getenv。"""
        for key in EXPECTED:
            self.assertRegex(SRC, rf'_env_override\.get\("{key}"\)',
                             f"{key} 未优先取快照 ⇒ 显式环境变量仍被 .env 覆盖")
        # data_dir / log_dir / port / host 四个读取点
        for line in ("self.port = int(_env_override", "self.host = _env_override",
                     "self.data_dir = Path(_env_override", "self.log_dir = Path(_env_override"):
            self.assertIn(line, SRC, f"读取点未走快照：{line}")


class TestBehaviourInCleanEnv(unittest.TestCase):
    """行为判据：清空环境时快照必须为空（反例证明「看起来生效」不成立）。"""

    def _run(self, env_extra: str):
        """在 `env -i` 的净环境里 import config，只打印快照内容。"""
        py = (
            "import os,sys;sys.path.insert(0,'src');"
            "import config;"
            "print('SNAP=' + repr(config._env_override))"
        )
        e = {"PATH": "/usr/bin:/bin"}
        if env_extra:
            e.update(env_extra)
        r = subprocess.run(
            [str(_REPO / "venv" / "bin" / "python"), "-c", py],
            cwd=str(_REPO), env=e, capture_output=True, text=True, timeout=60,
        )
        return r.stdout

    def test_clean_env_snapshot_empty(self):
        """**反例证明**：什么都不设 ⇒ 快照为空 ⇒ 生产完全由 `.env` 决定。

        这条是第一版假绿的照妖镜：若快照放在 load_dotenv 之后，
        它会拿到 `.env` 的 4 个值并打印出来，看着「有隔离能力」，实则无效。
        """
        out = self._run("")
        self.assertIn("SNAP={}", out, f"净环境下快照应为空，实际：{out.strip()[-300:]}")

    def test_explicit_env_wins_in_snapshot(self):
        """显式传入时快照必须拿到**调用方的值**（不是 .env 的同值键）。"""
        out = self._run({"DATA_DIR": "/tmp/__iso_probe__", "PORT": "3205"})
        self.assertIn("__iso_probe__", out, f"快照未拿到调用方的 DATA_DIR：{out.strip()[-300:]}")
        self.assertIn("3205", out, f"快照未拿到调用方的 PORT：{out.strip()[-300:]}")


class TestRunDualstackAlreadyCorrect(unittest.TestCase):
    """不回归：`run_dualstack.py` 的端口优先级本来就对，不要被「顺手改成一致」而改坏。"""

    def test_run_dualstack_prefers_environ(self):
        src = (_REPO / "run_dualstack.py").read_text(encoding="utf-8")
        self.assertRegex(src, r'if os\.getenv\("PORT"\)',
                         "run_dualstack 应仍优先读 os.getenv")

    @tiers.host_only
    def test_doc_records_the_pgrep_incident(self):
        """影子脚本头部必须记着「禁 pgrep/pkill 停影子」这条纪律。

        钉它的理由：2026-10-09 收工时 `kill $(pgrep -f run_dualstack.py | head -5)`
        **误杀了生产进程**（生产与影子同脚本名）—— 那是把 `pkill -f` 换了个名字。

        **分层（2026-10-10 修）**：被钉的 `work/l2-shadow-inject-verify.sh` 是本机
        scratch（`work/` 被 `.gitignore` 排除、不入库）⇒ **干净 runner 上不存在**。
        原先无守卫直接 `read_text` ⇒ 干净克隆/CI 会 `FileNotFoundError`（L0 判红），
        而加 `skipTest` 又会触发 L0「零跳过」闸（`run_tier.py` 判「分层放错」）。
        ⇒ 正确归类是 `@tiers.host_only`：归 L1，干净 runner 整层跳过、不计入 L0；
        再补一道存在性守卫，让「像本机但 scratch 已被清理」也不误判。
        """
        sh_path = _REPO / "work" / "l2-shadow-inject-verify.sh"
        if not sh_path.exists():
            self.skipTest(f"本机 scratch 不存在：{sh_path}（work/ 不入库）⇒ 无对象可钉")
        sh = sh_path.read_text(encoding="utf-8")
        self.assertIn("pgrep", sh)
        self.assertRegex(sh, r"pgrep[^\n]*同(一个)?脚本名|同一个脚本名",
                         "影子脚本必须写明「pgrep 分不出生产与影子」这条成因")
        self.assertIn('kill "$SHADOW_PID"', sh, "影子必须用精确 PID 停")


class TestKnownGapLogDirNotIsolated(unittest.TestCase):
    """**已知缺口登记**：只覆盖 `DATA_DIR` 时，`log_dir` **不随**它隔离。

    `config.py:93` 的默认推导 `self.data_dir / "logs"` 被 `.env` 的**绝对** `LOG_DIR`
    遮蔽 —— `_env_override` 里没有 `LOG_DIR` 时回退到 `os.getenv("LOG_DIR", ...)`，
    而 `.env:22` 已把它钉成 `<仓库>/data/logs`。⇒ `DATA_DIR=work/shadow`（不改 LOG_DIR）
    起影子实例时 `data_dir` 隔离了、`log_dir` **仍指生产**。

    本条**钉住现状**、非「必须修」—— 修不修是另一个批次对「隔离契约」的决定
    （`config.py:23` 白名单注释明写「只增不改」），处置口径同
    `test_xss_untrusted_render.py::TestReachabilityDocumented.test_validate_dag_has_no_charset_gate_yet`。
    **缺口的现实影响面小**：全仓 `config.log_dir` 除 `mkdir` 外无消费者。
    登记见 `PENDING-TASKS.md`（ENV_OVERRIDABLE 那条下的「残留缺口」）。

    **缺口修好后本条必红**：届时把断言从「= 假 `.env` 的绝对 LOG_DIR」改为
    「= DATA_DIR 之下的 `logs`」，并同步 PENDING-TASKS 条目。

    为什么建临时 `<T>/.env` 而不 import 真 `config`：真 `.env` 含凭据 ⇒ import 它就
    不 hermetic。这里把真 `config.py` **原样拷进** `<T>/src/`、配一个只含
    `DATA_DIR`/`LOG_DIR` 的假 `.env` —— 测的仍是**真源码**，只是换了不含凭据的 `.env`。
    """

    def _run(self, tmp: str, override: dict) -> dict:
        """在 <T> 里跑真 config.py（配假 .env），只回 {'DD':…, 'LD':…} 两行。"""
        t = pathlib.Path(tmp)
        (t / "src").mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO / "src" / "config.py", t / "src" / "config.py")
        prod_data = t / "prod" / "data"
        (t / ".env").write_text(
            f"DATA_DIR={prod_data}\nLOG_DIR={prod_data / 'logs'}\n", encoding="utf-8")
        py = ("import sys;sys.path.insert(0,'src');import config;c=config.Config();"
              "print('DD='+str(c.data_dir));print('LD='+str(c.log_dir))")
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp}  # HOME=<T> ⇒ 不读真 ~/.config
        env.update(override)
        r = subprocess.run([str(_REPO / "venv" / "bin" / "python"), "-c", py],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
        return dict(ln.split("=", 1) for ln in r.stdout.splitlines()
                    if ln.startswith(("DD=", "LD=")))

    def test_data_dir_override_leaves_log_dir_on_env_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow = str(pathlib.Path(tmp) / "shadow")
            prod_logs = str(pathlib.Path(tmp) / "prod" / "data" / "logs")
            got = self._run(tmp, {"DATA_DIR": shadow})
            # 前置：DATA_DIR 覆盖**是**生效的（这半条不是缺口，是绿臂）
            self.assertEqual(got.get("DD"), shadow,
                             f"DATA_DIR 覆盖应生效；实得 {got.get('DD')}")
            # 缺口本身：log_dir 没跟着 DATA_DIR 走，仍取假 .env 的绝对 LOG_DIR
            self.assertEqual(
                got.get("LD"), prod_logs,
                "🔔 log_dir 不再被 .env 的绝对 LOG_DIR 拖走 —— LOG_DIR 缺口疑似已修："
                "请把本条断言改为 `LD == <DATA_DIR>/logs`，并同步 PENDING-TASKS 登记")


if __name__ == "__main__":
    unittest.main()