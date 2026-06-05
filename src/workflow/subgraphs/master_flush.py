"""Master flush 子图 — write_summary → flush_conv 2 节点。"""
import os
from dataclasses import dataclass

from ..utils import (register_nodes, call_agent, ensure_write_file,
                     open_master_conv)
from ..checkpoint import save_checkpoint
from .base import SubgraphResult, SubgraphDef


@dataclass
class MasterFlushConfig:
    """Master flush 子图配置。

    domain / phase_name / next_step / artifacts / resume_node 为必填。
    """
    domain: str                         # "clarify" | "pm" | "dev" | "qa"
    phase_name: str                     # 显示用阶段名
    next_step: str                      # 下一步描述
    artifacts: tuple[str, ...]          # 产物路径（支持 {workspace} 占位）
    resume_node: str                    # checkpoint 的 resume_node
    summary_filename: str = ""          # 默认 "phase-summary-{domain}.md"

    def __post_init__(self):
        if not self.summary_filename:
            self.summary_filename = f"phase-summary-{self.domain}.md"


MASTER_FLUSH_SUMMARY_PROMPT = """\
请将你刚完成的阶段总结写入 {summary_path}。格式如下：

Summary:
1. Phase Completed:
   - 阶段：{phase_name}
   - 核心产出物

2. Key Decisions Made:
   - 本阶段的关键决策

3. Artifacts Produced:
   - 文件清单（含路径）

4. Open Issues / Risks:
   - 遗留问题及风险

5. Current Status:
   - 已完成: {phase_name}
   - 下一步: {next_step}

本阶段的实际产出文件（供撰写总结参考）：
{artifacts}"""


class MasterFlushDef(SubgraphDef):
    """Master flush 子图 — 2 节点：write_summary → flush_conv。"""

    def __init__(self, nodes):
        self.nodes = nodes

    def register(self, graph, runtime) -> SubgraphResult:
        for fn in self.nodes.values():
            fn._runtime = runtime
        register_nodes(graph, runtime, self.nodes)
        w, f = self.nodes
        graph.add_edge(w, f)
        self.entries = {"write_summary": w}
        self.exits = {"flush_conv": f}
        return SubgraphResult(entries=self.entries, exits=self.exits)


class MasterFlushSubgraph:
    """Master flush 通用子图工厂，只提供 define()。"""

    @staticmethod
    def define(config: MasterFlushConfig) -> MasterFlushDef:
        domain = config.domain
        write_node = f"master_flush_{domain}_summary"
        flush_node = f"master_flush_{domain}_conv"

        def write_summary(state):
            rt = write_summary._runtime
            master_conv = rt.context.get_ctx("master_conv")
            ws = rt.paths.workspace

            os.makedirs(rt.paths.phases, exist_ok=True)
            summary_path = os.path.join(rt.paths.phases, config.summary_filename)

            artifact_lines = []
            for a in config.artifacts:
                artifact_lines.append(
                    f"- {a.format(workspace=ws, project_context=rt.context.get_bg('project_context_path') or '')}")
            artifacts_text = "\n".join(artifact_lines)

            prompt = MASTER_FLUSH_SUMMARY_PROMPT.format(
                summary_path=summary_path,
                phase_name=config.phase_name,
                next_step=config.next_step,
                artifacts=artifacts_text,
            )
            call_agent(rt, "master", master_conv, prompt)

            if not ensure_write_file(rt, "master", master_conv, summary_path):
                call_agent(rt, "master", master_conv,
                           f"将阶段总结写入文件 {summary_path}，使用 write_file 工具。")

            rt.context.set_ctx("phase_summary_path", summary_path)
            return {"phase": f"{domain}_flushed", "judge_result": ""}

        def flush_conv(state):
            rt = flush_conv._runtime
            master_conv = rt.context.get_ctx("master_conv")
            summary_path = rt.context.get_ctx("phase_summary_path")

            rt.conversations.close("master", master_conv)
            new_conv = open_master_conv(rt, summary_path)
            save_checkpoint(rt, config.resume_node, config.phase_name,
                            summary_path=summary_path)
            print(f"\n  ── Master flush: {config.phase_name} → {config.next_step}"
                  f" (新对话: {new_conv})")
            return {"phase": f"{domain}_conv_flushed", "judge_result": ""}

        return MasterFlushDef(nodes={
            write_node: write_summary,
            flush_node: flush_conv,
        })
