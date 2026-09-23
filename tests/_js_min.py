"""JS 源码最小处理工具（闸门共用）。

为什么单独成文件：`strip_comments` 原先只住在 test_tdz_order.py 里，而
test_overlay_exclusion 的"断点单源"判据**docstring 承诺忽略注释、实现却没做**，
于是 09-23 我在注释里写了一句 `(max-width: 767px)` 就被数成第二源而误判红。
承诺与实现必须一致 ⇒ 提取共用。
"""


def strip_comments(src):
    """去注释但**保留行号**（把注释字符换成空格）。

    不这么做会被自己的注释骗到：v0.13.11 给 `_navHtml` 写的警示注释里有顶格的
    `histLoad() → renderNav()` 字样，闸门一度把它当成顶层调用而误报。
    """
    out, i, n, q = [], 0, len(src), ""
    while i < n:
        c = src[i]
        if q:
            out.append(" " if c == "\n" else c)
            if c == "\\":
                out.append(src[i + 1] if i + 1 < n else "")
                i += 2
                continue
            if c == q:
                q = ""
            i += 1
            continue
        if c in "\"'`":
            q = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)
