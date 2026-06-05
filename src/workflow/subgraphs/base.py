"""子图基础类型。"""
from dataclasses import dataclass


@dataclass
class SubgraphResult:
    """子图注册返回结果，统一 entries/exits 接入方式。"""
    entries: dict
    exits: dict
