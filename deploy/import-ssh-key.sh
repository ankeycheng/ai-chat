#!/usr/bin/env bash
# 在服务器上以部署用户执行：bash import-ssh-key.sh
# 作用：把袋马哥部署用公钥写入 ~/.ssh/authorized_keys（幂等，已存在则跳过）
set -euo pipefail

PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFkpOnRWXPPX1YVMhQty8ngG0NSxR/fYR3ZsU2nmWI5S daimage-aichat@box-20261019'
MARKER='daimage-aichat@box-20261019'

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"

if grep -qF "$MARKER" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  echo "公钥已存在，跳过写入。"
else
  printf '%s\n' "$PUBKEY" >> "$HOME/.ssh/authorized_keys"
  echo "已写入公钥。"
fi

echo
echo "当前用户: $(whoami)@$(hostname)"
echo "authorized_keys 行数: $(wc -l < "$HOME/.ssh/authorized_keys")"
echo
echo "导入完成后，把下面信息发给袋马哥即可："
echo "  SSH 用户名: $(whoami)"
echo "  服务器 IP 或域名: <请填写>"
echo "  SSH 端口: ${SSH_CLIENT:+见 sshd}（默认 22，若改过请写明）"
echo "  部署目录建议: /opt/ai-chat"
