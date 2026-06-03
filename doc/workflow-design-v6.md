# LangGraph 工作流编排设计 v6

## 变更对比（v5 → v6）

| 变更 | v5 | v6 |
|:-----|:---|:---|
| 源文件结构 | 单 `workflow.py` | 拆分为 `graph.py` + `phase0~3.py` + `flush.py` + `checkpoint.py` |
| 节点组织 | 独立函数 | 全部 class + entries/exits + register 模式 |
| 每节点 call_agent | 可能多个 | 严格一个（除 judge_reply 可共存） |
| NODES 列表 | 全部节点 | 仅剩 qa_handoff + qa_align |
| Phase 0 clarify | 6 节点循环 | 3 节点（init → clarify → close），用 clarify_loop 工具函数 |
| clarify_loop | 含 judge 判读 + 确认子循环 | 简化为用户↔Master 循环，无 judge |
| Phase 1 flush | 单函数 | `MasterFlushClarify` 类，2 节点 |
| Phase 2 review | 无 design 审查 | 新增 `DevReviewDesign` 节点 |
| Phase 2 flush | 单函数 | `MasterFlushPM` 类，2 节点 |
| Phase 3 flush | 单函数 | `MasterFlushDev` 类，2 节点 |
| QA 阶段 | `qa_handoff` + `qa_align` 两个独立函数 | `QAHandoff` + `QAAlign` 类，9 节点；扩展为测试全流程（写标准 → 写计划 → 写代码 → 运行 → 修 bug 循环） |
| Phase 4 交付 | 不存在 | 新增 `ConsistencyAudit` + `WriteMaintenanceDocs` + `DeliverySummary`，交付阶段（一致性审计 → 写维护文档 → 交付总结） |
| Resume 节点 | 5 个独立函数 | `ResumeRouter` 类，5+1 节点 |
| 中断 flag 清理 | except 块冗余清除 | 全部移除（call_agent 抛出异常前已自清） |
| ensure_write_file | stream=True | stream=False，不与中断冲突 |

## 依赖关系

```
langgraph (1.2.0)
langgraph-checkpoint (4.1.0)
requests (2.33.0)
```

## 源文件结构

工作流已拆分为多个模块，按阶段组织：

```
src/workflow/
├── __init__.py       # 包标记
├── __main__.py       # Entry point: python -m src.workflow
├── graph.py          # LangGraph 图构建入口 + main()
├── prompt.py         # 常量与 prompt 模板
├── utils.py          # 工具函数：call_agent, register_nodes, clarify_loop 等
├── phase0.py         # PreFlightClarify — 需求澄清
├── phase1.py         # PMHandoff ~ HumanReview — PM 出方案
├── phase2.py         # DevHandoff ~ DevEscalate — Dev 设计 + 编码
├── phase3.py         # QAHandoff + QAAlign + QA 测试全流程（计划→代码→运行→修 bug）
├── phase4.py         # ConsistencyAudit + WriteMaintenanceDocs + DeliverySummary — 交付
├── flush.py          # MasterFlushClarify/PM/Dev/QA — phase 边界 flush
├── checkpoint.py     # ResumeRouter + save/load/clear_checkpoint
```

`graph.py` 中的 `NODES` 列表已清空（所有节点均为 class + register 模式）。

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│  graph.py                                                         │
│  StateGraph + WorkflowState + MemorySaver                        │
│  + AgentRuntime (agent/conversation/context/logger/checkpoint)    │
│                                                                    │
│   ┌────────┐  ┌──────────┐  ┌──────┐  ┌──────┐  ┌──────┐        │
│   │ Master │  │ Reviewer │  │  PM  │  │ Dev  │  │  QA  │        │
│   │ 8642   │  │ 8642     │  │ 8643 │  │ 8644 │  │ 8645 │        │
│   │ cg     │  │ cg       │  │ pm   │  │ dev  │  │ qa   │        │
│   └────┴──┘  └────┴─────┘  └──┬───┘  └──┬───┘  └──┬───┘        │
│        └──shared gateway──┘     │         │         │           │
│                           separate gateways                      │
└──────────────────────────────────────────────────────────────────┘
```

| Agent | Profile | Gateway Port | 用途 |
|:------|:--------|:-------------|:------|
| **Master** | `cg` | 8642 | 编排决策、写审核标准、维护 state、回答疑问 |
| **Judge** | `cg` | 8642（同 gateway） | 回复分类（A/B/C/D 路由），每次独立 conversation |
| **Reviewer** | `cg` | 8642（同 gateway） | 按标准审查产出，不同 conversation |
| **PM** | `pm` | 8643 | 需求分析 + HTML 静态原型 |
| **Dev** | `dev` | 8644 | 详细设计 + 代码实现 |
| **QA** | `qa` | 8645 | 测试计划 + 测试执行 |

## 状态定义

```python
class WorkflowState(TypedDict):
    phase: str              # 当前阶段名
    judge_result: str       # Judge 判读结果，用于条件边路由
