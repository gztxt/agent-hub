#!/usr/bin/env bash
# 备份件清点（**只读**，不删不移）。09-24 立：军规要求"改前必备份"，但没人管"备份的出口"，
# 结果本仓 09-24 实测积压 **340 份 / 20.5 MB**，同一目标文件最多 73 份（templates/index.html）。
# 后果不是"占地方"这一条：
#   ① 09-24 那次外部 `git clean` 把整片 `*.bak-*` 当成垃圾一起收走 —— 备份件多到无人能分辨
#      "哪份是回滚依据"时，它们与垃圾等价；真需要的回滚点会在清理中一起消失。
#   ② `git clean -xdff`（L0 影子树的标准清场步骤）不区分"我的回滚点"与"历史残留"。
# 判据（写死在本脚本里，别再靠人眼数）：
#   保留序：同一目标文件按 mtime 倒序保留最新 N=3 份；其余为"可归档"（不是"可删"——删除须用户批准）。
#   另：文件名含 rollback / pre- 者**永不进入可归档集**（它们是事故回滚依据）。
set -uo pipefail
cd "$(dirname "$0")/.."
KEEP=${KEEP:-3}
echo "备份件清点（KEEP=$KEEP，只读）"
echo "────────────────────────────────────────────────────────────"
total=0; arch=0
while IFS= read -r stem; do
  # 限定目录：同名 .bak 可能存在于多个目录（如 ./data/ 与 ./data/backups/），
  # 无目录限定的 find 会重复计数（与 bak_archive.sh 同一修法，09-24）。
  mapfile -t files < <(find "$(dirname "$stem")" -maxdepth 1 -name "$(basename "$stem").bak-*" \
                        -not -path './venv/*' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
  n=${#files[@]}; total=$((total+n))
  [ "$n" -le "$KEEP" ] && continue
  printf '%-52s 共 %3d 份' "$stem" "$n"
  for f in "${files[@]:$KEEP}"; do
    case "$f" in
      *rollback*|*pre-*) continue ;;   # 事故回滚依据，永不计数
    esac
    arch=$((arch+1))
  done
  printf '（超出保留窗 %d 份）\n' "$((n-KEEP))"
done < <(find . -name '*.bak-*' -not -path './venv/*' 2>/dev/null \
          | sed 's/\.bak-[^/]*$//' | sort -u)
echo "────────────────────────────────────────────────────────────"
echo "总份数=$total ；超出保留窗（可归档候选，**非可删**）=$arch"
echo "归档动作需用户批准；本脚本不做任何写/删。"
