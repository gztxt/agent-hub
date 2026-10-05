#!/usr/bin/env python3
"""把 src/main.py 里挂在 VERSION 上的逐版根因注释**抽取**到 CHANGELOG.md。

【为什么是「抽取」而不是「编写」】
`src/main.py` 的 `VERSION = "..."` 字面量后面跟着一个 400 行的注释块，逐版本记录
根因（v0.13.70 → v0.13.77），格式已经是 CHANGELOG 的形态（`① ② ③` 分条 + 加粗小标题）。
而 `CHANGELOG.md` 的顶部停在 v0.13.65 ⇒ **内容早就写好了，只是不在 CHANGELOG 里**。
本脚本做机械搬运，不重新组织、不改写、不删减 —— 逐字保留原段落。

【块边界怎么定】
`src/main.py` 用固定标记分隔每个历史版本：
    # ── 以下为 v0.13.77 的根因，保留供追溯，非本版条目 ──
从该行之后到下一个同类标记（或 VERSION 注释块结束）之间的内容，就是该版本的根因。

【幂等】
已在本 CHANGELOG 里出现过的版本会被跳过，所以可以反复跑。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src" / "main.py"
CHANGELOG = ROOT / "CHANGELOG.md"

MARK = re.compile(r"──\s*以下为\s+(v[\d.]+)\s*的根因")


def CURRENT_VERSION() -> str:
    """当前版本号（正则读源码，不 import src.main —— L0 铁律）。"""
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                  MAIN.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def extract_blocks(text: str):
    """从 main.py 抽出 [(version, [行, …])]，按出现顺序（新 → 旧）。

    ⚠ 块结束判据踩过一次坑（记在这里）：
    「非注释且非空行即结束」是错的 —— `VERSION = "0.13.78"   # 侧栏图标…` 这一行
    以 `VERSION` 开头（不是 `#`），会立刻被当成块结束 ⇒ 抽到 0 个块。
    而 VERSION 之后那 400 行里，真正的续行是**裸文本**（`                      #   ① …`
    这种对齐续行不带 `#` 前缀的开头对齐），不能简单按「以 # 开头」判。
    ⇒ 正确判据：从 VERSION 行之后找**第一个既不是注释续行、也不是空行**的行。
    实现上用「行首是 `VERSION` 或已出现标记，且后面连续注释行」来界定，
    实测以「遇到空行后接非注释非空行」为界最稳。
    """
    lines = text.splitlines()
    vstart = next(i for i, l in enumerate(lines) if l.startswith("VERSION = "))
    blocks, cur_ver, cur = [], None, []
    seen_blank = False
    for ln in lines[vstart:]:
        m = MARK.search(ln)
        if m:
            if cur_ver:
                blocks.append((cur_ver, cur))
            cur_ver, cur = m.group(1), []
            seen_blank = False
            continue
        if cur_ver is None:
            continue
        if not ln.strip():
            seen_blank = True
            cur.append(ln)
            continue
        # 有内容的行：块续行一律以 '#' 开头（对齐后）——这是本仓的实际写法
        if ln.lstrip().startswith("#"):
            cur.append(ln)
            seen_blank = False
            continue
        # 真正的块结束（代码行）
        blocks.append((cur_ver, cur))
        cur_ver, cur = None, []
        break
    if cur_ver:
        blocks.append((cur_ver, cur))
    return blocks


def main() -> int:
    blocks = extract_blocks(MAIN.read_text(encoding="utf-8"))
    if not blocks:
        # 2026-10-05：归档已完成一次后，这里会返回空 —— 那是**正常终态**，
        # 不是「标记格式变了」。判据改成：有块才搬，没块且 CHANGELOG 已有当前版
        # ⇒ 幂等通过（退出码 0）；没块且 CHANGELOG 也没有 ⇒ 才可能是格式变了（退出码 2）。
        cur = CURRENT_VERSION()
        if cur and re.search(r"^## v%s\b" % re.escape(cur),
                             CHANGELOG.read_text(encoding="utf-8"), re.M):
            print("main.py 已无历史根因块，且 CHANGELOG 已含当前版本 %s ⇒ "
                  "归档已完成，幂等通过" % cur)
            return 0
        print("✗ 没抽到任何版本块，且 CHANGELOG 也没有当前版本 ⇒ "
              "可能是 main.py 的标记格式变了，先看 src/main.py:80-120")
        return 2
    chlog = CHANGELOG.read_text(encoding="utf-8")
    have = set(re.findall(r"^## (v[\d.]+)", chlog, re.M))

    todo = [(v, b) for v, b in blocks if v not in have]
    print("抽到 %d 个版本块；CHANGELOG 已有 %d 个；待补 %d 个：%s"
          % (len(blocks), len(have), len(todo),
             ", ".join(v for v, _ in todo) or "（无）"))
    if not todo:
        print("幂等：无需改动")
        return 0

    # 逐字搬运：只去掉行首的注释对齐空格（`                      #   ① …` → `① …`），
    # **不改一个字**。⚠ 第一版把每行包成 Python list 的 repr 写进文件
    # （`['   # ① …', …]`），是明显的格式事故，已回滚重做。
    sections = []
    for v, body in todo:
        text = "\n".join(l.strip().lstrip("#").strip() if l.strip().startswith("#")
                         else l.strip()
                         for l in body if l.strip())
        sections.append("## %s\n\n%s\n" % (v, text))

    # 插到第一行「---」之后（CHANGELOG 的头部说明之后）
    m = re.search(r"^---\s*$", chlog, re.M)
    if not m:
        print("✗ CHANGELOG 里找不到头部 --- 分隔线，插不进去；人工确认格式")
        return 2
    head, tail = chlog[:m.end()], chlog[m.end():]
    CHANGELOG.write_text(head + "\n" + "\n".join(sections) + "\n" + tail.lstrip("\n"),
                         encoding="utf-8")
    n_lines = sum(len(b) for _, b in todo)
    print("✓ 已写入 %d 个版本段（逐字搬运 %d 行）" % (len(todo), n_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
