"""ConversationClient 接口测试。"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from agent_runtime import ConversationClient, HermesClient, AgentRuntime
from workflow.utils import call_agent


class TestConversationClient:
    """验证 ABC 接口契约。"""

    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError, match="abstract"):
            ConversationClient()  # type: ignore

    def test_mock_client_is_valid_subclass(self, mock_client):
        assert isinstance(mock_client, ConversationClient)
        # call() returns a CallResult
        result = mock_client.call("test", "conv-1", "hello")
        assert result.success is True
        assert result.text == "默认 mock 回复"

    def test_mock_client_close(self, mock_client):
        mock_client.close("dev", "conv-1")
        assert ("close:dev", "conv-1", "") in mock_client.call_history

    def test_mock_client_preset_response(self, mock_client):
        mock_client.set_response("specific prompt", "定制的回答")
        result = mock_client.call("master", "c1", "specific prompt and more")
        assert result.text == "定制的回答"

    def test_mock_client_call_history(self, mock_client):
        mock_client.call("master", "c1", "prompt 1")
        mock_client.call("dev", "c2", "prompt 2")
        assert len(mock_client.call_history) == 2
        assert mock_client.call_history[0] == ("master", "c1", "prompt 1")
        assert mock_client.call_history[1] == ("dev", "c2", "prompt 2")


class TestAgentRuntimeInjection:
    """验证 AgentRuntime 接受外部 conversation_client。"""

    def test_default_creates_hermes(self):
        """含 HermesClient 的 runtime，call 会因 gateway 未启动而失败——但类型正确。"""
        rt = AgentRuntime()
        assert isinstance(rt.conversations, HermesClient)

    def test_mock_injection(self, mock_client):
        rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        assert rt.conversations is mock_client

    def test_call_agent_with_mock(self, mock_client):
        """call_agent → runtime.conversations.call() → MockClient。"""
        mock_client.set_response("hello agent", "mock reply 123")
        rt = AgentRuntime(config_path=None, conversation_client=mock_client)

        result = call_agent(rt, "master", "conv-1", "hello agent")
        assert result == "mock reply 123"

    def test_call_agent_history(self, mock_client):
        rt = AgentRuntime(config_path=None, conversation_client=mock_client)
        call_agent(rt, "dev", "conv-2", "write design doc")

        assert len(mock_client.call_history) == 1
        agent, conv, text = mock_client.call_history[0]
        assert agent == "dev"
        assert conv == "conv-2"
        assert "write design" in text
