#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：各 Agent 默认模型统一设置（src/modelcfg.py，v0.13.41）。

为什么这些用例必须存在：
1. **落点是白名单，不是通用写手**：每家 agent 的模型字段形状不同（json / toml /
   yaml / 命名 profile），通用编辑器必然退化成整文件重写 —— 09-07 pi 改 CCR 配置
   把同文件其它 profile 一起改坏就是那类事故。用例逐家钉"只动了目标 key"。
2. **备份是硬前置**：apply 必须在写之前留下一份 `.bak-<时间戳>`，且内容与原文
   逐字节一致；没有备份的写入等于不可回滚。
3. **预览不落笔**：preview 只能算 diff，磁盘字节不许变（这页是给人在设置页里
   看一眼再决定的，预览就写盘是本功能最不能犯的错）。
4. **口令门 fail-closed**：没配 HUB_PASSCODE ⇒ 503（不是放行），错口令 ⇒ 401，
   且拒绝原因里不许回显口令。
5. **终端注入只认白名单**：MODEL_ARGV 之外的 agent 返回 []，绝不猜 flag。
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

_L0_TMP = pathlib.Path(os.getenv("HUB_L0_TMP",
                                 pathlib.Path.home() / "hub-l0test-fixtures"))

import db                        # noqa: E402
import modelcfg                  # noqa: E402


def _mktmp(prefix: str) -> pathlib.Path:
    _L0_TMP.mkdir(parents=True, exist_ok=True)
    p = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=str(_L0_TMP)))
    return p


CLAUDE_JSON = {
    "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
            "ANTHROPIC_MODEL": "old/model-a",
            "CCR_CLAUDE_CODE_MODEL": "old/model-a"},
    "model": "old/model-a",
}
JCODE_TOML = """[provider]
default_model = "old/model-a"
default_provider = "ccr"

[providers.ccr]
type = "open-ai-compatible"
base_url = "http://127.0.0.1:3456/v1"
default_model = "old/model-a"
"""
CODEX_TOML = """# BEGIN CCR managed profile
model_provider = "claude-code-router"
model = "old/model-a"
model_catalog_json = "/home/x/.codex/ccr-model-catalog.json"
# CCR configured model = "old/model-a"
# END CCR managed profile
approval_policy = "never"
"""
PI_SETTINGS = {"defaultProvider": "openrouter-direct", "defaultModel": "old/model-a"}
PI_MODELS = {"providers": {"ccr-free": {"api": "openai-completions",
                                        "baseUrl": "http://127.0.0.1:3456/v1",
                                        "apiKey": "k-fixture",
                                        "models": [{"id": "old/model-b"}]}}}
GROK_TOML = """[model.ccr-old]
model = "old/model-a"
base_url = "http://127.0.0.1:3456/v1"
api_key = "key-fixture-ccr"

[models]
default = "ccr-old"
"""
HERMES_YAML = """model:
  default: old/model-a
  provider: some-provider
toolsets:
  - hermes-cli
"""
CCR_CONFIG = {"APIKEY": "ccr-key-fixture", "Router": {"default": "alibaba,qwen3.8-flash"}}


class _HomeFixture(unittest.TestCase):
    """造假 HOME：modelcfg 的所有落点都在 ~ 下，换 HOME 即可全隔离。"""

    def setUp(self):
        self.tmp = _mktmp("modelcfg-")
        self.home = self.tmp / "home"
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        db.init_db(self.tmp / "modelcfg.db")
        self._write(".claude/settings.json", json.dumps(CLAUDE_JSON))
        self._write(".jcode/config.toml", JCODE_TOML)
        self._write(".codex/config.toml", CODEX_TOML)
        self._write(".pi/agent/settings.json", json.dumps(PI_SETTINGS))
        self._write(".pi/agent/models.json", json.dumps(PI_MODELS))
        self._write(".grok/config.toml", GROK_TOML)
        self._write(".hermes/config.yaml", HERMES_YAML)
        self._write(".claude-code-router/config.json", json.dumps(CCR_CONFIG))

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def _read(self, rel: str) -> str:
        return (self.home / rel).read_text(encoding="utf-8")

    def _backups(self, rel: str):
        p = self.home / rel
        return sorted(p.parent.glob(p.name + ".bak-*-hub-modelcfg-*"))


