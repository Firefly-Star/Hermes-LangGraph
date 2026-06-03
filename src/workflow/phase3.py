"""Phase 3: QA 对齐阶段。"""
import os, time

from .utils import (WorkflowState, conv_name, call_agent, letter_path,
                    write_letter, read_letter, read_and_write_letter,
                    judge_reply, clarify_loop, register_nodes, write_criteria)
from .checkpoint import clear_checkpoint


class QAHandoff:
    """Master 写 handoff 信给 QA (1 call_agent via write_letter)."""

    entries = {"run": "qa_handoff"}
    exits = {"run": "qa_handoff"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = QAHandoff._runtime
        print(f"\n{'='*60}\n  ==> Phase 3a: Master 写信给 QA\n{'='*60}")

        master_conv = runtime.context.get_ctx("master_conv")
        if not master_conv:
            raise RuntimeError("master conversation 不存在")

        ws = runtime.paths.workspace
        qa_dir = os.path.join(ws, "QA")
        os.makedirs(qa_dir, exist_ok=True)

        lpath = letter_path(runtime, "master-to-qa")
        write_letter(runtime, "master", master_conv, lpath,
                     "Master 给 QA 的信",
                     f"介绍项目上下文。信件需包含：\n"
                     "1. 开宗明义：这是 Master 给 QA 的信\n"
                     "2. 项目概况和核心需求（简要描述）\n"
                     f"3. 项目决策记录：{runtime.context.get_bg('project_context_path')}\n"
                     f"4. PRD：{ws}/PM/PRD.md\n"
                     f"5. 详细设计：{ws}/Dev/design.md\n"
                     f"6. 实现计划：{ws}/Dev/plan.md\n"
                     "7. 要求 QA：先阅读所有文档，写出你对项目的理解"
                     "和初步测试思路大纲（测什么、怎么测），"
                     "得到 PM 和 Dev 确认后才能开始写详细测试计划\n"
                     "8. 强调：在确认之前，不得开始写测试用例或执行测试")

        runtime.context.set_ctx("qaletter_path", lpath)
        print(f"\n  ── Master 给 QA 的信件已就绪 ──")
        return {"phase": "qa_handoff_done"}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"qa_handoff": cls.run})


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
        print(f"\n{'='*60}\n  ==> Phase 3b: QA 对齐（QA ↔ PM / Dev / Master）\n{'='*60}")

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
        print(f"\n  ✓ QA 对齐完成，理解已写入 {understanding_path}")
        return {"phase": "qa_align_done", "judge_result": "exit"}

    @staticmethod
    def master(state) -> dict:
        """Master 处理升级问题 + judge (read_and_write_letter + judge_reply)."""
        runtime = QAAlign._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        pm_review_path = runtime.context.get_ctx("pm_review_path")
        dev_review_path = runtime.context.get_ctx("dev_review_path")

        print("\n  ── 升级到 Master ──")
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
        print("\n  ── Master 需要向用户确认 ──")
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


class QAWriteCriteria:
    """Master 制定 QA 审核标准 (1 call_agent via write_criteria)."""

    entries = {"run": "qawrite_criteria"}
    exits = {"run": "qawrite_criteria"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = QAWriteCriteria._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        project_context_path = runtime.context.get_bg("project_context_path")
        ws = runtime.paths.workspace

        runtime.logger.log_event("phase_started", detail="QA 审核标准制定")

        feedback_path = runtime.context.get_ctx("qa_criteria_feedback_path") or ""
        feedback_note = ""
        if feedback_path and os.path.exists(feedback_path):
            feedback_note = (
                "\n## 反馈意见\n"
                "上一轮审查中有反馈意见需要处理，请先使用 read_file 工具读取反馈意见文件，"
                "然后根据反馈修改标准。\n\n"
                f"反馈意见文件：{feedback_path}\n\n"
            )
            runtime.context.set_ctx("qa_criteria_feedback_path", "")

        prompt = (
            "你即将为 QA 的测试计划和测试代码制定审核标准。\n\n"
            "## 上游约束\n"
            "标准必须与以下已确认的内容对齐：\n"
            f"- 项目决策记录：{project_context_path or '（无项目决策记录）'}\n"
            f"- PRD：{ws}/PM/PRD.md\n"
            f"- 原型：{ws}/PM/prototype.html\n"
            f"- QA 对齐理解：{ws}/QA/understanding.md\n\n"
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
            "文件中只需要写测什么以及怎么样算是测试完成，不需要写审查方法（reviewer 自己知道怎么测）。\n"
            "请具体、可操作，避免空泛描述。"
        )

        if feedback_note:
            prompt = feedback_note + prompt

        write_criteria(
            runtime, master_conv,
            title="Master 制定 QA 审核标准",
            file_path=os.path.join(ws, "criteria-qa.md"),
            prompt=prompt,
            context_key="qa_criteria",
        )

        if feedback_path:
            os.remove(feedback_path)

        return {"phase": "qa_criteria_done", "judge_result": "review_qa_criteria"}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"qawrite_criteria": cls.run})


