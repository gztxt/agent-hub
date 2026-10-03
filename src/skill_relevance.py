"""技能相关性打分（同步内存 BM25F）—— `/api/skill/relevant` 的快路径。

【为什么不借 SQLite FTS5】本仓已经踩过这个坑并把结论写进了代码注释：
- `kb.py:20`：「**没有 `tokenize=` 子句**（默认 unicode61），中文查询『端口』FTS 只召回 **2** 条」
- `memindex.py:431` 用的是 `fts5(tokenize='trigram')`，`memindex.py:80` 补刀
  「这不是调优问题，是**分词器决定的事实**」。
技能条目是**名称以 ASCII 为主、描述以中文为主**的混合语料，unicode61 切不出中文，
trigram 又对短查询不友好 ⇒ 这里自己切，两种语言都切得开。

【为什么不引 rank_bm25 / jieba】`requirements.txt` 里没有它们。语料只有 ~400 条、
总量几百 KB，纯 Python 打分的成本可忽略（与 `skill.py` 模块 docstring 里「实测扫描是
毫秒级」同量级）。为了 400 条数据引入两个新依赖，是拿维护成本换零收益。

【打分口径：BM25F 两字段】标准 BM25 把整篇文档当一个袋子，name 和 description 等权，
于是「描述里出现 3 次 query 词」会盖过「名字就叫这个」。技能匹配里**名字命中远比描述命中
可信**（`agent-dispatch` 就是唯一该叫这个名字的东西），所以用 BM25F：
每个字段先各自算长度归一化，权重再乘上去（W_NAME > W_DESC）。

【不做的事】本模块**不发网络请求**、不读磁盘、不碰 jev。它是纯函数：
输入 query + 已扫好的 items，输出带分数和命中词的排序。外部精排在 `jev_client.py`，
由端点决定要不要调、调不通就原样退回本层结果——本层永远给得出答案，所以它是可测的。
"""
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# ── BM25F 参数（取值是常规默认；W_NAME 是本项目自己的判断，见模块 docstring）──
K1 = 1.5
B = 0.75
W_NAME = 2.5
W_DESC = 1.0

#: 名称命中权重与描述命中权重，供端点回显，避免「权重写死在别处、这里改了没人知道」
FIELD_WEIGHTS = {"name": W_NAME, "desc": W_DESC}

#: CJK 字符类。用 \uXXXX 转义而不是直接写「㐀-䶿」这类字面量：
#: 字面量在源码里会因编辑器/终端/复制粘贴而丢字或变码位，而正则悄悄少一段区间不会报错，
#: 只会让某些汉字静默切不出词——这正是本模块最不该发生的那种失败。
_CJK_CLASS = (
    "\u3400-\u4dbf"   # 扩展 A
    "\u4e00-\u9fff"   # 基本区
    "\uf900-\ufaff"   # 兼容汉字
    "\u3040-\u30ff"   # 日文假名
    "\uac00-\ud7af"   # 谚文
)
_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
_SEP_RE = re.compile(r"[._+-]+")
_CJK_RE = re.compile("[" + _CJK_CLASS + "]+")

#: query 分词后少于此数 ⇒ 结果不可信，端点要回 short_query=true 让调用方自己决定要不要用
#: （与 `memindex.py` 的 `is_short_query()`、`short_query_fallback` 同一口径）
SHORT_QUERY_TOKENS = 2