class AReadCurrent(_HomeFixture):
    def test_each_agent_reads_its_own_field(self):
        for aid, want in (("claude", "old/model-a"), ("jcode", "old/model-a"),
                          ("codex", "old/model-a"), ("pi", "old/model-a"),
                          ("grok", "old/model-a"), ("hermes", "old/model-a")):
            st = modelcfg.agent_state(aid)
            self.assertTrue(st["supported"], aid)
            self.assertTrue(st["writable"], aid)
            self.assertEqual(st["current"], want, aid)

    def test_unsupported_agents_are_marked_not_writable(self):
        for aid in ("codebuddy", "qwenpaw"):
            st = modelcfg.agent_state(aid)
            self.assertFalse(st["supported"], aid)
            self.assertFalse(st["writable"], aid)
            self.assertTrue(st["reason"], f"{aid} 的不支持理由必须写清")

    def test_missing_file_does_not_raise(self):
        (self.home / ".hermes" / "config.yaml").unlink()
        st = modelcfg.agent_state("hermes")
        self.assertFalse(st["writable"], "文件没了就不可写，但不许抛异常打断设置页")
        self.assertIn("未找到", st["note"])

    def test_ccr_router_is_read_only_view(self):
        v = modelcfg.ccr_router_view()
        self.assertTrue(v["read_only"], "Router 视图必须自带只读标记（军规：不写网关配置）")
        self.assertEqual(v["router"]["default"], "alibaba,qwen3.8-flash")


class BPreviewNoWrite(_HomeFixture):
    def test_preview_leaves_bytes_untouched(self):
        before = {f: self._read(f) for f in (".claude/settings.json", ".jcode/config.toml",
                                             ".codex/config.toml", ".pi/agent/settings.json",
                                             ".pi/agent/models.json", ".grok/config.toml",
                                             ".hermes/config.yaml")}
        for aid in ("claude", "jcode", "codex", "pi", "grok", "hermes"):
            modelcfg.preview(aid, "new/model-z")
        for f, was in before.items():
            self.assertEqual(self._read(f), was, f"preview 落笔了：{f}")

    def test_preview_diff_shape(self):
        p = modelcfg.preview("jcode", "new/model-z")
        self.assertEqual(p["agent_id"], "jcode")
        self.assertEqual(p["argv"], ["--model", "new/model-z"])
        self.assertTrue(p["files"])
        self.assertTrue(any(c["to"] == "new/model-z" for c in p["files"][0]["changes"]))

    def test_bad_model_id_rejected(self):
        for bad in ("", "a b", 'x"y', "x\ny", "a" * 200):
            with self.assertRaises(modelcfg.ModelCfgError, msg=f"{bad!r} 本该被拒"):
                modelcfg.validate_model(bad)

    def test_unsupported_agent_rejected(self):
        with self.assertRaises(modelcfg.ModelCfgError):
            modelcfg.preview("codebuddy", "new/model-z")


