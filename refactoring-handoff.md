# Refactoring Handoff — 单 call_agent 节点重构

## 核心原则

每个 LangGraph 节点只包含**一次** `call_agent` 调用（或其等价如 `write_letter` / `read_letter` / `judge_reply` / `clarify_loop`）。

`clarify_loop` 和 `DevEscalate.dialogue` 内部虽然可能有多次 `call_agent`，但它们在内部正确处理 `WorkflowInterrupted`（捕获后继续循环），不传播给 `interruptible`，所以是安全的特例。

## Phase 2 重构完成

### DevHandoff（未拆分，1 节点）

原函数 `dev_handoff` → 类 `DevHandoff`。1 个 call_agent（`write_letter`），无需拆分。

### DevAlign（7 节点 + 1 空节点）

原 `dev_align` 的 while 循环拆为线性图节点：
- `dev` → `pm` → `judge`
- judge 路由：exit / feedback(→dev) / escalate(→master)
- `master` → 内部 judge 路由：confirm(→confirm→record→final→dev) / dev
- 共 7 功能节点 + 1 空节点 `judge_exit`

注意：`master` 节点用了 `read_and_write_letter` + `judge_reply` 两个 call_agent。这是历史遗留，后续应拆分。

### DevWriteCriteria（1 节点）

原函数 `devwrite_criteria` → 类 `DevWriteCriteria`。将 `read_letter`（读反馈信）合并到 prompt 的 if-else 中：有反馈时在 prompt 前加一段让 agent 先 `read_file` 读反馈。去掉了独立的 `read_letter` 调用。

### ReviewDevCriteria（2 节点 + 1 空节点）

原函数 `review_dev_criteria` → 类 `ReviewDevCriteria`。review 和 judge 共存于同一节点（judge 是 stream=False），写反馈信拆为独立节点 `write_feedback`。引入空节点 `to_dev_design` 做 PASS 出口。

### DevWriteDesign（2 节点）

原函数 `dev_write_design` → 类 `DevWriteDesign`。
- `write_design_letter` — Master 写信（`write_letter`）
- `read_design_letter` — Dev 读信写 design.md（`read_letter`）
- 加 `design_feedback_path` 判断，和 DevWriteCriteria 一样模式

### DevReviewDesign（新增，2 节点 + 1 空节点）

原流程缺少对 design.md 的审查，新增 `DevReviewDesign` 类，和 `ReviewDevCriteria` 模式相同。
- `review_design` — review + judge
- `write_feedback` — 不通过写反馈信
- `exit_pass` — 空节点

### DevWritePlan（2 节点）

原函数 `dev_write_plan` → 类 `DevWritePlan`。和 DevWriteDesign 同样模式。
- 加 `plan_feedback_path` 判断

### DevReviewPlan（2 节点 + 1 空节点）

原函数 `dev_review_plan` → 类 `DevReviewPlan`。和 ReviewDevCriteria 同样模式。
- 条件边从 graph.py 移到 register 内部
- graph.py 外部改成两条简单边

### DevGitInit（3 节点）

原函数 `dev_git_init` → 类 `DevGitInit`。
- `git_init` — git init 操作
- `write_summary` — 写初始 compact-summary.md（保证 `flush_context` 能读到）
- `flush_context` — 关旧对话 + 开新对话 + 注入压缩上下文 + 检查点

### DevExecStep（2 节点）

原函数 `dev_exec_step` → 类 `DevExecStep`。和 DevWriteDesign 同样模式。

### DevReviewStep（1 节点）

原函数 `dev_review_step` → 类 `DevReviewStep`。review + judge 共存，路由逻辑（fail_count、rollback/escalate 判断）是纯 Python，无额外 call_agent。

### DevCommit（3 节点 + 1 空节点）

原函数 `dev_commit` → 类 `DevCommit`。
- `git_commit` — 条件路由 "done"（全部完成）/ "continue"（还有步骤）
- `write_summary` — 写进度摘要 + `ensure_write_file`
- `flush_context` — 关旧对话 + 开新对话 + 注入 + 检查点
- `exit_pass` — 空节点，统一出口

### DevRollback（1 节点）

原函数 `dev_rollback` → 类 `DevRollback`。1 个 call_agent，无需拆分。

### DevEscalate（3 节点）

原函数 `dev_escalate` → 类 `DevEscalate`。
- `summarize` — Dev 写问题简述
- `dialogue` — 用户对话循环（内部 try/except WorkflowInterrupted，类似 clarify_loop）
- `conclude` — Dev 总结决策

## 节点组织约定（重申）

参考 `doc/node-organization-pattern.md`，实践中补充：

1. **`entries`** — 只放**外部入点**（被 graph.py 或外部条件边路由进来的节点）
2. **`exits`** — 放外部出点（被 graph.py 引用于跨组边的源节点）
3. **`register` 内** — 组内所有边（包括条件边），目标可以是内部节点或外部节点
4. **graph.py** — 只做纯跨组边（`add_edge`），不处理组内条件边
5. **组内空节点** — 如果条件边需要混合内部和外部目标，加一个空 pass-through 节点做出口，让条件边所有目标都在组内
6. **entries/exits 键约定** — 键名使用**方法名**（如 `write_summary`、`flush_conv`），graph.py 通过 `ClassName.exits["method_name"]` 引用

