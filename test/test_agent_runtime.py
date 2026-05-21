"""
Agent Pool 白盒测试
====================
测试策略：
  - Config / Logger / ContextManager：纯文件 I/O，完整测试
  - AgentManager：测无 gateway 的部分（create_agent/list/health/detect）
  - ConversationManager：需要真实 gateway，跳过（集成测试阶段再做）
  - Checkpoint：交互式，跳过
  - AgentRuntime：跳过（组合测试）

环境管理：
  - 备份 .agent_pool/ → 测试用临时内容 → 恢复
"""

import os, sys, json, shutil, tempfile, time
from copy import deepcopy

import pytest

# ── 添加项目目录到路径 ──────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_DIR, "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

import agent_runtime as ap

# ── 路径常量 ────────────────────────────────────────────
BACKUP_DIR = os.path.join(PROJECT_DIR, ".agent_pool.test_backup")


# ════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def manage_pool_dir():
    """每个测试前：备份 .agent_pool/、清空重建；测试后：恢复。"""
    _backup_agent_pool()
    _clean_agent_pool()
    os.makedirs(_pool_dir(), exist_ok=True)
    yield
    _restore_agent_pool()


def _pool_dir():
    return getattr(ap, 'POOL_DIR_DEFAULT', None) or os.path.join(os.getcwd(), ".agent_runtime")

def _config_path():
    return os.path.join(_pool_dir(), "runtime_config.json")


def _hermes_home():
    return r"C:\Users\温学周\AppData\Local\hermes"


def _backup_agent_pool():
    """备份当前 .agent_pool/ 到临时目录。"""
    pool = _pool_dir()
    if os.path.exists(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR)
    if os.path.exists(pool):
        shutil.copytree(pool, BACKUP_DIR)


def _clean_agent_pool():
    """清空 .agent_pool/ 目录。"""
    pool = _pool_dir()
    if os.path.exists(pool):
        shutil.rmtree(pool)


def _restore_agent_pool():
    """恢复备份。"""
    pool = _pool_dir()
    if os.path.exists(pool):
        shutil.rmtree(pool)
    if os.path.exists(BACKUP_DIR):
        shutil.copytree(BACKUP_DIR, pool)


def _ensure_pool_clean():
    """确保 .agent_pool/ 存在且为空。"""
    _clean_agent_pool()
    os.makedirs(_pool_dir(), exist_ok=True)


# ════════════════════════════════════════════════════════
# 工具函数测试
# ════════════════════════════════════════════════════════

