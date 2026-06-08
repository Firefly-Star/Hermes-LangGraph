"""Phase 3: QA 对齐阶段。"""
import os, time

from .utils import (WorkflowState, conv_name, call_agent, letter_path,
                    ensure_write_file, write_letter,
                    read_and_write_letter, judge_reply, clarify_loop,
                    register_nodes)
from .checkpoint import clear_checkpoint
from .prompt import PLAYWRIGHT_TEST_TIPS
from .subgraphs import (HandoffConfig, HandoffSubgraph,
                         CriteriaDefinitionConfig, CriteriaDefinitionSubgraph,
                         ArtifactReviewConfig, ArtifactReviewSubgraph)


QA_HANDOFF_LETTER = (
    "介绍项目上下文。信件需包含：\n"
    "1. 开宗明义：这是 Master 给 QA 的信\n"
    "2. 项目概况和核心需求（简要描述）\n"
    "3. 项目决策记录：{project_context}\n"
    "4. PRD：{workspace}/PM/PRD.md\n"
    "5. 详细设计：{workspace}/Dev/design.md\n"
    "6. 实现计划：{workspace}/Dev/plan.md\n"
    "7. 要求 QA：先阅读所有文档，写出你对项目的理解"
    "和初步测试思路大纲（测什么、怎么测），"
    "得到 PM 和 Dev 确认后才能开始写详细测试计划\n"
    "8. 强调：在确认之前，不得开始写测试用例或执行测试"
)

QA_HANDOFF_CONFIG = HandoffConfig(
    receiver="qa",
    letter_title="Master 给 QA 的信",
    letter_prompt=QA_HANDOFF_LETTER,
    context_letter_key="qaletter_path",
    create_dirs=("QA",),
)
QA_HANDOFF_DEF = HandoffSubgraph.define(QA_HANDOFF_CONFIG)


