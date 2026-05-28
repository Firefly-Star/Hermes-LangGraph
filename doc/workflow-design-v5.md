# LangGraph 工作流编排设计 v5

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
│   + AgentRuntime (agent/conversation/context/logger/checkpoint)   │
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
    judge_result: str       # Judge 判读结果（A/B/C），用于条件边路由
```

## Judge 路由

Judge 是工作流的路由枢纽。使用通用 `judge_reply()` 公用函数，每次调用独立 conversation：

```python
judge_reply(runtime, target_role, reply, options, tag)
```

返回值：选项字母（A/B/C/D），根据 options 列表首字母确定。prompt 强约束"只回复单个字母"，并加入首字母提取 fallback，防止 agent 返回"PASS"而非"P"导致误判。

### 判读场景

| 场景 | tag | 路由选项 | 用途 |
|:-----|:----|:---------|:------|
| 需求澄清 | `judge-clarify` | A=已明确, B=仍有疑问 | 判读 Master 的回复 |
| Master 回复 PM | `judge-master-reply` | A=已答复无需问用户, B=需转发PM, C=需问用户 | 三路路由 |
| PM 标准自检 | `judge-pm-criteria` | PASS/FAIL | Master 自检审核标准 |
| Reviewer 审标准 | `judge-pm-criteria` | PASS/FAIL | Reviewer 审查 PM 标准 |
| Dev 标准自检 | `judge-dev-criteria` | PASS/FAIL | Master 自检 Dev 审核标准 |
| Reviewer 审标准 | `judge-dev-criteria` | PASS/FAIL | Reviewer 审查 Dev 标准 |
| Dev 计划审查 | `judge-dev-plan` | PASS/FAIL | Reviewer 审查执行计划 |
| Step 审查 | `judge-step-{n}` | PASS/FAIL | Reviewer 审查每步实现 |
| QA 对齐审查 | `judge-qa-align` | A=无需修改, B=有反馈需修改, C=需升级Master | 综合判读 PM+Dev 的反馈 |
| QA Master 回复 | `judge-qa-master` | A=已解决, B=需问用户 | 判读 Master 的 QA 升级回复 |

## Agent 通信模型

通过"信件"机制（`handoffs/` 目录下的 markdown 文件）：

- **write_letter** — sender 用 write_file 写一封信到指定路径
- **read_letter** — 给 receiver 信件路径，让 agent 自读；workflow 读完后删信
- **read_and_write_letter** — 读输入信 → 写回信到输出路径；workflow 读完后删输入信

信件内容 workflow 不读取，但 agent 的回复指令（`read_and_write_letter` 的 `instruction` 参数）是直接通过 prompt 传递给 agent 的，非纯文件路径传递。

### `_resolve_file_refs`

`{文件路径}` 语法在 `ConversationManager.call()` 内自动替换为文件内容。终端显示路径字符串，LLM 收到文件内容。用于 flush 时注入设计文档等大段静态上下文。

## Conversation 命名

| 用途 | Agent | Conversation 名 | 说明 |
|:-----|:------|:----------------|:------|
| Master 全流程 | master | `master-{ws}-{ts}` | 单一对话贯穿全流程 |
| Judge 判读 | judge | `judge-{tag}-{ws}-{ts}` | 每次判读独立 conv，用完即弃 |
| Reviewer 审查 | reviewer | `review-{type}-{ws}-{ts}` | 按场景独立 conv |
| PM 对齐 | pm | `pm-align-{ws}-{ts}` | PM 与 Master 对齐 |
| PM 出文档 | pm | `pm-doc-{ws}-{ts}` | 写 PRD + 原型 |
| Dev 对齐 | dev | `dev-align-{ws}-{ts}` | Dev 与 PM/Master 对齐 |
| Dev 设计 | dev | `dev-design-{ws}-{ts}` | 出详细设计 |
| Dev 计划 | dev | `dev-plan-{ws}-{ts}` | 出执行计划 |
| Dev 执行 | dev | `dev-exec-{ws}-{ts}` | 编码步骤 |
| Dev 其他 | dev | `dev-git-init-{ws}-{ts}` | git init 专用 |
| QA 对齐 | qa | `qa-align-{ws}-{ts}` | QA 与 PM/Dev/Master 对齐 |

> WS = basename(getcwd())，TS = time.strftime("%Y%m%d_%H%M%S")。

## 图结构

### Phase 0: 需求澄清

```
pre_flight_clarify
  → 初始化 Master conversation，注入 MASTER_SYSTEM_PROMPT
  → 调用 clarify_loop（用户输入 → Master 回答 → judge 判读 → 确认子循环）
  → 退出前 Master 写 project_context.md（存入 artifacts/）
  → phase = "done"