```

## 节点组织约定

每个逻辑分组是一个类，遵循统一模式：

```python
class SomeNode:
    entries = {"method_name": "registered_node_name"}
    exits = {"method_name": "registered_node_name"}
    _runtime = None

    @staticmethod
    def method_name(state) -> dict:
        runtime = SomeNode._runtime
        # ... 1 call_agent（或 write_letter / read_letter / judge_reply）
        return {"phase": "xxx", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "registered_node_name": cls.method_name,
        })
        # 组内边（条件边、连接边）
```

- entries/exits 键名为方法名，值为注册时的图节点名
- graph.py 通过 `ClassName.entries["xx"]` / `ClassName.exits["xx"]` 引用
- 每个 `@staticmethod` 只包含一次 `call_agent`（或等价调用）
- `judge_reply`（stream=False）可与前一个 call_agent 共存于同一节点
- 组内条件路由在 `register()` 内定义，graph.py 只做跨组简单边

## Judge 路由

Judge 是工作流的路由枢纽。使用通用 `judge_reply()` 公用函数，每次调用独立 conversation：

```python
judge_reply(runtime, target_role, reply, options, tag)
```

返回值：选项字母（A/B/C/D），根据 options 列表首字母确定。

## Agent 通信模型

通过"信件"机制（`handoffs/` 目录下的 markdown 文件）：

- **write_letter** — sender 用 write_file 写一封信到指定路径。重跑时先删旧文件。
- **read_letter** — 给 receiver 信件路径，让 agent 自读
- **read_and_write_letter** — 读输入信 → 写回信。重跑时删旧输出文件。

## Conversation 命名

| 用途 | Agent | Conversation 名 |
|:-----|:------|:----------------|
| Master 全流程 | master | `master-{ws}-{ts}` |
| Judge 判读 | judge | `judge-{tag}-{ws}-{ts}` |
| Reviewer 审查 | reviewer | `review-{type}-{ws}-{ts}` |
| PM 对齐 | pm | `pm-align-{ws}-{ts}` |
| PM 出文档 | pm | `pm-doc-{ws}-{ts}` |
| Dev 对齐 | dev | `dev-align-{ws}-{ts}` |
| Dev 设计 | dev | `dev-design-{ws}-{ts}` |
| Dev 计划 | dev | `dev-plan-{ws}-{ts}` |
| Dev 执行 | dev | `dev-exec-{ws}-{ts}` |
| Dev git init | dev | `dev-git-init-{ws}-{ts}` |
| QA 对齐 | qa | `qa-align-{ws}-{ts}` |
| QA 测试计划 | qa | `qa-plan-{ws}-{ts}` |
| QA 测试代码 | qa | `qa-code-{ws}-{ts}` |
| QA 测试运行 | qa | `qa-run-{ws}-{ts}` |
| 一致性审计 | master | `master-{ws}-{ts}`（沿用） |
| 写维护文档 | dev | `dev-doc-{ws}-{ts}` |
| 交付总结 | master | `master-{ws}-{ts}`（沿用） |

## 图结构

### 入口：ResumeRouter

```
resume_router → 检测 checkpoint
              ├─ 无 → resume_to_pre_flight
              ├─ 有 + 用户确认 y → resume_pm_handoff / resume_dev_handoff
              │                        / resume_qa_handoff / resume_dev_exec_step
              └─ 有 + 用户否决 → resume_to_pre_flight
```

ResumeRouter 类内部包含条件路由（5 个目标全部为组内节点）：
- `to_pre_flight` — 空节点，外部 `add_edge` 到 `PreFlightClarify.entries["init"]`
- `resume_pm` — 清 PM 产出 + 重建 Master 对话
- `resume_dev` — 清 Dev 产出 + 重建 Master 对话
- `resume_qa` — 清 QA 产出 + 重建 Master 对话
- `resume_dev_exec` — git reset + 重建 Dev 执行对话

### Phase 0: 需求澄清

```
pre_flight_init
  → 初始化 Master conversation，注入 MASTER_SYSTEM_PROMPT
  → clarify_loop（用户输入 → Master 回答，EOF 结束）
  → close：Master 写 project_context.md（存入 artifacts/）
  
