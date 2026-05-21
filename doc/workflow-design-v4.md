# LangGraph 工作流编排设计 v4

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
| **Master** | `cg` | 8642 | 编排决策、写审核标准、维护 state、回答疑问 |
| **Judge** | `cg` | 8642（同 gateway） | 回复分类（A/B/C/D 路由），不同 conversation |
| **Reviewer** | `cg` | 8642（同 gateway） | 按标准审查产出，不同 conversation |
| **PM** | `pm` | 8643 | 需求分析 + HTML 静态原型 |
| **Dev** | `dev` | 8644 | 详细设计 + 代码实现 + 自验证 Playwright |
| **QA** | `qa` | 8645 | 黑盒测试（Playwright E2E）+ 白盒测试（API）+ 出测试报告 |

## 状态定义

```python
class WorkflowState(TypedDict):
    phase: str              # 当前阶段名
    judge_result: str       # Judge 判读结果（A/B/C），用于条件边路由
```

## Judge 路由定义

Judge 是工作流的路由枢纽，负责将 agent 的回复分类后路由到正确的下游。使用模块级公用函数 `_judge_clarify`：

```python
def _judge_clarify(runtime, reply: str) -> str:
    """判读 Master 回复是已确认还是有疑问。"""
    judge_prompt = (
        "你是一个流程裁判。以下是 Master 的回复。\n\n"
        f"## Master 的回复\n{reply}\n\n"
        "判定当前状态是以下哪一种：\n"
        "A. 需求已明确，可以进入下一阶段\n"
        "B. Master 有疑问需要用户继续回答\n\n"
        "回复 A 或 B 即可，不要输出其他内容。"
    )
    result = call_agent(runtime, "judge", _conv_name("judge-clarify"), judge_prompt)
    return result.strip()
```

### Phase 0 judge：`_judge_clarify`
- 输入：Master 的回复
- 输出：A / B
  - A = 需求已明确，进入确认子循环
  - B = Master 仍有疑问，继续澄清

### Phase 1 judge：`judge_master_reply`
- 输入：Master 对 PM 疑问的回复
- 输出：A / B / C
  - A = Master 确认 PM 理解正确，无需再问用户 → 进入 pm_write_doc
  - B = Master 已答复 PM，需要转发给 PM 继续确认 → 回 pm_align
  - C = Master 有无法判定的问题，需要向用户确认 → 进入 clarify_inject

Judge 的 conversation 每次调用独立命名（`judge-{target}-{ws}-{ts}`），区分不同判读场景。

## Agent 命名规范

| 用途 | Agent 名 | Conversation 名 | 说明 |
|:-----|:---------|:----------------|:------|
| Master 编排 | master | clarify-{ws}-{ts} | Phase 0 主对话，后续复用 |
| Master 回复 PM | master | master-reply-pm-{ws}-{ts} | 专用于回答 PM 疑问 |
| Master 写给 PM | master | master-to-pm-{ws}-{ts} | 写 handoff 委托信 |
| Judge 判读 | judge | judge-{target}-{ws}-{ts} | 每次判读独立 conv |
| Reviewer 审查 | master | review-{target}-{seq} | 和 master 同 gateway |
| Master 自省 | master | self-check-{seq} | 短期对话，用完即弃 |
| PM 对齐 | pm | pm-align-{ws}-{ts} | PM 与 Master 对齐对话 |
| PM 出文档 | pm | pm-doc-{ws}-{ts} | PM 产出 PRD + 原型 |
| Dev 出详细设计 | dev | dev-design | |
| Dev 出实现计划 | dev | dev-plan | |
| Dev 执行 | dev | dev-impl-{step_id} | 每步独立 conv |
| Dev 修 bug | dev | dev-fix-{bug_id} | |
| QA 出计划 | qa | qa-plan | |
| QA 黑盒（Playwright） | qa | qa-blackbox-{round} | E2E 测试 |
| QA 白盒（API） | qa | qa-whitebox-{round} | 接口测试 |

> **说明**：WS = basename(getcwd())，TS = time.strftime("%Y%m%d_%H%M%S")。

## 图结构（重新划分 Phase）

### Phase 0: Pre-Flight / Clarification

```
[pre_flight_clarify]
  → 初始化 conversation（存入 context conv_clarify）
  → 调用 _clarify_loop（共享函数，含 judge + 确认子循环）
  → 退出前通知 Master 写 project_context.md
  → phase = "done"
```

> **关键改动**：澄清循环逻辑抽取为模块级 `_clarify_loop`，`pre_flight_clarify` 和 `clarify_inject` 共用。conversation 名存入 context 而非 state。

### Phase 1: PM 对齐 + 出方案

