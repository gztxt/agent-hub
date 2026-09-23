"""L0 · `agent-hubctl.sh` 自调用必须能真的执行（纯静态 + bash -n，零宿主依赖）。

事故形态（09-23 真重启时实测到）：脚本 start/restart 分支收尾用 `$0 status` 打印状态，
而运维惯例是 `bash agent-hubctl.sh restart` —— 此时 `$0 == "agent-hubctl.sh"`（没有 `./`），
shell 把它当**命令**去找 ⇒ `agent-hubctl.sh: line 120: command not found`。
最阴的地方：systemctl 委托已经跑完，**退出码还是 0**，只在输出末尾安静留一行错误。
所以这属于"自证与生命周期收口"那批里剩下的最后一环：脚本自己那条尾巴没被任何测试覆盖。

守三件事：
  1 `SELF` 必须由 `BASH_SOURCE` 推绝对路径（`bash xx.sh` 与 `./xx.sh` 两种调用都对）；
  2 全文件不得出现**命令位**的 `$0`（判据：`$0 <子命令>` 紧跟 `;` 或 ` ||`）；
     提示文案里的 `$0` 不管 —— 那是给人看的示例，写成 SELF 反而绕。
  3 `bash -n` 必须过（这条挡住"改 shell 改出语法错"，与 py_compile 同理但不够，故另有条 4）；
  4 结构上 `bash "$SELF" status` 至少出现 3 次（105/109/120 那三个尾巴，删一个就红）。
"""
import re
import subprocess
import unittest
from pathlib import Path

CTL = Path(__file__).resolve().parents[1] / "agent-hubctl.sh"
SRC = CTL.read_text(encoding="utf-8")
SUBCMDS = "status|start|stop|restart|log"


class TestSelfInvoke(unittest.TestCase):
    def test_self_derived_from_bash_source(self):
        self.assertRegex(
            SRC,
            r'SELF="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)/\$\(basename "\$\{BASH_SOURCE\[0\]\}"\)"',
            "SELF 没从 BASH_SOURCE 取绝对路径（$0 在 `bash xx.sh` 形态下没有 ./）")

    def test_no_command_position_dollar_zero(self):
        bad = re.findall(r"\$0 (?:%s)(?=;| \|\|)" % SUBCMDS, SRC)
        self.assertEqual(bad, [], f"命令位仍在用 $0：{bad} —— 用 bash \"$SELF\" <子命令>")

    def test_guarded_self_calls_present(self):
        n = SRC.count('bash "$SELF" status')
        self.assertGreaterEqual(n, 3, f"SELF 自调用只剩 {n} 处（应有 3 个分支收尾）")

    def test_syntax_clean(self):
        r = subprocess.run(["bash", "-n", str(CTL)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"bash -n 失败：{r.stderr[:200]}")

    def test_message_only_dollar_zero_is_ok(self):
        """反向自检：文案位（echo/c_ok 里）的 $0 不该被误判为缺陷，别把守卫写过头。"""
        msg = [l for l in SRC.splitlines()
               if re.search(r"\$0 (?:%s)" % SUBCMDS, l) and l.lstrip().startswith(("echo", "c_ok", "#"))]
        self.assertGreaterEqual(len(msg), 1,
                                "示例文案里的 $0 也被换掉了？运维看到的提示会变成路径噪音")


if __name__ == "__main__":
    unittest.main(verbosity=2)
