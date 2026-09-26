#!/usr/bin/env bash
# turbovec 技术文档语义索引重建（v0.13.26 批3 配套，hub jobs 的执行体）
#
# 用途：kb.py 的 turbovec 路走 /vol1/turbovec-mcp 的语义索引；索引是
#       /fs/1000/ftp/技术文档 的**投影**（09-24 实测 2791 chunks / 1.02MB），
#       文档更新后必须重建才可检索。重建是长任务（全量 embed，经验 ≥1800s），
#       所以做成独立脚本供 jobs 引擎或人工调用，绝不在请求路径上跑。
#
# 安全口径：
#  - 只重建索引，不动任何源文档（索引损坏可随时重跑，幂等）
#  - 日志落 /vol1（运行时资产不进 git 的军规）
#  - 超时硬顶 TURBOVEC_REBUILD_TIMEOUT_S（默认 7200s），防「无限等待」纪律
#
# 注册成 hub job（需先扩 JOB_SHELL_ALLOW 含本脚本路径——env 属生产配置，
# 须用户逐路授权，见 PENDING-TASKS PT 台账）：
#   POST /api/jobs {"name":"turbovec-rebuild","cron":"0 4 * * 0",
#                   "kind":"shell","payload":"/home/gztxt/agent-hub/scripts/rebuild_turbovec.sh"}
#
# 退出码：0 成功；2 上游命令失败；3 超时。

set -u

TV_PY="${KB_TURBOVEC_PY:-/vol1/turbovec-pilot/venv/bin/python}"
TV_CWD="${KB_TURBOVEC_CWD:-/fs/1000/ftp/技术文档/turbovec-mcp}"
# 文档根（rebuild 子命令只有 --doc-dir；索引路径是 turbovec 包内部固定的
# /vol1/turbovec-pilot/data/techdocs.tvim，此处仅用于事后 ls 验证产物）
TV_DOCDIR="${KB_TURBOVEC_DOCDIR:-/fs/1000/ftp/技术文档}"
TV_INDEX="${KB_TURBOVEC_INDEX:-/vol1/turbovec-pilot/data/techdocs.tvim}"
TIMEOUT_S="${TURBOVEC_REBUILD_TIMEOUT_S:-7200}"
LOG_DIR="/vol1/turbovec-pilot/logs"
LOG="$LOG_DIR/rebuild-$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"
{
  echo "== rebuild start $(date -Is) pid=$$ =="
  echo "doc_dir=$TV_DOCDIR index=$TV_INDEX timeout=${TIMEOUT_S}s"
  cd "$TV_CWD" || exit 2
  timeout "$TIMEOUT_S" "$TV_PY" -m techdocs_mcp.cli rebuild --doc-dir "$TV_DOCDIR"
  rc=$?
  echo "== rebuild done rc=$rc $(date -Is) =="
  if [ "$rc" = 124 ]; then
    echo "TIMEOUT after ${TIMEOUT_S}s — 索引可能半成品，重跑本脚本即幂等修复"
    exit 3
  fi
  [ "$rc" = 0 ] && ls -la "$TV_INDEX"
  exit "$rc"
} >> "$LOG" 2>&1
