"""Integration test fixtures."""
from __future__ import annotations
import pytest
from agent_runtime import AgentRuntime


@pytest.fixture
def rt(mock_client, test_config):
    """Create AgentRuntime with MockClient for integration tests."""
    _rt = AgentRuntime(config_path=test_config, conversation_client=mock_client)
    return _rt
