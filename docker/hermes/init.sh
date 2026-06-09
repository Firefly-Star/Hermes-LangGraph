#!/bin/bash
set -e

source /opt/hermes/.venv/bin/activate

HERMES_HOME="${HERMES_HOME:-/opt/data}"
TEMPLATES="/opt/data/templates"
MARKER="$HERMES_HOME/.workflow-initialized"

PORTS=(8642 8643 8644 8645 8646 8647)
PROFILES=(master judge reviewer pm dev qa)

# SSH_HOST 未设置时自动检测宿主机 IP
if [ -z "${SSH_HOST:-}" ]; then
    SSH_HOST=$(ip route | grep default | awk '{print $3}' 2>/dev/null || echo "")
fi
export SSH_HOST

render() {
    python3 -c "
import os, sys
with open('${1}') as f:
    sys.stdout.write(os.path.expandvars(f.read()))
"
}

# ── 初始化全局配置（仅首次） ──
if [ ! -f "$MARKER" ]; then
    echo "=== Initializing Hermes config ==="
    render "$TEMPLATES/config.yaml" > "$HERMES_HOME/config.yaml"
    render "$TEMPLATES/global.env" > "$HERMES_HOME/.env"
    touch "$MARKER"
    echo "=== Global config initialized ==="
fi

# ── 确保所有 profile 存在 ──
for i in "${!PROFILES[@]}"; do
    p="${PROFILES[$i]}"
    port="${PORTS[$i]}"
    if [ ! -d "$HERMES_HOME/profiles/$p" ]; then
        echo "  Creating profile: $p"
        hermes profile create "$p"
    fi
    API_SERVER_PORT=$port render "$TEMPLATES/profile.env" > "$HERMES_HOME/profiles/$p/.env"
    # API_SERVER_KEY: 已有有效值时不覆盖，除非用户显式传入
    if [ -n "$API_SERVER_KEY" ]; then
        sed -i "s|^API_SERVER_KEY=.*|API_SERVER_KEY=$API_SERVER_KEY|" "$HERMES_HOME/profiles/$p/.env"
    else
        existing_val=$(sed -n 's/^API_SERVER_KEY=//p' "$HERMES_HOME/profiles/$p/.env" 2>/dev/null | head -1)
        case "$existing_val" in
            ''|'${API_SERVER_KEY}'|kaguya)
                API_SERVER_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))")"
                sed -i "s|^API_SERVER_KEY=.*|API_SERVER_KEY=$API_SERVER_KEY|" "$HERMES_HOME/profiles/$p/.env"
                ;;
        esac
    fi
    render "$TEMPLATES/config.yaml" > "$HERMES_HOME/profiles/$p/config.yaml"
    cp "$TEMPLATES/SOUL-$p.md" "$HERMES_HOME/profiles/$p/SOUL.md" 2>/dev/null || true
    echo "  $p → port $port"
done

echo "=== Starting all gateways ==="
for i in "${!PROFILES[@]}"; do
    p="${PROFILES[$i]}"
    port="${PORTS[$i]}"
    echo "  $p (port $port)..."
    hermes -p "$p" gateway run &
    sleep 2
done

echo "=== All gateways started ==="
echo "Profiles: master(8642) judge(8643) reviewer(8644) pm(8645) dev(8646) qa(8647)"

# 保持容器运行
wait
