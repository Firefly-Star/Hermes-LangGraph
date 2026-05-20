# LangGraph 工作流编排设计 v1

## 依赖关系

```
langgraph (1.2.0)
langgraph-checkpoint (4.1.0)
requests (2.33.0)
```

## 架构总览

```
┌──────────────────────────────────────────────────┐
│                workflow.py                        │
│   LangGraph StateGraph + TypedDict state          │
│   + AgentPool agent/conversation/context 工具      │
│                                                    │
│   ┌──────┐  ┌──────┐  ┌─────────┐  ┌──────────┐  │
│   │Master │  │ Rev  │  │  Dev    │  │   QA     │  │
│   │8642   │  │8642  │  │ 8643    │  │ 8644     │  │
│   │cg     │  │cg    │  │ dev     │  │ qa       │  │
│   └──────┘  └──────┘  └─────────┘  └──────────┘  │
│                                                    │
│   shared gateway    separate gateway               │
└──────────────────────────────────────────────────┘
```

- **Master** & **Reviewer** — 共用 profile `cg`, gateway 8642，不同 conversation
- **Dev** — profile `dev`, gateway 8643
- **QA** — profile `qa`, gateway 8644

## 状态定义 (WorkflowState)

```python
class WorkflowState(TypedDict):
    # 阶段控制
    phase: str              # 当前阶段名
    step_index: int         # 步进循环的当前位置
    loop_count: int         # 审查循环/对齐循环计数
    conv_seq: int           # 对话序列号，flush 后递增

    # 产出的文档
    pm_doc_approved: bool
    dev_plan_approved: bool
    qa_plan_approved: bool

    # 执行结果缓存
    dev_results: dict       # {step_id: summary}
    qa_results: dict        # {step_id: summary}
    bug_loop_count: int     # QA 发现 bug 后的 fix 循环次数
```

> 持久化数据全部走 AgentPool 的 ContextManager（background / phase / contexts），
> WorkflowState 只存运行时需要快速判断的字段。

## Agent 命名规范

| 用途 | Agent 名 | Conversation 名 | 说明 |
|:-----|:---------|:----------------|:------|
| Master 编排 | master | master-{phase}-{seq} | seq 随 flush 递增 |
| Reviewer 审查 | master | review-{target}-{seq} | 和 master 同 gateway |
| PM 出文档 | pm | pm-doc | |
| Dev 计划 | dev | dev-plan | |
| Dev 执行 | dev | dev-impl-{step_id} | 每步独立 conv |
| QA 计划 | qa | qa-plan | |
| QA 黑盒 | qa | qa-blackbox-{step_id} | |
| QA 白盒 | qa | qa-whitebox | |

## 图结构

### 阶段 0: Pre-Flight / Clarification

```
[pre_flight_clarify]
    → 环境检查（用户确认 scope）
    → 写入 background 到 ContextManager
    → 写入 5 条原则到 background
    →
    phase → "pm"
```

### 阶段 1: PM 出方案

```
[pm_write_doc]           → 调 pm agent，注入角色上下文感知模板
[pm_write_criteria]      → 调 master agent 写审核标准 + 自检
[pm_review_doc]          → 调 reviewer agent 按标准审查
    ├── 通过 → 写入 contexts["approved_pm_doc"]
    │        → set_phase_node(["PM 文档评审"], "done")
    │        → phase → "dev_plan"
    └── 不通过，且 loop_count < max → loop_count++ → 回 pm_write_doc
```

### 阶段 2: Dev 出计划

```
[dev_plan]               → 调 dev agent 出计划 + 角色上下文感知
[dev_plan_criteria]      → 写审核标准 + 自检
[dev_plan_review]        → reviewer 审查
    ├── 通过 → 写入 contexts["approved_dev_plan"] + phase
    │        → phase → "qa_plan"
    └── 不通过 → loop_count++ → 回 dev_plan
```

### 阶段 3: QA 出计划

```
[qa_plan]
[qa_plan_criteria]
[qa_plan_review]
    ├── 通过 → phase → "align_pm_dev"
    └── 不通过 → 循环
```

### 阶段 4: Cross-Agent Alignment

```
[align_pm_dev]           → Dev 读 PM 文档，列问题
    ├── 无问题 → phase → "dev_exec"
    └── 有问题 → [route_to_pm] → PM 解答 → 回到 align_pm_dev
```

