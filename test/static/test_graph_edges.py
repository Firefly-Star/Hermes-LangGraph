"""静态校验：graph.py 中所有边引用的 node_name 都存在。"""
from __future__ import annotations
import pytest
from agent_runtime import AgentRuntime, ConversationClient
from src.workflow.graph import build_graph


class DummyClient(ConversationClient):
    def call(self, *a, **kw):
        pass
    def close(self, *a, **kw):
        pass


@pytest.fixture
def app(test_config):
    rt = AgentRuntime(config_path=test_config, conversation_client=DummyClient())
    return build_graph(rt)


@pytest.fixture
def graph(app):
    return app.get_graph()


class TestGraphEdges:
    """验证每条边的 source/target 都是已注册的 node。"""

    @staticmethod
    def _non_internal(edges):
        """过滤掉 __start__ / __end__ 等 LangGraph 内部节点。"""
        internal = {"__start__", "__end__"}
        return [e for e in edges if e.source not in internal and e.target not in internal]

    def test_all_edge_nodes_are_registered(self, graph):
        registered = set(graph.nodes.keys())
        errors = []
        for edge in self._non_internal(graph.edges):
            if edge.source not in registered:
                errors.append(f"边源点 '{edge.source}' 未在 graph 中注册")
            if edge.target not in registered:
                errors.append(f"边目标点 '{edge.target}' 未在 graph 中注册")
        assert not errors, "\n".join(errors)

    def test_no_node_is_orphan(self, graph):
        """注册的节点至少被一条边引用（起点或终点）。"""
        registered = set(graph.nodes.keys())
        internal = {"__start__", "__end__"}
        referenced = set()
        for edge in graph.edges:
            if edge.source not in internal:
                referenced.add(edge.source)
            if edge.target not in internal:
                referenced.add(edge.target)
        orphans = registered - referenced - internal
        assert not orphans, f"以下节点未在边中被引用：{sorted(orphans)}"

    def test_graph_has_edges(self, graph):
        """确保图不为空。"""
        edges = self._non_internal(graph.edges)
        assert len(edges) > 0, "图中没有边"

    def test_entry_point_is_registered(self, graph):
        """验证入口节点已注册（__start__ 指向的节点）。"""
        for edge in graph.edges:
            if edge.source == "__start__":
                assert edge.target in graph.nodes, (
                    f"入口节点 '{edge.target}' 未在 graph 中注册"
                )
                return
        pytest.fail("未找到入口边（__start__ → ?）")