class QAAlign:
    """QA ↔ PM / Dev / Master 对齐循环，6 节点 + 1 空节点。"""

    entries = {"qa_read": "qa_align_qa"}
    exits = {"judge_exit": "qa_align_judge_exit"}

    _runtime = None

    @staticmethod
    def qa(state) -> dict:
        """QA 读 handoff/feedback，写理解 (1 call_agent via read_and_write_letter)."""
        runtime = QAAlign._runtime
        qa_conv = conv_name("qa-align")
        runtime.context.set_ctx("qa_conv", qa_conv)

        runtime.logger.log_event("phase_started", detail="QA 对齐")
        runtime.msg.phase("Phase 3b: QA 对齐")

        feedback_path = runtime.context.get_ctx("qa_feedback_path")
        is_first = not (feedback_path and os.path.exists(feedback_path))

        if is_first:
            handoff_path = runtime.context.get_ctx("qaletter_path")
            if not handoff_path:
                raise RuntimeError("没有 handoff 信件路径")
            qa_reply_path = letter_path(runtime, "qa-understanding")
            read_and_write_letter(runtime, "qa", qa_conv,
                                  handoff_path, qa_reply_path,
                                  "From QA, Re: Master 的委托",
                                  "阅读所有项目文档后，"
                                  "写出你对项目的理解总结，"
                                  "以及初步的测试思路大纲。\n\n"
                                  "测试思路大纲需覆盖：\n"
                                  "- 测试范围（功能模块、边界场景）\n"
                                  "- 每个模块的测试方法（E2E / API / 单元）\n"
                                  "- 不清楚或有疑问的地方\n\n"
                                  "你的信件会被 PM 和 Dev 查看"
                                  "（PM 检查测试的范围，Dev 检查测试的可行性），"
                                  "并且由他们回复你的问题。"
                                  "注意：这是大纲阶段，不要写详细测试用例。",
                                  "在 PM 和 Dev 明确许可之前，不得开始写测试用例")
        else:
            if not os.path.exists(feedback_path):
                raise RuntimeError("QA 反馈信缺失")
            qa_reply_path = letter_path(runtime, "qa-understanding")
            read_and_write_letter(runtime, "qa", qa_conv,
                                  feedback_path, qa_reply_path,
                                  "From QA, Re: 修订后的理解与测试思路",
                                  "根据上轮反馈修订你的理解总结和测试思路大纲，"
                                  "如果有新的疑问也一并提出。"
                                  "如果已经没有疑问，明确说明已无疑问。",
                                  "在 PM 和 Dev 明确许可之前，不得开始写测试用例")

        if os.path.exists(qa_reply_path):
            with open(qa_reply_path, "r", encoding="utf-8") as f:
                qa_reply_text = f.read()
            runtime.context.set_ctx("qa_reply_text", qa_reply_text)
        runtime.context.set_ctx("qa_reply_path", qa_reply_path)
        return {"phase": "qa_align_qa_done", "judge_result": ""}

    @staticmethod
    def pm(state) -> dict:
        """PM 读 QA 理解，写 review (1 call_agent via read_and_write_letter)."""
        runtime = QAAlign._runtime
        qa_reply_path = runtime.context.get_ctx("qa_reply_path")
        if not qa_reply_path or not os.path.exists(qa_reply_path):
            raise RuntimeError("QA 理解信件不存在")

        pm_conv = runtime.context.get_ctx("pm_conv") or conv_name("pm-align")
        runtime.context.set_ctx("pm_conv", pm_conv)

        pm_review_path = letter_path(runtime, "pm-review-qa")
        read_and_write_letter(runtime, "pm", pm_conv,
                              qa_reply_path, pm_review_path,
                              "From PM, Re: QA 的理解与测试思路",
                              "逐一检查 QA 的理解是否正确，测试范围是否覆盖了所有功能点。\n"
                              "回答 QA 的疑问。\n"
                              "有无法回答的问题，在对应处标记 ❓需要升级。\n"
                              "如果 QA 的理解完全正确且测试范围无遗漏，也请明确说明。\n"
                              "注意：如果需要升级到 Master，你的回信中必须包含"
                              "QA 的全部理解和全部疑问清单"
                              "（包括你已解答的和需要升级给 Master 的），"
                              "以便 Master 掌握完整上下文。",
                              "审 QA 的理解和测试范围",
                              keep=True)

        pm_review = ""
        if os.path.exists(pm_review_path):
            with open(pm_review_path, "r", encoding="utf-8") as f:
                pm_review = f.read()
        runtime.context.set_ctx("pm_review_text", pm_review)
        runtime.context.set_ctx("pm_review_path", pm_review_path)
        return {"phase": "qa_align_pm_done", "judge_result": ""}

    @staticmethod
    def dev(state) -> dict:
        """Dev 读 QA 理解，写 review (1 call_agent via read_and_write_letter)."""
        runtime = QAAlign._runtime
        qa_reply_path = runtime.context.get_ctx("qa_reply_path")
        if not qa_reply_path or not os.path.exists(qa_reply_path):
            raise RuntimeError("QA 理解信件不存在")

        dev_conv = runtime.context.get_ctx("dev_conv") or conv_name("dev-align")
        runtime.context.set_ctx("dev_conv", dev_conv)

        dev_review_path = letter_path(runtime, "dev-review-qa")
        read_and_write_letter(runtime, "dev", dev_conv,
                              qa_reply_path, dev_review_path,
                              "From Dev, Re: QA 的测试思路大纲",
                              "逐一检查 QA 的测试方法在技术实现上是否可行。\n"
                              "如果测试方案涉及当前未实现的接口或功能点，需明确指出。\n"
                              "如果测试环境配置有问题，也请指出。\n"
                              "回答 QA 的疑问。\n"
                              "有无法回答的问题，在对应处标记 ❓需要升级。\n"
                              "如果 QA 的测试思路完全可行，也请明确说明。\n"
                              "注意：如果需要升级到 Master，你的回信中必须包含"
                              "QA 的全部理解和全部疑问清单"
                              "（包括你已解答的和需要升级给 Master 的），"
                              "以便 Master 掌握完整上下文。",
                              "审 QA 测试思路的技术可行性")

        dev_review = ""
        if os.path.exists(dev_review_path):
            with open(dev_review_path, "r", encoding="utf-8") as f:
                dev_review = f.read()
        runtime.context.set_ctx("dev_review_text", dev_review)
        runtime.context.set_ctx("dev_review_path", dev_review_path)
        return {"phase": "qa_align_dev_done", "judge_result": ""}

    @staticmethod
    def judge(state) -> dict:
        """Judge 判读 PM+Dev 审查，路由 (1 judge_reply)."""
        runtime = QAAlign._runtime
        pm_review = runtime.context.get_ctx("pm_review_text") or ""
        dev_review = runtime.context.get_ctx("dev_review_text") or ""
        combined_review = f"## PM 的审查\n{pm_review}\n\n## Dev 的审查\n{dev_review}"
        needs_upgrade = "❓" in combined_review

        judge_result = judge_reply(runtime, "PM/Dev", combined_review, [
            "A. QA 理解完全正确且测试范围无遗漏，无需修改",
            "B. PM 和 Dev 均没有需要升级到 Master 的问题，但有反馈需要 QA 修改",
            "C. PM 或 Dev 有需要升级到 Master 的问题",
        ], "judge-qa-align")

        if judge_result in ("C",) or needs_upgrade:
            runtime.context.set_ctx("combined_review", combined_review)
            return {"phase": "qa_align_escalate", "judge_result": "qa_align_master"}

        if judge_result == "B":
            feedback_dir = runtime.paths.handoffs
            combined_path = os.path.join(
                feedback_dir, f"qa-combined-feedback-{int(time.time())}.md")
            with open(combined_path, "w", encoding="utf-8") as f:
                f.write(combined_review)
            runtime.context.set_ctx("qa_feedback_path", combined_path)
            return {"phase": "qa_align_feedback", "judge_result": "qa_align_qa"}

        # A — 对齐完成
        qa_reply_text = runtime.context.get_ctx("qa_reply_text")
        qa_dir = os.path.join(runtime.paths.workspace, "QA")
        os.makedirs(qa_dir, exist_ok=True)
        understanding_path = os.path.join(qa_dir, "understanding.md")
        with open(understanding_path, "w", encoding="utf-8") as f:
            f.write(qa_reply_text)
        runtime.context.set_ctx("qa_understanding_path", understanding_path)
        runtime.logger.log_event("phase_completed", detail="QA 对齐完成")
        clear_checkpoint(runtime)
        runtime.msg.ok(f"QA 对齐完成，理解已写入 {understanding_path}")
        return {"phase": "qa_align_done", "judge_result": "exit"}

    @staticmethod
    def master(state) -> dict:
        """Master 处理升级问题 + judge (read_and_write_letter + judge_reply)."""
        runtime = QAAlign._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        pm_review_path = runtime.context.get_ctx("pm_review_path")
        dev_review_path = runtime.context.get_ctx("dev_review_path")

        runtime.msg.step("升级到 Master")
        master_reply_path = letter_path(runtime, "master-reply-qa")
        read_and_write_letter(runtime, "master", master_conv,
                              [pm_review_path, dev_review_path], master_reply_path,
                              "From Master, Re: QA 对齐中的争议",
                              "阅读 PM 和 Dev 的审查报告，逐条回答他们无法解决的问题。\n"
                              "如果报告中有你无法判定的问题，明确写出需要向用户确认。\n"
                              "你的回复中将包含 QA 的全部理解和全部疑问清单，"
                              "确保 QA 收到后掌握完整的对齐结论。",
                              "回答 QA 对齐中升级上来的问题")

        master_reply = ""
        if os.path.exists(master_reply_path):
            with open(master_reply_path, "r", encoding="utf-8") as f:
                master_reply = f.read()

        master_judge = judge_reply(runtime, "Master", master_reply, [
            "A. Master 已解决所有问题",
            "B. Master 还有疑问需要向用户确认",
        ], "judge-qa-master")

        if master_judge == "B" or "❓" in master_reply:
            runtime.context.set_ctx("master_reply_path", master_reply_path)
            return {"phase": "qa_align_need_confirm", "judge_result": "qa_align_confirm"}

        runtime.context.set_ctx("qa_feedback_path", master_reply_path)
        return {"phase": "qa_align_master_done", "judge_result": "qa_align_qa"}

    @staticmethod
    def confirm(state) -> dict:
        """用户确认 (clarify_loop)."""
        runtime = QAAlign._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        runtime.msg.step("Master 需要向用户确认")
        clarify_loop(runtime, master_conv, "== 向用户确认（QA 对齐）==",
                     "Master 需要向用户确认 QA 对齐中的争议问题")
        return {"phase": "qa_align_confirmed", "judge_result": ""}

    @staticmethod
    def record(state) -> dict:
        """记录决策到 project_context.md (1 call_agent)."""
        runtime = QAAlign._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        pc_path = runtime.context.get_bg("project_context_path")
        call_agent(runtime, "master", master_conv,
                   f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{pc_path}")
        return {"phase": "qa_align_recorded", "judge_result": ""}

    @staticmethod
    def final(state) -> dict:
        """Master 写最终答复 (1 call_agent via write_letter)."""
        runtime = QAAlign._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        final_path = letter_path(runtime, "master-final-qa")
        write_letter(runtime, "master", master_conv, final_path,
                     "Master 给 QA 的最终答复",
                     "根据用户确认的决策以及你的分析，"
                     "写出对 QA 对齐中所有问题的最终答复。")
        runtime.context.set_ctx("qa_feedback_path", final_path)
        return {"phase": "qa_align_final_done", "judge_result": "qa_align_qa"}

    @staticmethod
    def judge_exit(state) -> dict:
        """空节点：对齐完成出口 (0 call_agent)."""
        return state

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "qa_align_qa": cls.qa,
            "qa_align_pm": cls.pm,
            "qa_align_dev": cls.dev,
            "qa_align_judge": cls.judge,
            "qa_align_master": cls.master,
            "qa_align_confirm": cls.confirm,
            "qa_align_record": cls.record,
            "qa_align_final": cls.final,
            "qa_align_judge_exit": cls.judge_exit,
        })

        graph.add_edge("qa_align_qa", "qa_align_pm")
        graph.add_edge("qa_align_pm", "qa_align_dev")
        graph.add_edge("qa_align_dev", "qa_align_judge")

        graph.add_conditional_edges("qa_align_judge", lambda s: s.get("judge_result", ""), {
            "exit": "qa_align_judge_exit",
            "qa_align_qa": "qa_align_qa",
            "qa_align_master": "qa_align_master",
        })

        graph.add_conditional_edges("qa_align_master", lambda s: s.get("judge_result", ""), {
            "qa_align_qa": "qa_align_qa",
            "qa_align_confirm": "qa_align_confirm",
        })

        graph.add_edge("qa_align_confirm", "qa_align_record")
        graph.add_edge("qa_align_record", "qa_align_final")
        graph.add_edge("qa_align_final", "qa_align_qa")


