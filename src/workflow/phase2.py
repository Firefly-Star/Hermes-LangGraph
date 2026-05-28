"""Phase 2: Dev 出设计 + 编码执行阶段。"""
import os

from .utils import (WorkflowState, conv_name, call_agent, letter_path,
                    ensure_write_file, write_letter, read_letter,
                    read_and_write_letter, judge_reply, clarify_loop,
                    write_criteria, get_step_from_plan, count_steps)
from .config import DEV_SYSTEM_PROMPT, FLUSH_CONTINUATION_NOTE
from .checkpoint import save_checkpoint
from langgraph.graph import END


def dev_handoff(state: WorkflowState) -> dict:
    """Phase 2a: Master 写 handoff 信给 Dev。"""
    runtime = getattr(dev_handoff, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Phase 2a: Master 写信给 Dev\n{'='*60}")

    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")
    ws = runtime.workspace

    lpath = letter_path(runtime, "master-to-dev")
    write_letter(runtime, "master", master_conv, lpath,
                 "Master 给 Dev 的信",
                 f"介绍项目上下文。信件需包含：\n"
                 "1. 开宗明义：这是 Master 给 Dev 的信\n"
                 "2. 项目概况和核心需求（简要描述即可）\n"
                 f"3. 告知 Dev 详细内容在以下文件：\n"
                 f"   项目顶层决策：{project_context_path}\n"
                 f"   PRD：{ws}/PM/PRD.md\n"
                 f"   原型：{ws}/PM/prototype.html\n"
                 "4. 要求 Dev：先阅读以上所有文档，然后写出你对需求的理解总结和疑问清单\n"
                 "5. 你的直接对接人是 PM，PM 无法回答的问题会由 Master 处理\n"
                 "6. 在 PM 明确许可之前，不得开始写详细设计\n\n"
                 "信件要有 Master 的口吻，是上级对下级的沟通与任务委派。")

    runtime.context.set_ctx("devletter_path", lpath)
    print(f"\n  ── Master 给 Dev 的信件已就绪 ──")
    return {"phase": "dev_handoff_done"}


def dev_align(state: WorkflowState) -> dict:
    """Phase 2b: Dev↔PM/Master 对齐循环。"""
    runtime = getattr(dev_align, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    ws = runtime.workspace

    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = conv_name("dev-align")
        runtime.context.set_ctx("dev_conv", dev_conv)

    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = conv_name("pm-align")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="Dev 对齐")
    print(f"\n{'='*60}\n  ==> Phase 2b: Dev 对齐（Dev ↔ PM / Master）\n{'='*60}")

    is_first = True
    while True:
        if is_first:
            handoff_path = runtime.context.get_ctx("devletter_path")
            if not handoff_path:
                raise RuntimeError("没有 handoff 信件路径")
            dev_reply_path = letter_path(runtime, "dev-understanding")
            read_and_write_letter(runtime, "dev", dev_conv,
                                  handoff_path, dev_reply_path,
                                  "From Dev, Re: Master 的委托",
                                  "阅读所有项目文档后，"
                                  "写出你对项目需求的理解总结，"
                                  "以及不清楚或有疑问的地方的清单。",
                                  "在 PM 明确许可之前，不得开始写详细设计")
            is_first = False
        else:
            feedback_path = runtime.context.get_ctx("dev_feedback_path")
            if not feedback_path or not os.path.exists(feedback_path):
                raise RuntimeError("Dev 反馈信缺失")
            dev_reply_path = letter_path(runtime, "dev-understanding")
            read_and_write_letter(runtime, "dev", dev_conv,
                                  feedback_path, dev_reply_path,
                                  "From Dev, Re: 修订后的理解",
                                  "根据上轮反馈修订你的理解总结，"
                                  "如果有新的疑问也一并提出。"
                                  "如果已经没有疑问，明确说明已无疑问。",
                                  "在 PM 明确许可之前，不得开始写详细设计")

        pm_reply_path = letter_path(runtime, "pm-reply-dev")
        read_and_write_letter(runtime, "pm", pm_conv,
                              dev_reply_path, pm_reply_path,
                              "From PM, Re: Dev 的理解与疑问",
                              "逐一检查 Dev 的理解是否正确，有误则纠正。"
                              "回答 Dev 的所有疑问。"
                              "有无法回答的问题，在对应处标记 ❓需要升级。"
                              "如果 Dev 的理解完全正确且无疑问，也请明确说明。"
                              "不得许可 Dev 写详细设计。"
                              "注意：如果需要升级到 Master，你的回信中必须包含"
                              "Dev 对项目的全部理解和全部疑问清单"
                              "（包括你已解答的和需要升级给 Master 的），"
                              "以便 Master 掌握完整上下文。",
                              "审 Dev 的理解并回答问题")

        pm_reply = ""
        if os.path.exists(pm_reply_path):
            with open(pm_reply_path, "r", encoding="utf-8") as f:
                pm_reply = f.read()
            runtime.context.set_ctx("pm_reply_text", pm_reply)

        judge_result = judge_reply(runtime, "PM", pm_reply, [
            "A. Dev 理解完全正确且无疑问，无需修改",
            "B. PM 有反馈需要 Dev 修改或回答疑问",
            "C. PM 有需要升级到 Master 的问题",
        ], "judge-dev-align")
        needs_upgrade = "❓" in pm_reply

        if judge_result in ("C",) or needs_upgrade:
            print(f"\n  ── 升级到 Master ──")
            master_reply_path = letter_path(runtime, "master-reply-dev")
            read_and_write_letter(runtime, "master", master_conv,
                                  pm_reply_path, master_reply_path,
                                  "From Master, Re: Dev 对齐中的争议",
                                  "阅读 PM 的报告，逐条回答 PM 无法解决的问题。"
                                  "如果 PM 报告中有你无法判定的问题，明确写出需要向用户确认。"
                                  "你的回复中将包含 Dev 对项目的全部理解和全部疑问清单"
                                  "（包括 PM 已解答的和需要升级给你的），"
                                  "确保 Dev 收到后掌握完整的对齐结论。",
                                  "回答 Dev 对齐中升级上来的问题")

            master_reply = ""
            if os.path.exists(master_reply_path):
                with open(master_reply_path, "r", encoding="utf-8") as f:
                    master_reply = f.read()

            master_judge = judge_reply(runtime, "Master", master_reply, [
                "A. Master 已解决所有问题",
                "B. Master 还有疑问需要向用户确认",
            ], "judge-dev-master")

            if master_judge == "B" or "❓" in master_reply:
                print(f"\n  ── Master 需要向用户确认 ──")

                def _close(reason: str):
                    pc_path = runtime.context.get_bg("project_context_path")
                    call_agent(runtime, "master", master_conv,
                               f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{pc_path}")

                clarify_loop(runtime, master_conv, "== 向用户确认（Dev 对齐）==",
                             "Master 需要向用户确认 Dev 对齐中的争议问题", _close)

                final_path = letter_path(runtime, "master-final-dev")
                write_letter(runtime, "master", master_conv, final_path,
                            "Master 给 Dev 的最终答复",
                            "根据用户确认的决策以及你的分析，"
                            "写出对 Dev 对齐中所有问题的最终答复。")
                runtime.context.set_ctx("dev_feedback_path", final_path)
            else:
                runtime.context.set_ctx("dev_feedback_path", master_reply_path)

        elif judge_result == "B":
            runtime.context.set_ctx("dev_feedback_path", pm_reply_path)

        else:
            runtime.logger.log_event("phase_completed", detail="Dev 对齐完成")
            print(f"\n  ✓ Dev 对齐完成")
            if os.path.exists(pm_reply_path):
                os.remove(pm_reply_path)
            return {"phase": "dev_align_done"}


def devwrite_criteria(state: WorkflowState) -> dict:
    """Master 制定 Dev 详细设计的审核标准。"""
    runtime = getattr(devwrite_criteria, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")
    ws = runtime.workspace

    runtime.logger.log_event("phase_started", detail="Dev 设计审核标准制定")

    feedback_path = runtime.context.get_ctx("dev_criteria_feedback_path") or ""
    if feedback_path and os.path.exists(feedback_path):
        read_letter(runtime, "master", master_conv, feedback_path,
                    "根据反馈意见重新制定审核标准")
        runtime.context.set_ctx("dev_criteria_feedback_path", "")

    prompt = (
        "你即将为 Dev 的详细设计方案制定审核标准。\n\n"
        "## 上游约束\n"
        "标准必须与以下已确认的内容对齐：\n"
        f"- 项目决策记录：{project_context_path or '（无项目决策记录）'}\n"
        f"- PRD：{ws}/PM/PRD.md\n"
        f"- 原型：{ws}/PM/prototype.html\n\n"
        "## 标准覆盖维度\n"
        "1. 架构合理性 — 设计方案是否与 PRD 一致？技术选型是否合理？\n"
        "2. 功能完整性 — 设计方案是否覆盖了 PRD 中所有功能点？\n"
        "3. 数据流正确性 — 数据流转路径是否清晰？前后端接口定义是否完整？\n"
        "4. 可实现性 — 在当前技术栈和约束下是否可行？\n"
        "5. 可测试性 — 设计是否考虑了如何验证每个功能？\n"
        "6. 边界与异常 — 是否涵盖了错误处理、空状态、异常场景等边界情况？\n"
        "## 下游需求\n"
        "- Dev 将按这些标准撰写详细设计方案\n"
        "- Reviewer 将按这些标准审查 Dev 的设计\n\n"
        "## 要求\n"
        "文件中只需要写测什么以及怎么样算是测试完成，不需要写审查方法（reviewer 自己知道怎么测）。\n"
        "请具体、可操作，避免空泛描述。"
    )

    write_criteria(
        runtime, master_conv,
        title="Master 制定 Dev 设计审核标准",
        file_path=os.path.join(ws, "criteria-design.md"),
        prompt=prompt,
        context_key="dev_criteria",
    )
    return {"phase": "dev_criteria_done", "judge_result": "review_dev_criteria"}


def review_dev_criteria(state: WorkflowState) -> dict:
    """Reviewer 审查 Dev 审核标准是否具体可执行。"""
    runtime = getattr(review_dev_criteria, "_runtime", None)
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Dev 审核标准\n{'='*60}")

    if not criteria_path or not os.path.exists(criteria_path):
        print(f"  ✗ Dev 审核标准文件不存在：{criteria_path}")
        return {"phase": "review_criteria_fail", "judge_result": "devwrite_criteria"}

    review = call_agent(runtime, "reviewer", conv_name("review-dev-criteria"),
        "请审查以下审核标准。\n\n"
        "逐条检查：\n"
        "1. 每条标准是否具体、可衡量(审核标准不能带有“恰当”，“合理”等主观判断)？\n"
        "2. 每条标准是否写明了审查方法？(agent可以使用tool如file_read等方法进行审查)\n"
        "3. 标准是否覆盖了所有应覆盖的维度？\n"
        f"审核标准文件在：{criteria_path}\n\n"
        "逐条给出评价，如果完全没有任何问题，且没有任何可以提高的建议，则最后一行输出 == PASS =="
        "如果有任何问题或有任何建议则输出 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。",
        stream=True)

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "P. 审查通过，所有标准具体可衡量。",
        "F. 审查不通过，标准需要修正。",
    ], tag="judge-dev-criteria")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("dev_criteria_feedback_path", "")
    else:
        feedback_path = letter_path(runtime, "reviewer-dev-criteria-feedback")
        write_letter(runtime, "reviewer", conv_name("review-dev-criteria-feedback"),
                     feedback_path, "Dev 审核标准审查反馈",
                     f"以下是你在上一轮审查中给出的评审意见，请整理成一封反馈信。\n\n"
                     f"## 你的审查意见\n{review}")
        runtime.context.set_ctx("dev_criteria_feedback_path", feedback_path)

    runtime.logger.log_event("criteria_reviewed",
        detail=f"Dev 审核标准审查{'通过' if passed else '不通过'}")
    return {
        "phase": "review_dev_criteria_done" if passed else "review_dev_criteria_fail",
        "judge_result": "dev_write_design" if passed else "devwrite_criteria",
    }


def dev_write_design(state: WorkflowState) -> dict:
    """Master 写信指令 → Dev 产出详细设计方案。"""
    runtime = getattr(dev_write_design, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = conv_name("dev-design")
        runtime.context.set_ctx("dev_conv", dev_conv)

    runtime.logger.log_event("phase_started", detail="Dev 出详细设计")
    print(f"\n  ── Dev 出详细设计 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("master conversation 不存在")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""
    criteria_ref = ""
    if criteria_path and os.path.exists(criteria_path):
        criteria_ref = f"\n审核标准文件（Dev 需对着这些标准写，Reviewer 将用来审查）：{criteria_path}"

    design_path = os.path.join(dev_dir, "design.md")
    designletter_path = letter_path(runtime, "master-design")
    write_letter(runtime, "master", master_conv, designletter_path,
                 "详细设计编写说明",
                 "请以 Master 的身份给 Dev 写信，要求 Dev 输出详细设计方案并写入指定文件。\n"
                 "需包含：系统架构、数据流设计、路由/API 定义、组件结构、关键实现逻辑。\n"
                 "需要告知 Dev，在它写详细设计之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁（PM），给了它哪些上下文（PRD、原型），"
                 "这些上下文该如何约束它进行详细设计的编写。\n"
                 "2. 它的下游是谁（QA），会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
                 "4. 确保具体、可操作，避免空泛描述。\n"
                 "5. 在这个阶段中，只要求产出详细设计文档，"
                 "代码实现需要等进一步指令后再进行。"
                 + criteria_ref)
    read_letter(runtime, "dev", dev_conv, designletter_path,
                f"按信中的要求编写详细设计方案，写入文件 {design_path}。")

    print(f"  ✓ {design_path}")
    runtime.context.set_phase_node(["Dev 出详细设计"], "done")
    runtime.logger.log_event("phase_completed", detail="Dev 详细设计完成")
    return {"phase": "dev_design_done", "judge_result": "pass"}


def dev_write_plan(state: WorkflowState) -> dict:
    """Master 写信指令 → Dev 产出分步实现计划，每步含可执行验收标准。"""
    runtime = getattr(dev_write_plan, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = conv_name("dev-plan")
        runtime.context.set_ctx("dev_conv", dev_conv)

    runtime.logger.log_event("phase_started", detail="Dev 出实现计划")
    print(f"\n  ── Dev 出实现计划 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("master conversation 不存在")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    design_path = os.path.join(dev_dir, "design.md")
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""

    plan_path = os.path.join(dev_dir, "plan.md")
    planletter_path = letter_path(runtime, "master-plan")
    write_letter(runtime, "master", master_conv, planletter_path,
                 "分步实现计划编写说明",
                 "请以 Master 的身份给 Dev 写信，要求 Dev 输出分步实现的计划并写入指定文件。\n"
                 "告知 Dev，它的详细设计方案在：\n"
                 f"{design_path}\n\n"
                 "## 计划模板格式\n"
                 "每个 Step 必须按以下模板编写：\n"
                 "```markdown\n"
                 "## Step N: <简短标题>\n"
                 "### 改动文件\n"
                 "- 列出需要新增或修改的文件路径\n"
                 "### 验收方法\n"
                 "编写测试代码验证此步骤，验收时运行：\n"
                 "```bash\n"
                 "<运行测试的命令>\n"
                 "```\n"
                 "### 前置条件\n"
                 "- 列出需要上一步已完成的前提（如果有）\n"
                 "```\n\n"
                 "## 要求\n"
                 "1. 每个 Step 的改动不超过 3-5 个文件\n"
                 "2. 每个 Step 必须编写测试代码来验证实现。"
                 "前端代码需编写组件级单元测试（vitest/jest），仅编译检查不算验收通过。"
                 "后端代码使用 pytest / mvn test 等单元测试框架。"
                 "验收方法模板中写明运行这些测试的命令，测试代码作为改动文件的一部分。"
                 "每一步的验收需要覆盖这一 Step 中的所有改动。"
                 "不允许主观描述（如'确认代码正确'、'检查逻辑'）\n"
                 "3. 步骤必须按依赖顺序排列\n"
                 "4. 覆盖设计文档中的所有功能点\n"
                 "5. 这个阶段只要求产出计划文档，"
                 "代码实现需要等进一步指令后再进行。\n"
                 f"Plan需要约束未来所有代码的产出至{dev_dir}\n"
                 f"审核标准文件参考：{criteria_path}")
    read_letter(runtime, "dev", dev_conv, planletter_path,
                f"按信中的要求编写分步实现计划，写入文件 {plan_path}。")

    print(f"  ✓ {plan_path}")
    runtime.context.set_phase_node(["Dev 出实现计划"], "done")
    runtime.logger.log_event("phase_completed", detail="Dev 实现计划完成")
    return {"phase": "dev_plan_done", "judge_result": "pass"}


def dev_review_plan(state: WorkflowState) -> dict:
    """Reviewer 审查 Dev 的实现计划。"""
    runtime = getattr(dev_review_plan, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Dev 实现计划\n{'='*60}")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    plan_path = os.path.join(dev_dir, "plan.md")
    design_path = os.path.join(dev_dir, "design.md")
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""

    if not os.path.exists(plan_path):
        print(f"  ✗ 计划文件不存在：{plan_path}")
        return {"phase": "review_plan_fail", "judge_result": "dev_write_plan"}

    prompt = (
        "你是一个项目审查员。请审查 Dev 的分步实现计划。\n\n"
        "## 审查标准\n"
        "逐条检查以下维度，每一条回复 ✓ 或 ✗：\n\n"
        "1. **步骤完整性** — 计划是否覆盖了设计文档中的所有功能点？\n"
        f"   设计文档在：{design_path}\n"
        "2. **验收可执行性** — 每个 Step 的验收方法是否为可运行的命令、\n"
        "   Playwright 脚本、或测试代码？不允许'确认代码正确'、'检查逻辑'这类主观描述。\n"
        "3. **步骤粒度** — 每个 Step 的改动是否不超过 3-5 个文件？\n"
        "4. **步骤顺序** — 步骤是否按依赖关系排列？\n"
        "5. **可验证性** — 每个 Step 完成后是否可独立验证？\n"
        "6. **验收覆盖度** — 每个 Step 的验收方法是否覆盖了该步骤的所有改动？\n"
        "   如果用了 Playwright/E2E 方式，是否写明了具体的验证步骤和预期结果？\n\n"
        "## Dev 的实现计划\n"
        f"计划文件在：{plan_path}\n\n"
        f"## 审核标准参考\n{criteria_path}\n\n"
        "## 输出格式\n"
        "逐条给出评价，最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。"
    )

    review = call_agent(runtime, "reviewer", conv_name("review-plan"),
                        prompt, stream=True)
    print(f"\n── Reviewer 审查结果 ──\n{review}\n")

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "P. 计划审查通过。",
        "F. 计划审查不通过，需要修改。",
    ], tag="judge-dev-plan")
    passed = judge_result.strip() == "P"

    runtime.logger.log_event("plan_reviewed",
        detail=f"Dev 计划审查{'通过' if passed else '不通过'}")

    if passed:
        plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
        total = count_steps(plan_path)
        runtime.context.set_ctx("dev_step_index", "0")
        runtime.context.set_ctx("dev_total_steps", str(total))
        runtime.context.set_ctx("dev_step_fail_count", "0")
        runtime.context.set_ctx("dev_step_has_failed", "false")

    return {
        "phase": "plan_review_done" if passed else "plan_review_fail",
        "judge_result": "dev_exec" if passed else "dev_write_plan",
    }


def dev_git_init(state: WorkflowState) -> dict:
    """Dev 在 Dev/ 目录下初始化 Git 仓库。"""
    runtime = getattr(dev_git_init, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv") or conv_name("dev-git-init")
    runtime.context.set_ctx("dev_conv", dev_conv)

    dev_dir = os.path.join(runtime.workspace, "Dev")
    print(f"\n  ── Dev 初始化 Git 仓库 ──")

    call_agent(runtime, "dev", dev_conv,
        f"请在 {dev_dir} 目录下初始化 Git 仓库：\n"
        "1. cd 到该目录\n"
        "2. git init\n"
        "3. git config user.name 'Dev Agent'\n"
        "4. git config user.email 'dev@agent.local'\n"
        "5. git commit --allow-empty -m 'Initial empty commit'\n\n"
        "以上所有操作都完成后回复确认。")

    runtime.logger.log_event("phase_completed", detail="Dev Git 仓库初始化完成")
    return {"phase": "git_initted", "judge_result": "pass"}


def dev_exec_step(state: WorkflowState) -> dict:
    """依次执行 Dev plan 中的每一步。Master 写信 → Dev 实现。"""
    runtime = getattr(dev_exec_step, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    dev_conv = runtime.context.get_ctx("dev_conv") or conv_name("dev-exec")
    runtime.context.set_ctx("dev_conv", dev_conv)

    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
    design_path = os.path.join(runtime.workspace, "Dev", "design.md")

    step_content = get_step_from_plan(plan_path, step_idx)
    if not step_content:
        print(f"\n  ✗ 未找到 Step {step_idx + 1}，计划文件：{plan_path}")
        return {"phase": "dev_exec_error", "judge_result": "dev_exec_step"}

    print(f"\n{'='*60}\n  ==> Dev 执行 Step {step_idx + 1}\n{'='*60}")
    runtime.logger.log_event("phase_started", detail=f"Dev 执行 Step {step_idx + 1}")

    prev_review = runtime.context.get_ctx("dev_step_review_feedback")
    feedback = ""
    if prev_review:
        feedback = f"\n\n## 上一轮审查反馈（需修复）\n{prev_review}"

    escalation_decision = runtime.context.get_ctx("dev_escalation_decision")
    if escalation_decision:
        feedback += f"\n\n## 人工决策\n{escalation_decision}"
        runtime.context.set_ctx("dev_escalation_decision", "")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    lpath = letter_path(runtime, f"master-step-{step_idx + 1}")
    write_letter(runtime, "master", master_conv, lpath,
                 f"Step {step_idx + 1} 实现说明",
                 f"请以 Master 的身份给 Dev 写信，要求 Dev 实现以下步骤。\n\n"
                 f"## 待实现的步骤\n{step_content}\n\n"
                 f"## 上下文\n"
                 f"这是第 {step_idx + 1} 步。\n"
                 f"详细设计方案：{design_path}\n"
                 f"所有代码文件必须放在 {dev_dir} 目录下。\n"
                 f"所有之前的步骤已完成，请在此基础上继续开发。\n"
                 f"完成实现后自行验证验收方法。"
                 + feedback)
    read_letter(runtime, "dev", dev_conv, lpath,
                "按信中要求实现当前步骤。所有代码产出必须放在 Dev/ 目录下，"
                "不要将文件生成到项目根目录或其他地方。"
                "完成实现后，运行该步骤的验收方法确认通过。\n\n"
                "## Git 操作限制\n"
                "没有允许不要做任何 git 操作（包括 git add、git commit、git push 等），"
                "代码只需要写在文件中即可。")

    return {"phase": "dev_exec", "judge_result": "dev_review_step"}


def dev_review_step(state: WorkflowState) -> dict:
    """Reviewer 按验收标准审查当前 Step 的实现。"""
    runtime = getattr(dev_review_step, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Step\n{'='*60}")

    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")
    plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
    design_path = os.path.join(runtime.workspace, "Dev", "design.md")

    step_content = get_step_from_plan(plan_path, step_idx)
    if not step_content:
        return {"phase": "review_step_error", "judge_result": "dev_exec_step"}

    review = call_agent(runtime, "reviewer", conv_name(f"review-step-{step_idx + 1}"),
        "请审查 Dev 的最新实现。\n\n"
        "## 验收标准\n"
        f"来自计划的当前步骤：\n{step_content}\n\n"
        f"## 参考设计文档\n{design_path}\n\n"
        "逐条检查：\n"
        "1. 实现是否满足该步骤的验收方法？\n"
        "2. 实现是否与详细设计方案一致？\n"
        "3. 代码质量和错误处理是否合理？\n\n"
        "最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题和原因。",
        stream=True)

    print(f"\n── Reviewer 审查结果 ──\n{review}\n")

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "P. 实现满足所有验收标准。",
        "F. 实现存在问题，需要修正。",
    ], tag=f"judge-step-{step_idx + 1}")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("dev_step_fail_count", "0")
        runtime.context.set_ctx("dev_step_has_failed", "false")

        new_idx = step_idx + 1
        runtime.context.set_ctx("dev_step_index", str(new_idx))
        runtime.context.set_ctx("dev_step_review_feedback", "")
        runtime.logger.log_event("step_completed",
            detail=f"Step {step_idx + 1} 通过（{new_idx}/{total}）")

        if new_idx >= total:
            print(f"\n  ✓ 所有步骤完成！")
            runtime.logger.log_event("phase_completed", detail="Dev 执行全部完成")
        else:
            print(f"\n  ✓ Step {step_idx + 1} 通过，进入 Step {new_idx + 1}")
        return {"phase": "dev_exec_done" if new_idx >= total else "step_pass",
                "judge_result": "dev_commit"}
    else:
        runtime.context.set_ctx("dev_step_review_feedback", review)

        has_failed_before = runtime.context.get_ctx("dev_step_has_failed") == "true"
        if not has_failed_before:
            runtime.context.set_ctx("dev_step_has_failed", "true")
            count = 0
        else:
            count = int(runtime.context.get_ctx("dev_step_fail_count") or "0") + 1
            runtime.context.set_ctx("dev_step_fail_count", str(count))

        rollback_threshold = runtime.config.get("fail_rollback_threshold")
        escalation_threshold = runtime.config.get("fail_escalation_threshold")
        if rollback_threshold is None:
            raise RuntimeError("config 中缺少 fail_rollback_threshold")
        if escalation_threshold is None:
            raise RuntimeError("config 中缺少 fail_escalation_threshold")

        runtime.logger.log_event("step_failed",
            detail=f"Step {step_idx + 1} 未通过（fail_count={count}）")

        if count >= escalation_threshold:
            print(f"\n  ⚠ Step {step_idx + 1} 失败 {count} 次，升级人工决策")
            return {"phase": "step_escalate", "judge_result": "dev_escalate"}
        elif count >= rollback_threshold:
            print(f"\n  ⚠ Step {step_idx + 1} 失败 {count} 次，触发回滚")
            return {"phase": "step_rollback", "judge_result": "dev_rollback"}
        else:
            print(f"\n  ✗ Step {step_idx + 1} 未通过（fail_count={count}），重新执行")
            return {"phase": "step_fail", "judge_result": "step_retry"}


def dev_commit(state: WorkflowState) -> dict:
    """Dev 审查通过后提交代码到 Git。"""
    runtime = getattr(dev_commit, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")

    print(f"\n  ── Dev 提交 Step {step_idx} 的代码 ──")

    call_agent(runtime, "dev", dev_conv,
        f"你的 Step {step_idx} 已通过审查，请将改动提交到 Git：\n"
        "1. cd 到 Dev/ 目录\n"
        "2. git add 相关文件——不要将测试中间产物、缓存文件等无关内容 add 进去\n"
        "3. git commit -m \"Step {step_idx}: <提交说明>\"\n\n"
        "完成后回复确认。")

    runtime.logger.log_event("phase_completed", detail=f"Dev Step {step_idx} 代码已提交")

    if step_idx < total:
        summary_path = os.path.join(runtime.workspace, "Dev", "compact-summary.md")
        design_path = os.path.join(runtime.workspace, "Dev", "design.md")
        plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")

        call_agent(runtime, "dev", dev_conv,
            f"请将你的工作进度写入 {summary_path}。格式如下：\n\n"
            "Summary:\n"
            "1. Primary Request and Intent:\n"
            "   - 刚完成的 Step 实现了什么\n\n"
            "2. Key Technical Concepts:\n"
            "   - 涉及的技术要点、配置变更\n\n"
            "3. Files and Code Sections:\n"
            "   - 具体到文件路径，新增/修改了什么\n\n"
            "4. Errors and fixes:\n"
            "   - 遇到的问题和解决方法\n\n"
            "5. Dependencies / Assumptions:\n"
            "   - 对后续步骤的依赖和假设\n\n"
            "6. Current Status:\n"
            f"   - 已完成: Step {step_idx}/{total}\n"
            f"   - 下一步: Step {step_idx + 1}")

        if not ensure_write_file(runtime, "dev", dev_conv, summary_path):
            call_agent(runtime, "dev", dev_conv,
                       f"你刚才没有将工作进度写入文件 {summary_path}。"
                       "请使用 write_file 工具写入，不要只在对话中回复。")

        runtime.conversations.close("dev", dev_conv)

        dev_principles = runtime.context.get_bg("dev_principles")
        new_conv = conv_name("dev-exec")
        runtime.context.set_ctx("dev_conv", new_conv)

        runtime.conversations.begin("dev", new_conv,
            f"{dev_principles}{FLUSH_CONTINUATION_NOTE}"
            f"## 已完成的工作\n"
            f"{{{summary_path}}}\n\n"
            f"## 项目设计文档\n"
            f"{{{design_path}}}\n\n"
            f"## 执行计划\n"
            f"{{{plan_path}}}")

        save_checkpoint(runtime, "dev_exec_step",
                        f"Dev 实现 Step {step_idx + 1}",
                        step_idx=step_idx, summary_path=summary_path)

    if step_idx >= total:
        return {"phase": "dev_commit_done", "judge_result": "done"}
    else:
        return {"phase": "dev_commit_done", "judge_result": "dev_exec_step"}


def dev_rollback(state: WorkflowState) -> dict:
    """Dev 失败次数过多，回滚到上一个 commit 重新开始。"""
    runtime = getattr(dev_rollback, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")

    print(f"\n{'='*60}\n  ==> Dev Step {step_idx + 1} 回滚中...\n{'='*60}")

    call_agent(runtime, "dev", dev_conv,
        f"当前 Step {step_idx + 1} 已失败多次，执行以下操作：\n"
        "1. 注意：你的改动将被回滚至上一个提交，重新开始实现这个 Step\n"
        "2. cd Dev/ && git reset --hard HEAD\n"
        "3. 确认工作区已清理干净\n"
        "4. 重新实现 Step\n\n"
        "完成后回复确认。")

    runtime.logger.log_event("phase_started", detail=f"Dev Step {step_idx + 1} 回滚重来")
    return {"phase": "step_rollback", "judge_result": "dev_exec_step"}


def dev_escalate(state: WorkflowState) -> dict:
    """Dev 失败次数过多，升级到用户对话。Dev 简述 → 用户对话 → Dev 总结。"""
    runtime = getattr(dev_escalate, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")
    end_word = runtime.config.get("input_end_word") or None

    print(f"\n{'='*50}")
    print(f"【Dev Step {step_idx + 1}/{total} 多次失败，进入人工对话】")
    print(f"{'='*50}")

    dev_summary = call_agent(runtime, "dev", dev_conv,
        "请用简短的篇幅向用户说明以下信息：\n"
        f"1. 整体计划概述\n"
        f"2. 当前 Step {step_idx + 1} 的内容和进展\n"
        "3. 最近一次审查反馈中指出的问题\n"
        "4. 你认为可能的原因是什么\n\n"
        "用户将与你对话帮助你解决问题。保持简洁。")
    print(f"\n── Dev 的简述 ──\n{dev_summary}\n")

    print("进入对话模式。输入你的意见／修改要求，Dev 将回应。直接 EOF 结束对话。\n")

    round_num = 0
    while True:
        round_num += 1
        hint = "输入你的意见（直接 EOF 结束）："
        cp = runtime.checkpoint.wait(
            f"与 Dev 对话（Step {step_idx + 1}）",
            hint,
            prompt="", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            print("对话结束。\n")
            break

        call_agent(runtime, "dev", dev_conv,
            f"用户说：{user_input}\n\n"
            "请回应用户的意见。如果需要修改计划或其他文档，可以直接修改。\n"
            "保持对话简洁、有建设性。")

    decision = call_agent(runtime, "dev", dev_conv,
        "对话已结束。请总结用户最终的决策：\n"
        "1. 计划需要如何调整？是否已修改 Dev/plan.md？\n"
        "2. 下一步应该怎么做？\n"
        "3. 是否需要修改其他文档？\n\n"
        "输出决策总结。")

    print(f"\n── Dev 决策总结 ──\n{decision}\n")
    runtime.context.set_ctx("dev_escalation_decision", decision)
    runtime.logger.log_event("phase_escalated",
        detail=f"Dev Step {step_idx + 1} 升级人工对话")
    return {"phase": "step_escalated", "judge_result": "dev_exec_step"}
