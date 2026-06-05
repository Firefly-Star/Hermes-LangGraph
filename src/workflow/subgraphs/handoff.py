"""Master 写信给下游 agent 的子图。"""
import os
from dataclasses import dataclass
from typing import Optional

from ..utils import register_nodes, letter_path, write_letter
from .base import SubgraphResult


@dataclass
class HandoffConfig:
    """Master 写信给下游 agent 的配置。"""
    receiver: str                       # "pm" | "dev" | "qa"
    letter_title: str                   # 信件标题
    letter_prompt: str                  # 信件模板，可用 {workspace} {project_context} 占位
    context_letter_key: str             # 信件路径存 context 的 key
    domain: Optional[str] = None        # 节点的前缀，默认为{receiver}
    sender: str = "master"              # 发信人 agent 名
    conversation_key: str = "master_conv"  # 发信人对话的 context key
    create_dirs: tuple[str, ...] = ()   # 写信前要创建的目录（相对 workspace）
    next_phase: Optional[str] = None    # 返回的 phase 值，留空自动设为 "{receiver}_handoff_done"

    def __post_init__(self):
        if self.next_phase is None:
            self.next_phase = f"{self.receiver}_handoff_done"
        if self.domain is None:
            self.domain = self.receiver


class HandoffSubgraph:
    """Master 写信给下游 agent 的通用子图工厂。"""

    @staticmethod
    def register(graph, runtime, config: HandoffConfig):
        node_name = f"{config.domain}_handoff"

        def run(state):
            rt = runtime
            conv = rt.context.get_ctx(config.conversation_key)
            if not conv:
                raise RuntimeError(f"{config.conversation_key} 对话不存在")

            for d in config.create_dirs:
                os.makedirs(os.path.join(rt.paths.workspace, d), exist_ok=True)

            prompt = config.letter_prompt.format(
                workspace=rt.paths.workspace,
                project_context=rt.context.get_bg("project_context_path"),
            )

            lpath = letter_path(rt, f"master-to-{config.receiver}")
            write_letter(rt, config.sender, conv, lpath,
                         config.letter_title, prompt)
            rt.context.set_ctx(config.context_letter_key, lpath)
            return {"phase": config.next_phase}

        register_nodes(graph, runtime, {node_name: run})
        return SubgraphResult(
            entries={"run": node_name},
            exits={"run": node_name},
        )
