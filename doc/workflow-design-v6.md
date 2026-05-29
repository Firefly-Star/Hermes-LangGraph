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
├── graph.py          # LangGraph 图构建入口 + main()
├── config.py         # 常量与 prompt 模板
├── utils.py          # 工具函数：call_agent, register_nodes, clarify_loop 等
├── phase0.py         # PreFlightClarify — 需求澄清
├── phase1.py         # PMHandoff ~ HumanReview — PM 出方案
├── phase2.py         # DevHandoff ~ DevEscalate — Dev 设计 + 编码
├── phase3.py         # qa_handoff, qa_align — QA 对齐
├── flush.py          # MasterFlushClarify/PM/Dev — phase 边界 flush
├── checkpoint.py     # ResumeRouter + save/load/clear_checkpoint
```

`graph.py` 中的 `NODES` 列表只保留尚未 class 化的节点（当前仅 `qa_handoff`、`qa_align`），其余节点通过各类的 `register()` 方法注册。

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

### Phase 3: QA 对齐

```
qa_handoff → qa_align → END
```

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
| Dev 开始执行前 | `dev_exec_step` | DevGitInit.flush_context |
| Dev 每步提交后 | `dev_exec_step` | DevCommit.flush_context |

### 7. `.agent_runtime` 目录结构

```
{runtime_dir}/
├── checkpoint.json          # 断线重连检查点
├── context.json             # 三段式上下文（bg/ctx/phase）
├── registry.json            # Agent 注册信息
├── config.json              # 配置
├── calls.jsonl              # Agent 调用日志
├── events.jsonl             # 事件日志
├── artifacts/               # 项目顶层决策等固化文档
│   └── project_context.md
├── phases/                  # 阶段总结
│   └── phase-summary-{name}.md
└── handoffs/                # Agent 间通信信件
    └── {name}-{ws}-{ts}.md
```
