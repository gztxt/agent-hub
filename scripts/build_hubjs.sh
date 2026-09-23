#!/usr/bin/env bash
# static/hub/NN-*.js  →  static/hub.js（纯拼接，不压缩不转译不重排）
# 为什么还要留一个单文件：模板/缓存键/前端探针都指向 /static/hub.js
# 拆分只发生在**源码组织**层，产物形态一律不变 ⇒ 手机端零风险
# 漂移由 tests/test_hubjs_split.py 判红：拼接结果与仓库里的 hub.js 必须逐字节相等
#
# v0.13.11 两条改动（都是被 09-23 事故逼出来的）：
# ① **原子写**（临时文件 + mv）。原来是 `cat … > static/hub.js`，重建窗口内
#    客户端可以取到**截断体**并在端侧缓存下来 —— 这与"清缓存才好一次"同构。
# ② **模板里的 ?v= 提手由产物内容派生**（md5 前 8 位），不再靠人记得改。
#    原来一天里 hub.js 改了三次而 URL 不变，是"服务端全绿 + 用户仍坏"连续三轮
#    无法归因的根本原因；而且服务端 ETag 与 query/编码无关 ⇒ ?v= 提手本身
#    并不保证端侧换新体。现在 ?v= == 文件内容哈希，服务端才允许 immutable
#    （见 src/staticguard.py::cache_policy），改错/忘改会自动退回 revalidate。
set -euo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp static/.hubjs.XXXXXX.js)   # 必须带 .js：node --check 按扩展名判格式
cat $(ls static/hub/[0-9][0-9]-*.js | sort) > "$tmp"
node --check "$tmp"
mv -f "$tmp" static/hub.js          # 同目录 rename ⇒ 原子替换，永不出现半文件

python3 - <<'PY'
import hashlib, re, sys
from pathlib import Path
n = 0
for tf in sorted(Path("templates").glob("*.html")):
    s = orig = tf.read_text(encoding="utf-8")
    def fix(m):
        global n
        rel, old = m.group(1), m.group(2)
        f = Path("static") / rel
        if not f.is_file():
            sys.stderr.write("  ! %s 不存在，保留原提手 %s\n" % (f, old)); return m.group(0)
        tok = hashlib.md5(f.read_bytes(), usedforsecurity=False).hexdigest()[:8]
        if tok != old:
            n += 1
        return '/static/%s?v=%s' % (rel, tok)
    s = re.sub(r'/static/([\w./-]+)\?v=([0-9a-z]+)', fix, s)
    if s != orig:
        bak = Path(str(tf) + ".bak-" + hashlib.md5(s.encode()).hexdigest()[:6] + "-token")
        if not bak.exists():
            bak.write_text(orig, encoding="utf-8")   # 首次改动留可回滚点
        tf.write_text(s, encoding="utf-8")
print("  ?v= 提手同步 %d 处（内容派生 md5 前 8 位）" % n)
PY

echo "built static/hub.js  ($(wc -l < static/hub.js) 行, md5 $(md5sum static/hub.js | cut -c1-8))"
