"""Phase 3: QA 对齐阶段。"""
import os, time

from .utils import (WorkflowState, conv_name, call_agent, letter_path,
                    write_letter, read_letter, read_and_write_letter,
                    judge_reply, clarify_loop, register_nodes)
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