QA_CRITERIA_PROMPT = (
    "你即将为 QA 的测试计划和测试代码制定审核标准。\n\n"
    "## 上游约束\n"
    "标准必须与以下已确认的内容对齐：\n"
    "- 项目决策记录：{project_context}\n"
    "- PRD：{workspace}/PM/PRD.md\n"
    "- 原型：{workspace}/PM/prototype.html\n"
    "- QA 对齐理解：{workspace}/QA/understanding.md\n\n"
    "## 标准覆盖维度\n"
    "1. 测试范围覆盖 — 测试计划是否覆盖了 PRD 中的所有功能点和核心用户路径？\n"
    "2. 测试方法适宜性 — E2E / API / 单元测试的选择是否合理？\n"
    "3. 边界与异常覆盖 — 是否涵盖错误处理、空状态、边界值、非法输入？\n"
    "4. 数据流一致性 — 用户完整链路的测试是否自洽（注册→登录→操作→登出）？\n"
    "5. 可重复执行性 — 测试是否幂等、不依赖外部状态或环境？\n"
    "6. 可维护性 — 测试代码是否清晰、有适当的抽象、避免硬编码？\n"
    "## 下游需求\n"
    "- QA 将按这些标准撰写测试计划\n"
    "- Reviewer 将按这些标准审查 QA 的测试计划和测试代码\n\n"
    "## 要求\n"
    "文件中只需要写测什么以及怎么样算是测试完成，"
    "不需要写审查方法（reviewer 自己知道怎么测）。\n"
    "请具体、可操作，避免空泛描述。"
)

