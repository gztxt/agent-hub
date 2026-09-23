"""静态资源闸门（纯函数，零 fastapi 依赖）。

为什么要单独成模块：这条判据此前**内联**在 `src/main.py` 的 `/static/{file_path:path}`
路由里，而 `src/main.py` 一 import 就跑 lifespan（开真库、起后台探针任务、绑端口）
⇒ 单元测试根本没法测它。抽出来之后，"哪些文件许服务"这件事第一次可离线钉住。

判据演进（都有实测红-绿对照，见 tests/verify_p1_backend.py）：
  旧①：str(f).startswith(str(static_path))
       —— 前缀不是**目录边界**：`static/../static_evil/secret.txt` 解析后
          落在 `static_evil/`，仍以 `static` 开头 ⇒ 兄弟目录可逃逸。
          实测改前 HTTP 200 且取回 CANARY 内容。
  旧②：无 .bak 判据 ⇒ hub.js.bak-* 一类历史备份件可被 HTTP 直取（实测 200 / 88779B）。
  新：is_relative_to（真目录边界）+ is_file + 名字含 ".bak" 一律拒。

备份件按工作区铁律必须留在磁盘上供回滚，因此这里只做**服务面**收敛，不删文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def resolve_serveable(static_root: Path, file_path: str) -> Optional[Path]:
    """返回可服务的绝对路径；应 404 时返回 None。

    只判"许不许服务"，不判"怎么服务"（ETag/gzip 仍在路由里）。
    """
    root = Path(static_root).resolve()
    try:
        f = (root / file_path).resolve()
        if not f.is_relative_to(root):       # 目录边界，不是字符串前缀
            return None
        if not f.is_file():                  # 目录本身、断链软链、不存在、名字过长 ENAMETOOLONG
            return None
    except (OSError, ValueError):        # 空字节、代理对、超长路径等——只能返回 None，绝不抛
        return None
    if ".bak" in f.name:                 # 备份件永不从 Web 口读
        return None
    return f


# ---------------------------------------------------------------- 缓存策略判据
# 为什么也放这里：09-23 事故证明"能不能回 304"和"许不许服务"是同一类判断，
# 必须同样可单测（内联在路由里就测不到，历史三版判据都是这么漂过去的）。
import hashlib
from typing import Tuple

_tok_cache: dict = {}     # "路径|mtime_ns|size" -> md5[:8]；键随文件变，天然失效


def content_token(f: Path) -> str:
    """产物内容的短哈希（md5 前 8 位）。构建脚本用它写模板的 ?v= 提手。"""
    st = f.stat()
    key = f"{f}|{st.st_mtime_ns}|{st.st_size}"
    tok = _tok_cache.get(key)
    if tok is None:
        tok = hashlib.md5(f.read_bytes(), usedforsecurity=False).hexdigest()[:8]
        if len(_tok_cache) > 64:
            _tok_cache.clear()
        _tok_cache[key] = tok
    return tok


def cache_policy(vtoken: str, f: Path) -> Tuple[str, str]:
    """返回 ("immutable"|"revalidate", 该用的 token)。

    immutable 的**唯一准入条件**：URL 上的 ?v= 等于文件当前内容哈希。
    这样提手写错/忘了改时自动退回 revalidate（no-cache + ETag），
    绝不会把端侧永久钉在旧体上 —— 比"只要带 ?v= 就 immutable"保守一档。
    """
    want = content_token(f)
    if vtoken and vtoken == want:
        return "immutable", want
    return "revalidate", want
