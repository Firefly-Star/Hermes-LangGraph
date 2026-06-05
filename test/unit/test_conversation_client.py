"""ConversationClient 接口 + MockClient 测试。"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agent_runtime import ConversationClient, CallResult
from test.conftest import MockClient


class TestConversationClientInterface:
    """ConversationClient 抽象基类校验。"""

    def test_cannot_instantiate_abstract_class(self):
        """抽象类不能直接实例化。"""
        with pytest.raises(TypeError):
            ConversationClient()

    def test_subclass_must_implement_call(self):
        """子类不实现 call 会抛 TypeError。"""
        with pytest.raises(TypeError):
            type("BadClient", (ConversationClient,), {})()

    def test_subclass_must_implement_close(self):
        """子类不实现 close 会抛 TypeError。"""
        with pytest.raises(TypeError):
            class Partial(ConversationClient):
                def call(self, *a, **kw): pass
            Partial()


class TestMockClientCall:
    """MockClient.call 行为。"""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = MockClient()

    def test_records_agent_conv_and_prompt(self):
        """call 记录 (agent, conversation, prompt) 到 call_history。"""
        self.client.call("master", "conv-1", "你好")
        assert len(self.client.call_history) == 1
        assert self.client.call_history[0] == ("master", "conv-1", "你好")

    def test_multiple_calls_accumulate(self):
        """多次调用依次追加到 call_history。"""
        self.client.call("master", "conv-1", "第一轮")
        self.client.call("dev", "conv-2", "第二轮")
        assert len(self.client.call_history) == 2
        assert self.client.call_history[1][0] == "dev"

    def test_returns_default_reply(self):
        """默认返回「默认 mock 回复」。"""
        result = self.client.call("qa", "conv-1", "测试")
        assert isinstance(result, CallResult)
        assert result.success is True
        assert result.text == "默认 mock 回复"

    def test_returns_custom_reply_when_set(self):
        """set_response 设置前缀匹配的回复。"""
        self.client.set_response("审计", "审计报告通过")
        result = self.client.call("master", "conv-1", "审计项目文档")
        assert result.text == "审计报告通过"

    def test_falls_back_to_default_when_prefix_mismatch(self):
        """不匹配任何 set_response 时返回默认回复。"""
        self.client.set_response("审计", "审计报告")
        result = self.client.call("qa", "conv-2", "写测试计划")
        assert result.text == "默认 mock 回复"

    def test_prefix_match_uses_first_80_chars(self):
        """前缀匹配基于 prompt 前 80 字符。"""
        long_prompt = "审计" + "x" * 100
        self.client.set_response("审计", "审计回复")
        result = self.client.call("master", "conv-1", long_prompt)
        assert result.text == "审计回复"

    def test_last_set_response_wins_on_overlap(self):
        """重叠前缀时后设置的覆盖先设置的。"""
        self.client.set_response("审计", "旧回复")
        self.client.set_response("审计项目", "新回复")
        result = self.client.call("master", "conv-1", "审计项目文档")
        assert result.text == "新回复"


class TestMockClientClose:
    """MockClient.close 行为。"""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = MockClient()

    def test_records_close_in_call_history(self):
        """close 记录 (close:{agent}, conversation, "") 到 call_history。"""
        self.client.close("master", "conv-1")
        assert len(self.client.call_history) == 1
        assert self.client.call_history[0][0] == "close:master"
        assert self.client.call_history[0][1] == "conv-1"

    def test_close_does_not_affect_call(self):
        """close 后 call 仍正常工作。"""
        self.client.close("dev", "conv-2")
        result = self.client.call("master", "conv-3", "测试")
        assert result.text == "默认 mock 回复"
        assert len(self.client.call_history) == 2

    def test_multiple_closes_accumulate(self):
        """多次 close 依次追加。"""
        self.client.close("master", "conv-1")
        self.client.close("dev", "conv-2")
        assert len(self.client.call_history) == 2
        assert self.client.call_history[1][0] == "close:dev"


class TestMockClientIntegration:
    """MockClient 在 AgentRuntime 中的集成行为（与测试 fixture 联动）。"""

    @pytest.fixture(autouse=True)
    def _rt(self, mock_client, test_config):
        self.mock = mock_client
        from agent_runtime import AgentRuntime
        self.rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)

    def test_call_agent_uses_mock_client(self):
        """call_agent 经由 MockClient 返回预设文本。"""
        from workflow.utils import call_agent
        result = call_agent(self.rt, "master", "test-conv", "你好")
        assert result == "默认 mock 回复"

    def test_call_agent_records_history(self):
        """call_agent 调用记录在 mock_client.call_history。"""
        from workflow.utils import call_agent
        call_agent(self.rt, "dev", "dev-conv", "开工")
        assert ("dev", "dev-conv", "开工") in self.mock.call_history

    def test_custom_response_through_call_agent(self):
        """通过 mock_client.set_response 定制 call_agent 的回复。"""
        from workflow.utils import call_agent
        self.mock.set_response("开工", "好的开始")
        result = call_agent(self.rt, "dev", "dev-conv", "开工吧")
        assert result == "好的开始"