master_flush_after_clarify
  → Master 写阶段总结 → 关旧对话 → 开新对话注入 project_context.md + 总结
  → 保存 checkpoint（resume_node="pm_handoff"）
```

澄清循环：用户输入 → Master 回复 → judge(A/B) 判读 → 如 A 进入用户确认子循环（EOF=确认）→ 如 B 继续循环。

### Phase 1: PM 出方案 + 审查

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
                    master_flush_after_pm
                              │
                              ▼
                    dev_handoff   review_pm_output
```

**pm_handoff**：Master 写 handoff 信给 PM，含项目概况和顶层决策文件路径。

**pm_align**：
- 首次：PM 读 handoff 信，写回信汇报理解 + 列出疑问
- 循环：Master 先写答复信 → PM 读信 → 写回信
- PM 对齐对话存入 context，后续 dev_align 和 qa_align 复用

**master_reply_pm**：Master 读 PM 回信，逐一检查 PM 理解并回答疑问。

**judge_master_reply**：三路路由 A/B/C。

**clarify_inject**：复用 Master 对话，调用 `clarify_loop` 向用户提问，决策追加到 `project_context.md`。

**pm_write_criteria**：Master 制定审核标准，自检循环（`judge-pm-criteria`）直至通过。

**pm_write_doc**：两次 write_letter + read_letter：
- Call 1：Master 写信要求 PRD → PM 写入 `{workspace}/PM/PRD.md`
- Call 2：Master 写信要求原型 → PM 写入 `{workspace}/PM/prototype.html`
- 从审查循环回来时注入 `review_result` + `human_feedback`

**review_pm_output**：Reviewer 对照 criteria 审查 PRD + prototype，judge 判读 PASS/FAIL。

**human_review**：展示文件路径让人确认。EOF=通过。

#### 产出

| 文件 | 路径 |
|:-----|:-----|
| 项目顶层决策 | `{runtime_dir}/artifacts/project_context.md` |
| 审核标准（PM） | `{workspace}/criteria-pm.md` |
| PRD | `{workspace}/PM/PRD.md` |
| Prototype | `{workspace}/PM/prototype.html` |

### Phase 2: Dev 出设计 + 编码执行

```
dev_handoff → dev_align → dev_write_criteria → review_dev_criteria
                                                   │ PASS   │ FAIL
                                                   ▼         │
                                              dev_write_design│
                                                   │          │
                                                   ▼          │
                                              dev_write_plan  │
                                                   │          │
                                                   ▼          │
                                              dev_review_plan │
                                               │ PASS │ FAIL  │
                                               ▼       └──────┘
                                          dev_git_init (flush + cp)
                                               │
                                               ▼
                    ┌────────────────── dev_exec_step ──────────┐
                    │                        │                  │
                    │                        ▼                  │
                    │              [ dev_review_step ]          │
                    │            ┌───────┼───┬───┬───┐          │
                    │            │ PASS  │   │   │   │          │
                    │            ▼       │   │   │   │          │
                    │       dev_commit   │   │   │   │          │
                    │      │ PASS │ FAIL │   │   │   │          │
                    │      └──┬───┘      │   │   │   │          │
                    │    next_step  done │   │   │   │          │
                    │         │     │    │   │   │   │          │
                    │         │     ▼    │   │   │   │          │
                    │         │  master_ │   │   │   │          │
                    │         │  flush_  │   │   │   │          │
                    │         │  after_  │   │   │   │          │
                    │         │  dev     │   │   │   │          │
                    │         │     │    │   │   │   │          │
                    └─────────┘     │    │   │   │   │
                                    │  step_  dev_  dev_
                                    │  retry rollback escalate
                                    ▼    │     │     │
                                 qa_      └──┬──┘     │
                                handoff      │        │
                                    └────────┴────────┘
                                           (all back to
                                          dev_exec_step)
```

