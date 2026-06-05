"""通用子图工厂。"""
from .base import SubgraphResult
from .handoff import HandoffConfig, HandoffSubgraph
from .criteria_definition import CriteriaDefinitionConfig, CriteriaDefinitionSubgraph

__all__ = [
    "SubgraphResult",
    "HandoffConfig", "HandoffSubgraph",
    "CriteriaDefinitionConfig", "CriteriaDefinitionSubgraph",
]
