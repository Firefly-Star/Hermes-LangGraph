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
    render "$TEMPLATES/config.yaml" > "$HERMES_HOME/profiles/$p/config.yaml"
    cp "$TEMPLATES/SOUL-$p.md" "$HERMES_HOME/profiles/$p/SOUL.md" 2>/dev/null || true
    echo "  $p → port $port"
done

# ── 启动 gateways ──
echo "Starting gateway processes..."
for i in "${!PROFILES[@]}"; do
    p="${PROFILES[$i]}"
    echo "  $p (port ${PORTS[$i]})..."
    hermes -p "$p" gateway run &
    sleep 2
done

# ── 等待 gateways 就绪 ──
echo "Waiting for gateways to be ready..."
for port in "${PORTS[@]}"; do
    for i in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:$port/health" > /dev/null 2>&1; then
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "  ERROR: gateway on port $port not ready"
            exit 1
        fi
        sleep 1
    done
done
echo "All gateways ready."

# ── 保持容器运行 ──
echo "Gateway container running. Workflow connects via host."
tail -f /dev/null