## ResumeRouter 重构

### 背景

原 `resume_router`、`resume_pm_handoff`、`resume_dev_handoff`、`resume_qa_handoff`、`resume_dev_exec_step` 是 5 个独立函数，注册在 NODES 列表中通过 `interruptible` 包装。

### 改动

改为 `ResumeRouter` 类，6 节点（5 功能 + 1 空节点）：

- `router` — 入口，检测 checkpoint + 询问用户
- `to_pre_flight` — 空节点，作为 `pre_flight` 路由出口
- `resume_pm` / `resume_dev` / `resume_qa` / `resume_dev_exec` — 恢复节点

条件路由（5 个目标）全部在 `register()` 内部，graph.py 只做 5 条简单跨组 `add_edge`。

### 关键点

- 空节点 `to_pre_flight` 避免了 checkpoint.py 对 PreFlightClarify 的循环依赖
- 3 个 resume_handoff 节点共享 `open_master_conv` 但清理的目标目录不同

## MasterFlush 重构

### 背景

原 `master_flush_after_clarify`、`master_flush_after_pm`、`master_flush_after_dev` 是 3 个独立函数，共用 `_master_flush` 辅助函数。`_master_flush` 内包含最多 3 个 call_agent 调用（写总结、ensure_write_file 回退、开新对话）。

### 改动

拆为 3 个独立类，每类 2 节点：

- **MasterFlushClarify** — Phase 0→1 边界
- **MasterFlushPM** — Phase 1→2 边界
- **MasterFlushDev** — Phase 2→3 边界

每类：
- `write_summary` — 写阶段总结（1 个 stream=True call_agent + ensure_write_file stream=False）
- `flush_conv` — 开新对话（open_master_conv → 1 个 call_agent）+ 存 checkpoint

### 关键点

- `ensure_write_file` 使用 `stream=False`，可和主 call_agent 共存于同一节点
- 三个类的结构完全一致，但 prompt 和产出路径不同，不做抽象抽取
- `_master_flush` 辅助函数已删除，逻辑内联到各节点

## 踩坑记录

### 1. 中文引号（U+201C/U+201D）导致的 SyntaxError

Python 字符串中如果包含中文引号 `"`（U+201C）和 `"`（U+201D），这些字符和 ASCII `"` 在视觉上几乎一样但编码不同。如果用 Edit 工具替换含有这些字符的代码段，容易意外将中文引号引入 Python 字符串定界符位置，导致 SyntaxError。

**预防**：Edit 操作的 `old_string` 和 `new_string` 直接从 Read 的输出中复制，不要在对话中手动写。如果 Read 显示的中文引号看起来可疑，先用 `xxd` 或 `python -c "print(ascii(text))"` 确认实际编码。

### 2. 函数转类时的三板斧

每次转换统一步骤：
1. 数 `call_agent`（含 `write_letter` / `read_letter` / `read_and_write_letter` / `judge_reply`）
2. 决定拆分方案：`judge_reply`（stream=False）可与前一个 call_agent 共存
3. 确定 `entries` / `exits` 键（统一用 `"run"` 除非有多个出口）
4. 确认 graph.py 中 import、register、边引用一致

### 3. ensure_write_file + stream=False

`ensure_write_file` 内部可能调 `call_agent`。当调用者切换到新对话后，如果 `ensure_write_file` 在旧对话中重试，会因为对话已关闭而失败。改为 `stream=False` 后不再响应中断，可以和相邻 Python 操作合并到同一节点。

### 4. DevGitInit 和 DevCommit 的 Summary/Flush 重复

两个类的 `write_summary` 和 `flush_context` 高度相似，但**不做抽象抽取**：
- 分别属于初始化阶段和迭代阶段，后续可能各自演化
- 只有两处使用，不值得引入抽象
- prompt 文本不同，统一会引入 if-else

### 5. 路由设计：首节点决定 vs 末节点决定

当条件路由的决定点在首节点（如 DevCommit：commit 后就知道 done 还是 continue），但条件目标中有外部节点时：
- 首节点用内部条件边路由到组内节点（continue→summary→flush）
- 末节点返回统一 `judge_result`，外部条件边从末节点（或空节点）统一路由
- 组内加空节点作为"done"出口，统一出口让外部条件边只从一个节点出发

### 6. dialogue 节点的中断处理

`DevEscalate.dialogue` 和 `clarify_loop` 一样，需要在内部 `try/except WorkflowInterrupted`。否则 Ctrl+U 中断后重新进入 `dialogue` 节点会从头开始循环，丢失对话进度。虽然后端 `dev_conv` 保留上下文，但用户体验很差。

## 尚未检查/拆分

Phase 3：
- `qa_handoff` — 待检查
- `qa_align` — 待检查