class CApplyWritesWithBackup(_HomeFixture):
    def test_claude_writes_model_and_env_only(self):
        out = modelcfg.apply_model("claude", "new/model-z")
        d = json.loads(self._read(".claude/settings.json"))
        self.assertEqual(d["model"], "new/model-z")
        self.assertEqual(d["env"]["ANTHROPIC_MODEL"], "new/model-z")
        self.assertEqual(d["env"]["CCR_CLAUDE_CODE_MODEL"], "new/model-z")
        self.assertEqual(d["env"]["CODEXL_CLAUDE_CODE_MODEL"], "new/model-z")
        # 无关键不许被动过（整文件重写会把它冲掉）
        self.assertEqual(d["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:3456")
        baks = self._backups(".claude/settings.json")
        self.assertEqual(len(baks), 1, "必须留且只留一份备份")
        self.assertEqual(baks[0].read_text(encoding="utf-8"), json.dumps(CLAUDE_JSON),
                         "备份内容必须与原文逐字节一致")
        self.assertEqual(out["agent_id"], "claude")

    def test_jcode_surgical_toml_edit(self):
        modelcfg.apply_model("jcode", "new/model-z")
        text = self._read(".jcode/config.toml")
        self.assertIn('default_model = "new/model-z"', text)
        self.assertIn('base_url = "http://127.0.0.1:3456/v1"', text, "无关键被整段重写了")
        self.assertEqual(text.count("default_model"), 2, "provider 与 providers.ccr 各一处")

    def test_codex_only_touches_managed_block(self):
        modelcfg.apply_model("codex", "new/model-z")
        text = self._read(".codex/config.toml")
        self.assertIn('model = "new/model-z"', text)
        self.assertIn('# CCR configured model = "new/model-z"', text)
        self.assertIn('approval_policy = "never"', text, "块外内容不许动")
        self.assertEqual(text.count("# BEGIN CCR managed profile"), 1)

    def test_pi_appends_model_when_missing_from_list(self):
        modelcfg.apply_model("pi", "new/model-z")
        s = json.loads(self._read(".pi/agent/settings.json"))
        self.assertEqual(s["defaultModel"], "new/model-z")
        self.assertEqual(s["defaultProvider"], modelcfg.PI_CCR_PROVIDER)
        m = json.loads(self._read(".pi/agent/models.json"))
        ids = [x["id"] for x in m["providers"]["ccr-free"]["models"]]
        self.assertIn("new/model-z", ids, "pi 只认清单内的模型 id，缺了必须补")
        self.assertIn("old/model-b", ids, "原有清单不许被覆盖")

    def test_grok_reuses_existing_ccr_key_not_hardcoded(self):
        modelcfg.apply_model("grok", "new/model-z")
        text = self._read(".grok/config.toml")
        self.assertIn('default = "ccr-hub"', text)
        self.assertIn('api_key = "key-fixture-ccr"', text, "应复用文件里已有的 CCR key，不硬编码")
        self.assertIn('model = "new/model-z"', text)

    def test_hermes_yaml_block_scope(self):
        modelcfg.apply_model("hermes", "new/model-z")
        text = self._read(".hermes/config.yaml")
        self.assertIn("default: new/model-z", text)
        self.assertIn("provider: ccr-free", text)
        self.assertIn("- hermes-cli", text, "toolsets 块在 model 块外，不许被写坏")

    def test_hub_side_model_stored_and_injected(self):
        self.assertEqual(modelcfg.hub_model("claude"), "")
        modelcfg.apply_model("claude", "new/model-z")
        self.assertEqual(modelcfg.hub_model("claude"), "new/model-z")
        self.assertEqual(modelcfg.terminal_argv("claude", "new/model-z"), ["--model", "new/model-z"])

    def test_apply_refuses_when_file_missing(self):
        (self.home / ".hermes" / "config.yaml").unlink()
        with self.assertRaises(modelcfg.ModelCfgError) as cm:
            modelcfg.apply_model("hermes", "new/model-z")
        self.assertEqual(cm.exception.status, 404, "文件没了必须拒绝新建，不能凭空造配置")

    @unittest.skipIf(os.geteuid() == 0, "root 下 os.access(W_OK) 恒真，验不出只读位")
    def test_apply_refuses_unwritable_file_before_making_backups(self):
        """v0.13.44：09-27 报障「保存不生效」的一面 —— pi 的 settings.json 被设了
        不可变属性，旧代码先备份再写，于是**备份留了一堆、文件没改、只回一句 500**，
        用户看到的只有一句 4 秒就消失的 toast。预检必须排在备份之前：既说清原因
        （409，含解除办法），也不许在写不动的文件旁留垃圾备份。"""
        p = self.home / ".jcode" / "config.toml"
        os.chmod(p, 0o444)
        try:
            self.assertFalse(os.access(p, os.W_OK), "夹具没造出只读态，用例本身失效")
            with self.assertRaises(modelcfg.ModelCfgError) as cm:
                modelcfg.apply_model("jcode", "new/model-z")
            self.assertEqual(cm.exception.status, 409)
            self.assertIn("不可写", cm.exception.args[0])
            self.assertIn("lsattr", cm.exception.args[0], "错误信息要给得出解除办法")
            self.assertEqual(self._backups(".jcode/config.toml"), [],
                             "写不动就别落备份，否则用户目录下堆一堆没用的副本")
        finally:
            os.chmod(p, 0o644)

    def test_preview_reports_writability(self):
        """预览就要把「这个文件写不动」摆出来，别等保存时才说。"""
        p = modelcfg.preview("jcode", "new/model-z")
        for f in p["files"]:
            self.assertIn("writable", f)
        if os.geteuid() != 0:
            path = self.home / ".jcode" / "config.toml"
            os.chmod(path, 0o444)
            try:
                p = modelcfg.preview("jcode", "new/model-z")
                self.assertFalse(p["files"][0]["writable"])
            finally:
                os.chmod(path, 0o644)


class DTerminalArgvWhitelist(unittest.TestCase):
    def test_only_whitelisted_agents_get_flags(self):
        self.assertEqual(modelcfg.terminal_argv("hermes", "m"), ["-m", "m"])
        self.assertEqual(modelcfg.terminal_argv("codebuddy", "m"), [],
                         "codebuddy 的 --model 只认自有清单，不许注入 CCR 模型")
        self.assertEqual(modelcfg.terminal_argv("claude", ""), [], "没设模型就不插 flag")


class FChatChannelDefault(_HomeFixture):
    """v0.13.50：对话 / 协同子任务 / 定时任务的模型必须认「设置 → 模型」的持久化值。

    事故形态（09-28 实弹取证）：POST /api/agents/claude/chat 打给 CCR 的仍是
    qwen3.8-flash（adapter 启动时算出来的常量），而 agent_models 里存的是
    agnes/agnes-2.5-flash ⇒ 「设置页改了、新建任务照旧」。终端那条通道早就读
    DB，唯独 chat 这条没人接 —— 所以这里锁的是**调用侧**的取值顺序。
    """

    def test_explicit_request_wins(self):
        modelcfg.set_hub_model("claude", "persist/model-p")
        self.assertEqual(modelcfg.chat_model("claude", "req/model-r"), "req/model-r")

    def test_falls_back_to_persisted_model(self):
        modelcfg.set_hub_model("claude", "persist/model-p")
        self.assertEqual(modelcfg.chat_model("claude", ""), "persist/model-p")
        self.assertEqual(modelcfg.chat_model("claude", None), "persist/model-p")
        self.assertEqual(modelcfg.chat_model("claude", "   "), "persist/model-p")

    def test_empty_when_nothing_persisted(self):
        """没有持久化值时必须返回空串，让 adapter 自兜 —— 不许这里替它猜。"""
        self.assertEqual(modelcfg.chat_model("claude", ""), "")


class GDriftReport(_HomeFixture):
    """配置文件漂移体检：只读，绝不落笔。

    CCR 每次启动都会重写 ~/.claude/settings.json 的 env.ANTHROPIC_MODEL 三兄弟
    （09-28 07:06:28 实测把 agnes 改回 alibaba/qwen3.8-flash[1m]，顶层 model 不动）
    ⇒ 不带 --model 的 claude 启动会退回旧值。体检要能在变更它们的那一刻被抓到。
    """

    def test_no_drift_when_file_matches_persisted(self):
        modelcfg.apply_model("claude", "new/model-z")
        self.assertEqual(modelcfg.drift_report(), [])

    def test_env_only_rewrite_is_caught(self):
        """只改 env 三兄弟、顶层 model 不动 —— 正是 CCR 重启干的那一手。"""
        modelcfg.apply_model("claude", "new/model-z")
        before = self._read(".claude/settings.json")
        d = json.loads(before)
        d["env"]["ANTHROPIC_MODEL"] = "alibaba/qwen3.8-flash[1m]"
        d["env"]["CCR_CLAUDE_CODE_MODEL"] = "alibaba/qwen3.8-flash[1m]"
        d["env"]["CODEXL_CLAUDE_CODE_MODEL"] = "alibaba/qwen3.8-flash[1m]"
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        rep = modelcfg.drift_report()
        self.assertEqual([x["id"] for x in rep], ["claude"])
        self.assertEqual(rep[0]["hub_model"], "new/model-z")
        self.assertIn("qwen3.8-flash[1m]", rep[0]["extra"]["env.CCR_CLAUDE_CODE_MODEL"])
        # 摆出来的必须是**不一致的那几个落点**：顶层 model 仍对 ⇒ 不该出现在 owned 里；
        # 被 CCR 改掉的三兄弟必须出现（"还剩一个对得上"不能算没事）。
        self.assertNotIn("current", rep[0]["owned"])
        for k in ("env.ANTHROPIC_MODEL", "env.CCR_CLAUDE_CODE_MODEL"):
            self.assertEqual(rep[0]["owned"][k], "alibaba/qwen3.8-flash[1m]", k)

    def test_report_is_read_only(self):
        modelcfg.apply_model("claude", "new/model-z")
        modelcfg.apply_model("hermes", "old/model-a")
        (self.home / ".hermes" / "config.yaml").write_text(
            "model:\n  default: somebody/else\n  provider: ccr-free\n", encoding="utf-8")
        before = self._read(".hermes/config.yaml")
        baks_before = self._backups(".hermes/config.yaml")   # apply_model 自身的备份不算
        modelcfg.drift_report()
        self.assertEqual(self._read(".hermes/config.yaml"), before, "体检不许落笔")
        self.assertEqual(self._backups(".hermes/config.yaml"), baks_before,
                         "体检不许再新增备份（建言误做成重写）")


class EPasscodeGate(unittest.TestCase):
    """HTTP 层：口令门必须 fail-closed（复用 writeauth 同款口径）。"""

    def setUp(self):
        self.tmp = _mktmp("modelcfg-gate-")
        self.old_pc = os.environ.get("HUB_PASSCODE")
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        db.init_db(self.tmp / "gate.db")

    def tearDown(self):
        if self.old_pc is None:
            os.environ.pop("HUB_PASSCODE", None)
        else:
            os.environ["HUB_PASSCODE"] = self.old_pc
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _err(self, coro):
        """跑协程并取出它抛的 HTTPException（FastAPI 在测试里没有 TestClient 时用）。"""
        import asyncio
        from fastapi import HTTPException
        try:
            asyncio.run(coro)     # 新建 loop：unittest 主线程里 get_event_loop() 已不再自动创建
        except HTTPException as e:
            return e
        return None

    def test_missing_passcode_config_is_503(self):
        os.environ.pop("HUB_PASSCODE", None)
        e = self._err(modelcfg.settings_model_apply(
            modelcfg.ApplyIn(agent_id="claude", model="m", passcode="x"), None))
        self.assertIsNotNone(e, "没配口令必须拒绝，不能静默放行")
        self.assertEqual(e.status_code, 503)

    def test_wrong_passcode_is_401_and_never_echoed(self):
        os.environ["HUB_PASSCODE"] = "secret-pc"
        e = self._err(modelcfg.settings_model_apply(
            modelcfg.ApplyIn(agent_id="claude", model="m", passcode="guess-me"), None))
        self.assertIsNotNone(e)
        self.assertEqual(e.status_code, 401)
        self.assertNotIn("secret-pc", str(e.detail), "拒绝原因里回显了口令")


if __name__ == "__main__":
    unittest.main()