QA_CRITERIA_CONFIG = CriteriaDefinitionConfig(
    domain="qa",
    criteria_title="Master 制定 QA 审核标准",
    criteria_prompt=QA_CRITERIA_PROMPT,
    criteria_filename="criteria-qa.md",
    context_key="qa_criteria",
    review_conv="review-qa-criteria",
    pass_judge_result="qa_write_plan",
)
QA_CRITERIA_DEF = CriteriaDefinitionSubgraph.define(QA_CRITERIA_CONFIG)


class QAWriteTestPlan:
    """QA 写测试计划 (1 call_agent)."""

    entries = {"run": "qawrite_plan"}
    exits = {"run": "qawrite_plan"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = QAWriteTestPlan._runtime
        qa_conv = conv_name("qa-plan")
        runtime.context.set_ctx("qa_conv", qa_conv)

        ws = runtime.paths.workspace
        qa_dir = os.path.join(ws, "QA")
        os.makedirs(qa_dir, exist_ok=True)
        plan_path = os.path.join(qa_dir, "test-plan.md")
        criteria_path = runtime.context.get_ctx("qa_criteria_path") or ""
        understanding_path = runtime.context.get_ctx("qa_understanding_path") or ""

        runtime.msg.phase("QA 写测试计划")
        runtime.logger.log_event("phase_started", detail="QA 写测试计划")

        feedback_path = runtime.context.get_ctx("qa_plan_feedback_path") or ""
        feedback_note = ""
        if feedback_path and os.path.exists(feedback_path):
            feedback_note = (
                "\n## 反馈意见\n"
                "上一轮审查中有反馈意见需要处理，"
                "请先使用 read_file 工具读取反馈意见文件，"
                "然后根据反馈修改测试计划。\n\n"
                f"反馈意见文件：{feedback_path}\n\n"
            )
            runtime.context.set_ctx("qa_plan_feedback_path", "")

        prompt = (
            "请阅读以下上下文，编写详细的测试计划。\n\n"
            "## 参考文件\n"
        )
        if criteria_path and os.path.exists(criteria_path):
            prompt += f"- 审核标准：{criteria_path}\n"
        if understanding_path and os.path.exists(understanding_path):
            prompt += f"- QA 对齐理解：{understanding_path}\n"
        prompt += (
            "\n## 测试计划要求\n"
            "1. 测试范围 — 覆盖 PRD 中所有功能模块\n"
            "2. 每个模块的测试方法 — E2E / API / 单元测试的选择及理由\n"
            "3. 测试环境与数据准备要求\n"
            "4. 关键测试用例清单（覆盖功能点、边界场景、异常路径）\n"
            "5. 不接受只写大纲，需要具体到每个模块的测试点\n"
            "6. 在测试计划中，需要将测试代码和功能模块一一对应\n\n"
            f"## 输出\n"
            f"请将测试计划写入：{plan_path}"
        )

        if feedback_note:
            prompt = feedback_note + prompt

        call_agent(runtime, "qa", qa_conv, prompt)
        if not ensure_write_file(runtime, "qa", qa_conv, plan_path):
            call_agent(runtime, "qa", qa_conv,
                       f"请将测试计划写入文件 {plan_path}，使用 write_file 工具。")

        runtime.context.set_ctx("qa_plan_path", plan_path)
        if feedback_path and os.path.exists(feedback_path):
            os.remove(feedback_path)

        runtime.msg.ok(f"测试计划已写入 {plan_path}")
        return {"phase": "qa_plan_written", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"qawrite_plan": cls.run})


