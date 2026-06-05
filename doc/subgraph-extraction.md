# 通用子图架构

> 工作流框架中重复出现的子图模式抽为可配置的通用组件。
> 架构受 C++ 设计模式启发：ABC 基类定义契约，具体 Def 类实现图拓扑，工厂类只负责创建闭包。

## 动机

原始工作流（`phase1.py` / `phase2.py` / `phase3.py`）中，多个节点组有相同的图结构，唯一的区别是角色名、prompt 内容和文件路径：

- **Handoff** — Master 写信给下游 agent（PM/Dev/QA），3 次
- **CriteriaReview** — 写审核标准 → 审查 → PASS/FAIL，3 次
- **ArtifactReview** — agent 产出 → 审查 → PASS/FAIL，5 次（待抽取）
- **Flush** — 阶段总结 → 重建对话 → checkpoint，4 次（待抽取）

将其抽取为配置驱动的子图，消除重复代码。

## 架构

### 三层分离

每个子图由三个层级组成，职责严格分离：

```
┌─ 工厂类（Factory）──────────────┐
│  HandoffSubgraph.define(config)  │  ← 只创建闭包，不知晓图结构
│  CriteriaDefinitionSubgraph      │
└──────────┬───────────────────────┘
           │ 返回 Def 实例
           ▼
┌─ Def 类（具体子图）──────────────┐
│  HandoffDef(SubgraphDef)         │  ← 知晓自己的图拓扑
│  CriteriaDefinitionDef           │  ← register() 做所有接线
│  .register(graph, runtime)       │
└──────────┬───────────────────────┘
           │ 返回 SubgraphResult
           ▼
┌─ ABC 基类 ───────────────────────┐
│  SubgraphDef                     │  ← 定义契约
│  - nodes: dict[str, Callable]    │
│  - entries: dict | None          │
│  - exits: dict | None            │
│  + register(graph, runtime)      │
└──────────────────────────────────┘
```

### SubgraphDef（ABC 基类）

```python
class SubgraphDef(ABC):
    nodes: dict[str, Callable] = {}       # define 时填充
    entries: dict | None = None           # register 前为 None
    exits: dict | None = None             # register 前为 None

    @abstractmethod
    def register(self, graph, runtime) -> SubgraphResult:
        """注入 runtime、注册节点、加组内边，设置 entries/exits。"""
```

**为什么 entries/exits 在 register 前为 None**：Def 实例创建时节点函数尚未注入 runtime，不能注册到图，也就不知道最终图节点名。register 调用后才确定。

### 具体 Def 类 — 掌握图拓扑

每个具体子图在自己的 `register()` 中定义组内边：

```python
class CriteriaDefinitionDef(SubgraphDef):
    def __init__(self, nodes, pass_judge_result, fail_judge_result):
        self.nodes = nodes           # dict 保持插入顺序
        self._pass = pass_judge_result
        self._fail = fail_judge_result

    def register(self, graph, runtime) -> SubgraphResult:
        for fn in self.nodes.values():
            fn._runtime = runtime    # 注入 runtime
        register_nodes(graph, runtime, self.nodes)
        w, r, p, f = self.nodes     # 依赖 dict 插入顺序
        graph.add_edge(w, r)
        graph.add_conditional_edges(r, lambda s: s.get("judge_result", ""), {
            self._pass: p,
            self._fail: f,
        })
        graph.add_edge(f, w)
        self.entries = {"run": w}
        self.exits = {"pass": p}
        return SubgraphResult(entries=self.entries, exits=self.exits)
```

### 工厂类 — 只 define，不 register

工厂类只提供静态 `define(config)` 方法，创建节点闭包，返回 Def 实例：

```python
class HandoffSubgraph:
    @staticmethod
    def define(config: HandoffConfig) -> HandoffDef:
        node_name = f"{config.domain}_handoff"

        def run(state):
            rt = run._runtime
            # ... 业务逻辑 ...

        return HandoffDef(node_name=node_name, run=run)
```

工厂对 register 的具体方式（有什么边、有什么条件路由）零了解。

### SubgraphResult

```python
class SubgraphResult:
    __slots__ = ("entries", "exits")

    def __init__(self, entries: dict, exits: dict):
        self.entries = entries
        self.exits = exits
```

让 graph.py 以一致的 `obj.entries["run"]` / `obj.exits["pass"]` 方式引用入口/出口，不关心具体子图类型。

## 配置与注册的调用链

```
# phase1.py — 定义配置 + 创建子图
PM_CRITERIA_CONFIG = CriteriaDefinitionConfig(
    domain="pm",
    criteria_title="PM 审核标准",
    criteria_prompt=...,
    criteria_filename="criteria-pm.md",
    context_key="pm_criteria",
    review_conv="review-pm-criteria",
    pass_judge_result="pmwrite_prd_letter",
)
PM_CRITERIA_DEF = CriteriaDefinitionSubgraph.define(PM_CRITERIA_CONFIG)

# graph.py — 注册到 LangGraph
pm_criteria = PM_CRITERIA_DEF.register(graph, runtime)

# graph.py — 连跨组边
graph.add_edge(pm_criteria.exits["pass"], PMWriteDoc.entries["write_prd_letter"])
```

配置在 phase 模块中定义（语义合理），注册在 graph.py 中执行（只有那里有 runtime）。

## 已实现的子图

| 子图 | 工厂 | Def | 节点数 | 图拓扑 |
|:-----|:-----|:----|:-------|:-------|
| Handoff | `HandoffSubgraph` | `HandoffDef` | 1 | 单节点 |
| CriteriaDefinition | `CriteriaDefinitionSubgraph` | `CriteriaDefinitionDef` | 4 | write → review → pass/feedback → write（循环） |

## 待抽取模式

- **ArtifactReview** — agent 产出 → 审查 → PASS/FAIL（PM 文档审查、Dev design/plan 审查、QA plan/code 审查），出现 5 次
- **Flush** — 阶段总结 → 重建对话 → checkpoint（4 次）
