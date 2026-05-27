# LangGraph 工作流编排设计 v2（已被取代）

## 依赖关系

```
langgraph (1.2.0)
langgraph-checkpoint (4.1.0)
requests (2.33.0)
```

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                        workflow.py                                │
│   LangGraph StateGraph + TypedDict state                          │
│   + AgentRuntime (agent/conversation/context/logger/checkpoint)      │
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
| **Master** | `cg` | 8642 | 编排决策、写审核标准、维护 state |
| **Reviewer** | `cg` | 8642（同 gateway） | 按标准审查产出，不同 conversation |
| **PM** | `pm` | 8643 | 需求分析 + HTML 静态原型 |
| **Dev** | `dev` | 8644 | 代码实现 + 自验证 Playwright |
| **QA** | `qa` | 8645 | 黑盒测试（Playwright E2E）+ 白盒测试（API）+ 出测试报告 |

## 状态定义

```python
class WorkflowState(TypedDict):
    phase: str              # 当前阶段名
```

> 当前仅实现骨架阶段控制。后续迭代会逐步扩展字段（审查计数、执行索引等）。
> 持久化数据全部走 ContextManager（background / phase / contexts），
> WorkflowState 只存运行时需要快速判断的字段。

## Agent 命名规范

| 用途 | Agent 名 | Conversation 名 | 说明 |
|:-----|:---------|:----------------|:------|
| Master 编排 | master | master-{phase}-{seq} | seq 随 flush 递增 |
| Reviewer 审查 | master | review-{target}-{seq} | 和 master 同 gateway |
| PM 出方案 | pm | pm-doc | |
| Dev 出计划 | dev | dev-plan | |
| Dev 执行 | dev | dev-impl-{step_id} | 每步独立 conv |
| Dev 修 bug | dev | dev-fix-{bug_id} | |
| QA 出计划 | qa | qa-plan | |
| QA 黑盒（Playwright） | qa | qa-blackbox-{round} | E2E 测试 |
| QA 白盒（API） | qa | qa-whitebox-{round} | 接口测试 |

## 图结构

### Phase 0: Pre-Flight / Clarification

```
[pre_flight_clarify]
  → 交互式需求澄清（无限循环）
  → 用户输入 CONFIRMED 或 Master 回复 ## 确认时退出
  → 退出前通知 Master 阶段结束
  → phase = "done"
```

> 当前 Phase 0 仅做需求澄清，不写 background。后续阶段会补充环境检查和原则注入。

### Phase 1: PM 出方案（含 HTML 原型）

```
[pm_write_doc]
  → 调 pm agent，注入角色上下文感知模板
  → PM 产出：需求文档 + HTML 静态原型界面
  → 存入 contexts["pm_doc"]

[pm_write_criteria]
  → 调 master agent 写审核标准
  → 自检："你确定每条标准你都能实际执行检查？"

[pm_review_doc]
  → 调 reviewer agent 按标准审查
  ├── 通过 → set_ctx("approved_pm_doc", ...)
  │        → set_phase_node(["PM 方案评审"], "done")
  │        → phase = "dev_plan"
  └── 不通过且 loop_count < max → loop_count++ → 回 pm_write_doc
```

### Phase 2: Dev 出实现计划

```
[dev_plan]
  → 调 dev agent，注入 PM 方案 + 角色上下文感知
  → Dev 产出：实现计划（每步 = 一个可验证的动作）

[dev_plan_criteria]
  → 写审核标准 + 自检

[dev_plan_review]
  → reviewer 审查
  ├── 通过 → set_ctx("approved_dev_plan", ...)
  │        → set_phase_node(["Dev 计划评审"], "done")
  │        → phase = "qa_plan"
  └── 不通过 → loop_count++ → 回 dev_plan
```

### Phase 3: QA 出测试计划

```
[qa_plan]
  → 调 qa agent，注入 PM 方案 + 角色上下文感知
  → QA 产出：测试计划（黑盒 Playwright E2E + 白盒 API 测试）

[qa_plan_criteria]
  → 写审核标准 + 自检

[qa_plan_review]
  → reviewer 审查
  ├── 通过 → set_ctx("approved_qa_plan", ...)
  │        → phase = "align_pm_dev"
  └── 不通过 → 循环
```

### Phase 4: Cross-Agent Alignment (PM → Dev)

```
[align_pm_dev]
  → Master 刷新 context
  → 调 dev agent 读 PM 文档 + HTML 原型，列问题
  ├── 无问题 → phase = "dev_exec"
  └── 有问题 → [route_to_pm] → PM 解答 → 回到 align_pm_dev
```

### Phase 5: Dev 执行循环

```
[dev_exec]
  → Master flush context (每 3 步或 phase 边界)
  → 提示 Dev agent："执行前 git add + git commit"
  → 调 dev agent 执行一个 subtask（3~8 文件）

[dev_review_step]
  → reviewer 按审核标准审查
  ├── pass → step_index++
  │        ├── 还有下一步 → 回 dev_exec
  │        └── 全部完成 → phase = "align_dev_qa"
  └── fail → 提示回滚 → 重做
```

