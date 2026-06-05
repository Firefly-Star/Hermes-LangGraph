"""通用子图工厂。"""
from .base import SubgraphResult, SubgraphDef
from .handoff import HandoffConfig, HandoffSubgraph, HandoffDef
from .criteria_definition import (CriteriaDefinitionConfig, CriteriaDefinitionSubgraph,
                                   CriteriaDefinitionDef)
from .artifact_review import (ArtifactReviewConfig, ArtifactReviewSubgraph,
                               ArtifactReviewDef)
from .master_flush import MasterFlushConfig, MasterFlushSubgraph, MasterFlushDef

__all__ = [
    "SubgraphResult", "SubgraphDef",
    "HandoffConfig", "HandoffSubgraph", "HandoffDef",
    "CriteriaDefinitionConfig", "CriteriaDefinitionSubgraph", "CriteriaDefinitionDef",
    "ArtifactReviewConfig", "ArtifactReviewSubgraph", "ArtifactReviewDef",
    "MasterFlushConfig", "MasterFlushSubgraph", "MasterFlushDef",
]
