#!/usr/bin/env bash
# 续聊会话 --model 注入的实弹取证（v0.13.51）。
# 做法：在探针实例（:3199，真实 HOME + 生产库副本，关掉自动写回以免干扰）上，
# 用各家**真实**的历史会话 id 走一遍 POST /api/term/sessions，然后连 WS 读首屏输出：
#   · 返回的 cmd 必须带 --model/-m；
#   · 首屏不能出现 CLI 的「未知选项 / unexpected argument / unknown option」；
#   · 进程 5s 后仍活着（flag 被拒的 CLI 会立刻退出）。
# 用完立刻 DELETE，不留会话。
set -u
REPO=/home/gztxt/agent-hub-wt-52b51a1a
PY=/home/gztxt/agent-hub/venv/bin/python
PORT=3199
TOKEN=probe
DATA=$REPO/work/probe/resume-argv/data
LOG=$REPO/work/probe/resume-argv/server.log

fuser -k -n tcp $PORT 2>/dev/null; sleep 1
mkdir -p "$DATA" "$(dirname "$LOG")"
cp /home/gztxt/agent-hub/data/agents.db "$DATA/agents.db"

cd "$REPO/src" || exit 1
DATA_DIR=$DATA TERM_TOKEN=$TOKEN HUB_PASSCODE=probe HUB_MODEL_DRIFT_REPAIR=0 \
  LOG_DIR=$REPO/work/probe/resume-argv/logs \
  "$PY" -m uvicorn main:app --host 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; fuser -k -n tcp $PORT 2>/dev/null' EXIT

for i in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  sleep 1
done
echo "=== 服务就绪（v$($PY - <<'EOF'
import json,urllib.request
print(json.load(urllib.request.urlopen("http://127.0.0.1:3199/health"))["version"])
EOF
)）"

for AGENT in claude codex grok hermes; do
  echo
  echo "=== $AGENT"
  SID=$($PY - "$AGENT" <<'EOF'
import json, sys, urllib.request
a = sys.argv[1]
r = urllib.request.Request(f"http://127.0.0.1:3199/api/term/history/{a}?limit=3",
                           headers={"x-term-token": "probe"})
d = json.load(urllib.request.urlopen(r, timeout=20))
items = d.get("items") or []
print(items[0]["id"] if items else "")
EOF
)
  if [ -z "$SID" ]; then echo "  无可用历史会话，跳过"; continue; fi
  echo "  历史会话 id=$SID"
  OUT=$(curl -s -X POST "http://127.0.0.1:$PORT/api/term/sessions" \
        -H 'Content-Type: application/json' -H "x-term-token: $TOKEN" \
        -d "{\"agent_id\":\"$AGENT\",\"session_id\":\"$SID\"}")
  echo "  创建返回: $OUT" | head -c 400; echo
  HSID=$(echo "$OUT" | $PY -c "import sys,json; print(json.load(sys.stdin)['session']['id'])" 2>/dev/null || echo "")
  [ -z "$HSID" ] && { echo "  创建失败，跳过"; continue; }
  sleep 5
  $PY - "$HSID" <<'EOF'
import asyncio, json, sys, urllib.request, websockets
sid = sys.argv[1]
st = json.load(urllib.request.urlopen(
    urllib.request.Request(f"http://127.0.0.1:3199/api/term/sessions",
                           headers={"x-term-token": "probe"}), timeout=10))
s = next((x for x in st["sessions"] if x["id"] == sid), None)
print(f"  alive={s['alive'] if s else '?'}  cmd={s['cmd'] if s else '?'}")

async def grab():
    try:
        async with websockets.connect(f"ws://127.0.0.1:3199/ws/term/{sid}?token=probe",
                                      additional_headers={"x-term-token": "probe"},
                                      open_timeout=8) as ws:
            txt = ""
            for _ in range(12):
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    break
                if isinstance(m, bytes):
                    m = m.decode("utf-8", "ignore")
                txt += m
                if len(txt) > 4000:
                    break
            return txt
    except Exception as e:
        return f"<ws error {type(e).__name__}: {e}>"
txt = asyncio.run(grab())
flat = " ".join(txt.split())
low = flat.lower()
bad = [k for k in ("unknown option", "unexpected argument", "unrecognized",
                   "unknown flag", "invalid option", "error: unknown") if k in low]
print(f"  首屏({len(flat)}字符) 异常flag命中={bad or '无'}")
print(f"  首屏摘录: {flat[:220]}")
EOF
  curl -s -X DELETE "http://127.0.0.1:$PORT/api/term/sessions/$HSID" -H "x-term-token: $TOKEN" >/dev/null
  echo "  已清理 $HSID"
done
echo
echo "=== 服务端日志（modelcfg 相关）"
grep -E "modelcfg" "$LOG" | tail -6
