"""子图基础类型。"""
from abc import ABC, abstractmethod
from typing import Callable


class SubgraphDef(ABC):
    """子图定义基类。

    nodes       — define 时设置，节点名 → 函数
    entries/exits — register 时设置，未注册时为 None
    """

    nodes: dict[str, Callable] = {}
    entries: dict | None = None
    exits: dict | None = None

    @abstractmethod
    def register(self, graph, runtime) -> "SubgraphResult":
        """注入 runtime、注册节点、加组内边，设置 entries/exits。"""
        ...


class SubgraphResult:
    """子图注册返回结果。"""
    __slots__ = ("entries", "exits")

    def __init__(self, entries: dict, exits: dict):
        self.entries = entries
        self.exits = exits
