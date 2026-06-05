"""审核标准审查子图。"""
import os
from dataclasses import dataclass

from ..utils import (register_nodes, letter_path, write_letter, write_criteria,
                     conv_name, call_agent, judge_reply)
from .base import SubgraphResult


@dataclass
class CriteriaDefinitionConfig:
    """审核标准审查子图配置。

    domain / criteria_title / criteria_prompt / criteria_filename
    context_key / review_conv / pass_judge_result 为必填.
    feedback_conv / fail_judge_result / judge_tag 有自动推导默认值。
    """
    domain: str                             # 节点名前缀，"pm" | "dev" | "qa"
    criteria_title: str                     # 审核标准标题（显示用）
    criteria_prompt: str                    # 写标准的 prompt，支持 {workspace} {project_context} 占位
    criteria_filename: str                  # 标准文件名，如 "criteria-pm.md"
    context_key: str                        # context 中存标准文件路径的 key 前缀，如 "pm_criteria"
    review_conv: str                        # Reviewer 审查对话名，如 "review-pm-criteria"
    pass_judge_result: str                  # PASS 时 judge_result（给 graph.py 路由用）
    feedback_conv: str = ""                 # 反馈信对话名，默认 "{review_conv}-feedback"
    fail_judge_result: str = ""             # FAIL 时 judge_result，默认 "{domain}write_criteria"
    judge_tag: str = ""                     # judge 日志标签，默认 "judge-{domain}-criteria"

    def __post_init__(self):
        if not self.feedback_conv:
            self.feedback_conv = f"{self.review_conv}-feedback"
        if not self.fail_judge_result:
            self.fail_judge_result = f"{self.domain}write_criteria"
        if not self.judge_tag:
            self.judge_tag = f"judge-{self.domain}-criteria"


class CriteriaDefinitionSubgraph:
    """审核标准审查通用子图工厂。

    内部结构（4 节点 + 3 内部边）：
      {domain}write_criteria → review_{domain}_criteria
          → conditional → review_to_{domain}_artifact (PASS)
                       → review_{domain}_criteria_feedback → (loop back to write)

    外部接口：
      entries = {{"run": "{domain}write_criteria"}}
      exits   = {{"pass": "review_to_{domain}_artifact"}}
    """

    @staticmethod
    def register(graph, runtime, config: CriteriaDefinitionConfig) -> SubgraphResult:
        domain = config.domain
        write_node = f"{domain}write_criteria"
        review_node = f"review_{domain}_criteria"
        pass_node = f"review_to_{domain}_artifact"
        feedback_node = f"review_{domain}_criteria_feedback"
        feedback_path_key = f"{domain}_criteria_feedback_path"
        review_text_key = f"{domain}_criteria_review"

        def write(state):
            rt = runtime
            master_conv = rt.context.get_ctx("master_conv")
            ws = rt.paths.workspace
            project_context = rt.context.get_bg("project_context_path") or "（无项目决策记录）"

            rt.logger.log_event("phase_started", detail=config.criteria_title)

            feedback_path = rt.context.get_ctx(feedback_path_key) or ""
            feedback_note = ""
            if feedback_path and os.path.exists(feedback_path):
                feedback_note = (
                    "\n## 反馈意见\n"
                    "上一轮审查中有反馈意见需要处理，请先使用 read_file 工具读取反馈意见文件，"
                    "然后根据反馈修改标准。\n\n"
                    f"反馈意见文件：{feedback_path}\n\n"
                )
                rt.context.set_ctx(feedback_path_key, "")

            prompt = config.criteria_prompt.format(
                workspace=ws,
                project_context=project_context,
            )
            if feedback_note:
                prompt = feedback_note + prompt

            write_criteria(
                rt, master_conv,
                title=config.criteria_title,
                file_path=os.path.join(ws, config.criteria_filename),
                prompt=prompt,
                context_key=config.context_key,
            )

            if feedback_path and os.path.exists(feedback_path):
                os.remove(feedback_path)

            return {"phase": f"{domain}_criteria_done",
                    "judge_result": f"review_{domain}_criteria"}

        def review(state):
            rt = runtime
            criteria_path = rt.context.get_ctx(f"{config.context_key}_path") or ""
            print(f"\n{'='*60}\n  ==> Reviewer 审查 {config.criteria_title}\n{'='*60}")

            if not criteria_path or not os.path.exists(criteria_path):
                print(f"  ✗ 审核标准文件不存在：{criteria_path}")
                return {"phase": f"review_{domain}_criteria_fail",
                        "judge_result": config.fail_judge_result}

            review = call_agent(rt, "reviewer", conv_name(config.review_conv),
                "请审查以下审核标准。\n\n"
                "逐条检查：\n"
                "1. 每条标准是否具体、可衡量(审核标准不能带有\"恰当\"，\"合理\"等主观判断)？\n"
                "2. 每条标准是否都拥有可以完整完成审查的审查方法？"
                "(agent可以使用tool如file_read等方法进行审查，不需要标准中写明，"
                "但是你可以根据标准确定改用什么方法进行完整的审查)\n"
                "3. 标准是否覆盖了所有应覆盖的维度？\n"
                f"审核标准文件在：{criteria_path}\n\n"
                "逐条给出评价，如果完全没有任何问题，且没有任何可以提高的建议，"
                "则最后一行输出 == PASS ==。"
                "如果有任何问题或有任何建议则输出 == FAIL ==。\n"
                "如果 FAIL，写明需要修正的具体问题。",
                stream=True)

            judge_result = judge_reply(rt, "Reviewer", review, [
                "P. 审查通过，所有标准具体可衡量。",
                "F. 审查不通过，标准需要修正。",
            ], tag=config.judge_tag)
            passed = judge_result.strip() == "P"

            if passed:
                rt.context.set_ctx(feedback_path_key, "")
                rt.logger.log_event("criteria_reviewed",
                    detail=f"{config.criteria_title}审查通过")
                return {"phase": f"review_{domain}_criteria_done",
                        "judge_result": config.pass_judge_result}
            else:
                rt.context.set_ctx(review_text_key, review)
                rt.logger.log_event("criteria_reviewed",
                    detail=f"{config.criteria_title}审查不通过")
                return {"phase": f"review_{domain}_criteria_fail",
                        "judge_result": config.fail_judge_result}

        def pass_through(state):
            return state

        def write_feedback(state):
            rt = runtime
            review = rt.context.get_ctx(review_text_key) or ""
            if not review:
                raise RuntimeError("审查意见为空")

            feedback_path = letter_path(rt, f"reviewer-{domain}-criteria-feedback")
            write_letter(rt, "reviewer", conv_name(config.feedback_conv),
                         feedback_path, f"{config.criteria_title}审查反馈",
                         f"以下是你在上一轮审查中给出的评审意见，"
                         f"请整理成一封反馈信。\n\n"
                         f"## 你的审查意见\n{review}")
            rt.context.set_ctx(feedback_path_key, feedback_path)
            rt.context.set_ctx(review_text_key, "")
            return {"phase": f"review_{domain}_criteria_failed",
                    "judge_result": config.fail_judge_result}

        register_nodes(graph, runtime, {
            write_node: write,
            review_node: review,
            pass_node: pass_through,
            feedback_node: write_feedback,
        })

        # 组内边
        graph.add_edge(write_node, review_node)
        graph.add_conditional_edges(review_node,
            lambda s: s.get("judge_result", ""), {
                config.pass_judge_result: pass_node,
                config.fail_judge_result: feedback_node,
            })
        graph.add_edge(feedback_node, write_node)

        return SubgraphResult(
            entries={"run": write_node},
            exits={"pass": pass_node},
        )