MasterFlushClarify（2 节点）：
  1. write_summary — Master 写阶段总结 + ensure_write_file
  2. flush_conv — 关旧对话 → 开新对话注入 project_context.md + 总结
                  → 保存 checkpoint（resume_node="pm_handoff"）
```

### Phase 1: PM 出方案 + 审查

```
PMHandoff → PMAlign.read → MasterReplyPM → JudgeMasterReply
                                               │
                                        ┌──────┼──────┐
                                        │ A    │ B    │ C
                                        ▼      ▼      ▼
                                   PMWriteCriteria  PMAlign   ClarifyInject
                                        │        master_reply   │
                                        ▼           ▲      (write → record)
                                   ReviewPMCriteria │
                                    │   │            │
                               PASS  FAIL────────────┘
                                 │
                                 ▼
                            PMWriteDoc（4 节点）
                            write_prd_letter → read_prd_letter
                            → write_proto_letter → read_proto_letter
                                 │
                                 ▼
                            ReviewPMOutput
                             │   │
                         PASS  FAIL────────────┐
                           │                   │
                           ▼                   │
                       HumanReview             │
                        │       │              │
                    PASS     FAIL──────────────┘
                      │
                      ▼
                 MasterFlushPM（2 节点）
                 1. write_summary → 2. flush_conv
                      │
                      ▼
                 DevHandoff
```

### Phase 2: Dev 出设计 + 编码执行

```
DevHandoff → DevAlign.dev → 循环 → judge_exit
                                        │
                                        ▼
                                  DevWriteCriteria
                                        │
                                        ▼
                                  ReviewDevCriteria
                                   │           │
                               PASS           FAIL
                                 │             │
                                 ▼             │
                            DevWriteDesign     │
                            (write_letter →    │
                             read_letter)      │
                                 │             │
                                 ▼             │
                            DevReviewDesign    │
                             │         │       │
                          PASS      FAIL───────┘
                            │
                            ▼
                       DevWritePlan
                       (write_letter → read_letter)
                            │
                            ▼
                       DevReviewPlan
                        │         │
                     PASS      FAIL──┘
                       │
                       ▼
                  DevGitInit（3 节点）
                  git_init → write_summary → flush_context
                       │
                       ▼
                  DevExecStep
                  (write_step_letter → read_step_letter)
                       │
                       ▼
                  DevReviewStep
                  ┌────┼────┬────┬────┐
                  │PASS│    │    │    │
                  ▼    │    │    │    │
             DevCommit │    │    │    │
           │ PASS│ FAIL│    │    │    │
           └──┬──┘     │    │    │    │
         continue  done│    │    │    │
             │      │  │    │    │    │
             │      ▼  │    │    │    │
             │  Master │    │    │    │
             │  Flush  │    │    │    │
             │  Dev    │    │    │    │
             │   │     │    │    │    │
             └───┘  step_  dev_  dev_
                    retry rollback escalate
                      │     │      │
                      └──┬──┘      │
                         └─────────┘
                        (全部回到 DevExecStep)
```

### Phase 3: QA 测试

```
qa_handoff → QAAlign（9 节点对齐循环）→ align done
                                              │
                                              ▼
                                        QAWriteCriteria
                                              │
                                              ▼
                                        ReviewQACriteria
                                         │           │
                                      PASS         FAIL
                                         │           │
                                         ▼           │
                                     QAWriteTestPlan │
                                         │           │
                                         ▼           │
                                    MasterReviewPlan │
                                      │         │    │
                                   PASS      FAIL────┘
                                     │
                                     ▼
                                 QAWriteTestCase
                                     │
                                     ▼
                                 ReviewerReviewCode
                                   │           │
                                PASS         FAIL──┘
                                  │
                                  ▼
                              QARunTests
                                  │
                                  ▼
                            JudgeTestResult
                              │         │
                          全部通过    有 bug
                              │         │
                              ▼         │
                          MasterFlushQA  │
                              │          │
                              ▼          │
                       ConsistencyAudit  │
                              │          │
                              ▼          │
                      WriteMaintenanceDocs│
                              │          │
                              ▼          │
                         DeliverySummary  │
                              │          │
                              ▼          │
                             END     DevFix
                                       │
                                       ▼
                                   QARunTests（重跑）
                                       │
                                       ▼
                                   JudgeTestResult（循环）
