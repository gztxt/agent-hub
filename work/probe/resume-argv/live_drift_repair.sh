#!/usr/bin/env bash
# 漂移写回的实弹取证（v0.13.51）。与续聊探针的区别：这次开 HUB_MODEL_DRIFT_REPAIR=1，
# 探针实例会对**真实 HOME** 下的配置文件落笔（用户已授权；落笔前 modelcfg 自带时间戳备份）。
# 判据：① 启动日志出现「漂移已写回」；② 写回后 GET /api/settings/models 的 drift 为空；
#      ③ 两家配置文件现值 == 持久化值；④ 每家都留下了一份新备份。
set -u
REPO=/home/gztxt/agent-hub-wt-52b51a1a
PY=/home/gztxt/agent-hub/venv/bin/python
PORT=3199
DATA=$REPO/work/probe/resume-argv/data
LOG=$REPO/work/probe/resume-argv/drift.log

fuser -k -n tcp $PORT 2>/dev/null; sleep 1
cd "$REPO/src" || exit 1
DATA_DIR=$DATA TERM_TOKEN=probe HUB_PASSCODE=probe HUB_MODEL_DRIFT_REPAIR=1 \
  HUB_MODEL_DRIFT_SWEEP_SEC=20 \
  LOG_DIR=$REPO/work/probe/resume-argv/logs \
  "$PY" -m uvicorn main:app --host 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; fuser -k -n tcp $PORT 2>/dev/null' EXIT

for i in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
sleep 2
echo "=== ① 启动日志（modelcfg）"
grep -E "modelcfg" "$LOG" | head -8

echo
echo "=== ② 写回后 drift 应为空"
curl -s "http://127.0.0.1:$PORT/api/settings/models" -H "x-term-token: probe" | $PY -c "
import sys, json
d = json.load(sys.stdin)
print('drift =', d.get('drift') or '[]（空）')
for a in d['agents']:
    if a.get('id') in ('claude','codex','grok','hermes'):
        print(f\"  {a['id']:7s} hub={a.get('hub_model','')!r:32s} current={a.get('current','')!r:26s} extra={a.get('extra')}\")
"

echo
echo "=== ③ 配置文件现值（磁盘直读）"
$PY - <<'EOF'
import json, pathlib
h = pathlib.Path.home()
d = json.loads((h / ".claude" / "settings.json").read_text(encoding="utf-8"))
print("  ~/.claude/settings.json model =", d.get("model"))
for k in ("ANTHROPIC_MODEL", "CCR_CLAUDE_CODE_MODEL", "CODEXL_CLAUDE_CODE_MODEL"):
    print(f"    env.{k} =", (d.get("env") or {}).get(k))
t = (h / ".codex" / "config.toml").read_text(encoding="utf-8")
print("  ~/.codex/config.toml 命中行:",
      [ln.strip() for ln in t.splitlines() if ln.strip().startswith("model =") or "CCR configured model" in ln][:3])
EOF

echo
echo "=== ④ 新产生的备份（本次运行内）"
ls -1 --time-style=+%H:%M:%S -la ~/.claude/settings.json.bak-* 2>/dev/null | tail -2
ls -1 --time-style=+%H:%M:%S -la ~/.codex/config.toml.bak-* 2>/dev/null | tail -2

echo
echo "=== ⑤ 定期巡检（20s 一轮）是否会重复落笔（无漂移 ⇒ 不该再有写回行）"
sleep 24
grep -c "漂移已写回" "$LOG" | sed 's/^/  写回次数=/'
grep -E "modelcfg" "$LOG" | tail -3
