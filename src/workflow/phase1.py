"""Phase 1: PM 出方案阶段。"""
import os

from .utils import (WorkflowState, conv_name, call_agent, letter_path,
                    ensure_write_file, write_letter, read_letter,
                    read_and_write_letter, judge_reply, clarify_loop,
                    write_criteria)
from langgraph.graph import END


def pm_handoff(state: WorkflowState) -> dict:
    """Phase 1a: Master 写 handoff 信给 PM。"""
    runtime = getattr(pm_handoff, "_runtime", None)

    print(f"\n{'='*60}\n  ==> Phase 1a: Master 写信给 PM\n{'='*60}")

    project_context_path = runtime.context.get_bg("project_context_path")
    if not project_context_path or not os.path.exists(project_context_path):
        raise RuntimeError(f"project_context.md 不存在：{project_context_path}")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    lpath = letter_path(runtime, "master-to-pm")
    write_letter(runtime, "master", master_conv, lpath,
                 "Master 给 PM 的信",
                 f"介绍项目上下文。信件需包含：\n"
                 "1. 开宗明义：这是 Master 给 PM 的信\n"
                 "2. 项目概况和核心需求（简要描述即可）\n"
                 f"3. 告知 PM 详细内容在项目顶层决策文件中，路径：{project_context_path}，让 PM 自行阅读\n"
                 "4. 要求 PM：先汇报你对项目的理解和疑问，得到 Master 明确许可后才能动手产出\n"
                 "5. 强调：在确认之前，不得开始写 PRD 或原型\n\n"
                 "信件要有 Master 的口吻，是上级对下级的沟通与任务委派。")

    runtime.context.set_ctx("pmletter_path", lpath)
    print(f"\n  ── Master 给 PM 的信件已就绪 ──")
    return {"phase": "pm_handoff_done"}


def pm_align(state: WorkflowState) -> dict:
    """Phase 1b: PM 读信，汇报理解 + 列出疑问。"""
    runtime = getattr(pm_align, "_runtime", None)

    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = conv_name("pm-align")
        runtime.context.set_ctx("pm_conv", pm_conv)

    round_num = int(runtime.context.get_ctx("pm_align_round") or 0)

    runtime.logger.log_event("phase_started", detail="PM 对齐理解")
    print(f"\n  ── PM 对齐理解（第 {round_num + 1} 轮）──")

    pm_reply_path = letter_path(runtime, "pm-reply")
    if round_num > 0:
        master_conv = runtime.context.get_ctx("master_conv")
        masterletter_path = letter_path(runtime, "master-to-pm-reply")
        write_letter(runtime, "master", master_conv, masterletter_path,
                     "Master 给 PM 的答复",
                     "你在刚才的分析中已核对了 PM 的理解并回答了疑问。"
                     "请将你的结论写成正式信件给 PM。\n"
                     "逐一核对 PM 的理解是否正确，回答所有疑问。"
                     "如果 PM 的理解完全正确且无疑问，也请告知 PM。"
                     "要求 PM 再次汇报它对项目的理解和疑问。\n"
                     "强调：不得许可 PM 写 PRD 或原型\n\n")
        read_and_write_letter(runtime, "pm", pm_conv,
                              masterletter_path, pm_reply_path,
                              "From PM, Re: 对 Master 的答复",
                              "逐一回应 Master 的答复，确认清楚所有疑问。"
                              "如有新的疑问也一并提出。如果已没有疑问，也需要明确说明没有疑问，并重新详细讲述自己对项目的了解。",
                              "在 Master 明确许可之前，不得开始写 PRD 或原型。")
    else:
        lpath = runtime.context.get_ctx("pmletter_path")
        if not lpath:
            raise RuntimeError("没有 handoff 信件路径")
        read_and_write_letter(runtime, "pm", pm_conv,
                              lpath, pm_reply_path,
                              "From PM, Re: Master 的委托",
                              "写一封回信汇报你对项目的理解和疑问。"
                              "列出不清楚或需要 Master 确认的地方。",
                              "在 Master 明确许可之前，不得开始写 PRD 或原型。")

    runtime.context.set_ctx("pm_align_round", str(round_num + 1))
    runtime.context.set_ctx("pm_reply_path", pm_reply_path)
    if os.path.exists(pm_reply_path):
        with open(pm_reply_path, "r", encoding="utf-8") as f:
            runtime.context.set_ctx("pm_reply_text", f.read())
    return {"phase": "pm_align_done"}


