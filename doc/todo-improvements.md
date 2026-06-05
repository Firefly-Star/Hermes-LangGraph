# 工作流框架待改进项

> 收集自 2026-06-03 测试运行中暴露的问题和后续优化方向。

---

## 1. 工具执行环境一致性

**问题**：Reviewer（以及其他 agent）的 `hermes_tools.terminal()` 跑在 Linux 容器内，但项目文件在 Windows 宿主机上。agent 尝试定位文件时路径拼不对（如 `langraph_test` 少个 `g`、`sandbox/tmp2` 不存在），直接卡死无法推进。

**背景**：这不是简单的路径字符串问题——agent 没有能力自行"发现"正确路径，它在容器里 `ls` 看到的文件系统跟 Windows 完全是两套。

**方向**：
- 统一文件访问方式，不让 agent 直接碰文件系统路径
- 通过 HTTP server 暴露工作目录，agent 只通过 URL 访问文件
- 或者在 runtime_config 中显式声明各 agent 的执行环境（host / container），由框架层做路径映射

---

## 2. 中断后的智能恢复

**问题**：Ctrl+U 中断只是打断当前 call_agent，返回节点重头跑。但中断发生时 agent 可能已经：
- 写了一半文件（部分写入、内容不完整）
- 创建了中间产物（如测试目录、临时文件）
- 修改了 state（如 dev_step_index 已经被更新）

重跑时会因为"已存在的文件"或"已递增的索引"导致状态不一致。

**方向**：
- 节点级幂等保护：重跑前清理当前节点可能产生的中间产物
- 事务性文件写入：写文件先写 `.tmp`，确认完成再 rename
- 取消当前的文件写操作（当前做不到）

---

## 3. 节点执行日志可视化

**问题**：工作流跑起来只有 stdout 输出。跑长流程时想查某个节点当时的产出/报错，只能翻终端日志，没有结构化存储。

**方向**：
- 每个节点执行时记录：入参 state、出参 state、耗时、call_agent 的 prompt/reply 摘要
- 输出到结构化的节点执行日志文件（如 `.agent_runtime/node-logs.jsonl`）
- 便于排查问题和工作流回放

---

## 4. 集成测试

**问题**：框架没有自测流程。每次修改 graph 后只能靠真实跑一遍来验证，成本高、反馈慢。一次完整测试跑下来可能几十分钟，修改→验证的循环太长。

**方向**：
- 写一套 mock agent 的集成测试，不调真实 LLM
- 各节点的 `call_agent` 用预设回复替代，验证边连接正确性和数据流
- 覆盖：正常流程全通路、中断恢复（各 resume 节点）、判断路由（PASS/FAIL 分支）

---

## 5. 非阻塞超时通知（替代阻塞式超时保护）

> 与用户确认的方向：不阻塞 agent 执行，只通知用户。

**问题**：PM agent 上次卡在 Ant Design 选择器问题上迭代了 5+ 轮，没有自动上报机制，用户不盯着看就不知道卡住了。

**方向**：
- 设定节点级别的耗时/轮次阈值，超时后发送通知（异步，不打断 agent）
- 通知方式：写入通知队列，用户主动查看或工作流暂停时展示
- 对已有的 Dev 步骤失败重试/回滚/升级机制，同理改为非阻塞通知优先，仅在明确配置时才自动执行回滚

---

## 6. Dev 步骤失败处理机制改造

**现状**：DevExecStep 有 fail_rollback_threshold（默认 3 次触发 git reset）和 fail_escalation_threshold（默认 5 次触发人工对话）。这些阈值写死在代码里，且自动执行破坏性操作（git reset --hard）。

**问题**：
- 自动 reset 可能丢失修改
- 阈值是硬编码，不够灵活
- 用户可能希望先被通知再决定操作

**方向**：
- 失败处理改为可配置策略（通知 → 等待指令 / 自动回滚 / 跳过）
- 默认策略改为：达到阈值后暂停并通知用户，由用户决定下一步
- 与第 5 项的通知机制统一

---

## 7. 通用子图抽取

> 目标：将当前工作流中重复出现的子图模式抽取为可配置的通用组件，使本项目从一个 Web 全栈专用工作流变为可复用的框架。

### 7.1 重复模式分析

当前工作流（PM → Dev → QA）中存在 7 类重复子图模式，其中 4 类结构完全一致可直接抽取，3 类结构相似但当前仅有单一实现：

