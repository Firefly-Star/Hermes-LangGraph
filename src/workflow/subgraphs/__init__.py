"""通用子图工厂。"""
from .base import SubgraphResult, SubgraphDef
from .handoff import HandoffConfig, HandoffSubgraph, HandoffDef
from .criteria_definition import (CriteriaDefinitionConfig, CriteriaDefinitionSubgraph,
                                   CriteriaDefinitionDef)
from .artifact_review import (ArtifactReviewConfig, ArtifactReviewSubgraph,
                               ArtifactReviewDef)

__all__ = [
    "SubgraphResult", "SubgraphDef",
    "HandoffConfig", "HandoffSubgraph", "HandoffDef",
    "CriteriaDefinitionConfig", "CriteriaDefinitionSubgraph", "CriteriaDefinitionDef",
    "ArtifactReviewConfig", "ArtifactReviewSubgraph", "ArtifactReviewDef",
]