MASTER_PLAN_REVIEW_PROMPT = (
    "请审查 QA 的测试计划。\n\n"
    "## 测试计划文件\n"
    "{workspace}/QA/test-plan.md\n\n"
    "请先使用 read_file 工具读取测试计划，然后逐项检查：\n"
    "1. 是否覆盖了 PRD 中的所有功能点？\n"
    "2. 测试方法选择是否合理（E2E/API/单元）？\n"
    "3. 是否涵盖了边界场景和异常路径？\n"
    "4. 测试环境要求是否明确？\n\n"
    "如果完全没问题，最后一行输出 == PASS ==\n"
    "如果有任何问题，最后一行输出 == FAIL ==，并写明需要修正的具体问题。"
)

MASTER_PLAN_REVIEW_CONFIG = ArtifactReviewConfig(
    domain="master_plan",
    review_title="Master 审查测试计划",
    review_prompt=MASTER_PLAN_REVIEW_PROMPT,
    review_conv="",                            # 使用 review_conv_key
    review_conv_key="master_conv",
    pass_judge_result="qa_write_code",
    fail_judge_result="qa_write_plan",
    review_text_key="qa_plan_review",
    feedback_path_key="qa_plan_feedback_path",
    agent_role="master",
    feedback_sender="master",
    feedback_letter_title="Master 对测试计划的审查反馈",
    judge_tag="judge-qa-plan",
    feedback_conv="",
    feedback_conv_key="master_conv",
)
MASTER_PLAN_REVIEW_DEF = ArtifactReviewSubgraph.define(MASTER_PLAN_REVIEW_CONFIG)


