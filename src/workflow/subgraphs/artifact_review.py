"""产出审查子图 — review → PASS/FAIL 两路出口。"""
import os
from dataclasses import dataclass
from typing import Optional, Callable

from ..utils import (register_nodes, conv_name, call_agent, judge_reply,
                     letter_path, write_letter)
from .base import SubgraphResult, SubgraphDef


@dataclass
class ArtifactReviewConfig:
    """产出审查子图配置。

    domain / review_title / review_prompt / review_conv
    pass_judge_result / fail_judge_result / review_text_key / feedback_path_key 为必填。
    agent_role / judge_tag / feedback_conv / feedback_conv_key 有自动推导默认值。
    """
    domain: str                             # 节点名前缀
    review_title: str                       # 显示用标题
    review_prompt: str                      # 审查 prompt，支持 {workspace} {project_context} 占位
    review_conv: str                        # 审查对话名（review_conv_key 为空时使用）
    pass_judge_result: str                  # PASS 时 judge_result
    fail_judge_result: str                  # FAIL 时 judge_result
    review_text_key: str                    # 存审查意见的 context key
    feedback_path_key: str                  # 存反馈信路径的 context key
    review_conv_key: str = ""               # 对话名从 context 读取，优先级高于 review_conv
    agent_role: str = "reviewer"            # call_agent 的角色名
    feedback_sender: str = "reviewer"       # 写反馈信的 sender
    feedback_letter_title: str = "审查反馈"  # 反馈信标题
    criteria_path_key: str = ""             # 审核标准文件的 context key（可选）
    judge_tag: str = ""                     # judge 日志标签
    feedback_conv: str = ""                 # 反馈信对话名，默认 "{domain}_feedback"
    feedback_conv_key: str = ""             # 反馈信对话从 context 读取
    on_pass: Optional[Callable] = None      # 通过时调用 (state, runtime) → dict

    def __post_init__(self):
        if not self.feedback_conv:
            self.feedback_conv = f"{self.domain}_feedback"
        if not self.judge_tag:
            self.judge_tag = f"judge-{self.domain}"


class ArtifactReviewDef(SubgraphDef):
    """产出审查子图 — 3 节点：review + pass_through + write_feedback。"""

    def __init__(self, nodes, pass_judge_result, fail_judge_result):
        self.nodes = nodes
        self._pass = pass_judge_result
        self._fail = fail_judge_result

    def register(self, graph, runtime) -> SubgraphResult:
        for fn in self.nodes.values():
            fn._runtime = runtime
        register_nodes(graph, runtime, self.nodes)
        r, p, f = self.nodes
        graph.add_conditional_edges(r, lambda s: s.get("judge_result", ""), {
            self._pass: p,
            self._fail: f,
        })
        self.entries = {"run": r}
        self.exits = {"pass": p, "fail": f}
        return SubgraphResult(entries=self.entries, exits=self.exits)


class ArtifactReviewSubgraph:
    """产出审查通用子图工厂，只提供 define()。"""

    @staticmethod
    def define(config: ArtifactReviewConfig) -> ArtifactReviewDef:
        domain = config.domain
        review_node = f"{domain}_review"
        pass_node = f"{domain}_review_pass"
        feedback_node = f"{domain}_review_feedback"

        def review(state):
            rt = review._runtime
            rt.msg.phase(config.review_title)

            # 格式化 prompt
            prompt = config.review_prompt.format(
                workspace=rt.paths.workspace,
                project_context=rt.context.get_bg("project_context_path") or "",
            )

            # 追加审核标准引用（可选）
            if config.criteria_path_key:
                cp = rt.context.get_ctx(config.criteria_path_key) or ""
                if cp and os.path.exists(cp):
                    prompt += f"\n\n## 审核标准\n{cp}"

            # 获取对话名
            if config.review_conv_key:
                conv = rt.context.get_ctx(config.review_conv_key) or ""
            else:
                conv = conv_name(config.review_conv)

            review_text = call_agent(rt, config.agent_role, conv, prompt,
                                     stream=True)

            judge_result = judge_reply(rt, config.agent_role, review_text, [
                "P. 审查通过，满足所有条件。",
                "F. 审查不通过，存在问题需要修正。",
            ], tag=config.judge_tag)
            passed = judge_result.strip() == "P"

            if passed:
                rt.logger.log_event("review_passed",
                                    detail=f"{config.review_title} 通过")
                if config.on_pass:
                    return config.on_pass(state, rt)
                return {"phase": f"{domain}_review_done",
                        "judge_result": config.pass_judge_result}
            else:
                rt.context.set_ctx(config.review_text_key, review_text)
                rt.logger.log_event("review_failed",
                                    detail=f"{config.review_title} 不通过")
                return {"phase": f"{domain}_review_fail",
                        "judge_result": config.fail_judge_result}

        def pass_through(state):
            return state

        def write_feedback(state):
            rt = write_feedback._runtime
            review = rt.context.get_ctx(config.review_text_key) or ""
            if not review:
                raise RuntimeError(f"审查意见为空（{config.review_text_key}）")

            if config.feedback_conv_key:
                feed_conv = rt.context.get_ctx(config.feedback_conv_key) or ""
            else:
                feed_conv = conv_name(config.feedback_conv)

            lpath = letter_path(rt, f"reviewer-{domain}-feedback")
            write_letter(rt, config.feedback_sender, feed_conv, lpath,
                         config.feedback_letter_title,
                         f"以下是你在上轮审查中给出的评审意见，"
                         f"请整理成一封反馈信。\n\n"
                         f"## 你的审查意见\n{review}")
            rt.context.set_ctx(config.feedback_path_key, lpath)
            rt.context.set_ctx(config.review_text_key, "")
            return {"phase": f"{domain}_review_failed",
                    "judge_result": config.fail_judge_result}

        return ArtifactReviewDef(
            nodes={
                review_node: review,
                pass_node: pass_through,
                feedback_node: write_feedback,
            },
            pass_judge_result=config.pass_judge_result,
            fail_judge_result=config.fail_judge_result,
        )