| 子图 | 出现次数 | 结构一致性 | 优先级 |
|:-----|:---------|:-----------|:-------|
| HandoffSubgraph | 3 次（PM/Dev/QA） | 100% 一致 | 高 |
| CriteriaReviewSubgraph | 3 次（PM/Dev/QA） | 100% 一致 | 高 |
| ArtifactReviewSubgraph | 5 次（PRD/Proto/Design/Plan/TestPlan/TestCode） | 100% 一致 | 高 |
| FlushSubgraph | 4 次（Clarify/PM/Dev/QA） | 90% 一致 | 高 |
| AlignLoopSubgraph | 3 次（PM/Dev/QA） | 结构不同 | 中 |
| ExecReviewLoopSubgraph | 1 次（仅 Dev） | 模式通用但单一实现 | 低 |
| TestJudgeFixLoopSubgraph | 1 次（仅 QA） | 模式通用但单一实现 | 低 |

### 7.2 子图接口定义

#### 7.2.1 HandoffSubgraph

**职责**：Master 给下游 agent 写 handoff 信。

```
HandoffSubgraph:
  entry → [Master 写信] → exit
```

**配置接口**：

```python
@dataclass
class HandoffConfig:
    """Handoff 子图配置。"""
    sender: str                         # "master"
    receiver: str                       # "pm" | "dev" | "qa"
    letter_title: str                   # 信件标题
    letter_prompt_template: str         # 信件内容模板，可引用 {workspace}, {project_context}, 各文档路径
    context_letter_key: str             # 信件路径存入 context 的 key
    conversation: str                   # 使用的对话 (如 "master_conv")
```

**注册接口**：

```python
# 返回 (entries, exits)，节点名自动以 receiver 为前缀防冲突
handoff_entries, handoff_exits = HandoffSubgraph.register(graph, runtime, config)
# 使用例：
graph.add_edge(pre_flush_exit, handoff_entries["run"])
# exit 总是 {"run": "{receiver}_handoff"}
```

**跨阶段复用**：

```python
pm_handoff = HandoffConfig(
    sender="master", receiver="pm", letter_title="Master 给 PM 的信",
    letter_prompt_template=PM_HANDOFF_PROMPT,
    context_letter_key="pmletter_path", conversation="master_conv",
)
dev_handoff = HandoffConfig(
    sender="master", receiver="dev", letter_title="Master 给 Dev 的信",
    letter_prompt_template=DEV_HANDOFF_PROMPT,
    context_letter_key="devletter_path", conversation="master_conv",
)
qa_handoff = HandoffConfig(
    sender="master", receiver="qa", letter_title="Master 给 QA 的信",
    letter_prompt_template=QA_HANDOFF_PROMPT,
    context_letter_key="qaletter_path", conversation="master_conv",
)
```

---

#### 7.2.2 CriteriaReviewSubgraph

**职责**：Master 写审核标准 → Reviewer 审查 → PASS/FAIL。

```
CriteriaReviewSubgraph:
  entry → [Master 写标准] → [Reviewer 审查] → exit
                                       │
                                  FAIL ─┘ (loop back)
```

**配置接口**：

```python
@dataclass
class CriteriaReviewConfig:
    domain: str                         # "pm" | "dev" | "qa" — 用作节点名前缀
    criteria_title: str                 # 审核标准标题
    criteria_prompt: str                # 引导 Master 写标准的 prompt
    criteria_file_path: str             # 标准文件写入路径 (含文件名)
    context_file_key: str               # 标准文件路径存 context 的 key
    review_task: str                    # Reviewer 审查任务描述
    pass_phase: str                     # 审查通过后的 phase 值
```

**注册接口**：

```python
entries, exits = CriteriaReviewSubgraph.register(graph, runtime, config)
# entries: {"run": "{domain}_criteria"}
# exits: {"to_artifact": "{domain}_criteria_pass", "write_feedback": "{domain}_criteria_fail"}
# 内部已处理 FAIL 回退边，外部只需要连 exit → 下一节点
```

**节点内部结构**：

```
criteria_{domain}_write ↔ criteria_{domain}_review
                                │
                           PASS ─┤
                                │
                           FAIL ─┘ (内部已连回 write)
```

---

#### 7.2.3 ArtifactReviewSubgraph

**职责**：agent 产出文档/代码 → reviewer 审查 → PASS/FAIL。

