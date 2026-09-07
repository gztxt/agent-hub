# 安装：cp deploy/agent-hub.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now agent-hub
# 前提：loginctl show-user gztxt --property=Linger = yes（本机已 yes）
