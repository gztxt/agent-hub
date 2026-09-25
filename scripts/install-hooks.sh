#!/usr/bin/env bash
# 把「提交前必过 L0、推送前必过 prepush 六道」装成 git hook（0924 方案档 §四 P5-9）。
#
# 为什么要有它：本机最贵的一次事故是 v0.13.16/17 两个提交**本地与远端皆不可达**（永久丢失），
# 而闸门脚本一直在仓里躺着 —— 靠人记得跑，就等于没跑。hook 把"记得"变成"默认"。
#
# 设计取舍（都写在这里，免得后人重新踩）：
#   · **hooks 存在 common git dir**：worktree 与主 checkout 共用同一份 hooks，所以在任一
#     worktree 里装一次，对所有会话生效 ⇒ 装完必须把这件事说出来（本脚本末尾会打印）。
#   · **测试失败 = 拦（fail-closed）；环境缺失 = 放行并大声警告（fail-open）**。
#     理由：hook 拦不住真缺陷就等于没有；但因为 runner 不存在而把所有人的提交卡死，
#     是把工具问题伪装成代码问题（09-12 winbuild 那次 root 属主就是这类"环境故障冒充代码故障"）。
#   · **绝不静默覆盖既有 hook**：先时间戳备份（本机铁律），备份不了就不装。
#   · 逃生口永远是 `git commit --no-verify` / `git push --no-verify`，脚本会打印出来。
#
# 用法：
#   bash scripts/install-hooks.sh              # 安装（幂等）
#   bash scripts/install-hooks.sh --dry-run    # 只看会做什么
#   bash scripts/install-hooks.sh --uninstall  # 卸掉（保留 .disabled 副本）
#   bash scripts/install-hooks.sh --status     # 看当前装了什么
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || true)"
[ -n "$COMMON_DIR" ] || { echo "FAIL：不在 git 仓里"; exit 2; }
case "$COMMON_DIR" in /*) ;; *) COMMON_DIR="$REPO_ROOT/$COMMON_DIR" ;; esac
HOOKS="$COMMON_DIR/hooks"
mkdir -p "$HOOKS"

MODE="install"
case "${1:-}" in
  --dry-run)   MODE="dry" ;;
  --uninstall) MODE="uninstall" ;;
  --status)    MODE="status" ;;
  "") ;;
  *) echo "未知参数：$1（支持 --dry-run / --uninstall / --status）"; exit 2 ;;
esac

VENV="${HUB_VENV:-$REPO_ROOT/venv/bin/python}"
[ -x "$VENV" ] || VENV="$(command -v python3 || true)"

MARK="# agent-hub install-hooks.sh"   # 识别"这是不是我装的"，避免误删别人的 hook

pre_commit_body() {
  cat <<EOF
#!/usr/bin/env bash
$MARK —— 提交前跑 L0 hermetic（约 3s，零网络零副作用）。
# 拦的是真缺陷；环境缺失只警告不拦（见 install-hooks.sh 头部取舍说明）。
set -uo pipefail
cd "\$(git rev-parse --show-toplevel)"
PY="$VENV"
if [ ! -x "\$PY" ]; then
  echo "[pre-commit] WARN：找不到 python（\$PY）⇒ 放行，请自行跑 bash scripts/run_tests.sh hermetic"
  exit 0
fi
echo "[pre-commit] L0 hermetic ..."
if ! "\$PY" scripts/run_tier.py hermetic >/tmp/agent-hub-precommit.\$\$.log 2>&1; then
  tail -30 /tmp/agent-hub-precommit.\$\$.log
  echo "[pre-commit] FAIL：L0 未过 ⇒ 提交被拦。修好再提交，或 git commit --no-verify 绕过（绕过后请自证）"
  rm -f /tmp/agent-hub-precommit.\$\$.log
  exit 1
fi
grep -E "^\[tier\]" /tmp/agent-hub-precommit.\$\$.log || true
rm -f /tmp/agent-hub-precommit.\$\$.log
exit 0
EOF
}

pre_push_body() {
  cat <<EOF
#!/usr/bin/env bash
$MARK —— 推送前跑六道闸门（scripts/prepush.sh）。
set -uo pipefail
cd "\$(git rev-parse --show-toplevel)"
if [ ! -f scripts/prepush.sh ]; then
  echo "[pre-push] WARN：scripts/prepush.sh 不存在 ⇒ 放行"
  exit 0
fi
HUB_VENV="${HUB_VENV:-}" bash scripts/prepush.sh || {
  echo "[pre-push] FAIL：闸门未过 ⇒ 推送被拦。逃生口：git push --no-verify（绕过后请在台账留痕）"
  exit 1
}
exit 0
EOF
}

install_one() {
  local name="$1"; shift
  local path="$HOOKS/$name"
  if [ -e "$path" ] && ! grep -qF "$MARK" "$path" 2>/dev/null; then
    local bak="$path.bak-$(date +%Y%m%d_%H%M%S)-install-hooks"
    if [ "$MODE" = "dry" ]; then
      echo "[dry] 既有 $name 非本脚本所装 ⇒ 会先备份到 $(basename "$bak")"
    else
      cp -p "$path" "$bak" || { echo "FAIL：备份 $name 失败 ⇒ 不装（无备份一律禁止写入）"; exit 2; }
      echo "  已备份既有 $name → $(basename "$bak")"
    fi
  fi
  if [ "$MODE" = "dry" ]; then
    echo "[dry] 会写 $path"
  else
    { case "$name" in pre-commit) pre_commit_body ;; pre-push) pre_push_body ;; esac; } > "$path"
    chmod +x "$path"
    echo "  已装 $name（$(wc -c <"$path") B）"
  fi
}

case "$MODE" in
  status)
    echo "hooks 目录：$HOOKS"
    for n in pre-commit pre-push; do
      if [ -e "$HOOKS/$n" ]; then
        if grep -qF "$MARK" "$HOOKS/$n" 2>/dev/null; then echo "  $n：本脚本所装 ($(wc -c <"$HOOKS/$n") B)";
        else echo "  $n：存在但**非本脚本所装**（不动它）"; fi
      else echo "  $n：未安装"; fi
    done
    ls -1 "$HOOKS"/*.bak-* 2>/dev/null | sed 's/^/  备份：/' || true
    exit 0 ;;
  uninstall)
    for n in pre-commit pre-push; do
      if [ -e "$HOOKS/$n" ] && grep -qF "$MARK" "$HOOKS/$n" 2>/dev/null; then
        mv -f "$HOOKS/$n" "$HOOKS/$n.disabled-$(date +%Y%m%d_%H%M%S)"
        echo "  已卸载 $n（保留 .disabled 副本）"
      else echo "  $n：非本脚本所装或不存在 ⇒ 跳过"; fi
    done
    exit 0 ;;
esac

echo "== install-hooks：仓库 $REPO_ROOT"
echo "   hooks 落位（common dir，worktree 与主 checkout 共用）：$HOOKS"
echo "   python：${VENV:-<未找到>}"
install_one pre-commit
install_one pre-push
[ "$MODE" = "dry" ] && { echo "[dry] 未做任何改动"; exit 0; }
echo "OK：已安装。注意 —— 这会影响**共用本仓的所有会话**（含其他 worktree）。"
echo "    逃生口：git commit --no-verify / git push --no-verify；卸载：bash scripts/install-hooks.sh --uninstall"
