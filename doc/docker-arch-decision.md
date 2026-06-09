# Docker 架构决策：Hermes Gateway 容器化 + Host 工作流

## 背景

当前 Docker 方案（v1）存在两个核心问题：

1. **权限问题**：容器内 Hermes 用户 UID 与宿主机不一致，bind mount 后文件操作权限冲突
2. **工具链割裂**：容器内没有 Node.js、MySQL、Playwright 等开发工具链，Agent 每次需要重新安装，或者通过 SSH 跳转——拿 mounted workspace 写到宿主机，矛盾地绕了一圈

## 试错记录

### v0：无容器化（初始状态）

最初项目没有 Docker 方案，用户需手动安装 Hermes Gateway 并配置 4 个 profile。运行工作流前要手动启动所有 gateway。开箱即用体验差。

### v1：全容器化（错误方向）

尝试将 Hermes Gateway + 工作流引擎 + 项目 workspace 全塞进一个容器：

- 直接把官方镜像 `nousresearch/hermes-agent:latest` 当基础镜像
- init.sh 启动 gateway + pip install + 运行工作流
- 项目代码通过 `.:/opt/workflow:ro` 挂载
- workspace 通过 `${WORKSPACE_DIR}:/workspace` bind mount

**遇到的问题**：

1. **pip 缺失**：镜像的 Python venv 没有 pip，`ensurepip` 也被裁剪。反复修了 3 次（pip → python3 -m pip → ensurepip → curl get-pip.py）
2. **profile 列表变更**：从 4 个 profile（cg/pm/dev/qa）改成 6 个（master/judge/reviewer/pm/dev/qa）后，旧的 marker 文件导致新 profile 创建被跳过，gateway 启动失败。修了一次（marker 不应拦住 profile 创建）
3. **UID 权限冲突**：容器内 `hermes` 用户（UID 非 1000）和宿主机用户 UID 不一致，bind mount 后读写互相冲突。最终解是 `chmod -R 777`，不优雅
4. **工具链缺失**：容器内没有 Node.js / MySQL / Playwright，Agent 每次要 SSH 到宿主机执行，绕回挂载本来想解决的问题
5. **工作流退出后 gateway 也停**：工作流跑完后容器退出，下次要重建，gateway 初始化（pip install 等）反复执行

### 转折点：Hermes 工具实现原理的发现

深入阅读 Hermes 源码后发现核心事实：

- read_file、write_file、terminal、execute_code **全部底层走 terminal backend**
- `write_file` 本质是 `cat > file << 'HERMES_EOF'`（Shell 命令），不是 Python 文件 API
- `execute_code` 生成 `hermes_tools.py` 存根，通过 RPC（UDS/文件）回调父进程，父进程最终还是调 terminal
- terminal.backend 支持 local / ssh / docker / modal 等多种后端

这意味着：**terminal.backend = ssh 后，Agent 所有操作都在 SSH 目标机上执行**，bind mount 变得多余。

### v2：Gateway 容器 + 工作流在宿主机（当前决策）

基于上述发现，将工作流移回宿主机，容器只跑 Hermes Gateway，Agent 操作通过 SSH 在宿主机上执行。

## 现状分析

Hermes 的所有工具（read_file / write_file / terminal / execute_code）**底层全部经过 terminal backend**。将 terminal.backend 设为 SSH 后，Agent 的所有文件操作和命令执行都在宿主机完成。Hermes Gateway 本身退化成一个纯粹的 LLM API 代理 + 工具调度器。

这意味着当前 Docker 方案中的 volume mount（代码、workspace、profile 数据）实际上可以被 SSH 替代。

## 方案对比

### 方案 A：当前方案（workflow + Hermes 全在容器内，bind mount workspace）

| 组件 | 位置 |
|:-----|:-----|
| Hermes Gateway | 容器内 |
| Workflow 引擎 | 容器内 |
| 项目文件 / 工具链 | bind mount，权限问题频发 |
| Agent 实际操作 | `terminal.backend=local` → 容器内 Shell → 通过 bind mount 映射到宿主机 |

**缺点**：
- UID 不匹配导致文件权限问题
- 工具链要在容器内重新安装或额外挂载
- SSH 跳板绕了一圈，还不如直接 SSH

### 方案 B：Hermes Gateway 容器 + 工作流在宿主机（推荐）

| 组件 | 位置 |
|:-----|:-----|
| Hermes Gateway | 容器内，监听 8642-8647 |
| Workflow 引擎 | **宿主机**（WSL2） |
| 项目文件 / 工具链 | **宿主机**，原生访问 |
| Agent 实际操作 | `terminal.backend=ssh` → SSH 连宿主机 WSL2 |

