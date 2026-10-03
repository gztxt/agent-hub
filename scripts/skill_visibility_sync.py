#!/usr/bin/env python3
"""D6 可见性铺设：把精选技能软链到各家 Agent 的**真实发现点**。

四条铁律的出处见 work/dispatch/LEDGER-skillcenter.md：
 1. 只铺已证实的扫描根；`opencode` 不作目标（B3 已证伪 ~/.config/opencode/skill），
    它真正扫 ~/.claude/skills 与 ~/.agents/skills，本就在清单里 → 顺带覆盖。
 2. 禁改面拒绝并说明原因（jcode/hermes/qwenpaw/codebuddy）。
 3. 只软链不复制；同 realpath 幂等；指向别处则**报冲突不覆盖**。
 4. 默认 dry-run，--apply 才写。
用法：python3 scripts/skill_visibility_sync.py [--apply] [--target claude ...] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib

SRC_ROOT = pathlib.Path("/fs/1000/ftp/技术文档/skills")
HOME = pathlib.Path.home()

#: (路由, 目录, 证据, 是否禁改面)
TARGETS = [
    ("claude",    HOME / ".claude/skills",    "D1 实测 20 条", False),
    ("agents",    HOME / ".agents/skills",    "D1 12+28；opencode/jcode 均证其扫", False),
    ("pi",        HOME / ".pi/agent/skills",  "D1 实测 5 条", False),
    ("codex",     HOME / ".codex/skills",     "D1 实测 8 条", False),
    ("workbuddy", HOME / ".workbuddy/skills", "D1 实测 8 条", False),
    ("jcode",     HOME / ".jcode/skills",     "B1 报错文案证实为 global 根", True),
    ("hermes",    HOME / ".hermes/skills",    "B4 运行时主扫描面 120 条", True),
    ("qwenpaw",   HOME / ".qwenpaw/skills",   "B2 有 skill_paths 声明", True),
    ("codebuddy", HOME / ".codebuddy/skills", "D1 实测 8 条", True),
]


def curated():
    """精选集 = 自研权威副本里全部带 SKILL.md 的技能。不铺全量 ~240 条：
    技能列表要吃 prompt 预算，窄而准优于宽而全。"""
    if not SRC_ROOT.is_dir():
        return []
    return sorted(p.name for p in SRC_ROOT.iterdir()
                  if p.is_dir() and (p / "SKILL.md").is_file())


def _ok(p):
    return p.is_dir() and (p / "SKILL.md").is_file()


def plan(targets, names):
    out = {"would_create": [], "already_ok": [], "conflict": [], "blocked": [],
           "missing_src": [], "target_missing": [], "targets": []}
    for route, tdir, ev, guarded in targets:
        if guarded:
            out["blocked"].append({"route": route, "dir": str(tdir), "evidence": ev,
                                   "why": "禁改面（现有配置不在本批授权内）"})
            continue
        out["targets"].append({"route": route, "dir": str(tdir), "evidence": ev})
        if not tdir.is_dir():
            out["target_missing"].append({"route": route, "dir": str(tdir)})
            continue
        for name in names:
            src = SRC_ROOT / name
            if not _ok(src):
                out["missing_src"].append(name)
                continue
            dst = tdir / name
            if dst.is_symlink():
                if os.path.realpath(dst) == os.path.realpath(src):
                    out["already_ok"].append({"route": route, "name": name})
                else:
                    out["conflict"].append({"route": route, "name": name,
                                            "points_to": os.path.realpath(dst)})
            elif dst.exists():
                out["conflict"].append({"route": route, "name": name,
                                        "points_to": "(实体目录，非软链)"})
            else:
                out["would_create"].append({"route": route, "name": name,
                                            "link": str(dst), "src": str(src)})
    return out


def apply_plan(p):
    """真写：只建**不存在**的软链；冲突与已就位一律不碰。"""
    done, failed = [], []
    for row in p["would_create"]:
        try:
            os.symlink(row["src"], row["link"])
            done.append(row)
        except OSError as e:
            failed.append({"row": row, "err": str(e)})
    return {"created": done, "failed": failed}


def report(p, applied=None):
    print("目标（已证实的真实扫描根）:")
    for t in p["targets"]:
        print("  [OK ] %-9s %-34s %s" % (t["route"], t["dir"], t["evidence"]))
    for b in p["blocked"]:
        print("  [SKIP] %-9s %-34s %s" % (b["route"], b["dir"], b["why"]))
    print()
    print("  会新增软链 : %d" % len(p["would_create"]))
    print("  已就位     : %d（幂等 no-op）" % len(p["already_ok"]))
    print("  冲突跳过   : %d（不覆盖：覆盖会毁掉别人手工做的链接）" % len(p["conflict"]))
    for c in p["conflict"][:10]:
        print("        %s/%s -> 现在指向 %s" % (c["route"], c["name"], c["points_to"]))
    if p["missing_src"]:
        print("  源缺失     : %d %r" % (len(p["missing_src"]), p["missing_src"]))
    if p["target_missing"]:
        print("  目标目录不存在 : %r" % p["target_missing"])
    if applied is not None:
        print()
        print("已执行 --apply：建成 %d，失败 %d" % (len(applied["created"]), len(applied["failed"])))
        for f in applied["failed"]:
            print("  [!] %s：%s" % (f["row"]["link"], f["err"]))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--target", action="append")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    targets = TARGETS
    if a.target:
        want = set(a.target)
        unknown = want - {t[0] for t in TARGETS}
        if unknown:
            print("未知目标：%s（可选：%s）" % (sorted(unknown), [t[0] for t in TARGETS]))
            return 2
        targets = [t for t in TARGETS if t[0] in want]
    p = plan(targets, curated())
    applied = apply_plan(p) if a.apply else None
    if a.json:
        print(json.dumps({"plan": p, "applied": applied}, ensure_ascii=False, indent=2))
    else:
        report(p, applied)
        if not a.apply:
            print("\n（干跑，未写任何东西；加 --apply 才真建软链）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
