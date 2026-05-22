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

Judge 是工作流的路由枢纽，负责将 agent 的回复分类后路由到正确的下游。使用模块级公用函数：

### Phase 0 judge：`_judge_clarify`
- 输入：Master 的回复
- 输出：A / B
  - A = 需求已明确，进入确认子循环
  - B = Master 仍有疑问，继续澄清

### Phase 1 judge：`judge_master_reply`
- 输入：Master 对 PM 疑问的回复
- 输出：A / B / C
  - A = Master 确认 PM 理解正确，无需再问用户 → 进入 pm_write_criteria（先定标准，再出方案）
  - B = Master 已答复 PM，需要转发给 PM 继续确认 → 回 pm_align
  - C = Master 有无法判定的问题，需要向用户确认 → 进入 clarify_inject

Judge 的 conversation 每次调用独立命名（`judge-{target}-{ws}-{ts}`），区分不同判读场景。

## Agent 命名规范

| 用途 | Agent 名 | Conversation 名 | 说明 |
|:-----|:---------|:----------------|:------|
| Master 编排 | master | master-{ws}-{ts} | 全流程唯一 Master 对话 |
| Master 回复 PM | master | master-{ws}-{ts} | 复用同一段 Master 对话 |
| Master 写审核标准 | master | master-{ws}-{ts} | 复用同一段 Master 对话 |
| Judge 判读 | judge | judge-{target}-{ws}-{ts} | 每次判读独立 conv |
| Reviewer 审查 | reviewer | reviewer-{ws}-{ts} | 审查专用对话 |
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

## Agent 间通信模型

所有 agent 间通信通过"信件"机制实现（`handoffs/` 目录下的 markdown 文件）：

- **write_letter** — sender agent 用 write_file 工具写一封信到指定路径
- **read_letter** — 给 receiver agent 信件路径，让 agent 自读；workflow 在读完后删除邮件
- **read_and_write_letter** — 给 receiver agent 输入信路径，让 agent 自读 → 写回信到输出路径；workflow 在读完后删除输入信

workflow 只管理文件生命周期（写后检查存在性、读后删除），不读文件内容注入 prompt。

## 图结构

### Phase 0: Pre-Flight / Clarification

```
[pre_flight_clarify]
  → 初始化 conversation（存入 context master_conv）
  → 调用 _clarify_loop（共享函数，含 judge + 确认子循环）
  → 退出前通知 Master 写 project_context.md（存入 {runtime_dir}/project_context.md）
  → phase = "done"
```

> **澄清循环**：用户输入 → Master 回复 → judge 判读 → 如 A（已明确）→ 用户确认子循环（EOF=确认） → 如 B（仍有疑问）→ 继续循环。空输入（直接 EOF）视为确认，无需输入 CONFIRMED。

### Phase 1: PM 对齐 + 出方案 + 审查

```
pm_handoff → pm_align → master_reply_pm → judge_master_reply
                                               │
                                        ┌──────┼──────┐
                                        │ A    │ B    │ C
                                        ▼      ▼      ▼
                                   pm_write   pm_align  clarify_inject
                                  criteria       ▲           │
                                      │          │      (回 master_reply_pm)
                                      ▼          │
                                  pm_write_doc   │
                                      │           │
                                      ▼           │
                                  review_pm_output│
                                   │   │          │
                               PASS  FAIL─────────┘
                                 │
                                 ▼
                             human_review
                              │       │
                           PASS     FAIL
                             │       │
                             ▼       ▼
                            END   review_pm_output
```

#### 各节点说明

**pm_handoff**：Master 写 handoff 信给 PM，包含项目概况和顶层决策文件路径。信件读完即删（`os.remove`）。

**pm_align**：
- 首次：PM 读 handoff 信，写回信汇报理解 + 列出疑问
- 循环：Master 先写答复信 → PM 读信 → 写回信
- PM 回信内容缓存到 context（`pm_reply_text`），供 master_reply_pm 在文件已删除时回退使用

**master_reply_pm**：Master 读 PM 回信（优先读文件，回退用缓存），逐一检查 PM 理解并回答疑问。需明确区分"已答复"和"需问用户"。

**judge_master_reply**：判读 Master 回复 → A/B/C 三路路由。

**clarify_inject**：复用 Phase 0 的 `master_conv`，调用 `_clarify_loop` 向用户提问，退出时将确认的决策追加到 `project_context.md`。

**pm_write_criteria**：Master 制定审核标准（需求完整性、MVP 边界、逻辑自洽性、一致性、原型质量），自检循环直至通过才放行。

**pm_write_doc**：两次 write_letter + read_letter：
  - Call 1：Master 写信要求 PRD → PM 写入 `{workspace}/PM/PRD.md`
  - Call 2：Master 写信要求原型 → PM 写入 `{workspace}/PM/prototype.html`
  - 如果从审查循环回来，注入 `review_result` + `human_feedback` 作为反馈

**review_pm_output**：Reviewer 对照 criteria.md + project_context.md 审查 PRD + prototype，输出 PASS/FAIL。

**human_review**：展示文件路径，让人确认或提意见。EOF=通过。

#### 产出路径

| 文件 | 路径 |
|:-----|:-----|
| 项目顶层决策 | `{runtime_dir}/project_context.md` |
| 审核标准 | `{workspace}/criteria.md` |
| PRD | `{workspace}/PM/PRD.md` |
| Prototype | `{workspace}/PM/prototype.html` |
| Handoff 信件 | `{runtime_dir}/handoffs/{name}-{ws}-{ts}.md` |

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
| Master conversation | 局部变量，用完丢弃 | 单一 conversation 存入 context，贯穿全流程 |
| Phase 1 内部拆节点 | 单节点 pm_write_doc | pm_handoff → pm_align → master_reply_pm → judge_master_reply → clarify_inject |
| Judge 路由 | A/B 二选一 | A/B/C 三选一（含 clarify_inject 分支） |
| `clarify_inject` | 无循环，一次性问答 | 共享 `_clarify_loop`，多轮问答 + judge + 确认 |
| 审核循环 | 无 | pm_write_criteria 自检循环 + review_pm_output + human_review |
| Agent 通信 | 内联内容到 prompt | 信件路径传参，agent 自读自写 |
| 空输入处理 | 要求输入 CONFIRMED | EOF 即视为确认 |

## 关键设计决策

### 1. 为什么 Master 用单一 conversation 贯穿全流程？

PM 的疑问可能指向用户最初未明确的需求。Master 单一 conversation 的好处：
- 用户看到的是同一段对话历史，上下文连贯
- Master 不需要重新交代"我们在做一个 XX 项目"
- 用户可以直接回答，Master 在已有上下文中理解
- 所有派生任务（写 handoff 信、写标准、回复 PM）都复用同一对话上下文

### 2. Judge 的路由范围

Judge 不再是 Phase 0 专用，而是每次调用独立命名（`judge-{target}-{ws}-{ts}`），区分不同判读场景。目前有两个判读场景：
- `judge-clarify`：判读 Master 是否有疑问（A/B 路由）
- `judge-master-reply`：判读 Master 能否独立回答、已答复还是需问用户（A/B/C 路由）

### 3. Handoff 信是一次性的

Master 写给 PM 的信读完即删（`os.remove`），不留痕迹。后续 PM↔Master 的对话通过各自的 conversation 延续，不再通过文件。文件 only 用于初始交接。

### 4. 信件通信不走内联

所有 letter 函数（write_letter/read_letter/read_and_write_letter）都传路径让 agent 用自己的工具读写，workflow 代码不内联文件内容到 prompt。workflow 只负责文件生命周期管理（创建后检查存在性、读后删除）。