**优点**：
- 无权限问题：Agent 操作直接走 SSH，以宿主机用户身份执行
- 零工具链冲突：Node.js / Python / MySQL / Playwright 全在宿主机，Agent 拿来就用
- 零数据冗余：项目代码、.agent_runtime 都在宿主机，容器只做 API 代理
- 工作流直连 Gateway：localhost 端口映射即可，不经过额外网络层
- 容器可随时删除重建：对话数据在 named volume 中持久化，工具链和项目文件全在宿主机不受影响

**代价**：
- 宿主机需要装 SSH server（WSL2 默认已装）
- 宿主机需要 Node.js / Python / MySQL 等运行环境（本来就有）

## 新方案架构

```
┌─────────────────────────────────────────┐
│  Docker 容器                              │
│  ┌─────────────────────────────────┐    │
│  │  Hermes Gateway (x4-6)          │    │
│  │  port 8642-8647                 │    │
│  │  terminal.backend = ssh         │    │
│  └──────────┬──────────────────────┘    │
└─────────────┼────────────────────────────┘
              │ SSH (localhost)
              │ user@host
┌─────────────┼────────────────────────────┐
│  WSL2 宿主机  │                           │
│              ▼                            │
│  ┌─────────────────────────────────┐    │
│  │  Workflow 引擎                     │    │
│  │  python -m src.workflow          │    │
│  │  → POST localhost:8642 (master)  │    │
│  └──────────┬──────────────────────┘    │
│             │                             │
│             ▼                             │
│  ┌─────────────────────────────────┐    │
│  │  Agent 实际操作 (via SSH)        │    │
│  │  - read_file / write_file       │    │
│  │  - terminal (npm, node, pip)     │    │
│  │  - execute_code (Python 脚本)    │    │
│  └─────────────────────────────────┘    │
│                                           │
│  ┌─────────────────────────────────┐    │
│  │  项目文件 / .agent_runtime / Dev │    │
│  │  工具链：Node.js / Python / MySQL│    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

## 容器启动流程（简化）

相比 v1 的 init.sh（创建 6 个 profile + 写 config + 启动 gateway + pip install + 运行工作流），v2 的 init.sh 只需要：

1. 从模板创建 profile + config（同 v1，这是 Hermes 本身的初始化）
2. 启动 4-6 个 Gateway

工作流不再由容器启动——用户在宿主机手动执行 `python -m src.workflow`。

## Docker Compose 配置变化

```yaml
# v2 核心：只有 Gateway，没有工作流、没有 workspace 挂载
services:
  hermes-gateway:
    image: nousresearch/hermes-agent:latest
    container_name: hermes-gateway
    ports:
      - "${MASTER_PORT:-8642}:8642"
      - "${JUDGE_PORT:-8643}:8643"
      - "${REVIEWER_PORT:-8644}:8644"
      - "${PM_PORT:-8645}:8645"
      - "${DEV_PORT:-8646}:8646"
      - "${QA_PORT:-8647}:8647"
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:?err}
    volumes:
      - hermes-data:/opt/data       # 只需保存 profile 配置数据
    # 不再需要: .:/opt/workflow:ro
    # 不再需要: ${WORKSPACE_DIR}:/workspace
```

## 迁移步骤

1. 新写 `docker-compose.yml`（去掉工作流挂载，纯 Gateway）
2. 简化 `docker/hermes/init.sh`（去掉 pip install 和工作流启动）
3. 创建 `docker/runtime_config-ssh.json`（terminal.backend=ssh，端口指向 Gateway）
4. 用户前置条件：WSL2 SSH server + `ssh-keygen` + 公钥认证
5. 用户启动方式改为两步：`docker compose up -d` → `python -m src.workflow --config docker/runtime_config-ssh.json`

## 不做的改动

- **不改 Hermes Gateway 镜像**：直接用官方 `nousresearch/hermes-agent:latest`，不额外打包
- **不修改 workflow 源码**：工作流只改 runtime_config.json，代码逻辑不变
- **不修改 profile 创建逻辑**：init.sh 的 profile 初始化逻辑保持 v1 的方式

## 与 v1 对比

| 对比项 | v1（全容器） | v2（SSH） |
|:-------|:-------------|:----------|
| 配置复杂度 | 低（一个命令启动） | 中（需提前配 SSH） |
| 权限问题 | 有（UID 不匹配） | 无（SSH 用宿主机身份） |
| 工具链可用性 | 容器内不全 | 宿主机全部可用 |
| 重置难度 | 低（容器重启即可） | 低（```docker compose down && up``` 即可，volume 保留） |
| 第一次上手门槛 | 低 | 中（需要 SSH key） |
| 适用场景 | Hermes 独立试用 | 实际项目协作开发 |