```

### 各节点职责

| 节点 | 角色 | call_agent | 产出 |
|:-----|:-----|:-----------|:-----|
| QAHandoff | Master | 1（write_letter） | handoff 信 |
| QAAlign（9 节点） | QA ↔ PM/Dev/Master | 各 1 | understanding.md |
| QAWriteCriteria | Master | 1（write_criteria） | criteria-qa.md |
| ReviewQACriteria | Reviewer | 2（review + judge_reply） | 通过/反馈 |
| QAWriteTestPlan | QA | 1（read_letter） | QA/test-plan.md |
| MasterReviewPlan | Master | 1（judge_reply） | 通过/反馈 |
| QAWriteTestCase | QA | 1（read_letter） | QA/tests/ |
| ReviewerReviewCode | Reviewer | 2（review + judge_reply） | 通过/反馈 |
| QARunTests | QA | 1（call_agent） | 测试结果 |
| JudgeTestResult | Judge | 1（judge_reply） | pass / bug 列表 |
| DevFix | Dev | 1（read_letter） | 修复代码 |
| MasterFlushQA | Master | 2（write_summary + flush_conv） | 阶段总结 |
| ConsistencyAudit | Master | 1（call_agent） | audit-report.md |
| WriteMaintenanceDocs | Dev | 1（call_agent） | README.md, deployment-guide.md |
| DeliverySummary | Master | 1（call_agent） | delivery-summary.md |

### 测试计划不分步

QA 测试计划为一次性产出，不接受分步（与 Dev plan 不同）：
- 测试计划描述**测什么**（模块、场景、边界）、**怎么测**（E2E / API / 单元）、**测试数据准备**
- 不拆步骤，因为测试代码的「验收标准」无法像实现代码那样二进制判定
- Master 审查通过后进入编码

### 测试代码不分步

- QA 一次性编写全部测试脚本
- Reviewer 一次性审查全部代码
- 审查不通过退回修改，不拆步骤

### 修 bug 循环

当 JudgeTestResult 判定有 bug 时：
1. Judge 将 bug 清单写入 bug report 文件
2. DevFix 节点：Dev 读取 bug report，修复代码
3. 回到 QARunTests 重新运行测试
4. 循环直到全部通过


### Phase 4: 交付

#### ConsistencyAudit — 一致性审计

只读审计，不修改任何文件。

Master 做全局四方自洽检查：
1. **需求 vs 方案** — PRD 中的每个功能点在 design.md 中是否有对应实现方案？
2. **方案 vs 代码** — design.md 中的每个组件/接口在代码中是否有对应实现？
3. **代码 vs 测试** — 核心功能路径是否有测试覆盖？测试是否通过？
4. **配置一致性** — runtime_config.json 与代码中的配置引用是否对应？

输出 `audit-report.md`，列出全部不一致项及严重程度（阻塞 / 建议）。

#### WriteMaintenanceDocs — 写维护文档

Dev 一次性产出：
- **README.md** — 项目介绍、技术栈、快速启动
- **deployment-guide.md** — 部署步骤、环境要求、配置说明

不出设计方案和计划，直接编写，不分步。

#### DeliverySummary — 交付总结

Master 汇总整个项目的产出物清单和阶段回顾，写入 `delivery-summary.md`。

### Conversation 生命周期

| 子阶段 | Agent | Conversation 名 |
|:-------|:------|:----------------|
| QA 对齐（已完成） | qa | `qa-align-{ws}-{ts}` |
| QA 测试计划 | qa | `qa-plan-{ws}-{ts}` |
| QA 测试代码 | qa | `qa-code-{ws}-{ts}` |
| QA 测试运行 | qa | `qa-run-{ws}-{ts}` |
| 一致性审计 | master | `master-{ws}-{ts}`（沿用） |
| 写维护文档 | dev | `dev-doc-{ws}-{ts}` |
| 交付总结 | master | `master-{ws}-{ts}`（沿用） |

## 关键设计决策

### 1. Master 单一 conversation 贯穿全流程

PM 的疑问、Dev 的执行问题都可能回溯到初始需求。Master 单一 conversation 让上下文连贯。

### 2. 一节点一 call_agent

每个 graph 节点只包含一次 `call_agent` 调用。中断恢复时只重放一个 call，最大限度减少 token 浪费。

### 3. 类内条件路由

条件边从 graph.py 移入各 class 的 `register()` 内部，graph.py 只做跨组简单边。组内加空节点作为外部出口，让所有条件目标都在组内。

### 4. Dev 的失败回滚与升级

三档阈值体系：
- `fail_rollback_threshold`：默认 3，触发 git 回滚
- `fail_escalation_threshold`：默认 5，触发人工对话

### 5. Dev 对话 flush

每 step 完成后 flush conversation，控制上下文窗口。注入 design.md + plan.md + compact_summary。

### 6. Checkpoint / Resume 断线重连

工作流在以下位置保存 checkpoint：

| 位置 | resume_node | 触发时机 |
|:-----|:------------|:---------|
| Phase 0→1 边界 | `pm_handoff` | MasterFlushClarify.flush_conv |
| Phase 1→2 边界 | `dev_handoff` | MasterFlushPM.flush_conv |
| Phase 2→3 边界 | `qa_handoff` | MasterFlushDev.flush_conv |
| Phase 3→4 边界 | `consistency_audit` | MasterFlushQA.flush_conv |
| Dev 开始执行前 | `dev_exec_step` | DevGitInit.flush_context |
| Dev 每步提交后 | `dev_exec_step` | DevCommit.flush_context |

### 7. 一致性审计只审计不修改

Phase 4 的 ConsistencyAudit 是只读审计：
- 检查 PRD / design / code / tests 四方自洽性
- 输出不一致清单及严重程度（阻塞/建议）
- 不自动修改任何文件
- 不一致项由用户决定是否修复、何时修复
- 避免审计节点变成「隐形的回归修改者」，引入新 bug

### 8. QA 测试不分步

测试计划和测试代码均不分步编写（与 Dev plan 不同）：

**计划不分步**：测试计划的验收标准无法像实现代码那样二进制判定（"这段测试代码是否正确地测试了正确的东西"是主观的），一次性写完 + 一次性审查更高效。

**代码不分步**：测试代码之间有依赖关系（注册测试先于登录测试），拆分步骤需要处理前置条件传递；且测试代码的真正验证在运行时才发生，不是编写时。

### 9. `.agent_runtime` 目录结构

```
{runtime_dir}/
├── checkpoint.json          # 断线重连检查点
├── context.json             # 三段式上下文（bg/ctx/phase）
├── registry.json            # Agent 注册信息
├── calls.jsonl              # Agent 调用日志
├── events.jsonl             # 事件日志
├── artifacts/               # 项目顶层决策等固化文档
│   └── project_context.md
├── phases/                  # 阶段总结
│   └── phase-summary-{name}.md
└── handoffs/                # Agent 间通信信件
    └── {name}-{ws}-{ts}.md