def tokenize(text: Any) -> List[str]:
    """切词：ASCII/数字按词；CJK 出**相邻二字**（长度为 1 时出该单字）。

    【为什么不同时出单字】2026-10-03 真实盘实测（365 条语料）出过一次假证据：
    同时出单字+二字时，“能/不/登/录/态” 这类高频字各自带一点 IDF，20 个 query 词累加，
    把 `arkcli-auth`/`arkcli-deploy` 抬到“登录态加载不出但能新建”的前四名——
    **三条真实查询的 `name命中` 全为空**，也就是没有任何一条是靠名字命中的，全是噪声。
    只出二字后噪声词没了，“端口/登录/记忆召回”这类真实词反而拉得开。
    单字查询（`q="端"`）由长度为 1 的回落分支接住，不会被漏。

    【为什么还要拆连字符】同一批实测：`_LATIN_RE` 把 `-` 当词内字符，于是
    `agent-dispatch` 是一个不可分的 token，而查询里写的是 `dispatch`——
    **对全部 365 条技能，name命中恒为空**。所以带分隔符的词额外拆出子词。
    两处实测教训是同一类：**分词器的错误不抛异常，只安静地少召回**，所以只能在真实盘上验。
    """
    if not text:
        return []
    s = str(text).lower()
    out: List[str] = []
    for m in _LATIN_RE.finditer(s):
        w = m.group(0).strip("._+-")
        if not w:
            continue
        out.append(w)
        # 本仓技能名几乎全是连字符命名（agent-dispatch / book-to-skill / skill-creator）。
        # 实测：不拆的话 “skill dispatch” 这类查询对它们 **name命中恒为空**——因为
        # 整名是一个不可分的 token，查询里的 “skill” 永远匹配不上。
        if _SEP_RE.search(w):
            out.extend(p for p in _SEP_RE.split(w) if len(p) >= 2)
    for m in _CJK_RE.finditer(s):
        run = m.group(0)
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def build_index(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, float], Dict[str, float]]:
    """预计算：(docs, idf, avg_len)。语料不变时可以复用，避免每次请求重算 IDF。

    `docs[i]["tf"]["name"|"desc"]` 是该条的两字段词频，`len` 是字段长度（BM25F 归一化要用）。
    """
    docs: List[Dict[str, Any]] = []
    for it in items:
        nt = tokenize(it.get("name"))
        dt = tokenize(it.get("description"))
        docs.append({"item": it,
                     "tf": {"name": Counter(nt), "desc": Counter(dt)},
                     "len": {"name": len(nt), "desc": len(dt)}})
    n = len(docs)
    df: Counter = Counter()
    for d in docs:
        for f in ("name", "desc"):
            df.update(set(d["tf"][f].keys()))
    idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()} if n else {}
    avg = {f: (sum(d["len"][f] for d in docs) / n if n else 0.0) for f in ("name", "desc")}
    return docs, idf, avg


def score(docs: List[Dict[str, Any]], idf: Dict[str, float],
          avg: Dict[str, float], q_tokens: List[str]) -> List[Dict[str, Any]]:
    """逐条打分。返回与 docs 同序的 `{item, score, matched, matched_in}`。

    `matched` / `matched_in` 是给「相关性实验室」看的**可解释性**：
    面板要能回答「为什么这条排第一」，只给一个分数就答不上来。
    """
    out: List[Dict[str, Any]] = []
    for d in docs:
        total = 0.0
        matched: List[str] = []
        in_name: List[str] = []
        for t in q_tokens:
            w = idf.get(t)
            if not w:
                continue
            for f in ("name", "desc"):
                tf = d["tf"][f].get(t, 0)
                if not tf:
                    continue
                # avg[f] 为 0（该字段全空）时把归一化系数兜成 1，避免除零把整条打成 0 分
                norm = 1.0 - B + B * (d["len"][f] / avg[f] if avg[f] else 1.0)
                total += FIELD_WEIGHTS[f] * w * tf * (K1 + 1.0) / (tf + K1 * norm)
                if t not in matched:
                    matched.append(t)
                if f == "name" and t not in in_name:
                    in_name.append(t)
        out.append({"item": d["item"], "score": round(total, 4),
                    "matched": matched, "matched_in": in_name})
    return out


def rank(query: str, items: List[Dict[str, Any]], n: int = 5) -> Dict[str, Any]:
    """把 items 按 query 相关度排序，返回前 n 条 + 足够的降级说明。

    **三条不静默的规矩**（承接 `skill.py` 模块 docstring 的「失败表态纪律」）：
    1. 查询切不出词 ⇒ 回 `why`，不返回一份看着像「没找到相关内容」的空表；
    2. 语料为空 ⇒ 回 `why`，不说成「都不相关」；
    3. 一条都没命中 ⇒ 回 `why`，不说成「匹配成功但为空」。
    """
    q_tokens = tokenize(query)
    base: Dict[str, Any] = {
        "query": query,
        "query_tokens": q_tokens,
        "corpus": len(items),
        "short_query": len(q_tokens) < SHORT_QUERY_TOKENS,
        "weights": dict(FIELD_WEIGHTS),
    }
    if not q_tokens:
        return dict(base, items=[], why="查询切词后为空（无 ASCII 词也无 CJK 字），无从打分")
    if not items:
        return dict(base, items=[], why="语料为空：没有任何技能条目进入打分")

    docs, idf, avg = build_index(items)
    scored = score(docs, idf, avg, q_tokens)
    scored.sort(key=lambda x: (-x["score"], str(x["item"].get("name") or "")))
    hit = [s for s in scored if s["score"] > 0]
    if not hit:
        return dict(base, items=[], why="没有词命中任何技能（查询词全部不在技能名称与描述中）")
    top = hit[:max(1, int(n))]
    return dict(base, items=top, total_hits=len(hit),
                top_score=top[0]["score"], lowest_score=top[-1]["score"])
