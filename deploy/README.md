# 安装：cp deploy/agenthub.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now agenthub
# 前提：loginctl show-user gztxt --property=Linger = yes（本机已 yes）
#
# 2026-10-08：本副本与生产 unit 已分叉，**别拿它覆盖生产** ——
#   生产 ExecStart 走 run_dualstack.py 自建 V6ONLY=0 双栈 socket；
#   本副本还是老的 `uvicorn --host 0.0.0.0`（单栈 v4）。分叉是 09-29 双栈改造时
#   只改生产留下的，属既存问题、未修（白名单制：发现既存问题停手报请，不顺手修）。
#   要对齐请先 diff ~/.config/systemd/user/agenthub.service。
