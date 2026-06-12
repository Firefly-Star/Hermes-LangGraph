#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# ── 颜色 ──
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

# ── 读取已有配置 ──
load_env() {
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi
}
load_env

# 检测 Python 命令（Ubuntu/WSL 用 python3，Windows 用 python）
PYTHON=$(command -v python3 || command -v python || echo "python")

# ── 系统依赖检查 ──
check_system_deps() {
    local missing=()
    # Python 本体
    if ! command -v "$PYTHON" &>/dev/null; then
        missing+=("python3")
    fi
    # Python venv + ensurepip（Ubuntu 上 python3-venv 包提供）
    if ! "$PYTHON" -c "import ensurepip" &>/dev/null 2>&1; then
        missing+=("python3-venv")
    fi
    # Docker
    if ! command -v docker &>/dev/null; then
        missing+=("docker.io")
    fi
    # curl（容器健康检查用）
    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}⚠ 缺少系统依赖：${missing[*]}${NC}"
        echo -e "  ${DIM}将执行: sudo apt install -y ${missing[*]}${NC}"
        read -p "  是否安装？[Y/n]: " input_install
        case "$input_install" in
            n|N|no)
                echo -e "${RED}请手动安装后重新运行${NC}"
                exit 1
                ;;
            *)
                sudo apt update -qq && sudo apt install -y "${missing[@]}"
                echo -e "${GREEN}✓ 系统依赖已安装${NC}"
                ;;
        esac
    fi
}

# ── 工具函数 ──

