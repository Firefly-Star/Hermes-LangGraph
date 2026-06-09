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

## 3. [已完成] Dev 步骤失败处理机制改造

**已于 2026-06-09 完成。** 改动：
- `DevReviewStep` 失败后全部路由到 `step_retry`，不再走 `DevRollback`（git reset）或 `DevEscalate`（阻塞对话）
- 到达 `fail_rollback_threshold` / `fail_escalation_threshold` 时弹出 Windows MessageBox 通知用户，不阻塞、不破坏
- `DevRollback` 类和 `DevEscalate` 类已删除

---

## 4. 通用子图抽取

> 目标：将当前工作流中重复出现的子图模式抽取为可配置的通用组件，使本项目从一个 Web 全栈专用工作流变为可复用的框架。

### 4.1 重复模式分析

当前工作流（PM → Dev → QA）中存在 7 类重复子图模式，其中 4 类结构完全一致可直接抽取，3 类结构相似但当前仅有单一实现：

| 子图 | 出现次数 | 结构一致性 | 优先级 |
|:-----|:---------|:-----------|:-------|
| HandoffSubgraph | 3 次（PM/Dev/QA） | 100% 一致 | 高 |
| CriteriaDefinitionSubgraph | 3 次（PM/Dev/QA） | 100% 一致 | 高 |
| ArtifactReviewSubgraph | 5 次（PRD/Proto/Design/Plan/TestPlan/TestCode） | 100% 一致 | 高 |
| MasterFlushSubgraph | 4 次（Clarify/PM/Dev/QA） | 90% 一致 | 高 |
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

**配置接口**（已实现于 `src/workflow/subgraphs/handoff.py`）：

```python
@dataclass
class HandoffConfig:
    receiver: str                       # "pm" | "dev" | "qa"
    letter_title: str                   # 信件标题
    letter_prompt: str                  # 信件模板，可用 {workspace} {project_context} 占位
    context_letter_key: str             # 信件路径存 context 的 key
    domain: Optional[str] = None        # 节点名前缀，默认 {receiver}
    sender: str = "master"              # 发信人 agent
    conversation_key: str = "master_conv"  # 发信人对话的 context key
    create_dirs: tuple[str, ...] = ()   # 写信前创建的目录（相对 workspace）
    next_phase: Optional[str] = None    # 返回的 phase，默认 "{receiver}_handoff_done"
```

**调用方式**：

```python
handoff_def = HandoffSubgraph.define(config)       # 创建 Def 实例
handoff = handoff_def.register(graph, runtime)     # 注入 runtime + 注册节点
# handoff.entries["run"] / handoff.exits["run"]
graph.add_edge(prev, handoff.entries["run"])
```

---

#### 4.2.2 CriteriaDefinitionSubgraph

**职责**：Master 写审核标准 → Reviewer 审查 → PASS/FAIL。

```
CriteriaDefinitionSubgraph:
  entry → [Master 写标准] → [Reviewer 审查] → exit
                                       │
                                  FAIL ─┘ (loop back)
```

**配置接口**：

```python
@dataclass
class CriteriaDefinitionConfig:
    domain: str                             # 节点名前缀，"pm" | "dev" | "qa"
    criteria_title: str                     # 审核标准标题（显示用）
    criteria_prompt: str                    # 写标准的 prompt，支持 {workspace} {project_context} 占位
    criteria_filename: str                  # 标准文件名，如 "criteria-pm.md"
    context_key: str                        # context 中存标准文件路径的 key 前缀，如 "pm_criteria"
    review_conv: str                        # Reviewer 审查对话名，如 "review-pm-criteria"
    pass_judge_result: str                  # PASS 时 judge_result（给 graph.py 路由用）
    feedback_conv: str = ""                 # 反馈信对话名，默认 "{review_conv}-feedback"
    fail_judge_result: str = ""             # FAIL 时 judge_result，默认 "{domain}write_criteria"
    judge_tag: str = ""                     # judge 日志标签，默认 "judge-{domain}-criteria"
```

**注册接口**：

