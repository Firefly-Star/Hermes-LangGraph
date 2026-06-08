# 工作流框架待改进项

> 收集自 2026-06-03 测试运行中暴露的问题和后续优化方向。

---

## 1. 节点执行日志可视化

**问题**：工作流跑起来只有 stdout 输出。跑长流程时想查某个节点当时的产出/报错，只能翻终端日志，没有结构化存储。

**方向**：
- 每个节点执行时记录：入参 state、出参 state、耗时、call_agent 的 prompt/reply 摘要
- 输出到结构化的节点执行日志文件（如 `.agent_runtime/node-logs.jsonl`）
- 便于排查问题和工作流回放

---

## 2. 非阻塞超时通知（替代阻塞式超时保护）

> 与用户确认的方向：不阻塞 agent 执行，只通知用户。

**问题**：PM agent 上次卡在 Ant Design 选择器问题上迭代了 5+ 轮，没有自动上报机制，用户不盯着看就不知道卡住了。

**方向**：
- 设定节点级别的耗时/轮次阈值，超时后发送通知（异步，不打断 agent）
- 通知方式：写入通知队列，用户主动查看或工作流暂停时展示
- 对已有的 Dev 步骤失败重试/回滚/升级机制，同理改为非阻塞通知优先，仅在明确配置时才自动执行回滚

---

## 3. Dev 步骤失败处理机制改造

**现状**：DevExecStep 有 fail_rollback_threshold（默认 3 次触发 git reset）和 fail_escalation_threshold（默认 5 次触发人工对话）。这些阈值写死在代码里，且自动执行破坏性操作（git reset --hard）。

**问题**：
- 自动 reset 可能丢失修改
- 到达阈值后自动执行回滚/升级，用户没有被通知和决定的机会

**方向**：
- 失败处理改为可配置策略（通知 → 等待指令 / 自动回滚 / 跳过）
- 默认策略改为：达到阈值后暂停并通知用户，由用户决定下一步
- 与第 2 项的通知机制统一

---

## 4. 通用子图抽取

> 目标：将当前工作流中重复出现的子图模式抽取为可配置的通用组件，使本项目从一个 Web 全栈专用工作流变为可复用的框架。

### 4.1 重复模式分析

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

### 4.2 子图接口定义

#### 4.2.1 HandoffSubgraph

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

#### 4.2.2 CriteriaReviewSubgraph

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

#### 4.2.3 ArtifactReviewSubgraph

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

#### 4.2.4 FlushSubgraph

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

#### 4.2.5 AlignLoopSubgraph（中期候选）

**职责**：agent 读 handoff → 理解对齐 → 判读是否完成。

**为什么是中期候选**：PM/Dev/QA 三者的对齐循环结构差异较大：

- **PMAlign**：PM 写理解 → Master 回复 → Judge 判读（A=通过/B=继续对齐/C=找用户澄清），涉及 3 个 agent
- **DevAlign**：Dev 写理解 → Master 判读（循环直到通过），涉及 2 个 agent
- **QAAlign**：QA 写理解 → 分别与 PM/Dev 对齐 → Master 最终确认，涉及 4 个 agent

强行统一会导致配置参数过多，建议等有第 4 个 phase 使用对齐循环后再考虑提取。当前保留为各 phase 自实现。

---

#### 4.2.6 ExecReviewLoopSubgraph（远期候选）

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

#### 4.2.7 TestJudgeFixLoopSubgraph（远期候选）

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

### 4.3 组合后的效果

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

### 4.4 迁移路径

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