def master_reply_pm(state: WorkflowState) -> dict:
    """Master 读取 PM 回信，回答疑问，复用 clarify conversation。"""
    runtime = getattr(master_reply_pm, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    task = ("逐一检查以下内容：\n"
            "1. PM 的理解是否正确？如有误，逐一指出\n"
            "2. PM 的疑问中，你能回答的全部回答。"
            "如果你修改了项目顶层决策文件，你需要答复 PM "
            "让它从顶层决策文件中获取更新，不能假设 PM 已经得知了你对文件的修改\n"
            "3. 如果遇到你无从判定的问题（涉及顶层决策、技术选型、使用场景等），"
            "不要猜测，明确写出需要向用户确认的具体问题\n\n"
            "你的回复中需明确区分两部分：\n"
            "- 你对 PM 的答复/纠正\n"
            "- 需要向用户确认的问题（如无则说'无需向用户提问'）\n"
            "4. 在回复末尾，你必须用以下格式之一明确声明结论：\n"
            "   - 「结论：需要转发给PM」— 你对 PM 有任何答复、纠正或补充说明需要让 PM 看到\n"
            "   - 「结论：无需转发，PM已完全正确」— PM 理解完全无误，且你没有任"
            "何需要告诉 PM 的内容\n"
            "   - 「结论：需要向用户确认」— 你有无法判定的问题需要问用户\n\n"
            "注意：如果你写了任何对 PM 的答复或纠正，就一定属于[需要转发给PM]。"
            "只有当你一个字都没需要跟 PM 说时，才属于[无需转发]。\n"
            "后续用户会主动指示让你编写对 PM 产出的审核标准以及对 PM 的prompt信件。")

    pm_reply_path = runtime.context.get_ctx("pm_reply_path")
    if pm_reply_path and os.path.exists(pm_reply_path):
        reply = read_letter(runtime, "master", master_conv, pm_reply_path, task)
    else:
        pm_reply = runtime.context.get_ctx("pm_reply_text")
        if not pm_reply:
            raise RuntimeError("PM 回信缺失，既无文件也无缓存")
        reply = call_agent(runtime, "master", master_conv,
                          f"请阅读以下 PM 的回信，然后{task}\n\n"
                          f"## PM 回信内容\n{pm_reply}")

    runtime.context.set_ctx("master_reply", reply)
    return {"phase": "master_reply_done"}


def judge_master_reply(state: WorkflowState) -> dict:
    """判读 Master 的回复，路由到下一步。"""
    runtime = getattr(judge_master_reply, "_runtime", None)
    master_reply = runtime.context.get_ctx("master_reply")

    print("  ── judge: Master 回复 ──")
    result = judge_reply(runtime, "Master", master_reply, [
        "A. Master 明确声明「无需转发，PM已完全正确」，且无任何需要再向PM说明或向用户提问的内容 → 进入下一阶段",
        "B. Master 明确声明「需要转发给PM」，或有任何对 PM 的答复或纠正需要转发 → 回 pm_align",
        "C. Master 明确声明「需要向用户确认」，或有无法判定的问题 → 进入 clarify_inject",
    ], "judge-master-reply")
    return {"judge_result": result.strip()}


def clarify_inject(state: WorkflowState) -> dict:
    """向用户提问 Master 无法判定的问题，更新 project_context.md。"""
    runtime = getattr(clarify_inject, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    master_reply = runtime.context.get_ctx("master_reply")

    print(f"\n  ── Master 需要向用户确认 ──\n{master_reply}")

    def _close(reason: str):
        project_context_path = runtime.context.get_bg("project_context_path")
        call_agent(runtime, "master", master_conv,
                   f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{project_context_path}")
        runtime.logger.log_event("clarification_done", detail=reason)

    clarify_loop(runtime, master_conv, "== 向用户确认 ==", "请回答 Master 的疑问", _close)
    return {"phase": "clarify_done"}


def pmwrite_criteria(state: WorkflowState) -> dict:
    """Master 制定 PM 产出的审核标准（PRD + prototype）。循环直至自检通过。"""
    runtime = getattr(pmwrite_criteria, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")

    runtime.logger.log_event("phase_started", detail="PM 审核标准制定")

    feedback_path = runtime.context.get_ctx("pm_criteria_feedback_path") or ""
    if feedback_path and os.path.exists(feedback_path):
        read_letter(runtime, "master", master_conv, feedback_path,
                    "根据反馈意见重新制定审核标准")
        runtime.context.set_ctx("pm_criteria_feedback_path", "")

    prompt = (
        "你即将为 PM 产出的 PRD 和 prototype 制定审核标准。\n\n"
        "## 上游约束\n"
        "项目决策是你要考虑的上游上下文，标准必须与之对齐：\n"
        f"项目决策记录的文件地址为：{project_context_path or '（无项目决策记录）'}\n\n"
        "## 标准覆盖维度\n"
        "1. 需求完整性 — PRD 是否覆盖了所有已确认的功能？\n"
        "2. MVP 边界 — 范围是否控制在 MVP 内？有无超额？\n"
        "3. 逻辑自洽性 — 功能描述是否完整无矛盾？数据流是否有断点？\n"
        "4. 一致性 — 功能定义、用户角色、技术假设是否与项目决策文件冲突？\n"
        "5. 原型质量 — prototype 是否体现了核心交互和页面结构？\n"
        "   - 页面要素完整（输入框、按钮、链接等）\n"
        "   - 交互行为正确（表单校验触发、错误提示展示、页面切换、登出流程等）\n"
        "   - 边界情况体现（空输入拦截、重复注册检测、非法字符过滤等）\n"
        "   - 数据流一致性（注册后可登录、大小写区分、密码错误提示等信息流是否自洽）\n"
        "   - 视觉风格统一\n"
        "## 下游需求\n"
        "- PM 将按这些标准撰写 PRD 和 prototype\n"
        "- Reviewer 将按这些标准审查 PM 产出\n\n"
        "## 要求\n"
        "文件中只需要写测什么以及怎么样算是测试完成，不需要写审查方法（reviewer 自己知道怎么测）。\n"
        "（对于原型的审核，优先考虑Playwright可以验收的标准，不需要你编写playwright标准，但是需要体现playwright脚本可审核的标准）。\n"
        "确保标准不是模板化的文字堆砌，而是真正能为审查提供 actionable 的判断依据。\n"
        "请具体、可操作，避免空泛描述。"
    )

    write_criteria(
        runtime, master_conv,
        title="Master 制定 PM 审核标准",
        file_path=os.path.join(runtime.workspace, "criteria-pm.md"),
        prompt=prompt,
        context_key="pm_criteria",
    )
    return {"phase": "criteria_done", "judge_result": "review_pm_criteria"}


def review_pm_criteria(state: WorkflowState) -> dict:
    """Reviewer 审查 PM 审核标准是否具体可执行。"""
    runtime = getattr(review_pm_criteria, "_runtime", None)
    criteria_path = runtime.context.get_ctx("pm_criteria_path") or ""
    print(f"\n{'='*60}\n  ==> Reviewer 审查 PM 审核标准\n{'='*60}")

    if not criteria_path or not os.path.exists(criteria_path):
        print(f"  ✗ PM 审核标准文件不存在：{criteria_path}")
        return {"phase": "review_criteria_fail", "judge_result": "pmwrite_criteria"}

    review = call_agent(runtime, "reviewer", conv_name("review-pm-criteria"),
        "请审查以下审核标准。\n\n"
        "逐条检查：\n"
        "1. 每条标准是否具体、可衡量(审核标准不能带有\"恰当\"，\"合理\"等主观判断)？\n"
        "2. 每条标准是否写明了审查方法？(agent可以使用tool如file_read等方法进行审查)\n"
        "3. 标准是否覆盖了所有应覆盖的维度？\n"
        f"审核标准文件在：{criteria_path}\n\n"
        "逐条给出评价，最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。",
        stream=True)

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "PASS. 审查通过，满足所有条件。",
        "FAIL. 审查不通过，存在问题需要修正。",
    ], tag="judge-pm-criteria")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("pm_criteria_feedback_path", "")
    else:
        feedback_path = letter_path(runtime, "reviewer-pm-criteria-feedback")
        write_letter(runtime, "reviewer", conv_name("review-pm-criteria-feedback"),
                     feedback_path, "PM 审核标准审查反馈",
                     f"以下是你在上一轮审查中给出的评审意见，请整理成一封反馈信。\n\n"
                     f"## 你的审查意见\n{review}")
        runtime.context.set_ctx("pm_criteria_feedback_path", feedback_path)

    runtime.logger.log_event("criteria_reviewed",
        detail=f"PM 审核标准审查{'通过' if passed else '不通过'}")
    return {
        "phase": "review_pm_criteria_done" if passed else "review_pm_criteria_fail",
        "judge_result": "pm_write_doc" if passed else "pmwrite_criteria",
    }


def pm_write_doc(state: WorkflowState) -> dict:
    """Phase 1e: Master 写信指令 → PM 产出 PRD.md + prototype.html。"""
    runtime = getattr(pm_write_doc, "_runtime", None)
    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = conv_name("pm-doc")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="PM 出方案")
    print(f"\n  ── PM 出方案 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    pm_dir = os.path.join(runtime.workspace, "PM")
    os.makedirs(pm_dir, exist_ok=True)

    prev_review = runtime.context.get_ctx("review_result") or ""
    human_feedback = runtime.context.get_ctx("human_feedback") or ""
    feedback_ref = ""
    if prev_review:
        feedback_ref += f"\n\n## 上一轮审查发现的问题\n{prev_review}"
    if human_feedback:
        feedback_ref += f"\n\n## 人工反馈（需优先处理）\n{human_feedback}"

    prd_path = os.path.join(pm_dir, "PRD.md")
    criteria_path = runtime.context.get_ctx("pm_criteria_path") or ""
    criteria_ref = ""
    if criteria_path and os.path.exists(criteria_path):
        criteria_ref = f"\n审核标准文件（PM 需对着这些标准写，Reviewer 将用来审查）：{criteria_path}"
    prdletter_path = letter_path(runtime, "master-prd")
    write_letter(runtime, "master", master_conv, prdletter_path,
                 "PRD 编写说明",
                 "请以 Master 的身份给 PM 写信，要求 PM 输出 PRD.md 并写入指定文件。\n"
                 "需包含：项目概述、功能需求、MVP 范围、页面结构、验收标准。\n"
                 "需要告知 PM ，在它写文档之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁，给了它哪些上下文，这些上下文该如何约束它进行文档的编写。\n"
                 "2. 它的下游是谁，会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
                 "4. 确保具体、可操作，避免空泛描述\n"
                 "5. 在这个阶段中，只要求它产出PRD.md，原型需要等你进一步下达指令后再进行产出。\n"
                 "6. 数据流描述必须覆盖每个角色的完整链路。例如不能只写「前端解析」，"
                 "而要写「前端解析 JWT payload 中的哪个字段、做什么用」。\n"
                 "7. 异常状态的 UI 描述必须和你将要产出的 prototype 的实际设计保持一致。"
                 + criteria_ref + feedback_ref)
    read_letter(runtime, "pm", pm_conv, prdletter_path,
                f"按信中的要求编写 PRD.md，写入文件 {prd_path}。")

    proto_path = os.path.join(pm_dir, "prototype.html")
    protoletter_path = letter_path(runtime, "master-prototype")
    pm_agent_dir = os.path.join(runtime.workspace, "pm")
    pm_script_dir = os.path.join(pm_agent_dir, "tests")
    write_letter(runtime, "master", master_conv, protoletter_path,
                 "原型编写说明",
                 "请以 Master 的身份给 PM 写信，要求 PM 基于 PRD 产出 prototype.html 并写入指定文件。\n"
                 "需包含：核心交互、页面布局、导航流程。\n"
                 "单文件自包含（CSS/JS 内嵌），可双击在浏览器中直接打开。\n"
                 "需要告知 PM，在它写原型之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁，给了它哪些上下文（PRD），这些上下文该如何约束它进行原型的编写。\n"
                 "2. 它的下游是谁，会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的原型。\n"
                 "4. 确保具体、可操作，避免空泛占位符。")
    read_letter(runtime, "pm", pm_conv, protoletter_path,
                f"按信中要求编写 prototype.html，写入文件 {proto_path}。\n\n"
                "编写完成后，对照 PRD 自检：所有 PRD 中定义的 UI 状态（包括异常状态）"
                "是否都有对应的页面展示。\n\n"
                "编写完成后如果需要进行自测，使用 Playwright 脚本测试，不要使用 Playwright MCP 交互式测试。\n"
                f"Playwright 环境搭建在 {pm_agent_dir}，脚本保存到 {pm_script_dir}。\n"
                f"  a. 首次运行：cd \"{pm_agent_dir}\" && npm init -y && cd \"{pm_agent_dir}\" && npm install playwright\n"
                "  b. 检查 package.json 是否已存在，如已存在则跳过 npm init\n"
                "  c. 脚本命名格式：pm-test.spec.js\n"
                "  d. 运行脚本验证 prototype 行为是否符合预期\n"
                "  e. 系统已预装兼容的 Chrome 无头浏览器，无需 npx playwright install\n"
                "  f. 测试失败时，先诊断是测试脚本的问题还是原型本身的问题：\n"
                "     - 页面交互与预期不符（如点按纽触发错误行为） → 检查原型 HTML/CSS/JS 逻辑\n"
                "     - 测试脚本选择器或交互方式不当 → 修正测试脚本\n"
                "     - 明确说明本轮修复的是什么问题\n"
                "  g. 每次只修复一个根因，不要同时改脚本又改原型\n"
                "  h. 同一问题连续调试 3 轮仍未通过，使用 Playwright MCP 工具确认问题，不要继续改脚本")

    print(f"  ✓ {prd_path}")
    print(f"  ✓ {proto_path}")

    runtime.context.set_phase_node(["PM 出方案"], "done")
    runtime.logger.log_event("phase_completed", detail="PM 方案完成")
    return {"phase": "done", "judge_result": "pass"}


def review_pm_output(state: WorkflowState) -> dict:
    """Reviewer 对照审核标准和项目决策，审查 PM 产出。"""
    runtime = getattr(review_pm_output, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 PM 产出\n{'='*60}")

    criteria_path = os.path.join(runtime.workspace, "criteria-pm.md")
    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    project_context_path = runtime.context.get_bg("project_context_path") or ""

    human_feedback = runtime.context.get_ctx("human_feedback") or "(无人工反馈)"
    reviewer_dir = os.path.join(runtime.workspace, "reviewer")
    script_dir = os.path.join(reviewer_dir, "pm")

    prompt = "你是一个项目审查员。请根据以下材料审查 PM 的产出。\n"
    prompt += f"## 审核标准在：{criteria_path}\n"
    prompt += f"## 项目顶层决策在：{project_context_path}\n"
    if human_feedback:
        prompt += f"## 人工反馈（需优先处理）\n{human_feedback}\n\n"
    prompt += f"PM的产出：\n PRD 在：{prd_path}\n\n"
    prompt += f"Prototype 在：{proto_path}\n\n"

    prompt += (
        "## 审查步骤\n"
        "1. 先阅读 PRD，对照审核标准中的需求完整性、MVP 边界、逻辑自洽性等维度检查，输出结论\n"
        f"2. 针对 prototype，在以下目录编写 Playwright 脚本并执行。所有脚本保存到：{script_dir}\n"
        "   a. 首次执行 Playwright 前，先初始化运行环境：\n"
        f"      cd \"{reviewer_dir}\" && npm init -y\n"
        f"      cd \"{reviewer_dir}\" && npm install playwright\n"
        "   b. 检查 package.json 是否已存在，如已存在则跳过 npm init\n"
        "   c. 脚本必须逐条覆盖审核标准中所有交互/UI 相关的条目，包括但不限于：\n"
        "      - 页面结构：登录页、注册页、主页面要素是否完整\n"
        "      - 表单校验：空输入、非法字符、密码长度等\n"
        "      - 交互流程：注册 → 自动登录 → 登出 → 重新登录\n"
        "      - 边界情况：重复注册、密码错误、未登录访问保护页面\n"
        "      - 数据一致性：注册后可用新账号登录、大小写用户名区分\n"
        "   d. 命名格式：pm-prototype.spec.js\n"
        "   e. 运行脚本验证 prototype 行为是否符合预期\n"
        "   f. 系统已经npx playwright install chrome预安装过兼容的chrome无头浏览器\n"
        "   g. 测试失败时，先诊断是测试脚本的问题还是原型本身的问题：\n"
        "      - 页面交互与预期不符 → 检查原型 HTML/CSS/JS 逻辑\n"
        "      - 脚本选择器或交互方式不当 → 修正测试脚本\n"
        "      - 明确说明本轮修复的是什么问题\n"
        "   h. 每次只修复一个根因，不要同时改脚本又改原型\n"
        "   i. 同一问题连续调试 3 轮仍未通过，使用 Playwright MCP 工具确认问题\n"
        "3. 综合 PRD 审查结论和 Playwright 脚本执行结果，逐条输出审查结论。\n"
        "明确列出每个不通过项及其原因。\n"
        "如果全部通过，最后一行回复 == PASS ==\n"
        "如果有不通过项，最后一行回复 == FAIL ==")

    conv = conv_name("reviewer")
    reply = call_agent(runtime, "reviewer", conv, prompt)

    judge_result = judge_reply(runtime, "Reviewer", reply, [
        "P. 审查通过，满足所有条件。",
        "F. 审查不通过，存在问题需要修正。",
    ], tag="judge-pm-output")
    passed = judge_result.strip() == "P"

    runtime.context.set_ctx("review_result", reply)
    runtime.logger.log_event("review_completed", detail=f"审查{'通过' if passed else '不通过'}")
    print(f"  {'✓ Reviewer 审查通过' if passed else '✗ Reviewer 审查不通过'}")
    return {"phase": "review_done", "judge_result": "human_review" if passed else "pm_write_doc"}


def human_review(state: WorkflowState) -> dict:
    """人工审核 PM 产出。展出文件路径，让人确认或提意见。"""
    runtime = getattr(human_review, "_runtime", None)

    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    criteria_path = os.path.join(runtime.workspace, "criteria-pm.md")

    print(f"\n{'='*60}\n  ==> 人工审核 PM 产出\n{'='*60}")
    print(f"  PM 产出位置：")
    print(f"    PRD:       {prd_path}")
    print(f"    Prototype: {proto_path}")
    print(f"    审核标准:   {criteria_path}")
    print()

    end_word = runtime.config.get("input_end_word") or None
    cp = runtime.checkpoint.wait(
        "人工审核 PM 产出",
        f"请查看以上文件，确认 PM 产出符合要求。\n"
        f"直接 EOF 通过审核；如有问题请说明：",
        prompt="输入内容后按 Enter：", end_word=end_word,
    )
    feedback = cp.message.strip()

    if not feedback:
        print("  ✓ 人工审核通过")
        runtime.logger.log_event("human_review_passed")
        return {"phase": "done", "judge_result": END}

    round_num = runtime.context.get_ctx("human_feedback_round") or 0
    round_num += 1
    runtime.context.set_ctx("human_feedback_round", round_num)
    entry = f"第 {round_num} 次人工反馈:\n{feedback}"
    prev = runtime.context.get_ctx("human_feedback") or ""
    runtime.context.set_ctx("human_feedback",
                            prev + "\n\n---\n\n" + entry if prev else entry)
    runtime.logger.log_event("human_review_rejected", detail=feedback)
    print(f"  ⚠ 人工审核不通过，反馈已记录")
    return {"phase": "human_review_rejected", "judge_result": "pm_write_doc"}