```
ArtifactReviewSubgraph:
  entry → [agent 产出] → [reviewer 审查 + judge] → exit
                                          │
                                    FAIL ──┘ (loop back)
```

**配置接口**：

```python
@dataclass
class ArtifactReviewConfig:
    domain: str                         # 节点名前缀
    producer: str                       # 产出者 agent 名 (如 "pm", "dev", "qa")
    reviewer: str                       # 审查者 agent 名 (如 "reviewer", "master")
    conversation: str                   # 产出者使用的对话 context key
    production_prompt: str              # 引导产出者写产出的 prompt
    production_file_path: str           # 产出文件路径
    review_task: str                    # 审查任务描述
    context_output_key: str             # 产出路径存 context 的 key
    pass_phase: str                     # 审查通过的 phase
    has_internal_steps: bool = False    # 产出是否分多步（如 PM 要写 PRD + prototype）
    internal_steps: list[Step] = None   # 多步时的子步骤列表
```

**注册接口**：

```python
entries, exits = ArtifactReviewSubgraph.register(graph, runtime, config)
# entries: {"run": "{domain}_produce"}
# exits: {"to_next": "{domain}_produce_pass", "write_feedback": "{domain}_produce_fail"}
# 内部已处理 FAIL 回退边
```

**对比当前实现中的差异处理**：

- **单步产出**（Design、Plan、TestPlan、TestCode）：`[write_letter] → [read_letter + judge_reply]`
- **多步产出**（PM 的 PRD + Prototype）：`[write PRD] → [read PRD] → [write Proto] → [read Proto] → [review + judge]`
- 还支持 **HumanReview** 作为可选的 review 后置门控

子图通过 `has_internal_steps` 和可选的 `human_review` 参数来覆盖这些变体：

```python
@dataclass
class ArtifactReviewConfig:
    ...
    human_review: bool = False          # 审查通过后是否还需人工确认
```

---

#### 7.2.4 FlushSubgraph

**职责**：phase 边界写总结 + 重建 Master 对话 + 保存 checkpoint。

```
FlushSubgraph:
  entry → [write_summary] → [flush_conv + save_checkpoint] → exit
```

**配置接口**：

```python
@dataclass
class FlushConfig:
    domain: str                         # "clarify" | "pm" | "dev" | "qa"
    phase_name: str                     # 阶段中文名 (如 "需求澄清")
    summary_title: str                  # 总结 prompt
    resume_node: str                    # checkpoint 的 resume_node (如 "pm_handoff")
    next_handoff: str                   # 下一个 HandoffConfig 的 receiver
```

**注册接口**：

```python
entries, exits = FlushSubgraph.register(graph, runtime, config)
# entries: {"write_summary": "flush_{domain}_summary", "flush_conv": "flush_{domain}_conv"}
# exits: {"write_summary": "flush_{domain}_summary", "flush_conv": "flush_{domain}_conv"}
# 内部已连接 write_summary → flush_conv
```

---

#### 7.2.5 AlignLoopSubgraph（中期候选）

**职责**：agent 读 handoff → 理解对齐 → 判读是否完成。

**为什么是中期候选**：PM/Dev/QA 三者的对齐循环结构差异较大：

- **PMAlign**：PM 写理解 → Master 回复 → Judge 判读（A=通过/B=继续对齐/C=找用户澄清），涉及 3 个 agent
- **DevAlign**：Dev 写理解 → Master 判读（循环直到通过），涉及 2 个 agent
- **QAAlign**：QA 写理解 → 分别与 PM/Dev 对齐 → Master 最终确认，涉及 4 个 agent

强行统一会导致配置参数过多，建议等有第 4 个 phase 使用对齐循环后再考虑提取。当前保留为各 phase 自实现。

---

#### 7.2.6 ExecReviewLoopSubgraph（远期候选）

**职责**：分步执行 → 审查 → 提交/重试/回滚/升级。

```python
@dataclass
class ExecReviewConfig:
    domain: str
    executor: str                       # 执行者 agent
    reviewer: str                       # 审查者 agent  
    plan_source: str                    # 计划文件路径
    total_steps: int                    # 总步数
    rollback_threshold: int = 3         # 回滚阈值
    escalation_threshold: int = 5       # 升级阈值
```

当前仅用于 Dev 阶段，待出现第二个使用者时提取。

---

