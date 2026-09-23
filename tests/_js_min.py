"""JS 源码最小处理工具（闸门共用）。

为什么单独成文件：`strip_comments` 原先只住在 test_tdz_order.py 里，而
test_overlay_exclusion 的"断点单源"判据**docstring 承诺忽略注释、实现却没做**，
于是 09-23 我在注释里写了一句 `(max-width: 767px)` 就被数成第二源而误判红。
承诺与实现必须一致 ⇒ 提取共用。

09-24 修掉的两个真缺陷（都是"闸门静默失效"级，不是洁癖）：
  ① 正则字面量：`/[&<>"']/g` 这类 HTML 转义表里的引号会**误开字符串态**，而旧实现在字符串态
     里把换行替换成空格 ⇒ 一路吞到下一个引号，01 片实测**丢 204 行**（442→238）。
     依赖它的两只闸门（顶层 TDZ 顺序扫描、断点单源计数）从此在**残缺文本**上跑，
     是典型的"看着绿、其实在漏"。
  ② 判定顺序：引号检查排在注释检查之前。撇号一旦出现在不该出现的位置，后面整段被当字符串吃掉。
修法：注释判定提到引号之前；单独识别正则字面量（含 `[…]` 字符类里的引号）；
普通 `'`/`"` 字符串遇到裸换行**立即退出字符串态**（JS 里单双引号串不允许跨行，跨行即误判，
宁可当场恢复也不静默吞行）；模板串 ` `…` ` 允许跨行，照旧保留换行。
"""

# 这些前缀之后出现的 `/` 是正则字面量，而不是除法
_REGEX_PREV = set("=(,:[!&|?{};+-*%~^<>\n")


def strip_comments(src):
    """去注释但**严格保行号**（输出与输入行数必须相等，见 tests/test_js_min_lossless.py）。

    为什么要保行号：多只闸门拿 `filename:lineno` 比对（TDZ 声明顺序、顶层调用检测），
    一旦压缩行数，比对结果就是错的。
    """
    out = []
    i, n = 0, len(src)
    prev_sig = "\n"          # 上一个"显著字符"（用于判断 / 是除法还是正则）

    def put(s):
        out.append(s)

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        # ── 1) 行注释 // ────────────────────────────────────────────
        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                put(" ")
                i += 1
            continue

        # ── 2) 块注释 /* … */（保留内部换行，保行数）────────────────
        if c == "/" and nxt == "*":
            put("  ")
            i += 2
            while i < n:
                if src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    put("  ")
                    i += 2
                    break
                put("\n" if src[i] == "\n" else " ")
                i += 1
            continue

        # ── 3) 正则字面量 /…/flags（其中的引号不得开字符串态）────────
        if c == "/" and (prev_sig in _REGEX_PREV or prev_sig == ""):
            # 保守：只在本行剩余部分确实存在未转义收尾 `/` 时才当正则，
            # 否则按除法处理（避免把 `a / b // c` 里的除号吃成长段）
            j, in_class, closed = i + 1, False, False
            while j < n and src[j] != "\n":
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if in_class:
                    if ch == "]":
                        in_class = False
                elif ch == "[":
                    in_class = True
                elif ch == "/":
                    closed = True
                    break
                j += 1
            if closed:
                for k in range(i, j + 1):
                    put(src[k])          # 原样输出（不是注释，也不含换行）
                prev_sig = "/"
                i = j + 1
                while i < n and src[i].isalpha():   # flags
                    put(src[i])
                    i += 1
                continue
            # 落到这里 ⇒ 当普通字符（除法）处理

        # ── 4) 模板串：允许跨行，换行必须保留 ───────────────────────
        if c == "`":
            put("`")
            i += 1
            while i < n:
                if src[i] == "\\":
                    put(src[i]); put(src[i + 1] if i + 1 < n else "")
                    i += 2
                    continue
                if src[i] == "`":
                    put("`")
                    i += 1
                    break
                put(src[i])
                i += 1
            prev_sig = "`"
            continue

        # ── 5) 单/双引号串：遇裸换行立即退出（JS 不允许跨行 ⇒ 跨行即误判）─
        if c in "\"'":
            q = c
            put(q)
            i += 1
            while i < n:
                if src[i] == "\\":
                    put(src[i]); put(src[i + 1] if i + 1 < n else "")
                    i += 2
                    continue
                if src[i] == q:
                    put(q)
                    i += 1
                    break
                if src[i] == "\n":
                    # 旧实现在这里把换行压成空格并一路吞下去 ⇒ 丢 204 行的元凶。
                    # 现在：原样吐出换行、结束字符串态，从下一行继续正常解析。
                    put("\n")
                    i += 1
                    break
                put(src[i])
                i += 1
            prev_sig = q
            continue

        # ── 6) 普通字符 ─────────────────────────────────────────────
        put(c)
        if not c.isspace():
            prev_sig = c
        elif c == "\n":
            prev_sig = "\n"
        i += 1

    return "".join(out)