> Dev **不做** API 测试，只管代码实现和编译通过。

### Phase 6: Cross-Agent Alignment (Dev → QA)

```
[align_dev_qa]
  → 调 qa agent 读 Dev 代码 + 测试计划，列问题
  ├── 无问题 → phase = "qa_exec"
  └── 有问题 → [route_to_dev] → Dev 解答 → 回 align_dev_qa
```

### Phase 7: QA 测试循环

```
[qa_exec_test]
  ├── 首轮：执行全部测试用例（黑盒 Playwright E2E + 白盒 API）
  └── 后续轮次：只执行上次未通过的测试用例

[qa_write_report]
  → QA 将测试结果写入测试文档（含 HTTP 响应体、Playwright 输出）
  → 存入 contexts["qa_report"]

[master_route_to_dev]
  → Master 审查 QA 报告
  ├── 全部通过 → phase = "deliver"
  └── 有 bug → Master 将测试报告转交 Dev
              → [dev_fix_bug] → QA 验证修复 → 回 qa_exec_test
```

### Phase 8: Dev Bug Fix 循环

```
[dev_fix_bug]
  → Master 将 QA 报告（本次 fail 的 case）注入 Dev context
  → Dev 针对每个 bug：
     ① 修改代码
     ② git add + git commit
     ③ 跑对应的 Playwright 脚本（Dev 自己跑，结果客观）
     ④ 通过 → 修下一个
     ⑤ 不通过 → 继续改

[qa_verify_fix]
  → QA 验证 Dev 的 fix（跑之前 fail 的测试）
  ├── 全部通过 → phase = "deliver"
  └── 仍有失败 → 回 dev_fix_bug
```

> **Playwright 谁跑的分工：**
> - Dev 修 bug 后自己跑 Playwright（快速反馈，结果客观）
> - QA 做完整验收测试 + 验证 fix 是否真修好

### Phase 9: 交付

```
[deliver]
  → 汇总全部结果给用户：完成的工作 + 测试报告 + phase 树
  → 调 checkpoint.wait() 等用户 sign-off

[user_signoff]
  ├── continue → END: stop all gateways
  └── modify  → 按用户要求做调整
```

## 关键机制

### 1. 上下文 Flush

时机：每个 phase 开始前，或 dev_exec 每 3 步后

```python
def flush_master(pool, phase, seq):
    pool.conversations.close_conversation("master", f"master-{phase}-{seq}")
    keys = ["background", "phase"]
    if pool.context.get_ctx("approved_pm_doc"):
        keys.append("approved_pm_doc")
    if pool.context.get_ctx("approved_dev_plan"):
        keys.append("approved_dev_plan")
    if pool.context.get_ctx("qa_report"):
        keys.append("qa_report")
    injection = pool.context.build_injection(keys)
    pool.conversations.init_conversation("master", f"master-{phase}-{seq+1}", injection)
    return seq + 1
```

### 2. 角色上下文感知模板

文档类/计划类 agent 的 prompt 注入：

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
        f"确保你的产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
        f"请具体、可操作，避免空泛描述。"
    )
```

### 3. 审核标准自检

```python
def write_criteria(pool, target):
    """写审核标准后，让 agent 自检是否可执行。"""
    result = pool.conversations.call(
        "master", f"review-criteria-{target}",
        f"请为以下内容制定可执行的审核标准：{target}\n"
        f"标准必须具体、可衡量，每一条都能通过检查代码/文档来判定通过/不通过。"
    )
    # 自检
    pool.conversations.call(
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

存入 `pool.context.set_bg("master_principles", PRINCIPLES)`

Master 委派子 agent 时，从 principles 中提取相关子集拼入 context：
- 委派 PM：强调 1, 2, 4
- 委派 Dev：强调 3, 4
- 委派 QA：强调 2, 4, 5
- 委派 Reviewer：强调 1, 2

### 5. QA 测试轮次策略

```python
def get_qa_test_scope(pool, round_number):
    """首轮全部测，后续只测上次 fail 的。"""
    prev_report = pool.context.get_ctx("qa_report")
    if not prev_report or round_number == 0:
        return "all"  # 首轮：全部测试
    # 从上一份报告中提取 fail 的 case ID
    failed_cases = extract_failed_case_ids(prev_report)
    return failed_cases
```

## 端口分配

| Profile | Port | Agent |
|:--------|:-----|:-------|
| `cg`（已有） | 8642 | Master + Reviewer |
| `pm` | 8643 | PM |
| `dev` | 8644 | Dev |
| `qa` | 8645 | QA |

## 文件清单

| 文件 | 说明 |
|:-----|:------|
| `workflow.py` | 主入口：定义 LangGraph 图 + AgentRuntime 初始化 + 运行 |
| `workflow-design-v2.md` | 本文档 |
