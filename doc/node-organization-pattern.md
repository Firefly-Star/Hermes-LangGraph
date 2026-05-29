# Node 组织约定

## 问题

原工作流中一个 node 函数可能包含多次 `call_agent` 调用，无法精确中断（Ctrl+U 打断的是整个 node，恢复时从头重入浪费 token）。需要将每个 node 拆分为「一个 node 只包含一次 `call_agent` 调用」的最小粒度。

拆分后，原本一个 node 变成多个 node，这些 node 在代码上需要保持内聚——它们属于同一个逻辑分组，连接拓扑也应封装在内。

## 约定

### 每个逻辑分组是一个类

原 node 函数 `snake_case_name` 拆分为 PascalCase 类：

```python
# 原 pre_flight_clarify → 类 PreFlightClarify
class PreFlightClarify:
    """原 pre_flight_clarify 拆分后的逻辑分组。"""
```

### 类内节点以 @staticmethod 表示

每个 `call_agent` 对应一个 `@staticmethod`，方法名取能表达其职责的短名称：

```python
class PreFlightClarify:
    @staticmethod
    def init(state) -> dict:
        """Setup + init Master conversation (Call 1)."""
        runtime = PreFlightClarify._runtime
        ...

    @staticmethod
    def close(state) -> dict:
        """Write project_context.md (Call 5)."""
        runtime = PreFlightClarify._runtime
        ...
```

### _runtime 通过类属性传递

```python
class PreFlightClarify:
    _runtime = None

    @staticmethod
    def init(state):
        runtime = PreFlightClarify._runtime  # 从类上取，不从自身取
```

### entries / exits 声明对外连接

- **`entries`**: 本组的入口节点，dict，键为方法名，值为图节点名（注册时的名字）
- **`exits`**: 本组的出口节点，dict，键为方法名，值为图节点名

`entries` 只声明节点名（不做连线），`exits` 也只声明节点名（不维护 dst）。具体连线在 `graph.py` 中完成，因为 dst 可能涉及条件边。

```python
class PreFlightClarify:
    entries = {"init": "pre_flight_init"}
    exits = {"close": "clarify_close"}
```

同一个方法可以同时出现在 `entries` 和 `exits` 中（既是入点也是出点）。

### register 类方法注册节点和内部边

```python
from .utils import register_nodes

class PreFlightClarify:
    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "pre_flight_init": cls.init,
            "clarify_ask": cls.ask,
            ...
        })

        # 组内边（不出本组的边）
        graph.add_edge("pre_flight_init", "clarify_ask")
        graph.add_conditional_edges("clarify_ask", ..., {...})
```

### register_nodes 工具函数

在 `utils.py` 中，统一处理 `interruptible` 包装和 `_runtime` 透传：

```python
def register_nodes(graph, runtime, nodes: dict):
    for name, fn in nodes.items():
        fn.__name__ = name
        wrapped = interruptible(fn)
        wrapped.__wrapped__._runtime = runtime
        wrapped._runtime = runtime
        graph.add_node(name, wrapped)
```

### graph.py 的责任

1. 调用各组的 `register` 注册节点和内部边
2. 通过 `entries` / `exits` 引用对外接线
3. 维护全局的路由拓扑（跨组边、条件边）

```python
def build_graph(runtime):
    graph = StateGraph(WorkflowState)

    NODES = [interruptible(raw_node), ...]  # 尚未重构的原始节点
    for f in NODES:
        graph.add_node(f.__name__, f)

    PreFlightClarify.register(graph, runtime)
    PMHandoff.register(graph, runtime)
    # ...

    graph.set_entry_point("resume_router")

    # 跨组边：exits["close"] → flush → entries["run"]
    graph.add_edge(PreFlightClarify.exits["close"], "master_flush_after_clarify")
    graph.add_edge("master_flush_after_clarify", PMHandoff.entries["run"])
    graph.add_edge(PMHandoff.exits["run"], "pm_align")
    # ...
```

## 命名对照

| 原 node 函数 | 类名 | 文件 |
|:---|:---|:---|
| `pre_flight_clarify` | `PreFlightClarify` | `src/workflow/phase0.py` |
| `pm_handoff` | `PMHandoff` | `src/workflow/phase1.py` |