class QAWriteTestCase:
    """QA 写测试代码 (1 call_agent)."""

    entries = {"run": "qawrite_code"}
    exits = {"run": "qawrite_code"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = QAWriteTestCase._runtime
        qa_conv = conv_name("qa-code")
        runtime.context.set_ctx("qa_conv", qa_conv)

        ws = runtime.paths.workspace
        qa_test_dir = os.path.join(ws, "QA", "tests")
        os.makedirs(qa_test_dir, exist_ok=True)
        plan_path = runtime.context.get_ctx("qa_plan_path") or ""

        runtime.msg.phase("QA 编写测试代码")
        runtime.logger.log_event("phase_started", detail="QA 编写测试代码")

        feedback_path = runtime.context.get_ctx("qa_code_feedback_path") or ""
        feedback_note = ""
        if feedback_path and os.path.exists(feedback_path):
            feedback_note = (
                "\n## 反馈意见\n"
                "上一轮审查中有反馈意见需要处理，"
                "请先使用 read_file 工具读取反馈意见文件，"
                "然后根据反馈修改测试代码。\n\n"
                f"反馈意见文件：{feedback_path}\n\n"
            )
            runtime.context.set_ctx("qa_code_feedback_path", "")

        prompt = (
            "请根据测试计划编写完整的测试代码。\n\n"
            f"## 测试计划\n{plan_path}\n\n"
            f"## 测试代码目录\n{qa_test_dir}\n\n"
            "## 要求\n"
            "1. 一次性编写全部测试脚本\n"
            "2. 测试脚本之间通过共享模块复用，不重复代码\n"
            "3. 遵守以下 Playwright 测试规范（如果涉及 E2E 测试）：\n"
        )
        prompt += "\n".join("   " + l for l in PLAYWRIGHT_TEST_TIPS.strip().split("\n"))
        prompt += (
            "\n"
            "4. 确保测试可以独立运行且幂等\n\n"
            f"请将所有测试文件写入 {qa_test_dir} 目录下。"
        )

        if feedback_note:
            prompt = feedback_note + prompt

        call_agent(runtime, "qa", qa_conv, prompt)

        runtime.context.set_ctx("qa_code_path", qa_test_dir)
        if feedback_path and os.path.exists(feedback_path):
            os.remove(feedback_path)

        runtime.msg.ok(f"测试代码已写入 {qa_test_dir}")
        return {"phase": "qa_code_written", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"qawrite_code": cls.run})


