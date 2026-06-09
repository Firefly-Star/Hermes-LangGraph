#!/bin/bash
set -e

source /opt/hermes/.venv/bin/activate

HERMES_HOME="${HERMES_HOME:-/opt/data}"
WORKFLOW_DIR="/opt/workflow"
TEMPLATES="$WORKFLOW_DIR/docker/hermes/templates"
MARKER="$HERMES_HOME/.workflow-initialized"

PORTS=(8642 8643 8644 8645)
PROFILES=(cg pm dev qa)

render() {
    python3 -c "
import os, sys
with open('${1}') as f:
    sys.stdout.write(os.path.expandvars(f.read()))
"
}

# ── 初始化 Hermes profiles（仅首次） ──
if [ ! -f "$MARKER" ]; then
    echo "=== Initializing Hermes profiles ==="
    render "$TEMPLATES/config.yaml" > "$HERMES_HOME/config.yaml"
    render "$TEMPLATES/global.env" > "$HERMES_HOME/.env"

    for i in "${!PROFILES[@]}"; do
        p="${PROFILES[$i]}"
        port="${PORTS[$i]}"
        if [ ! -d "$HERMES_HOME/profiles/$p" ]; then
            echo "  Creating profile: $p"
            hermes profile create "$p"
        fi
        API_SERVER_PORT=$port render "$TEMPLATES/profile.env" > "$HERMES_HOME/profiles/$p/.env"
        render "$TEMPLATES/config.yaml" > "$HERMES_HOME/profiles/$p/config.yaml"
        echo "  $p → port $port"
    done
    touch "$MARKER"
    echo "=== Initialization done ==="
fi

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

# ── 安装工作流依赖 ──
echo "Installing workflow dependencies..."
pip install -q -r "$WORKFLOW_DIR/requirements.txt"

# ── 运行工作流 ──
echo "Starting workflow..."
cd "$WORKFLOW_DIR"
python -m src.workflow --config "$WORKFLOW_DIR/docker/runtime_config.json"

# ── 工作流结束，清理 ──
echo "Workflow finished. Shutting down gateways..."
kill $(jobs -p) 2>/dev/null
wait
echo "Container stopped."