```python
result = CriteriaDefinitionSubgraph.define(config).register(graph, runtime)
# result.entries: {"run": "{domain}write_criteria"}
# result.exits: {"pass": "review_to_{domain}_artifact"}
# 内部已处理 FAIL 回退边（feedback → write 回环）
```

**节点内部结构**：

```
{domain}write_criteria ↔ review_{domain}_criteria
                                │
                           PASS ─┤
                                │
                           FAIL ─┘ (→ review_{domain}_criteria_feedback → 回写)
```

---

#### 4.2.3 ArtifactReviewSubgraph

**职责**：Reviewer 审查已有产出 → Judge 判读 → PASS 或写反馈信。

```
ArtifactReviewSubgraph:
  entry → [Reviewer 审查 + Judge 判读] → exit
                                  │
                            FAIL ──┘ (写反馈信)
```

**配置接口**：

```python
@dataclass
class ArtifactReviewConfig:
    domain: str                             # 节点名前缀
    review_title: str                       # 显示用标题
    review_prompt: str                      # 审查 prompt，支持 {workspace} {project_context} 占位
    review_conv: str                        # 审查对话名（review_conv_key 为空时使用）
    pass_judge_result: str                  # PASS 时 judge_result
    fail_judge_result: str                  # FAIL 时 judge_result
    review_text_key: str                    # 存审查意见的 context key
    feedback_path_key: str                  # 存反馈信路径的 context key
    review_conv_key: str = ""               # 对话名从 context 读取，优先级高于 review_conv
    agent_role: str = "reviewer"            # call_agent 的角色名
    feedback_sender: str = "reviewer"       # 写反馈信的 sender
    feedback_letter_title: str = "审查反馈"  # 反馈信标题
    criteria_path_key: str = ""             # 审核标准文件的 context key（可选）
    judge_tag: str = ""                     # judge 日志标签
    feedback_conv: str = ""                 # 反馈信对话名，默认 "{domain}_feedback"
    feedback_conv_key: str = ""             # 反馈信对话从 context 读取
    on_pass: Optional[Callable] = None      # 通过时调用 (state, runtime) → dict
```

**注册接口**：

```python
result = ArtifactReviewSubgraph.define(config).register(graph, runtime)
# result.entries: {"run": "{domain}_review"}
# result.exits: {"pass": "{domain}_review_pass", "fail": "{domain}_review_feedback"}
# 内部已处理 FAIL → write_feedback 边
```

**注意**：本子图只负责"审查"环节，不包含产出环节。产出（PRD、Prototype、Design、Plan、TestPlan、TestCode 等）由各 phase 的独立节点或外部子图负责，审查节点引用已产出的文件路径。

---

#### 4.2.4 MasterFlushSubgraph

**职责**：phase 边界写总结 → 重建 Master 对话 + 保存 checkpoint。

```
MasterFlushSubgraph:
  entry (write_summary) → (flush_conv + save_checkpoint) → exit
```

**配置接口**：

```python
@dataclass
class MasterFlushConfig:
    domain: str                         # "clarify" | "pm" | "dev" | "dev_step" | "qa"
    phase_name: str                     # 显示用阶段名
    next_step: str                      # 下一步描述（给 agent 看）
    artifacts: tuple[str, ...]          # 产物路径列表（支持 {workspace} 占位）
    resume_node: str                    # checkpoint 的 resume_node (如 "pm_handoff")
    summary_filename: str = ""          # 默认 "phase-summary-{domain}.md"
```

**注册接口**：

```python
result = MasterFlushSubgraph.define(config).register(graph, runtime)
# result.entries: {"write_summary": "master_flush_{domain}_summary"}
# result.exits: {"flush_conv": "master_flush_{domain}_conv"}
# 内部已连接 write_summary → flush_conv
```

**调用方式**：graph.add_edge(prev_node, result.entries["write_summary"])

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
    fail_rollback_threshold: int = 3    # 连续失败此阈值 → 弹窗提醒（不阻塞）
    fail_escalation_threshold: int = 5  # 连续失败此阈值 → 弹窗提醒（不阻塞）
```

**注意**：两个阈值当前仅触发 Windows MessageBox 异步弹窗通知用户，不执行 destructive 操作（无 git reset / 无阻塞对话）。所有失败路径均路由到 `step_retry`。

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