```
[pm_handoff]
  → 读 project_context.md
  → 新建 Master 对话（master-to-pm-xxx）
  → 调 Master 写 handoff 信（master_to_pm.md）
  → 读完即删

[pm_align]
  → 调 PM，注入 handoff 信 + 要求汇报理解+疑问
  └── 首次：发 handoff 信件
  └── 循环中：发 Master 的回复让 PM 确认

[master_reply_pm]
  → 将 PM 的疑问转发给 Master
  → 复用 Phase 0 的 clarify conversation（conv_clarify）
  → Master 逐一检查 PM 的理解并回答疑问
  → 如遇无法判定的问题，明确写出需要向用户确认

[judge_master_reply]
  → judge 判读 Master 的回复
  ├── A（PM 理解正确，无需再问）→ 进入 pm_write_doc
  ├── B（Master 已答复 PM）→ 回 pm_align 转发给 PM
  └── C（Master 需要问用户）→ 进入 clarify_inject

[clarify_inject]
  → 复用 Phase 0 的 clarify conversation
  → 调用 _clarify_loop 进行多轮问答（含 judge + 确认子循环）
  → 退出时将本轮确认的决策追加到 project_context.md

[pm_write_doc]
  → Call 1 — PM 写 PRD
  → Call 2 — PM 写 prototype
  → 写入 test/PRD.md + test/prototype.html
```

**Phase 1 完整流程图：**

```
pm_handoff → pm_align → master_reply_pm → judge_master_reply
                                               │
                                        ┌──────┼──────┐
                                        │ A    │ B    │ C
                                        ▼      ▼      ▼
                                   pm_write  pm_align  clarify_inject
                                      doc                │
                                                    (回 master_reply_pm)
```

> **关键改动**：PM 不再经 judge_pm_align，直接走 master_reply_pm（上一 session 移除）。Judge 由 A/B 扩展为 A/B/C。`clarify_inject` 使用与 `pre_flight_clarify` 共享的 `_clarify_loop`，拥有完整的多轮问答 + judge + 确认子循环。

### Phase 2: Dev 出详细设计（预留，暂不实现）

```
[dev_design]
[dev_design_criteria]
[dev_design_review]
→ 审核通过 → phase = "dev_plan"
```

### Phase 3: Dev 出实现计划（预留）

```
[dev_plan]
[dev_plan_criteria]
[dev_plan_review]
→ 审核通过 → phase = "dev_exec"
```

### Phase 4: Dev 执行循环（预留）

```
[dev_exec] ↔ [dev_review_step]
→ 全部完成 → phase = "align_dev_qa"
```

### Phase 5: QA 对齐 + 出测试计划（预留）

```
[align_dev_qa] → [qa_plan] → [qa_plan_review] → phase = "qa_exec"
```

### Phase 6: QA 测试循环（预留）

### Phase 7: 交付（预留）

---

## 对比 v3 的关键变更

| 变更 | v3 | v4 |
|:-----|:---|:---|
| Judge 机制 | 仅 Phase 0 | 通用 judge + 按阶段特化 |
| PM↔Master 对齐 | 无（直接写文档） | 对齐循环：PM 汇报 → Master 解答/问用户 |
| Clarify conversation | 局部变量，用完丢弃 | 存入 context，被后续节点复用 |
| Phase 1 内部拆节点 | 单节点 pm_write_doc | pm_handoff → pm_align → master_reply_pm → judge_master_reply → clarify_inject |
| Judge 路由 | A/B 二选一 | A/B/C 三选一（含 clarify_inject 分支） |
| `clarify_inject` | 无循环，一次性问答 | 共享 `_clarify_loop`，多轮问答 + judge + 确认 |

## 关键设计决策

### 1. 为什么 Master 回复 PM 用 clarify conversation？

PM 的疑问可能指向用户最初未明确的需求（如技术栈、使用场景等）。如果 Master 无法判定，需要直接向用户提问。复用 clarify conversation 的好处：
- 用户看到的是同一段对话历史，上下文连贯
- Master 不需要重新交代"我们在做一个 XX 项目"
- 用户可以直接回答，Master 在已有上下文中理解

### 2. Judge 的路由范围

Judge 不再是 Phase 0 专用，而是每次调用独立命名（`judge-{target}-{ws}-{ts}`），区分不同判读场景。目前有两个判读场景：
- `judge-clarify`：判读 Master 是否有疑问（A/B 路由）
- `judge-master-reply`：判读 Master 能否独立回答、已答复还是需问用户（A/B/C 路由）

### 3. Handoff 信是一次性的

Master 写给 PM 的信读完即删（`os.remove`），不留痕迹。后续 PM↔Master 的对话通过各自的 conversation 延续，不再通过文件。文件 only 用于初始交接。
