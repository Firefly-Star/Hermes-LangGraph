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

---

## Iteration 2 — 需求澄清

目标：可以和 Master 对话，逐轮澄清需求。

新增节点：1 个（`pre_flight_clarify`）

变更：`checkpoint.wait()` 新增 `end_word` 参数支持多行输入；`runtime_config.json` 新增 `input_end_word` 配置项

验收：输入需求 → Master 回应 → EOF 结束。

功能：
- 无限循环，通过 `input_end_word`（默认 EOF）支持多行输入
- Master 回应的内容通过 SSE 流式显示给用户
- 空输入（直接 EOF）视为确认，无需输入 CONFIRMED
- 退出前通知 Master 写 project_context.md

---

## Iteration 3 — PM 出方案 + 审核循环

目标：PM 产出 PRD.md + prototype.html，经过审核循环（criteria 自检 + Reviewer 审查 + 人工审查）后通过。

新增节点：
- `pm_handoff` — Master 写 handoff 信给 PM
- `pm_align` — PM 汇报理解 + 疑问
- `master_reply_pm` — Master 回答 PM 疑问
- `judge_master_reply` — 判读路由（A/B/C）
- `clarify_inject` — 向用户确认无法判定的问题
- `pm_write_criteria` — Master 制定审核标准，自检循环
- `pm_write_doc` — PM 产出 PRD + prototype
- `review_pm_output` — Reviewer 按标准审查
- `human_review` — 人工审核，可提反馈循环

变更：注册 PM/Reviewer agent，新增 role_aware_prompt、_letter_path、write_letter、read_letter、read_and_write_letter 等公用函数

验收：PM 生成文档 → 审查循环 → 通过或反馈循环。

图结构：
```
pre_flight_clarify → pm_handoff → pm_align → master_reply_pm → judge_master_reply
                                                                       │ A
                                                                       ▼
                                                                  pm_write_criteria
                                                                   │ pass
                                                                   ▼
                                                               pm_write_doc
                                                                   │
                                                                   ▼
                                                               review_pm_output
                                                                │ PASS
                                                                ▼
                                                            human_review
                                                             │ PASS → END
                                                             │ FAIL → review_pm_output
```

---

## Iteration 4 — Dev 详细设计 + 评审

目标：Dev 出详细设计文档并通过评审。

新增节点：3 个（`dev_design`、`dev_design_criteria`、`dev_design_review`）

验收：设计文档通过评审后进入下一步。

---

## Iteration 5 — Dev 计划 + 执行循环

目标：Dev 分步实现代码，每步被审查。

新增节点：4 个（`dev_plan`、`dev_plan_review`、`dev_exec_step`、`dev_review_step`）

变更：引入上下文 flush 机制

验收：Dev 逐步实现代码，每步审查通过才继续。

---

## Iteration 6 — QA 计划 + 测试循环

目标：QA 执行测试，bug 被修复验证。

新增节点：6 个（`qa_plan`、`qa_plan_review`、`qa_exec_test`、`qa_write_report`、`dev_fix_bug`、`qa_verify_fix`）

验收：QA 跑测试 → 发现 bug → Dev 修复 → QA 验证通过。

---

## Iteration 7 — 交付 + 收尾

目标：完整工作流打通，交付后用户 sign-off。

新增节点：1 个（`deliver`）

验收：完整走完所有阶段，输出总结报告。
