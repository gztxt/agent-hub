#!/usr/bin/env bash
# static/hub/NN-*.js  →  static/hub.js（纯拼接，不压缩不转译不重排）
# 为什么还要留一个单文件：模板/缓存键/前端探针都指向 /static/hub.js，
# 拆分只发生在**源码组织**层，产物形态一律不变 ⇒ 手机端零风险、也不需要重启服务。
# 漂移由 tests/test_hubjs_split.py 判红：拼接结果与仓库里的 hub.js 必须逐字相等。
set -euo pipefail
cd "$(dirname "$0")/.."
cat $(ls static/hub/[0-9][0-9]-*.js | sort) > static/hub.js
echo "built static/hub.js  ($(wc -l < static/hub.js) 行, md5 $(md5sum static/hub.js | cut -c1-8))"