REVIEWER_CODE_REVIEW_PROMPT = (
    "请审查 QA 的测试代码。\n\n"
    "## 测试代码目录\n"
    "{workspace}/QA/tests\n"
    "请先使用 read_file 工具读取所有测试文件，然后逐项检查：\n"
    "1. 测试是否覆盖了 PRD 中的所有功能点\n"
    "2. 测试方法选择是否合适\n"
    "3. 边界场景和异常路径是否有覆盖\n"
    "4. 测试代码质量：定位器是否稳定、断言是否正确、是否有不必要的等待\n"
    "5. 测试是否遵守 Playwright 测试规范：\n"
    + "\n".join("   " + l for l in PLAYWRIGHT_TEST_TIPS.strip().split("\n"))
    + "\n"
    "6. 测试是否可独立重复执行、是否幂等\n"
    "7. 在此过程中，你不需要执行测试代码，你只需要检验测试代码的合理性和可用性。\n"
    "\n逐条给出评价，如果完全没问题，最后一行输出 == PASS ==\n"
    "如果有任何问题，最后一行输出 == FAIL ==，并写明需要修正的具体问题。"
)

REVIEWER_CODE_REVIEW_CONFIG = ArtifactReviewConfig(
    domain="reviewer_code",
    review_title="Reviewer 审查测试代码",
    review_prompt=REVIEWER_CODE_REVIEW_PROMPT,
    review_conv="review-qa-code",
    pass_judge_result="qa_run_tests",
    fail_judge_result="qa_write_code",
    review_text_key="qa_code_review",
    feedback_path_key="qa_code_feedback_path",
    criteria_path_key="qa_criteria_path",
    judge_tag="judge-qa-code",
    feedback_conv="review-qa-code-feedback",
    feedback_letter_title="测试代码审查反馈",
)
REVIEWER_CODE_REVIEW_DEF = ArtifactReviewSubgraph.define(REVIEWER_CODE_REVIEW_CONFIG)


class QARunTests:
    """QA 运行测试 (1 call_agent)."""

    entries = {"run": "qa_run_tests"}
    exits = {"run": "qa_run_tests"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = QARunTests._runtime
        qa_run_conv = conv_name("qa-run")
        runtime.context.set_ctx("qa_conv", qa_run_conv)

        ws = runtime.paths.workspace
        qa_test_dir = runtime.context.get_ctx("qa_code_path") or os.path.join(ws, "QA", "tests")
        report_path = os.path.join(ws, "QA", "test-report.md")

        runtime.msg.phase("QA 运行测试")
        runtime.logger.log_event("phase_started", detail="QA 运行测试")

        call_agent(runtime, "qa", qa_run_conv,
            "请运行测试并输出测试报告。\n\n"
            f"## 测试代码目录\n{qa_test_dir}\n\n"
            "## 要求\n"
            "1. 运行所有测试脚本\n"
            "2. 记录每个用例的执行结果（通过/失败/错误）\n"
            "3. 如果有失败，记录失败的具体原因（日志、错误堆栈）\n"
            "4. 如果测试需要启动服务，也请执行启动命令并等待就绪\n"
            f"5. 将完整测试报告写入：{report_path}")

        if not ensure_write_file(runtime, "qa", qa_run_conv, report_path):
            call_agent(runtime, "qa", qa_run_conv,
                       f"请将测试报告写入文件 {report_path}。")

        runtime.context.set_ctx("qa_test_report_path", report_path)
        runtime.msg.ok(f"测试报告已写入 {report_path}")
        return {"phase": "qa_tests_run", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"qa_run_tests": cls.run})