```

### 10. Token 消耗特征与优化方向

#### 10.1 对话层级与 flush 覆盖范围

当前 flush 机制只在以下时机切断对话历史：
- Phase 边界（MasterFlush：关旧开新）
- Step 提交后（DevCommit.flush_context：关 dev-exec 开新）

一次 call_agent 内部（一次 HTTP 请求）的 tool call 循环**无法被 flush 打断**。

#### 10.2 单次请求的 token 消耗模型

一次 step exec 中，agent 的内部循环：

```
read_file(design.md 70KB)   → 文件内容进 history
read_file(plan.md 33KB)     → 同上
read_file(design-detail.md) → 同上
write_file × N              → 代码全文在入参中，进 history
execute_code(jest)          → 测试日志出参进 history
execute_code(jest 再跑)     → 又追加
```

每次 LLM 思考 → tool call → tool result → LLM 再思考的循环中，上一步的入参和出参全部带回给模型。这是 LLM chat API 的架构限制（所有 tool call 在同一 `messages[]` 数组中累加），无法避免。

一次 step exec 实测消耗：**~2.1M input tokens / 10K output tokens**，其中绝大部分来自大文件反复传输和测试日志累积。

#### 10.3 可行的优化方向

| 方向 | 措施 | 预期效果 |
|:-----|:-----|:---------|
| 大文件摘要注入 | 不依赖 agent 自行 read_file，改在 system prompt 中注入摘要 | 每次循环省 20K+ tokens |
| tool result 截断 | execute_code/terminal 返回值只保留关键行（报错、测试计数） | 每次循环省日志全文 |
| step 内再分段 | exec 写代码和测试分两个 conversation | 失败重跑时历史减半 |
| 惰性文件加载 | Hermes 端支持 tool result 不累加至 history | 需要 gateway 层支持 |

#### 10.4 性价比结论

工作流 token 效率低于纯手动对话，但这是**用 token 换用户时间**的设计取舍。Hermes 本地推理成本极低（实测 ~140M tokens = 10 元），在本地部署场景下 token 消耗不是瓶颈，无需为省 token 牺牲功能完整性。
