# Workflow 迭代计划

## 设计原则

- 每阶段代码总量 ≤200 行
- 每阶段完成后可直接运行验收
- 每次只新增 1-2 个节点
- 评审循环的逻辑只在第一次出现时完整实现，后续复用

---

## Iteration 1 — 骨架

目标：搭建 LangGraph + AgentRuntime 最小可运行框架。

节点数：1 个（`master_hello`）

验收：`python src/workflow.py` 跑通，看到 Master 回复。

功能：
- 启动 Master Gateway（仅一个 Agent）
- 一条边直通 END
- main() 输出可读结果

---

## Iteration 2 — 需求澄清

目标：可以和 Master 对话，逐轮澄清需求。

新增节点：1 个（`pre_flight_clarify`）

变更：`checkpoint.wait()` 新增 `end_word` 参数支持多行输入；`runtime_config.json` 新增 `input_end_word` 配置项

验收：输入需求 → Master 回应 → 输入 CONFIRMED 结束。

功能：
- 无限循环，通过 `input_end_word`（默认 EOF）支持多行输入
- Master 回应的内容通过 SSE 流式显示给用户
- 用户输入 CONFIRMED 或 Master 回复 `## 确认` 时结束
- 退出前通知 Master 阶段结束，避免遗留疑问悬空

---

## Iteration 3 — PM 出方案

目标：PM 产出 PRD.md + prototype.html 到磁盘。

新增节点：1 个（`pm_write_doc`）

变更：注册 PM agent、抽出公用函数 `role_aware_prompt`、`call_agent`

验收：PM 角色生成文档文件。

---

## Iteration 4 — PM 评审循环

目标：方案写审核标准 → 审查 → 循环或通过。

新增节点：2 个（`pm_write_criteria`、`pm_review_doc`）

变更：抽出 `write_criteria`、`archive_review` 公用函数

验收：方案不通过会循环，达上限可人工 override。

---

## Iteration 5 — Dev 详细设计 + 评审

目标：Dev 出详细设计文档并通过评审。

新增节点：3 个（`dev_design`、`dev_design_criteria`、`dev_design_review`）

验收：设计文档通过评审后进入下一步。

---

## Iteration 6 — Dev 计划 + 执行循环

目标：Dev 分步实现代码，每步被审查。

新增节点：4 个（`dev_plan`、`dev_plan_review`、`dev_exec_step`、`dev_review_step`）

变更：引入上下文 flush 机制

验收：Dev 逐步实现代码，每步审查通过才继续。

---

## Iteration 7 — QA 计划 + 测试循环

目标：QA 执行测试，bug 被修复验证。

新增节点：6 个（`qa_plan`、`qa_plan_review`、`qa_exec_test`、`qa_write_report`、`dev_fix_bug`、`qa_verify_fix`）

验收：QA 跑测试 → 发现 bug → Dev 修复 → QA 验证通过。

---

## Iteration 8 — 交付 + 收尾

目标：完整工作流打通，交付后用户 sign-off。

新增节点：1 个（`deliver`）

验收：完整走完 9 阶段，输出总结报告。
