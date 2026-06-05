# 通用子图抽取设计

> 工作流框架中重复出现的子图模式抽为可配置的通用组件。

## 动机

原始工作流（`phase1.py` / `phase2.py` / `phase3.py`）中，多个节点组有相同的图结构，唯一的区别是角色名、prompt 内容和文件路径：

- **Handoff** — Master 写信给下游 agent（PM/Dev/QA），3 次
- **CriteriaReview** — 写审核标准 → 审查 → PASS/FAIL，3 次
- **ArtifactReview** — agent 产出 → 审查 → PASS/FAIL，5 次
- **Flush** — 阶段总结 → 重建对话 → checkpoint，4 次

将其抽取为配置驱动的子图工厂，消除重复代码，并为未来新项目提供可复用的构建块。

## 架构

### 子图工厂模式

每个子图由两个核心概念组成：

1. **Config** — `@dataclass` 配置，决定图结构和节点行为的差异点
2. **Subgraph** — 工厂类，含 `register(graph, runtime, config)` 静态方法

```python
result = SomeSubgraph.register(graph, runtime, config)
# result = SubgraphResult(entries={...}, exits={...})
# graph.py 通过 result.entries / result.exits 连边
```

### SubgraphResult

统一返回值，让调用方以一致的 `obj.entries["key"]` 方式引用入口/出口：

```python
@dataclass
class SubgraphResult:
    entries: dict    # {方法名: 节点名}
    exits: dict      # {方法名: 节点名}
```

对比原始模式的类属性引用：

```
# 原始：读类属性
PMHandoff.entries["run"]

# 抽取后：读对象属性
pm_handoff.entries["run"]
```

两种读法在使用上无差别，但抽取后的 entries/exits 由 config 动态生成，不是硬编码。

### HandoffSubgraph 实现（参考实现）

```python
@dataclass
class HandoffConfig:
    receiver: str                       # "pm" | "dev" | "qa"
    letter_title: str                   # 信件标题
    letter_prompt: str                  # 信件模板，可用 {workspace} {project_context} 占位
    context_letter_key: str             # 信件路径存 context 的 key
    sender: str = "master"              # 发信人 agent 名
    conversation_key: str = "master_conv"
    create_dirs: tuple[str, ...] = ()
    next_phase: Optional[str] = None    # 留空自动设为 "{receiver}_handoff_done"

    def __post_init__(self):
        if self.next_phase is None:
            self.next_phase = f"{self.receiver}_handoff_done"


class HandoffSubgraph:
    @staticmethod
    def register(graph, runtime, config: HandoffConfig) -> SubgraphResult:
        node_name = f"{config.receiver}_handoff"

        def run(state):
            # 创建目录 → 解析模板占位符 → write_letter → 存 context → 返回 phase
            ...

        register_nodes(graph, runtime, {node_name: run})
        return SubgraphResult(
            entries={"run": node_name},
            exits={"run": node_name},
        )
```

工厂内部创建闭包捕获 config，节点注册到 LangGraph 后返回 entries/exits 给 graph.py 连边。

### 边界：__post_init__ 处理动态默认值

Config 中那些依赖其他字段的默认值（如 `next_phase` 依赖 `receiver`），在 `__post_init__` 中计算。不依赖其他字段的（如 `sender="master"`）直接写静态默认值。二者混用是 dataclass 的常规做法。

## 配置 vs 注册

子图抽取的关键设计决策：**配置和注册分离**。

- **配置**（Config）在 phase 模块中定义，紧邻该阶段的 prompt 常量
- **注册**（Subgraph.register）在 graph.py 中执行，因为只有那里有 runtime 实例

```
# phase1.py — 定义配置
PM_HANDOFF_CONFIG = HandoffConfig(receiver="pm", ...)

# graph.py — 执行注册
pm_handoff = HandoffSubgraph.register(graph, runtime, PM_HANDOFF_CONFIG)
```

这种分离让配置可以放在语义最合理的位置（如 PM 相关的 config 放在 phase1.py），而注册统一发生在 graph 构建入口。

## 迁移路径

三步走，不破坏现有代码：

1. **Handoff 先行**（已完成）— 从三个 handoff 类中抽取 HandoffSubgraph
2. **CriteriaReview + ArtifactReview** — 相同模式，各自 3+ 次重复
3. **Flush** — 4 次重复，且 resume_node 等配置项天然适合 config 化

每步独立可提交，不需要大爆炸式重构。
