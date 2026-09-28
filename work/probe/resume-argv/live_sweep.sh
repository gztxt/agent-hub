#!/usr/bin/env bash
# 巡检回路的实弹取证（v0.13.51）：证明「启动之后**再**被 CCR 改写」也能被修回来，
# 而不是只有启动那一轮管用（CCR 与 hub 开机同秒启动，改写可能晚于 hub 的启动修复）。
#
# 做法：造假 HOME（只拷各家的配置文件，不动真 HOME），让启动轮**没有漂移**；
# 起好之后再把 claude 的 env 三兄弟改回 qwen（复刻 CCR 重启那一手）；
# 巡检周期设 15s，看它是否在下一次巡检里自己写回。
set -u
REPO=/home/gztxt/agent-hub
PY=/home/gztxt/agent-hub/venv/bin/python
PORT=3198
TH=$REPO/work/probe/resume-argv/fakehome
DATA=$REPO/work/probe/resume-argv/data
LOG=$REPO/work/probe/resume-argv/sweep.log

fuser -k -n tcp $PORT 2>/dev/null; sleep 1
rm -rf "$TH" "$DATA"; mkdir -p "$TH/.claude" "$TH/.codex" "$TH/.claude-code-router" "$DATA"
cp ~/.claude/settings.json "$TH/.claude/settings.json"
cp ~/.codex/config.toml "$TH/.codex/config.toml"
cp ~/.claude-code-router/config.json "$TH/.claude-code-router/config.json"
cp /home/gztxt/agent-hub/data/agents.db "$DATA/agents.db"

cd "$REPO/src" || exit 1
HOME=$TH DATA_DIR=$DATA TERM_TOKEN=probe HUB_PASSCODE=probe HUB_MODEL_DRIFT_REPAIR=1 \
  HUB_MODEL_DRIFT_SWEEP_SEC=15 LOG_DIR=$REPO/work/probe/resume-argv/logs \
  "$PY" -m uvicorn main:app --host 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; fuser -k -n tcp $PORT 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
sleep 2
echo "=== ① 启动轮（此时不该有漂移，故不该有写回）"
grep -E "modelcfg" "$LOG" | head -5 || echo "  （无 modelcfg 行）"

echo
echo "=== ② 人为复刻 CCR 重启：只改 env 三兄弟"
$PY - "$TH" <<'EOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / ".claude" / "settings.json"
d = json.loads(p.read_text(encoding="utf-8"))
for k in ("ANTHROPIC_MODEL", "CCR_CLAUDE_CODE_MODEL", "CODEXL_CLAUDE_CODE_MODEL"):
    d["env"][k] = "alibaba/qwen3.8-flash[1m]"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("  已改成", d["env"]["ANTHROPIC_MODEL"], "（顶层 model 仍为", d["model"], "）")
EOF

echo
echo "=== ③ 等巡检（15s 一轮，等 22s）"
sleep 22
grep -E "modelcfg" "$LOG" | tail -5

echo
echo "=== ④ 巡检后磁盘现值"
$PY - "$TH" <<'EOF'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / ".claude" / "settings.json").read_text(encoding="utf-8"))
print("  model =", d["model"])
for k in ("ANTHROPIC_MODEL", "CCR_CLAUDE_CODE_MODEL", "CODEXL_CLAUDE_CODE_MODEL"):
    print(f"  env.{k} =", d["env"][k])
EOF
ls -1 "$TH/.claude/" | grep bak | sed 's/^/  备份: /'