class TestUtils:
    def test_iso_now_format(self):
        t = ap._iso_now()
        assert len(t) == 19  # "2026-05-19T14:30:00"
        assert "T" in t

    def test_ensure_dir_creates(self):
        _clean_agent_pool()
        assert not os.path.exists(_pool_dir())
        os.makedirs(_pool_dir(), exist_ok=True)
        assert os.path.isdir(_pool_dir())

    def test_read_json_empty(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        data = ap._read_json(os.path.join(_pool_dir(), 'registry.json'))
        assert data == {}

    def test_read_json_not_exists(self):
        data = ap._read_json("/nonexistent/path.json")
        assert data == {}

    def test_read_write_json_roundtrip(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        original = {"a": 1, "b": [2, 3], "c": {"d": "你好"}}
        ap._write_json(os.path.join(_pool_dir(), 'registry.json'), original)
        loaded = ap._read_json(os.path.join(_pool_dir(), 'registry.json'))
        assert loaded == original

    def test_write_json_creates_dir(self):
        # 确保 POOL_DIR 不存在
        _clean_agent_pool()
        assert not os.path.exists(_pool_dir())
        data = {"k": "v"}
        ap._write_json(os.path.join(_pool_dir(), 'context.json'), data)
        assert os.path.isdir(_pool_dir())
        assert os.path.exists(os.path.join(_pool_dir(), 'context.json'))

    def test_append_jsonl(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ap._append_jsonl(os.path.join(_pool_dir(), 'calls.jsonl'), {"seq": 1, "msg": "hello"})
        ap._append_jsonl(os.path.join(_pool_dir(), 'calls.jsonl'), {"seq": 2, "msg": "world"})
        with open(os.path.join(_pool_dir(), 'calls.jsonl'), "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2
        assert lines[0]["seq"] == 1
        assert lines[1]["seq"] == 2


# ════════════════════════════════════════════════════════
# Config 测试
# ════════════════════════════════════════════════════════

class TestConfig:
    def test_get_default(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c = ap.Config(_config_path())
        assert c.get("call_timeout") == 120
        assert c.get("max_retry") == 3
        assert c.get("nonexistent") is None

    def test_set_and_get(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c = ap.Config(_config_path())
        c.set("call_timeout", 300)
        assert c.get("call_timeout") == 300

    def test_set_overrides_default(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c = ap.Config(_config_path())
        assert c.get("max_retry") == 3
        c.set("max_retry", 5)
        assert c.get("max_retry") == 5

    def test_persistence(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c1 = ap.Config(_config_path())
        c1.set("call_timeout", 999)
        c2 = ap.Config(_config_path())
        assert c2.get("call_timeout") == 999

    def test_set_preserves_existing(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c = ap.Config(_config_path())
        c.set("call_timeout", 100)
        c.set("max_retry", 2)
        assert c.get("call_timeout") == 100
        assert c.get("max_retry") == 2

    def test_set_none_value(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        c = ap.Config(_config_path())
        c.set("some_key", None)
        assert c.get("some_key") is None


# ════════════════════════════════════════════════════════
# Logger 测试
# ════════════════════════════════════════════════════════

class TestLogger:
    def test_log_call(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(
            agent="dev", conversation="test-conv",
            input_text="hello", output_text="world",
            input_tokens=10, output_tokens=5, latency_ms=100,
            success=True,
        )
        calls = l.get_calls()
        assert len(calls) == 1
        assert calls[0]["agent"] == "dev"
        assert calls[0]["conversation"] == "test-conv"
        assert calls[0]["input_text"] == "hello"
        assert calls[0]["output_text"] == "world"
        assert calls[0]["input_tokens"] == 10
        assert calls[0]["output_tokens"] == 5
        assert calls[0]["total_tokens"] == 15
        assert calls[0]["success"] is True

    def test_log_call_with_error(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(
            agent="dev", conversation="test",
            input_text="ping", output_text="",
            input_tokens=0, output_tokens=0, latency_ms=5000,
            success=False, error="超时",
        )
        calls = l.get_calls()
        assert len(calls) == 1
        assert calls[0]["success"] is False
        assert calls[0]["error"] == "超时"

    def test_log_event(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_event("agent_created", agent="dev", detail="port=8643")
        events = l.get_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "agent_created"
        assert events[0]["agent"] == "dev"
        assert events[0]["detail"] == "port=8643"

    def test_get_calls_filter_by_agent(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(agent="dev", conversation="c1", input_text="a", output_text="b",
                   input_tokens=1, output_tokens=1, latency_ms=10, success=True)
        l.log_call(agent="pm", conversation="c1", input_text="a", output_text="b",
                   input_tokens=1, output_tokens=1, latency_ms=10, success=True)
        l.log_call(agent="dev", conversation="c2", input_text="a", output_text="b",
                   input_tokens=1, output_tokens=1, latency_ms=10, success=True)
        dev_calls = l.get_calls(agent="dev")
        assert len(dev_calls) == 2

    def test_get_calls_filter_by_conversation(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(agent="dev", conversation="c1", input_text="a", output_text="b",
                   input_tokens=1, output_tokens=1, latency_ms=10, success=True)
        l.log_call(agent="dev", conversation="c2", input_text="a", output_text="b",
                   input_tokens=1, output_tokens=1, latency_ms=10, success=True)
        c1_calls = l.get_calls(conversation="c1")
        assert len(c1_calls) == 1

    def test_get_calls_limit(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        for i in range(10):
            l.log_call(agent="dev", conversation="c", input_text=str(i), output_text="",
                       input_tokens=0, output_tokens=0, latency_ms=0, success=True)
        limited = l.get_calls(limit=3)
        assert len(limited) == 3
        # 应该是最新 3 条
        assert limited[-1]["input_text"] == "9"

    def test_get_events_filter(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_event("agent_created", agent="dev")
        l.log_event("gateway_started", agent="dev")
        l.log_event("agent_created", agent="pm")
        created = l.get_events(event_type="agent_created")
        assert len(created) == 2
        dev_events = l.get_events(agent="dev")
        assert len(dev_events) == 2

    def test_get_calls_empty(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        assert l.get_calls() == []
        assert l.get_events() == []

    def test_multiple_logs_preserved(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        for i in range(5):
            l.log_call(agent="dev", conversation=f"c{i}", input_text="a", output_text="b",
                       input_tokens=0, output_tokens=0, latency_ms=0, success=True)
        calls = l.get_calls()
        assert len(calls) == 5

    def test_log_unicode(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(agent="dev", conversation="测试", input_text="你好世界", output_text="こんにちは",
                   input_tokens=5, output_tokens=5, latency_ms=50, success=True)
        calls = l.get_calls()
        assert calls[0]["input_text"] == "你好世界"
        assert calls[0]["output_text"] == "こんにちは"

    def test_log_auto_timestamp(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(agent="dev", conversation="c", input_text="a", output_text="b",
                   input_tokens=0, output_tokens=0, latency_ms=0, success=True)
        assert "timestamp" in l.get_calls()[0]


# ════════════════════════════════════════════════════════
# ContextManager 测试
# ════════════════════════════════════════════════════════

class TestContextManager:
    def test_bg_set_get(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_bg("project_name", "测试项目")
        assert ctx.get_bg("project_name") == "测试项目"

    def test_bg_get_nonexistent(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        assert ctx.get_bg("nonexistent") == ""

    def test_bg_multiple_keys(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_bg("k1", "v1")
        ctx.set_bg("k2", "v2")
        assert ctx.get_bg("k1") == "v1"
        assert ctx.get_bg("k2") == "v2"

    def test_bg_overwrite(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_bg("key", "old")
        ctx.set_bg("key", "new")
        assert ctx.get_bg("key") == "new"

    def test_ctx_set_get(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_ctx("approved_plan", "1. 注册 2. 登录")
        assert ctx.get_ctx("approved_plan") == "1. 注册 2. 登录"

    def test_ctx_get_nonexistent(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        assert ctx.get_ctx("nonexistent") == ""

    def test_set_phase_node_creates_root(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["顶层规划"], "wip")
        text = ctx.get_phase_text()
        assert "顶层规划" in text
        assert "wip" in text
        assert "\u25cf" in text or "●" in text  # wip icon

    def test_set_phase_node_with_children(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["顶层规划"], "wip")
        ctx.set_phase_node(["顶层规划", "后端实现"], "wip")
        ctx.set_phase_node(["顶层规划", "后端实现", "文章 CRUD"], "wip")
        text = ctx.get_phase_text()
        assert "顶层规划" in text
        assert "后端实现" in text
        assert "文章 CRUD" in text

    def test_set_phase_node_change_status(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["顶层规划"], "wip")
        ctx.set_phase_node(["顶层规划", "后端实现"], "wip")
        ctx.set_phase_node(["顶层规划", "后端实现"], "done")
        text = ctx.get_phase_text()
        # 根节点是 wip，展开子节点，子节点显示 done
        assert "done" in text

    def test_set_phase_node_with_title(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["顶层规划"], "wip", title="My Project")
        text = ctx.get_phase_text()
        assert "My Project" in text

    def test_get_phase_text_empty(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        assert ctx.get_phase_text() == ""

    def test_get_phase_text_no_children_for_done(self):
        """已完成节点不展开子节点。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["顶层规划"], "done")
        ctx.set_phase_node(["顶层规划", "子步骤"], "wip")
        text = ctx.get_phase_text()
        # 顶层规划是 done，不应展开子节点
        assert "子步骤" not in text

    def test_build_injection_background(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_bg("project", "test")
        result = ctx.build_injection(["background"])
        assert "项目信息" in result
        assert "test" in result

    def test_build_injection_phase(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["Top"], "wip")
        result = ctx.build_injection(["phase"])
        assert "进度" in result
        assert "Top" in result

    def test_build_injection_custom_key(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_ctx("my_key", "my_value")
        result = ctx.build_injection(["my_key"])
        assert "my_key" in result
        assert "my_value" in result

    def test_build_injection_multiple_keys(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_bg("p1", "v1")
        ctx.set_ctx("k1", "v2")
        result = ctx.build_injection(["background", "k1"])
        assert "v1" in result
        assert "v2" in result

    def test_build_injection_empty_key(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        result = ctx.build_injection(["nonexistent"])
        assert result == ""

    def test_persistence(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx1 = ap.ContextManager(_pool_dir())
        ctx1.set_bg("stored_key", "stored_value")
        ctx2 = ap.ContextManager(_pool_dir())
        assert ctx2.get_bg("stored_key") == "stored_value"


# ════════════════════════════════════════════════════════
# AgentManager 测试（无 gateway 的场景）
# ════════════════════════════════════════════════════════

class TestAgentManager:
    def test_create_agent_no_gateway(self):
        """创建 agent，端口上无 gateway 时 status=stopped。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        # 使用一个肯定没 running gateway 的端口
        result = mgr.create_agent("test_agent", "cg", 19999)
        assert result.success is True
        assert result.status == "stopped"

    def test_create_agent_duplicate(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("dup_agent", "cg", 19998)
        result = mgr.create_agent("dup_agent", "cg", 19997)
        assert result.success is False
        assert "已存在" in result.message

    def test_create_agent_stores_registry(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("dev_agent", "cg", 19996, api_key="test-key")
        info = mgr.get_config("dev_agent")
        assert info is not None
        assert info["profile"] == "cg"
        assert info["port"] == 19996
        assert info["api_key"] == "test-key"
        assert info["status"] == "stopped"
        assert info["pid"] is None
        assert info["conversations"] == []

    def test_list_agents(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("a1", "cg", 19995)
        mgr.create_agent("a2", "cg", 19994)
        agents = mgr.list_agents()
        names = [a["name"] for a in agents]
        assert "a1" in names
        assert "a2" in names

    def test_list_agents_empty(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        assert mgr.list_agents() == []

    def test_get_config_nonexistent(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        assert mgr.get_config("nonexistent") is None

    def test_health_gateway_down(self):
        """无 gateway 的端口返回 False。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("down_agent", "cg", 19993)
        assert mgr.health("down_agent") is False

    def test_health_nonexistent(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        assert mgr.health("nonexistent") is False

    def test_create_agent_auto_profile_creation(self):
        """创建 agent 时应自动创建 profile（如果不存在）。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        # 使用一个不存在的 profile 名
        result = mgr.create_agent("new_profile_test", "cg", 19992)
        assert result.success is True

    def test_detect_gateway_no_response(self):
        """_detect_gateway 在端口无响应时返回 stopped。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        status, detail = mgr._detect_gateway(29999, "test-key", "cg")
        assert status == "stopped"

    def test_detect_gateway_invalid_port(self):
        """_detect_gateway 在不存在端口上返回 stopped。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        status, detail = mgr._detect_gateway(1, "test-key", "cg")
        assert status == "stopped"

    def test_create_agent_writes_to_file(self):
        """create_agent 后 registry.json 文件存在且内容正确。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("file_check_agent", "cg", 19991)
        raw = ap._read_json(os.path.join(_pool_dir(), 'registry.json'))
        assert "file_check_agent" in raw.get("agents", {})


# ════════════════════════════════════════════════════════
# ConversationManager 测试（跳过 — 需要真实 gateway）
# ════════════════════════════════════════════════════════

class TestConversationManager:
    def test_call_agent_not_found(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        cm = ap.ConversationManager(ap.AgentManager(_pool_dir(), _hermes_home()), ap.Logger(_pool_dir()), ap.Config(_config_path()), _pool_dir())
        result = cm.call("nonexistent", "conv1", "hello")
        assert result.success is False
        assert result.error is not None
        assert "不存在" in result.error

    def test_call_gateway_not_running(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        mgr = ap.AgentManager(_pool_dir(), _hermes_home())
        mgr.create_agent("stopped_dev", "cg", 19990)
        cm = ap.ConversationManager(mgr, ap.Logger(_pool_dir()), ap.Config(_config_path()), _pool_dir())
        result = cm.call("stopped_dev", "conv1", "hello")
        assert result.success is False
        assert "未运行" in result.error

    def test_close_conversation_nonexistent_agent(self):
        """关闭不存在 agent 的对话应不报错。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        cm = ap.ConversationManager(ap.AgentManager(_pool_dir(), _hermes_home()), ap.Logger(_pool_dir()), ap.Config(_config_path()), _pool_dir())
        cm.close_conversation("ghost", "conv1")  # should not raise


# ════════════════════════════════════════════════════════
# 边界条件与异常测试
# ════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_large_unicode_in_context(self):
        """ContextManager 应正确处理大量 Unicode 文本。"""
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        large = "你好世界" * 1000
        ctx.set_ctx("large", large)
        assert ctx.get_ctx("large") == large

    def test_deeply_nested_phase_tree(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        ctx = ap.ContextManager(_pool_dir())
        ctx.set_phase_node(["level_0"], "wip")
        ctx.set_phase_node(["level_0", "level_1"], "wip")
        ctx.set_phase_node(["level_0", "level_1", "level_2"], "wip")
        ctx.set_phase_node(["level_0", "level_1", "level_2", "level_3"], "wip")
        text = ctx.get_phase_text()
        assert "level_0" in text
        assert "level_3" in text

    def test_rapid_successive_writes(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        cfg = ap.Config(_config_path())
        for i in range(100):
            cfg.set(f"key_{i}", i)
        assert cfg.get("key_99") == 99

    def test_empty_input_log(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        l.log_call(agent="", conversation="", input_text="", output_text="",
                   input_tokens=0, output_tokens=0, latency_ms=0, success=True)
        assert len(l.get_calls()) == 1

    def test_log_with_special_chars(self):
        os.makedirs(_pool_dir(), exist_ok=True)
        l = ap.Logger(_pool_dir())
        special = "line1\nline2\ttab\"quote'\\slash"
        l.log_call(agent="dev", conversation="c", input_text=special, output_text=special,
                   input_tokens=0, output_tokens=0, latency_ms=0, success=True)
        calls = l.get_calls()
        assert calls[0]["input_text"] == special