**dev_handoff**：Master 写 handoff 信给 Dev，含 PRD、prototype 路径。

**dev_align**：Dev ↔ PM/Master 对齐循环。Dev 产理解+疑问，PM 审查，有争议升级 Master。Dev 对齐对话存入 context（`dev_conv`），后续复用。

**dev_write_criteria**：Master 制定 Dev 审核标准（架构、功能、数据流、可实现性、可测试性），自检循环直至通过。

**review_dev_criteria**：Reviewer 审查标准，PASS 进设计，FAIL 回修。

**dev_write_design**：Master 写信 → Dev 产出 `Dev/design.md`（架构、数据流、API 定义、组件结构）。

**dev_write_plan**：Master 写信 → Dev 产出 `Dev/plan.md`（分步实现计划，每步含验收方法）。

**dev_review_plan**：Reviewer 审查计划，PASS 进执行，FAIL 回修。

**dev_git_init**：Dev 在 `Dev/` 目录初始化 git 仓库并做初始空提交。完成后关闭 Dev 对话（align/design/plan 阶段的上文已用尽），创建新的 dev-exec 对话并注入 design.md + plan.md + compact-summary，保存 checkpoint（`resume_node="dev_exec_step"`）。

**dev_exec_step**：从 plan.md 取当前 step，Master 写信给 Dev 实现。Master 信件限定代码产出必须在 `Dev/` 目录下。

**dev_review_step**：Reviewer 审查当前 step 的实现 + judge 判读 PASS/FAIL。失败计数：
- 首次 FAIL 免费（不计数）
- 后续每次 FAIL 递增 `dev_step_fail_count`
- `fail_count >= rollback_threshold` → git reset --hard HEAD 回滚重来
- `fail_count >= escalation_threshold` → 升级人工

**dev_commit**：审查通过后 Dev git add + commit。

**dev_rollback**：Dev `git reset --hard HEAD`，重做当前 step。

**dev_escalate**：三步流程：
1. Dev 简述 plan、当前 step 内容和问题
2. 用户对话循环（`checkpoint.wait`，EOF 结束）
3. Dev 总结决策写入 `dev_escalation_decision` context

**上下文 flush**（参见 `conversation-flush-design.md`）：每 step PASS 后关闭 Dev 对话，新对话注入 design.md + plan.md + compact_summary。

#### 产出

| 文件 | 路径 |
|:-----|:-----|
| 审核标准（Dev） | `{workspace}/criteria-dev.md` |
| 详细设计 | `{workspace}/Dev/design.md` |
| 执行计划 | `{workspace}/Dev/plan.md` |
| 代码 + git 仓库 | `{workspace}/Dev/` |

### Phase 3: QA 对齐

```
qa_handoff → qa_align → END
```

**qa_handoff**：Master 写 handoff 信给 QA，含项目概况、PRD、prototype、design.md 路径。

**qa_align**：QA ↔ PM/Dev/Master 对齐循环：
- 首次：QA 读 handoff 信 → 写理解总结 + 测试思路大纲
- PM 审查测试范围
- Dev 审查技术可行性
- 综合 PM+Dev 的反馈，judge 判读：
  - A：无需修改，对齐完成
  - B：有反馈需 QA 修改，无需升级
  - C：有争议需升级 Master
- Master 升级：Master 回答问题 → judge 判读是否需问用户 → 需则调用 `clarify_loop`
- 对齐完成后保存 QA 理解到 `{workspace}/QA/understanding.md`

### Phase 4: 交付（预留）

尚未实现。

## 关键设计决策

### 1. Master 单一 conversation 贯穿全流程

PM 的疑问、Dev 的执行问题都可能回溯到初始需求。Master 单一 conversation 让上下文连贯，用户看到的也是同一段对话历史。

### 2. 信件通信 + 内联指令混合

设计上 agent 之间通过信件文件通信，但 workflow 在 `read_and_write_letter` 中通过 prompt 直接向 agent 传递指令（instruction 参数），信件文件只承载 agent 之间的内容传递。

### 3. Dev 的失败回滚与升级

三次失败阈值体系（可配置）：
- `fail_rollback_threshold`：默认 3，触发 git 回滚
- `fail_escalation_threshold`：默认 5，触发人工对话

第一次 FAIL 不计数的理由是：首次可能只是审题偏差或微小遗漏，不应计入惩罚性计数。

