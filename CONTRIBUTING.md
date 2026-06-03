# Contributing

## 环境搭建

```bash
# 1. 克隆
git clone <repo-url>
cd Hermes-LangGraph

# 2. 安装依赖
pip install -r requirements.txt

# 3. 确认 Hermes Gateway 已安装
#    默认路径：~/AppData/Local/hermes（Windows）

# 4. 配置 runtime_config.json（路径、端口、输出目标）

# 5. 运行
python -m src.workflow

# 6. 跑测试
python -m pytest test/ -v
```

## 编码规范

见项目根目录 `CLAUDE.md`，关键几条：

- 文件/变量/函数：英文 snake_case
- 文档/注释：中文（代码内注释用英文）
- 提交信息：`<type>: <subject>` 英文（feat/fix/refactor/docs/test）
- 一步一 commit，commit 前需要确认

## 如何新增一个节点

1. 在对应 phase 文件中新建 class，继承 entries/exits/register 模式
2. 每个 `@staticmethod` 只包含一次 `call_agent`（或 judge_reply/read_letter 等等价调用）
3. 在 `register_nodes()` 中注册，组内条件边在 register 内定义
4. 在 `graph.py` 中 import + register + 连线（跨组边走 exits → entries）

```python
class MyNode:
    entries = {"run": "my_node_run"}
    exits = {"run": "my_node_run"}
    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = MyNode._runtime
        reply = call_agent(runtime, "master", conv, "prompt")
        return {"phase": "next_phase", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"my_node_run": cls.run})
```

## 新增一个 Agent

在 `runtime_config.json` 的 `agents` 节添加条目：

```json
"agent_name": {"profile": "profile_name", "port": 8646}
```

确认对应的 Hermes profile 存在（不存在则 `hermes profile create`），Gateway 端口不冲突。

## 设计文档

- `doc/workflow-design-v6.md` — 整体架构设计
- `doc/workflow-mechanisms.md` — 各机制详细说明
- `doc/agent_runtime-api-reference.md` — API 参考
- `doc/config-reference.md` — 配置说明
