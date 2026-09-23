"""v0.13.4 L4 探活预算 —— 真 CLI 端到端 A/B 实测（可复跑）。

用户裁定（2026-09-23）：「探活测试 2 次即结束，不要反复频繁探测」。
本脚本用生产同款 `Vitals.sweep()` 对同一份 claude 画像连跑 N 轮，并把 L4 硬超时压到 3s
（claude 冷启动 >3s ⇒ 每轮必然 timeout，属四种外部条件态之一），量出「失败后到底重烧几次」：

    改前（无预算）    → 每轮都重烧，N 轮 = N 次真请求（换算 900s 周期 = 96 次/天/家）
    改后（MAX_TRIES=2）→ 只烧 2 次，其余轮次 rt_runs=0 且墙钟掉到 <1s

三条判据（两条独立信号互证，不只看代码自己的账）：
  J1 旧版真请求数 >= 3           —— 证明风暴确实存在（否则本实验无意义）
  J2 新版真请求数 == MAX_TRIES   —— 2 次即结束
  J3 新版被闸住的轮次墙钟 < 1s   —— 独立旁证：没有子进程被拉起（rt_runs 自报之外的物理证据）
  J4 会话文件旁证（条件性）      —— 仅当 L4 未被硬超时杀死时 CLI 才会落盘；落盘则必须与自报数吻合，
                                  timeout 场景下为 0 属正常，打 SKIP 不算 FAIL

跑法：cd ~/agent-hub && venv/bin/python tests/verify_rt_budget.py
      可选 env：ROUNDS（默认 4）、CDP 无关、OLD_VITALS=<改前 vitals.py 路径>
副作用：只在 /tmp 下建 src 副本与临时 state；**绝不写生产 data/vitals.json**；不重启服务。
        但会真拉起 ROUNDS×2 次 claude 子进程（每次 3s 被砍），这是实验本身要测的东西。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

AG = str(Path(__file__).resolve().parents[1])
ROUNDS = int(os.getenv("ROUNDS", "4"))
CLAUDE_PROJ = Path.home() / ".claude/projects"
PROF = {"id": "claude", "kind": "agent", "name": "Claude Code", "cli": "claude",
        "verify_argv": ["claude", "-p", "{p}", "--model", "{m}"]}

CHILD = r'''
import json, os, sys, time
sys.path[:0] = [os.environ["_SRC"], os.environ["_AG"]]
import vitals as V   # 裸导入，才真的吃到 _SRC 那份（与生产 uvicorn 启动方式一致）
prof = json.loads(os.environ["_PROF"])
v = V.Vitals()
maxtries = getattr(V, "RT_MAX_TRIES", None)
out = []
for i in range(int(os.environ["_ROUNDS"])):
    r = v.sweep([dict(prof)])
    rt = dict(v._rt.get(prof["id"]) or {})
    ev = (v._latest.get(prof["id"]) or {}).get("evidence") or {}
    out.append({"round": i + 1, "rt_runs": r.get("rt_runs"), "secs": r.get("secs"),
                "rt_state": rt.get("rt_state"), "tries": rt.get("tries"),
                "needs_rt_after": v.needs_rt(dict(prof), ev), "max_tries": maxtries})
    time.sleep(1)
print("RESULT " + json.dumps(out, ensure_ascii=False))
'''


def check(name, ok, detail=""):
    tag = "PASS " if ok else ("SKIP " if ok is None else "FAIL ")
    print(("  " if tag != "FAIL " else "  ") + tag.strip() + " " + name
          + (" :: " + str(detail) if detail else ""))
    return ok is not False


def sessions_since(t0):
    n = 0
    for p in CLAUDE_PROJ.rglob("*.jsonl"):
        try:
            if p.stat().st_mtime >= t0:
                n += 1
        except OSError:
            pass
    return n


def resolve_old_vitals():
    """改前对照版来源：env > 时间戳备份 > git 上一版。找不到就返回 None。"""
    p = os.getenv("OLD_VITALS")
    if p and Path(p).exists():
        return p, "env OLD_VITALS"
    baks = sorted(glob.glob(AG + "/src/vitals.py.bak-*"), key=os.path.getmtime)
    if baks:
        return baks[-1], "备份件 " + os.path.basename(baks[-1])
    try:
        s = subprocess.run(["git", "-C", AG, "show", "HEAD~1:src/vitals.py"],
                           capture_output=True, text=True, timeout=20)
        if s.returncode == 0 and "RT_MAX_TRIES" not in s.stdout:
            tmp = Path("/tmp/vitals_prev_git.py")
            tmp.write_text(s.stdout)
            return str(tmp), "git HEAD~1"
    except Exception:
        pass
    return None, "无可用改前对照"


def run(src_dir, label, state_file):
    if os.path.exists(state_file):
        os.remove(state_file)
    t0 = time.time()
    env = dict(os.environ, _SRC=src_dir, _AG=AG, _ROUNDS=str(ROUNDS),
               _PROF=json.dumps(PROF), VITALS_STATE=state_file,
               # 把 L4 硬超时压到 3s：claude 冷启动必然超过 ⇒ 稳定 timeout
               VITALS_RT_TIMEOUT="3", VITALS_RT_MODEL="agnes/agnes-2.0-flash")
    p = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True, timeout=900)
    print("\n===== %s =====" % label)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        print("  FAIL 子进程未返回结果：")
        print("  " + (p.stderr or "")[-1500:].replace("\n", "\n  "))
        return None
    rows = json.loads(line[len("RESULT "):])
    for r in rows:
        print("  round %d: rt_runs=%s  state=%-10s tries=%-4s needs_rt_after=%-5s  %.2fs%s" % (
            r["round"], r["rt_runs"], r["rt_state"], r["tries"],
            r["needs_rt_after"], r["secs"] or 0,
            "" if r.get("max_tries") is None else "   [RT_MAX_TRIES=%s]" % r["max_tries"]))
    total = sum(r["rt_runs"] or 0 for r in rows)
    print("  --> 真请求合计 %d 次；轮次墙钟：%s" % (
        total, [r["secs"] for r in rows]))
    return {"rows": rows, "total": total, "sessions": sessions_since(t0)}


def main():
    old_file, old_how = resolve_old_vitals()
    print("改前对照来源：%s" % old_how)
    if not old_file:
        print("FAIL: 找不到改前版本，A/B 无法成立（至少 J2 仍须通过）")

    old_src = "/tmp/src_old_vitals_rt"
    shutil.rmtree(old_src, ignore_errors=True)
    shutil.copytree(AG + "/src", old_src, ignore=shutil.ignore_patterns("__pycache__"))
    if old_file:
        shutil.copy(old_file, old_src + "/vitals.py")

    a = b = None
    if old_file:
        a = run(old_src, "A 组：改前（无预算闸门）", "/tmp/vitals-rt-old.json")
    b = run(AG + "/src", "B 组：改后（RT_MAX_TRIES=2）", "/tmp/vitals-rt-new.json")

    MAX = int(os.getenv("VITALS_RT_MAX_TRIES", "2"))
    print("\n===== 判据 =====")
    ok = True
    if a:
        ok &= check("J1 改前确有风暴（真请求 %d >= 3）" % a["total"], a["total"] >= 3)
        ok &= check("J1b 改前 needs_rt 永不关闭（末轮仍为 True）",
                    a["rows"][-1]["needs_rt_after"] is True,
                    "末轮 needs_rt_after=%s" % a["rows"][-1]["needs_rt_after"])
    else:
        check("J1 改前对照缺失，跳过", None)
    ok &= check("J2 改后恰烧 %d 次即结束（实测 %d）" % (MAX, b["total"]), b["total"] == MAX,
                "tries 轨迹=%s" % [r["tries"] for r in b["rows"]])
    gated = [r for r in b["rows"] if (r["rt_runs"] or 0) == 0]
    fired = [r for r in b["rows"] if (r["rt_runs"] or 0) > 0]
    ok &= check("J3 改后被闸轮次墙钟 < 1s、真跑轮次 > 2s（独立物理旁证）",
                bool(gated) and all((r["secs"] or 0) < 1.0 for r in gated)
                and all((r["secs"] or 0) > 2.0 for r in fired),
                "gated=%s fired=%s" % ([r["secs"] for r in gated], [r["secs"] for r in fired]))
    ok &= check("J3b 改后末轮 needs_rt 必为 False（闸门真的关）",
                b["rows"][-1]["needs_rt_after"] is False)
    if b["sessions"] == 0:
        check("J4 会话文件旁证（timeout 场景 CLI 被 3s 硬杀不落盘）", None,
              "SKIP：本注入下不落盘属预期")
    else:
        ok &= check("J4 会话文件数与自报真请求数吻合", b["sessions"] == b["total"],
                    "sessions=%d total=%d" % (b["sessions"], b["total"]))

    print()
    print("RESULT: " + ("ALL PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