#### 7.2.7 TestJudgeFixLoopSubgraph（远期候选）

**职责**：运行测试 → 判读结果 → 修 bug → 重跑。

```python
@dataclass
class TestJudgeFixConfig:
    domain: str
    tester: str                         # 运行测试的 agent
    fixer: str                          # 修 bug 的 agent
    test_command: str                   # 测试运行命令
    # 其余配置与 ArtifactReview 类似
```

当前仅用于 QA 阶段，待出现第二个使用者时提取。

---

### 7.3 组合后的效果

#### graph.py 对比

**当前**（~170 行边定义）：

```python
graph.add_edge(PMHandoff.exits["run"], PMAlign.entries["read"])
graph.add_edge(PMAlign.exits["read"], MasterReplyPM.entries["run"])
graph.add_edge(MasterReplyPM.exits["run"], JudgeMasterReply.entries["run"])
graph.add_conditional_edges(JudgeMasterReply.exits["run"], ..., {...})
# ... 继续 20+ 行 PM 阶段边
```

**抽取后**（~40 行阶段定义）：

```python
# Phase 1: PM
pm_handoff = HandoffSubgraph.register(graph, runtime, PM_HANDOFF_CONFIG)
pm_align = AlignLoopSubgraph.register(graph, runtime, PM_ALIGN_CONFIG)  # phase-specific for now
pm_criteria = CriteriaReviewSubgraph.register(graph, runtime, PM_CRITERIA_CONFIG)
pm_prd = ArtifactReviewSubgraph.register(graph, runtime, PM_PRD_CONFIG)
pm_proto = ArtifactReviewSubgraph.register(graph, runtime, PM_PROTO_CONFIG)
pm_flush = FlushSubgraph.register(graph, runtime, PM_FLUSH_CONFIG)

graph.add_edge(ResumeRouter.exits["resume_pm"], pm_handoff.entries["run"])
graph.add_edge(pm_handoff.exits["run"], pm_align.entries["run"])
graph.add_edge(pm_align.exits["done"], pm_criteria.entries["run"])
graph.add_edge(pm_criteria.exits["to_artifact"], pm_prd.entries["run"])
graph.add_edge(pm_prd.exits["to_next"], pm_proto.entries["run"])
graph.add_edge(pm_proto.exits["to_next"], pm_flush.entries["write_summary"])
```

#### 超阶段组合

子图抽取的真正收益在于：**换一个项目类型时，只需要换配置和 prompt，不需要改图结构**。

```python
# 工作流 = 子图实例的组合
workflow_phases = [
    ClarifyPhase(ClARIFY_CONFIG),
    HandoffPhase(PM_HANDOFF_CONFIG, AlignPhase(PM_ALIGN_CONFIG),
                 CriteriaReview(PM_CRITERIA_CONFIG),
                 ArtifactReview(PM_PRD_CONFIG),
                 ArtifactReview(PM_PROTO_CONFIG),
                 HumanReview(PM_HUMAN_REVIEW_CONFIG)),
    HandoffPhase(DEV_HANDOFF_CONFIG, ...),
    HandoffPhase(QA_HANDOFF_CONFIG, ...),
    DeliveryPhase(AUDIT_CONFIG, DOCS_CONFIG, SUMMARY_CONFIG),
]

# 每个 Phase 自动注册节点和边
graph = build_framework(workflow_phases)
```

这种方式下，切换目标领域（如从 Web 全栈切到数据管线）只需要换 prompt 模板和 agent 角色名，核心图结构不变。

---

### 7.4 迁移路径

为避免大爆炸式重构，建议分三步走：

**Step 1：提取基础类**（当前项目内重构成 framework）
- 从 `phase1/2/3.py` 中把 Handoff、CriteriaReview、ArtifactReview 类签出为 `src/workflow/subgraphs/` 下的通用工厂
- 不改 graph.py 的边，只替换 import 来源
- 验证：现有测试运行不变

**Step 2：参数化配置**（从硬编码到配置驱动）
- 定义上述配置类
- 将各 phase 的配置集中到一个文件（如 `phase_configs.py`）
- graph.py 从配置驱动，不再直接 import 具体节点类

**Step 3：子图组合到框架**（拆分出 framework 层）
- 将通用子图移动到独立的包（如 `harness_engine/`）
- 保留当前 repo 作为 framework 的使用示例和 Web 全栈配置
- 新项目可以直接 pip install + 写配置