### 4. Dev 对话 flush

每 step 完成后 flush conversation，控制上下文窗口。注入 design.md + plan.md + compact_summary。具体策略见 `conversation-flush-design.md`。

### 5. `_resolve_file_refs` 文件内容注入

通过 `{路径}` 语法在 prompt 中引用文件，终端显示路径字符串，LLM 收到文件内容。用于 AgentRuntime 的 `ConversationManager.call()` 中，自动解析。

### 6. Judge 无状态调用

每次调用独立 conversation，用后即弃。不注册 registry，不走 begin/close 生命周期。

### 7. Checkpoint / Resume 断线重连

工作流在以下位置保存 checkpoint（JSON 文件 `{runtime_dir}/checkpoint.json`）：

| 位置 | resume_node | 触发时机 |
|:-----|:------------|:---------|
| Phase 0→1 边界 | `pm_handoff` | 需求澄清完成，master_flush_after_clarify |
| Phase 1→2 边界 | `dev_handoff` | PM 方案完成，master_flush_after_pm |
| Phase 2→3 边界 | `qa_handoff` | Dev 实现完成，master_flush_after_dev |
| Dev 开始执行前 | `dev_exec_step` | dev_git_init（step_idx=0） |
| Dev 每步提交后 | `dev_exec_step` | dev_commit（step_idx=N） |

重启后 `resume_router` 作为图入口节点，检测到 checkpoint 则询问用户是否恢复：

```
resume_router → 检测 checkpoint
               ├─ 有 → 询问用户 → y → 清理目标目录 + 重建对话 → 路由到 resume_node
               │                        └→ 其他 → 清除 checkpoint → pre_flight
               └─ 无 → pre_flight
```

恢复时的清理逻辑（`_clean_next_phase`）：
- 清理 `{runtime_dir}/handoffs/`（旧信件全部删除）
- 恢复 `pm_handoff`：清理 `{workspace}/PM/` + `criteria-pm.md`
- 恢复 `dev_handoff`：清理 `{workspace}/Dev/`
- 恢复 `qa_handoff`：清理 `{workspace}/QA/`
- 恢复 `dev_exec_step`：不清理（代码由 git 管理）
- 恢复 `dev_exec_step` 时自动重建 Dev 执行对话，注入 design.md + plan.md + compact-summary

### 8. `.agent_runtime` 目录结构

```
{runtime_dir}/
├── checkpoint.json          # 断线重连检查点
├── context.json             # 三段式上下文（bg/ctx/phase）
├── registry.json            # Agent 注册信息
├── config.json              # 配置（从 runtime_config.json 同步）
├── calls.jsonl              # Agent 调用日志
├── events.jsonl             # 事件日志
├── artifacts/               # 项目顶层决策等固化文档
│   └── project_context.md
├── phases/                  # 阶段总结
│   └── phase-summary-{name}.md
└── handoffs/                # Agent 间通信信件
    └── {name}-{ws}-{ts}.md
```

## 变更对比（v4 → v5）

| 变更 | v4 | v5 |
|:-----|:---|:---|
| Dev 循环 | 简单 exec→review→END | git init, commit, rollback, escalate, fail counting |
| Phase 3 | "预留，暂不实现" | QA 对齐完整实现（qa_handoff → qa_align） |
| Judge 场景 | 2 个 | ~10 个（含 step-N、qa 等） |
| Reviewer 对话 | 单一 `reviewer-{ws}-{ts}` | 按场景细分：review-pm-criteria, review-step-N 等 |
| 失败处理 | 无 | 三档阈值：重试/回滚/升级 |
| 人工介入 | 无 | dev_escalate 三步对话流程 |
| Context flush | 无 | 每 step flush + 文件注入 + phase 边界 flush |
| Checkpoint/Resume | 无 | 5 个断点 + resume_router + 目录清理 |
| .agent_runtime 结构 | 平铺 | 分 artifacts/phases/handoffs 子目录 |
| _resolve_file_refs | 无 | `{路径}` 语法自动解析 |
| Master 通信 | setup_runtime 写入 context | 同，v5 无变化 |
| phase_artifacts | 无 | flush 时传入实际产出路径，summary 更准确 |
| handoff 清理 | 无 | resume 时自动清理 handoffs/ 目录 |