### 阶段 5: Dev 执行循环

```
[dev_exec]               → Master 刷新 → [dev_flush_context]
                          → 每步前提示 git commit
                          → 调 dev agent 执行
[dev_exec_review]        → reviewer 审查
    ├── pass → step_index++
    │        ├── 还有下一步 → 回 dev_exec
    │        └── 全部完成 → phase → "align_dev_qa"
    └── fail → 提示回滚 + 重新执行
```

### 阶段 6: Alignment Dev→QA

```
[align_dev_qa]           → QA 读 Dev 产出，列测试计划问题
    ├── 无问题 → phase → "qa_exec"
    └── 有问题 → [route_to_dev] → Dev 解答 → 回 align_dev_qa
```

### 阶段 7: QA 执行循环

```
[qa_exec_blackbox]       → QA 黑盒测试（API）
[qa_exec_whitebox]       → QA 白盒测试（Playwright E2E）
[qa_review]              → reviewer 审查 QA 报告
    ├── pass → step_index++
    │        ├── 还有下一步 → 回 qa_exec
    │        └── 全部完成 → phase → "deliver"
    └── fail → bug_loop_count++
             → [dev_fix] → 回 qa_exec
```

### 阶段 8: 交付

```
[deliver]                → 汇总结果给用户
[user_signoff]           → 用户确认 sign-off
    └── 通过 → END: stop_gateway
```

## 关键机制

### 1. 上下文 Flush

时机：每个 phase 开始前，或 dev_exec 每 3 步后

```python
def flush_master(agent_pool, phase, seq):
    agent_pool.conversations.close_conversation("master", f"master-{phase}-{seq}")
    injection = agent_pool.context.build_injection(["background", "phase", "approved_pm_doc", "approved_dev_plan"])
    agent_pool.conversations.init_conversation("master", f"master-{phase}-{seq+1}", injection)
    return seq + 1
```

### 2. 角色上下文感知模板

文档类 agent 的 prompt 注入：

```python
def role_aware_prompt(role, upstream, upstream_doc, deliverable, downstream, downstream_needs):
    return (
        f"## 角色认知\n"
        f"你的角色是 **{role}**。\n\n"
        f"## 上游输入\n"
        f"上游角色 **{upstream}** 提供了以下上下文：\n"
        f"{upstream_doc}\n\n"
        f"## 你的任务\n"
        f"你需要产出 **{deliverable}**。\n\n"
        f"## 下游需求\n"
        f"下游角色 **{downstream}** 将使用你的产出做后续工作。\n"
        f"他们需要从你的产出中获得：{downstream_needs}\n\n"
        f"## 要求\n"
        f"请确保你的产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
        f"请具体、可操作，避免空泛描述。"
    )
```

### 3. 审核标准自检

```python
def write_criteria_and_self_check(agent_pool, target):
    """写审核标准后，让 agent 自检是否可执行。"""
    result = agent_pool.conversations.call(
        "master", f"review-criteria-{target}",
        f"请为以下内容制定可执行的审核标准：{target}\n"
        f"标准必须具体、可衡量，每一条都能通过检查代码/文档来判定通过/不通过。"
    )
    # 自检
    self_check = agent_pool.conversations.call(
        "master", f"review-criteria-{target}",
        f"你确定以上标准每一条你都可以执行吗？"
        f"你可以通过实际检查来逐条判定通过/不通过吗？请逐一确认。"
    )
    return result.text
```

### 4. 五原则注入

```python
PRINCIPLES = """
## 核心原则（Master 必须遵守）
1. Review NEVER optional — 每个子 agent 输出必须审查，再小也不行
2. 执行与验证分离 — 写代码的 agent 不能自己验证自己
3. 每步可回滚 — 执行前提醒 agent 做 git commit
4. 约束反复注入 — 核心规则在每次委派时重述
5. UI 验证必须自动化 — 有 UI 就须有 Playwright 脚本
"""
```

## 文件清单

| 文件 | 说明 |
|:-----|:------|
| `workflow.py` | 主入口：定义 LangGraph 图 + AgentPool 初始化和运行 |
| `workflow-design-v1.md` | 本文档 |
