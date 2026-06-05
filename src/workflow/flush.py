"""Master flush: phase 边界关闭旧对话、开新对话注入上下文。"""
from .subgraphs import MasterFlushConfig, MasterFlushSubgraph

# ── 四个 phase 边界的 flush Config + Def ──

MASTER_FLUSH_CLARIFY_CONFIG = MasterFlushConfig(
    domain="clarify",
    phase_name="需求澄清",
    next_step="PM 出方案",
    artifacts=("{project_context}",),
    resume_node="pm_handoff",
)
MASTER_FLUSH_CLARIFY_DEF = MasterFlushSubgraph.define(MASTER_FLUSH_CLARIFY_CONFIG)

MASTER_FLUSH_PM_CONFIG = MasterFlushConfig(
    domain="pm",
    phase_name="PM 出方案",
    next_step="Dev 实现",
    artifacts=("{workspace}/PM/PRD.md",
               "{workspace}/PM/prototype.html",
               "{workspace}/criteria-pm.md"),
    resume_node="dev_handoff",
)
MASTER_FLUSH_PM_DEF = MasterFlushSubgraph.define(MASTER_FLUSH_PM_CONFIG)

MASTER_FLUSH_DEV_CONFIG = MasterFlushConfig(
    domain="dev",
    phase_name="Dev 实现",
    next_step="QA 对齐",
    artifacts=("{workspace}/Dev/design.md",
               "{workspace}/Dev/plan.md",
               "{workspace}/Dev/（代码仓库）"),
    resume_node="qa_handoff",
)
MASTER_FLUSH_DEV_DEF = MasterFlushSubgraph.define(MASTER_FLUSH_DEV_CONFIG)

MASTER_FLUSH_QA_CONFIG = MasterFlushConfig(
    domain="qa",
    phase_name="QA 测试",
    next_step="项目完成",
    artifacts=("{workspace}/QA/test-plan.md",
               "{workspace}/QA/tests/",
               "{workspace}/QA/test-report.md"),
    resume_node="consistency_audit",
)
MASTER_FLUSH_QA_DEF = MasterFlushSubgraph.define(MASTER_FLUSH_QA_CONFIG)

__all__ = [
    "MASTER_FLUSH_CLARIFY_DEF",
    "MASTER_FLUSH_PM_DEF",
    "MASTER_FLUSH_DEV_DEF",
    "MASTER_FLUSH_QA_DEF",
]
