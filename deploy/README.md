# 安装：cp deploy/agenthub.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now agenthub
# 前提：loginctl show-user gztxt --property=Linger = yes（本机已 yes）
#
# 2026-10-08：**本副本是生产 unit 的逐字镜像，两者必须保持一致**（已对齐）。
#   起因：本副本曾在 09-29 双栈改造时只改了生产、没改本副本，于是副本还写着
#   `uvicorn --host 0.0.0.0 --port 3102`（单栈 v4 且把端口写死），而生产已走
#   run_dualstack.py 自建 V6ONLY=0 双栈 socket。**分叉不报错**：照 README 装一遍
#   就会把双栈服务悄悄降级成单栈，而 /health 照样 200。
#   对齐后由 tests/test_hublog.py::TestUnitNameConsistency 钉住：副本里
#   ExecStart 不含 run_dualstack.py 即判红。要改 unit 先改生产再 cp 回来。
