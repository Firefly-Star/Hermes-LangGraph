#!/bin/bash
set -e

echo "=== Hermes Gateway SSH 配置脚本 ==="

# 1. 检查 SSH server
if ! command -v sshd &>/dev/null; then
    echo "  安装 openssh-server..."
    sudo apt update && sudo apt install -y openssh-server
fi

# 2. 启动 SSH server
if systemctl is-active --quiet ssh 2>/dev/null; then
    echo "  SSH server 已运行"
else
    echo "  启动 SSH server..."
    sudo service ssh start 2>/dev/null || sudo /etc/init.d/ssh start 2>/dev/null || echo "  ⚠ 请手动启动 SSH server"
fi

# 3. 生成 SSH key（专用密钥，不覆盖已有文件）
SSH_KEY="$HOME/.ssh/id_hermes-gateway"
if [ ! -f "$SSH_KEY" ]; then
    echo "  生成 SSH 密钥..."
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
fi

# 4. 公钥加入 authorized_keys
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if ! grep -qF "$(cat "$SSH_KEY.pub")" ~/.ssh/authorized_keys 2>/dev/null; then
    echo "  添加公钥到 authorized_keys..."
    cat "$SSH_KEY.pub" >> ~/.ssh/authorized_keys
fi
chmod 600 ~/.ssh/authorized_keys

# 5. 检测 WSL2 IP 并写入 .env
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# 获取 WSL2 的 eth0 IP（重启后变化，需要重新获取）
WSL2_IP=$(ip addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
SSH_HOST="${WSL2_IP:-host.docker.internal}"

if [ ! -f "$ENV_FILE" ]; then
    echo "  创建 .env 文件..."
    cat > "$ENV_FILE" << EOF
DEEPSEEK_API_KEY=sk-你的key
TERMINAL_ENV=ssh
SSH_USER=$(whoami)
SSH_HOST=$SSH_HOST
EOF
    echo "  ⚠ 请编辑 .env 填入 DEEPSEEK_API_KEY"
    echo "  SSH_HOST 已设为: $SSH_HOST"
    echo "  （WSL2 重启后 IP 会变，届时重新运行本脚本更新）"
else
    # .env 已存在，更新 SSH_HOST
    if grep -q "^SSH_HOST=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s/^SSH_HOST=.*/SSH_HOST=$SSH_HOST/" "$ENV_FILE"
    else
        echo "SSH_HOST=$SSH_HOST" >> "$ENV_FILE"
    fi
    echo "  SSH_HOST 已更新为: $SSH_HOST"
fi

echo ""
echo "==== SSH 配置完成 ===="
echo "运行: docker compose up -d"