# 判断密钥是否"可用"（对应 Hermes has_usable_secret）
is_usable_secret() {
    local val="$1"
    local min_len="${2:-8}"
    [ ${#val} -ge "$min_len" ] || return 1
    local lower="${val,,}"
    case "$lower" in
        changeme|your_api_key|your-api-key|placeholder|example|dummy|null|none)
            return 1 ;;
    esac
    return 0
}

# 带已有值提示的输入
prompt_with_current() {
    local var="$1"
    local label="$2"
    local current="${!var:-}"
    if [ -n "$current" ]; then
        echo -e "  ${DIM}当前: $current${NC}"
    fi
    read -p "  $label: " input
    if [ -n "$input" ]; then
        eval "$var=\"$input\""
    fi
}

# ── [1] DEEPSEEK_API_KEY ──
input_dk() {
    echo ""
    echo -e "${BOLD}[1] API Key${NC}"
    echo -e "  ${DIM}DeepSeek API Key，必填。已有值且不输入则沿用，留空报错。${NC}"
    while true; do
        current_dk="${DEEPSEEK_API_KEY:-}"
        if [ -n "$current_dk" ]; then
            echo -e "  ${DIM}当前: ${current_dk:0:8}...${NC}"
        fi
        read -p "  DeepSeek API Key: " input_dk
        if [ -n "$input_dk" ]; then
            DEEPSEEK_API_KEY="$input_dk"
            break
        elif [ -n "$current_dk" ]; then
            echo -e "  ${DIM}沿用已有值${NC}"
            break
        else
            echo -e "  ${RED}⚠ 必填，不填容器无法启动${NC}"
        fi
    done
}

# ── [2] API_SERVER_KEY ──
input_ask() {
    echo ""
    echo -e "${BOLD}[2] API Server 密钥${NC}"
    echo -e "  ${DIM}用于 gateway HTTP 鉴权。不填则自动生成随机密钥；已有有效值则沿用。${NC}"
    echo -e "  ${DIM}你手动输入时，若密钥过短或为常见占位符，会提示确认。${NC}"

    local existing="${API_SERVER_KEY:-}"

    # 已有值且可用 → 直接问留空沿用还是覆写
    if [ -n "$existing" ] && is_usable_secret "$existing"; then
        echo -e "  ${DIM}当前: $existing（有效）${NC}"
        read -p "  新密钥（留空沿用）: " input_ask
        if [ -z "$input_ask" ]; then
            # 沿用已有值
            :
        elif is_usable_secret "$input_ask"; then
            API_SERVER_KEY="$input_ask"
        else
            echo -e "  ${YELLOW}⚠ 输入的值过短或是常见占位符，确定要用？${NC}"
            read -p "  确认使用 [y/N] " confirm
            if [[ "$confirm" =~ ^[yY] ]]; then
                API_SERVER_KEY="$input_ask"
            else
                # 重新输入
                API_SERVER_KEY=""
                input_ask
                return
            fi
        fi
        return
    fi

    # 无已有值，或已有值无效
    if [ -n "$existing" ]; then
        echo -e "  ${DIM}当前: $existing（不安全，将被替换）${NC}"
    fi
    read -p "  密钥（留空自动生成）: " input_ask
    if [ -z "$input_ask" ]; then
        API_SERVER_KEY="$($PYTHON -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "")"
        echo -e "  ${GREEN}✓ 已自动生成: $API_SERVER_KEY${NC}"
    elif is_usable_secret "$input_ask"; then
        API_SERVER_KEY="$input_ask"
    else
        echo -e "  ${YELLOW}⚠ 输入的值过短或是常见占位符，确定要用？${NC}"
        read -p "  确认使用 [y/N] " confirm
        if [[ "$confirm" =~ ^[yY] ]]; then
            API_SERVER_KEY="$input_ask"
        else
            API_SERVER_KEY=""
            input_ask
            return
        fi
    fi
}

# ── [3] 终端后端 ──
input_terminal() {
    echo ""
    echo -e "${BOLD}[3] 终端后端${NC}"
    echo -e "  ${DIM}local = 命令在容器内执行  ssh = 命令在宿主机执行${NC}"
    current_term="${TERMINAL_ENV:-}"
    if [ -n "$current_term" ]; then
        echo -e "  ${DIM}当前: $current_term${NC}"
    fi
    read -p "  终端模式 [local/ssh]（留空默认 ssh）: " input_term
    TERMINAL_ENV="${input_term:-${TERMINAL_ENV:-ssh}}"
}

# ── [4] SSH 配置 ──
setup_ssh() {
    echo ""
    echo -e "${BOLD}[4] SSH 配置${NC}"

    # 检查 SSH server
    if ! command -v sshd &>/dev/null; then
        echo "  安装 openssh-server..."
        sudo apt update && sudo apt install -y openssh-server
    fi
    if ! systemctl is-active --quiet ssh 2>/dev/null; then
        echo "  启动 SSH server..."
        sudo service ssh start 2>/dev/null || sudo /etc/init.d/ssh start 2>/dev/null || echo -e "  ${YELLOW}⚠ 请手动启动 SSH server${NC}"
    fi

    # SSH 密钥
    SSH_KEY="$HOME/.ssh/id_hermes-gateway"
    if [ ! -f "$SSH_KEY" ]; then
        echo "  生成 SSH 密钥..."
        ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
    fi

    # 公钥加入 authorized_keys
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    if ! grep -qF "$(cat "$SSH_KEY.pub")" ~/.ssh/authorized_keys 2>/dev/null; then
        echo "  添加公钥到 authorized_keys..."
        cat "$SSH_KEY.pub" >> ~/.ssh/authorized_keys
    fi
    chmod 600 ~/.ssh/authorized_keys

    # 自动检测 WSL2 IP
    WSL2_IP=$(ip addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
    if [ -n "$WSL2_IP" ]; then
        echo "  检测到 WSL2 IP: $WSL2_IP"
        SSH_HOST="${WSL2_IP}"
    else
        SSH_HOST="${SSH_HOST:-host.docker.internal}"
    fi
    export SSH_HOST
    echo -e "  ${DIM}SSH_HOST: $SSH_HOST（自动检测）${NC}"

    # SSH_USER
    echo ""
    echo -e "  ${DIM}WSL2 用户名，ssh 登录用。已有值且不输入则沿用，留空报错。${NC}"
    while true; do
        current_user="${SSH_USER:-}"
        if [ -n "$current_user" ]; then
            echo -e "  ${DIM}当前: $current_user${NC}"
        fi
        read -p "  WSL2 用户名: " input_user
        if [ -n "$input_user" ]; then
            SSH_USER="$input_user"
            break
        elif [ -n "$current_user" ]; then
            echo -e "  ${DIM}沿用已有值${NC}"
            break
        else
            echo -e "  ${RED}⚠ SSH 模式必须填写用户名${NC}"
        fi
    done
}

# ── 端口配置 ──
PORTS_DEF=(
    "MASTER_PORT:8642:Master"
    "JUDGE_PORT:8643:Judge"
    "REVIEWER_PORT:8644:Reviewer"
    "PM_PORT:8645:PM"
    "DEV_PORT:8646:Dev"
    "QA_PORT:8647:QA"
)

input_ports() {
    echo ""
    echo -e "${BOLD}[5] 端口配置${NC}"
    echo -e "  ${DIM}各 Agent Gateway 端口号。留空沿用当前值或默认值。${NC}"
    echo -e "  ${DIM}如需在本地另起 Hermes 测试，改这里避免端口冲突。${NC}"
    for entry in "${PORTS_DEF[@]}"; do
        local var="${entry%%:*}"
        local def="${entry#*:}"; def="${def%%:*}"
        local label="${entry##*:}"
        local current="${!var:-}"
        local show="${current:-$def}"
        echo ""
        echo -e "  ${DIM}${label} ($var) 默认: $def${NC}"
        if [ -n "$current" ]; then
            echo -e "  ${DIM}当前: $current${NC}"
        fi
        while true; do
            read -p "  端口号（留空保持 ${show}）: " input_port
            if [ -z "$input_port" ]; then
                if [ -z "$current" ]; then
                    eval "$var=\"$def\""
                fi
                break
            elif [[ "$input_port" =~ ^[0-9]+$ ]] && [ "$input_port" -ge 1 ] && [ "$input_port" -le 65535 ]; then
                eval "$var=\"$input_port\""
                break
            else
                echo -e "  ${RED}⚠ 请输入 1-65535 之间的有效端口号${NC}"
            fi
        done
    done
}

# ── [6] 工作目录 ──
input_workspace() {
    echo ""
    echo -e "${BOLD}[6] 工作目录${NC}"
    echo -e "  ${DIM}工作流的工作目录（产出文件存放位置）。留空默认当前目录。${NC}"
    local current_ws="${WORKSPACE_DIR:-$PWD}"
    echo -e "  ${DIM}当前: $current_ws${NC}"
    read -p "  工作目录（留空保持）: " input_ws
    WORKSPACE_DIR="${input_ws:-$current_ws}"
    # 统一正斜杠
    WORKSPACE_DIR="${WORKSPACE_DIR//\\//}"
    echo -e "  ${GREEN}✓ 工作目录: $WORKSPACE_DIR${NC}"
}

# ── 生成 Docker Runtime Config ──
generate_runtime_configs() {
    local ws="$WORKSPACE_DIR"
    local port_master="${MASTER_PORT:-8642}"
    local port_judge="${JUDGE_PORT:-8643}"
    local port_reviewer="${REVIEWER_PORT:-8644}"
    local port_pm="${PM_PORT:-8645}"
    local port_dev="${DEV_PORT:-8646}"
    local port_qa="${QA_PORT:-8647}"

    _render_template() {
        local src="$1" dst="$2"
        sed -e "s|__WORKSPACE__|$ws|g" \
            -e "s|__MASTER_PORT__|$port_master|g" \
            -e "s|__JUDGE_PORT__|$port_judge|g" \
            -e "s|__REVIEWER_PORT__|$port_reviewer|g" \
            -e "s|__PM_PORT__|$port_pm|g" \
            -e "s|__DEV_PORT__|$port_dev|g" \
            -e "s|__QA_PORT__|$port_qa|g" \
            "$src" > "$dst"
    }

    _render_template "$SCRIPT_DIR/docker/runtime_config.json.template" \
                     "$SCRIPT_DIR/docker/runtime_config.json"
    echo -e "  ${GREEN}✓ docker/runtime_config.json 已生成（local 模式）${NC}"

    _render_template "$SCRIPT_DIR/docker/runtime_config-ssh.json.template" \
                     "$SCRIPT_DIR/docker/runtime_config-ssh.json"
    echo -e "  ${GREEN}✓ docker/runtime_config-ssh.json 已生成（SSH 模式）${NC}"
}

# ── 检查 Docker ──
check_docker() {
    if ! command -v docker &>/dev/null; then
        echo -e "${RED}Docker 未安装，请先安装 Docker Desktop${NC}"
        exit 1
    fi
    if ! docker info &>/dev/null; then
        echo -e "${RED}Docker daemon 未运行，请启动 Docker Desktop${NC}"
        exit 1
    fi
}

# ========== 主流程 ==========
echo "============================================"
echo "  Hermes Gateway 配置向导"
echo "============================================"

# 前置系统依赖检查
check_system_deps

echo ""

input_dk
input_ask
input_terminal
if [ "$TERMINAL_ENV" = "ssh" ]; then
    setup_ssh
fi
input_ports
input_workspace

# ── 写入 .env ──
echo ""
echo -e "${BOLD}[7] 写入配置${NC}"

# 保留旧 .env 中不由本脚本管理的行
old_extra=""
if [ -f "$ENV_FILE" ]; then
    old_extra=$(grep -v -E "^(DEEPSEEK_API_KEY=|API_SERVER_KEY=|TERMINAL_ENV=|SSH_USER=|SSH_HOST=|MASTER_PORT=|JUDGE_PORT=|REVIEWER_PORT=|PM_PORT=|DEV_PORT=|QA_PORT=)" "$ENV_FILE" 2>/dev/null || true)
fi

cat > "$ENV_FILE" << EOF
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
API_SERVER_KEY=${API_SERVER_KEY}
TERMINAL_ENV=${TERMINAL_ENV:-local}
SSH_USER=${SSH_USER:-}
SSH_HOST=${SSH_HOST:-}
MASTER_PORT=${MASTER_PORT:-8642}
JUDGE_PORT=${JUDGE_PORT:-8643}
REVIEWER_PORT=${REVIEWER_PORT:-8644}
PM_PORT=${PM_PORT:-8645}
DEV_PORT=${DEV_PORT:-8646}
QA_PORT=${QA_PORT:-8647}
EOF

if [ -n "$old_extra" ]; then
    echo "" >> "$ENV_FILE"
    echo "$old_extra" >> "$ENV_FILE"
fi
echo -e "  ${GREEN}✓ .env 已写入${NC}"

# ── [8] 生成 Runtime Config ──
echo ""
echo -e "${BOLD}[8] 生成 Docker Runtime Config${NC}"
generate_runtime_configs

echo ""
echo -e "  ${DIM}DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:0:8}...${NC}"
echo -e "  ${DIM}API_SERVER_KEY:   ${API_SERVER_KEY:0:8}...${NC}"
echo -e "  ${DIM}TERMINAL_ENV:     ${TERMINAL_ENV:-local}${NC}"
if [ -n "${SSH_USER:-}" ]; then
    echo -e "  ${DIM}SSH_USER:         ${SSH_USER}${NC}"
    echo -e "  ${DIM}SSH_HOST:         ${SSH_HOST:-host.docker.internal}${NC}"
fi
echo -e "  ${DIM}MASTER_PORT:      ${MASTER_PORT:-8642}${NC}"
echo -e "  ${DIM}JUDGE_PORT:       ${JUDGE_PORT:-8643}${NC}"
echo -e "  ${DIM}REVIEWER_PORT:    ${REVIEWER_PORT:-8644}${NC}"
echo -e "  ${DIM}PM_PORT:          ${PM_PORT:-8645}${NC}"
echo -e "  ${DIM}DEV_PORT:         ${DEV_PORT:-8646}${NC}"
echo -e "  ${DIM}QA_PORT:          ${QA_PORT:-8647}${NC}"

# ── 启动容器 ──
echo ""
echo -e "${BOLD}[9] 启动容器${NC}"
check_docker
read -p "  现在启动 Hermes Gateway? [Y/n]: " input_start
case "$input_start" in
    n|N|no)
        echo "  跳过，运行 docker compose up -d 手动启动"
        ;;
    *)
        echo "  停止旧容器..."
        docker compose down 2>/dev/null || true
        echo "  启动新容器..."
        docker compose up -d
        echo -e "  ${GREEN}✓ 容器已启动${NC}"
        echo ""
        echo "  查看日志: docker compose logs -f"
        echo "  测试连接: curl http://127.0.0.1:${MASTER_PORT:-8642}/health"
        ;;
esac

# ── [10] 运行工作流 ──
echo ""
echo -e "${BOLD}[10] 运行工作流${NC}"

# 自动创建 .venv + 安装依赖
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo -e "  ${DIM}创建 Python 虚拟环境...${NC}"
    $PYTHON -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    echo -e "  ${DIM}安装依赖...${NC}"
    pip install -r "$SCRIPT_DIR/requirements.txt" -q
    touch "$VENV_DIR/.deps_installed"
    echo -e "  ${GREEN}✓ 依赖已安装${NC}"
fi

CONFIG_FILE="docker/runtime_config-ssh.json"
if [ "$TERMINAL_ENV" = "local" ]; then
    CONFIG_FILE="docker/runtime_config.json"
fi
echo -e "  ${DIM}使用配置: $CONFIG_FILE${NC}"
read -p "  现在启动工作流? [Y/n]: " input_run
case "$input_run" in
    n|N|no)
        echo "  跳过，手动运行:"
        echo "  source .venv/bin/activate"
        echo "  python -m src.workflow --config $CONFIG_FILE"
        ;;
    *)
        echo "  启动工作流..."
        cd "$SCRIPT_DIR"
        python -m src.workflow --config "$SCRIPT_DIR/$CONFIG_FILE"
        ;;
esac