class ReviewQACriteria:
    """Reviewer 审查 QA 审核标准，2 节点 + 1 空节点。"""

    entries = {"review": "review_qa_criteria"}
    exits = {"to_qa_plan": "review_to_qa_plan",
             "write_feedback": "review_qa_criteria_feedback"}

    _runtime = None

    @staticmethod
    def review(state) -> dict:
        """Reviewer 审查 + judge_reply (2 call_agents)."""
        runtime = ReviewQACriteria._runtime
        criteria_path = runtime.context.get_ctx("qa_criteria_path") or ""
        print(f"\n{'='*60}\n  ==> Reviewer 审查 QA 审核标准\n{'='*60}")

        if not criteria_path or not os.path.exists(criteria_path):
            print(f"  ✗ QA 审核标准文件不存在：{criteria_path}")
            return {"phase": "review_qa_criteria_fail", "judge_result": "qawrite_criteria"}

        review = call_agent(runtime, "reviewer", conv_name("review-qa-criteria"),
            "请审查以下审核标准。\n\n"
            "逐条检查：\n"
            "1. 每条标准是否具体、可衡量(审核标准不能带有\"恰当\"，\"合理\"等主观判断)？\n"
            "2. 每条标准是否都拥有可以完整完成审查的审查方法？(agent可以使用tool如file_read等方法进行审查，不需要标准中写明，但是你可以根据标准确定改用什么方法进行完整的审查)\n"
            "3. 标准是否覆盖了所有应覆盖的维度？\n"
            f"审核标准文件在：{criteria_path}\n\n"
            "逐条给出评价，如果完全没有任何问题，且没有任何可以提高的建议，则最后一行输出 == PASS =="
            "如果有任何问题或有任何建议则输出 == FAIL ==。\n"
            "如果 FAIL，写明需要修正的具体问题。",
            stream=True)

        judge_result = judge_reply(runtime, "Reviewer", review, [
            "P. 审查通过，所有标准具体可衡量。",
            "F. 审查不通过，标准需要修正。",
        ], tag="judge-qa-criteria")
        passed = judge_result.strip() == "P"

        if passed:
            runtime.context.set_ctx("qa_criteria_feedback_path", "")
            runtime.logger.log_event("criteria_reviewed", detail="QA 审核标准审查通过")
            return {"phase": "review_qa_criteria_done", "judge_result": "qa_write_plan"}
        else:
            runtime.context.set_ctx("qa_criteria_review", review)
            runtime.logger.log_event("criteria_reviewed", detail="QA 审核标准审查不通过")
            return {"phase": "review_qa_criteria_fail", "judge_result": "qawrite_criteria"}

    @staticmethod
    def to_qa_plan(state) -> dict:
        """空节点：PASS 出口 (0 call_agent)."""
        return state

    @staticmethod
    def write_feedback(state) -> dict:
        """写反馈信给 QAWriteCriteria (write_letter)."""
        runtime = ReviewQACriteria._runtime
        feedback_path = letter_path(runtime, "reviewer-qa-criteria-feedback")
        review = runtime.context.get_ctx("qa_criteria_review")
        if not review:
            raise RuntimeError("审查意见为空")

        write_letter(runtime, "reviewer", conv_name("review-qa-criteria-feedback"),
                     feedback_path, "QA 审核标准审查反馈",
                     f"以下是你在上一轮审查中给出的评审意见，请整理成一封反馈信。\n\n"
                     f"## 你的审查意见\n{review}")
        runtime.context.set_ctx("qa_criteria_feedback_path", feedback_path)
        runtime.context.set_ctx("qa_criteria_review", "")
        return {"phase": "review_qa_criteria_failed", "judge_result": "qawrite_criteria"}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "review_qa_criteria": cls.review,
            "review_to_qa_plan": cls.to_qa_plan,
            "review_qa_criteria_feedback": cls.write_feedback,
        })

        # 组内条件路由
        graph.add_conditional_edges("review_qa_criteria", lambda s: s.get("judge_result", ""), {
            "qa_write_plan": "review_to_qa_plan",
            "qawrite_criteria": "review_qa_criteria_feedback",
        })