class JudgeTestResult:
    """Judge 判读测试结果 (1 judge_reply)."""

    entries = {"judge": "judge_test_result"}
    exits = {"to_flush": "judge_test_result_pass",
             "to_dev_fix": "judge_test_result_fail"}

    _runtime = None

    @staticmethod
    def judge(state) -> dict:
        """Judge 判读测试报告."""
        runtime = JudgeTestResult._runtime
        report_path = runtime.context.get_ctx("qa_test_report_path") or ""

        runtime.msg.phase("Judge 判读测试结果")

        if not report_path or not os.path.exists(report_path):
            raise RuntimeError(f"测试报告文件不存在：{report_path}")

        with open(report_path, "r", encoding="utf-8") as f:
            report_text = f.read()

        judge_result = judge_reply(runtime, "QA 的测试报告", report_text, [
            "A. 全部测试通过，没有 bug。",
            "B. 有测试失败，存在 bug。",
        ], tag="judge-test-result")

        if judge_result.strip() == "A":
            runtime.logger.log_event("test_judged", detail="全部测试通过")
            return {"phase": "qa_tests_pass", "judge_result": "qa_flush"}
        else:
            bug_report_path = os.path.join(
                os.path.dirname(report_path), "bug-report.md")
            with open(bug_report_path, "w", encoding="utf-8") as f:
                f.write(report_text)
            runtime.context.set_ctx("qa_bug_report_path", bug_report_path)
            runtime.logger.log_event("test_judged", detail="有 bug 需要修复")
            runtime.msg.fail(f"测试未全部通过，Bug 报告已写入 {bug_report_path}")
            return {"phase": "qa_tests_fail", "judge_result": "dev_fix"}

    @staticmethod
    def to_flush(state) -> dict:
        """空节点：PASS 出口 (0 call_agent)."""
        return state

    @staticmethod
    def to_dev_fix(state) -> dict:
        """空节点：FAIL 出口 (0 call_agent)."""
        return state

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "judge_test_result": cls.judge,
            "judge_test_result_pass": cls.to_flush,
            "judge_test_result_fail": cls.to_dev_fix,
        })

        graph.add_conditional_edges("judge_test_result", lambda s: s.get("judge_result", ""), {
            "qa_flush": "judge_test_result_pass",
            "dev_fix": "judge_test_result_fail",
        })


class DevFix:
    """Dev 修 bug (1 call_agent)."""

    entries = {"run": "dev_fix"}
    exits = {"run": "dev_fix"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = DevFix._runtime
        bug_report_path = runtime.context.get_ctx("qa_bug_report_path") or ""
        dev_dir = os.path.join(runtime.paths.workspace, "Dev")

        runtime.msg.phase("Dev 修复 bug")
        runtime.logger.log_event("phase_started", detail="Dev 修复 bug")

        if not bug_report_path or not os.path.exists(bug_report_path):
            raise RuntimeError(f"Bug 报告文件不存在：{bug_report_path}")

        dev_fix_conv = conv_name("dev-fix")

        call_agent(runtime, "dev", dev_fix_conv,
            f"请阅读 bug 报告并修复代码。\n\n"
            f"## Bug 报告\n{bug_report_path}\n\n"
            f"## 你的工作目录\n{dev_dir}\n\n"
            "## 要求\n"
            "1. 读取 bug 报告中描述的测试失败信息\n"
            "2. 定位到 Dev/ 目录下对应的源码并修复\n"
            "3. 确保修复不破坏已有功能\n"
            "4. 修复完成后不需要等待确认，直接输出结果\n\n"
            "请使用 read_file 工具读取 bug 报告，然后修改代码。")

        runtime.msg.ok("Dev 修复完成，准备重新运行测试")
        return {"phase": "dev_fix_done", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"dev_fix": cls.run})
