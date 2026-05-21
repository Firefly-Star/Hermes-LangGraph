"""
边界情况补充测试
=================
覆盖 agent_runtime.py API 参考文档中列出的全部边界情况。
与 test_agent_runtime.py 配合使用：python -m pytest test/
"""

import os, sys, json, shutil, tempfile, pytest

# ── 路径设置 ──────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_DIR, "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

import agent_runtime as ap


# ════════════════════════════════════════════════════════
# 工具函数边界
# ════════════════════════════════════════════════════════

class TestUtilsEdgeCases:
    def test_read_json_corrupt(self):
        """损坏的 JSON 应抛出异常。"""
        tmp = os.path.join(tempfile.gettempdir(), "_test_corrupt.json")
        with open(tmp, "w") as f:
            f.write("{bad json")
        with pytest.raises(json.JSONDecodeError):
            ap._read_json(tmp)
        os.remove(tmp)

    def test_write_json_empty_dict(self):
        """写入空 dict 应创建合法空 JSON 文件。"""
        tmp = os.path.join(tempfile.gettempdir(), "_test_empty.json")
        ap._write_json(tmp, {})
        with open(tmp, "r") as f:
            assert f.read().strip() == "{}"
        os.remove(tmp)

    def test_write_json_deeply_nested(self):
        """深层嵌套 dict 应正常写入。"""
        tmp = os.path.join(tempfile.gettempdir(), "_test_deep.json")
        data = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        ap._write_json(tmp, data)
        loaded = ap._read_json(tmp)
        assert loaded["a"]["b"]["c"]["d"] == [1, 2, 3]
        os.remove(tmp)

    def test_append_jsonl_existing_file(self):
        """往已有文件追加应保留原有内容。"""
        tmp = os.path.join(tempfile.gettempdir(), "_test_append.jsonl")
        ap._append_jsonl(tmp, {"seq": 1})
        ap._append_jsonl(tmp, {"seq": 2})
        ap._append_jsonl(tmp, {"seq": 3})
        lines = open(tmp, "r").read().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[-1])["seq"] == 3
        os.remove(tmp)

    def test_write_json_nested_dir_creation(self):
        """路径中嵌套的多级父目录应被自动创建。"""
        deep_dir = os.path.join(tempfile.gettempdir(), "_a", "_b", "_c")
        tmp = os.path.join(deep_dir, "test.json")
        ap._write_json(tmp, {"ok": True})
        assert os.path.exists(tmp)
        shutil.rmtree(os.path.join(tempfile.gettempdir(), "_a"))


# ════════════════════════════════════════════════════════
# Dataclass 实例化测试
# ════════════════════════════════════════════════════════

class TestDataclasses:
    def test_create_agent_result_running(self):
        r = ap.CreateAgentResult(True, "ok", "running")
        assert r.success and r.status == "running"

    def test_create_agent_result_stopped(self):
        r = ap.CreateAgentResult(False, "err", "stopped")
        assert not r.success and r.status == "stopped"

    def test_run_gateway_result_success(self):
        r = ap.RunGatewayResult(True, "done", pid=12345)
        assert r.success and r.pid == 12345

    def test_run_gateway_result_failure(self):
        r = ap.RunGatewayResult(False, "fail")
        assert not r.success and r.pid is None

    def test_stop_gateway_result(self):
        r = ap.StopGatewayResult(True, "stopped")
        assert r.success

    def test_drop_agent_result(self):
        r = ap.DropAgentResult(True, "deleted")
        assert r.success

    def test_call_result_success(self):
        r = ap.CallResult(True, "hello", 10, 5, 100, raw_data={"id": "test"})
        assert r.text == "hello" and r.raw_data["id"] == "test"

    def test_call_result_failure(self):
        r = ap.CallResult(False, "", error="超时")
        assert not r.success and r.error == "超时"

    def test_call_result_defaults(self):
        r = ap.CallResult(True, "hi")
        assert r.input_tokens == 0 and r.output_tokens == 0
        assert r.latency_ms == 0 and r.error is None and r.raw_data is None

    def test_checkpoint_result_continue(self):
        r = ap.CheckpointResult("continue", "")
        assert r.action == "continue"

    def test_checkpoint_result_modify(self):
        r = ap.CheckpointResult("modify", "改一下")
        assert r.action == "modify" and r.message == "改一下"

    def test_checkpoint_result_reject(self):
        r = ap.CheckpointResult("reject", "")
        assert r.action == "reject"


# ════════════════════════════════════════════════════════
# AgentManager 边界
# ════════════════════════════════════════════════════════

class TestAgentManagerEdgeCases:
    def _setup(self):
        tmpdir = os.path.join(tempfile.gettempdir(), "_test_am_edge")
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        hh = r"C:\Users\温学周\AppData\Local\hermes"
        return tmpdir, ap.AgentManager(tmpdir, hh)

    def _cleanup(self, tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_profile_path_format(self):
        """_profile_path 应拼出正确路径。"""
        tmpdir, mgr = self._setup()
        path = mgr._profile_path("test_profile")
        assert path.endswith(os.path.join("profiles", "test_profile"))
        assert "hermes" in path
        self._cleanup(tmpdir)

    def test_profile_exists_false(self):
        """不存在的 profile 应返回 False。"""
        tmpdir, mgr = self._setup()
        assert mgr._profile_exists("nonexistent_profile_xyz") is False
        self._cleanup(tmpdir)

    def test_write_env_content(self):
        """_write_env 应写入正确的 .env 内容。"""
        tmpdir, mgr = self._setup()
        # Mock a profile dir
        fake_profile = os.path.join(tmpdir, "profiles", "test_env")
        os.makedirs(fake_profile, exist_ok=True)
        # Override _profile_path temporarily
        orig = mgr._profile_path
        mgr._profile_path = lambda p: os.path.join(tmpdir, "profiles", p)
        try:
            mgr._write_env("test_env", 8888, "test-key")
            env_path = os.path.join(fake_profile, ".env")
            assert os.path.exists(env_path)
            with open(env_path, "r") as f:
                content = f.read()
            assert "API_SERVER_ENABLED=true" in content
            assert "API_SERVER_PORT=8888" in content
            assert "API_SERVER_KEY=test-key" in content
        finally:
            mgr._profile_path = orig
        self._cleanup(tmpdir)

    def test_write_env_preserves_existing(self):
        """_write_env 应保留 .env 中已有的非 API_SERVER 变量。"""
        tmpdir, mgr = self._setup()
        fake_profile = os.path.join(tmpdir, "profiles", "test_env2")
        os.makedirs(fake_profile, exist_ok=True)
        with open(os.path.join(fake_profile, ".env"), "w") as f:
            f.write("DEEPSEEK_API_KEY=sk-old\nMY_VAR=hello\n")
        orig = mgr._profile_path
        mgr._profile_path = lambda p: os.path.join(tmpdir, "profiles", p)
        try:
            mgr._write_env("test_env2", 8642, "kaguya")
            with open(os.path.join(fake_profile, ".env"), "r") as f:
                content = f.read()
            assert "DEEPSEEK_API_KEY=sk-old" in content
            assert "MY_VAR=hello" in content
            assert "API_SERVER_PORT=8642" in content
        finally:
            mgr._profile_path = orig
        self._cleanup(tmpdir)

    def test_health_nonexistent_agent(self):
        """不存在的 agent 调用 health 应返回 False。"""
        tmpdir, mgr = self._setup()
        assert mgr.health("ghost") is False
        self._cleanup(tmpdir)

    def test_create_agent_saves_to_file(self):
        """create_agent 后 registry 文件应存在且正确。"""
        tmpdir, mgr = self._setup()
        mgr.create_agent("test_save", "cg", 29999)
        rpath = os.path.join(tmpdir, "registry.json")
        assert os.path.exists(rpath)
        data = json.load(open(rpath))
        assert "test_save" in data.get("agents", {})
        self._cleanup(tmpdir)

    def test_list_agents_format(self):
        """list_agents 返回的 dict 应包含所有字段。"""
        tmpdir, mgr = self._setup()
        mgr.create_agent("fmt_test", "cg", 29998)
        agents = mgr.list_agents()
        assert len(agents) == 1
        entry = agents[0]
        for key in ["name", "profile", "port", "status", "conversations"]:
            assert key in entry, f"缺少字段: {key}"
        self._cleanup(tmpdir)


# ════════════════════════════════════════════════════════
# ConversationManager 边界
# ════════════════════════════════════════════════════════

class TestConversationManagerEdgeCases:
    def _setup(self):
        tmpdir = os.path.join(tempfile.gettempdir(), "_test_cm_edge")
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        hh = r"C:\Users\温学周\AppData\Local\hermes"
        mgr = ap.AgentManager(tmpdir, hh)
        logger = ap.Logger(tmpdir)
        config = ap.Config(os.path.join(tmpdir, "runtime_config.json"))
        cm = ap.ConversationManager(mgr, logger, config, tmpdir)
        return tmpdir, cm, mgr

    def _cleanup(self, tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_call_agent_nonexistent(self):
        """不存在的 agent → 立即返回失败。"""
        tmpdir, cm, _ = self._setup()
        r = cm.call("ghost", "c1", "hi")
        assert not r.success and "不存在" in r.error
        self._cleanup(tmpdir)

    def test_call_gateway_stopped(self):
        """gateway 未运行 → 立即返回失败。"""
        tmpdir, cm, mgr = self._setup()
        mgr.create_agent("stopped_agent", "cg", 19999)
        r = cm.call("stopped_agent", "c1", "hi")
        assert not r.success and "未运行" in r.error
        self._cleanup(tmpdir)

    def test_init_conversation_nonexistent_agent(self):
        """init 不存在的 agent → call 返回失败。"""
        tmpdir, cm, _ = self._setup()
        r = cm.init_conversation("phantom", "c1", "hello")
        assert not r.success
        self._cleanup(tmpdir)

    def test_close_conversation_nonexistent_agent(self):
        """close 不存在的 agent → 不报错。"""
        tmpdir, cm, _ = self._setup()
        cm.close_conversation("phantom", "c1")  # should not raise
        self._cleanup(tmpdir)

    def test_close_conversation_not_tracked(self):
        """close 未被追踪的 conversation → 不报错。"""
        tmpdir, cm, mgr = self._setup()
        mgr.create_agent("track_test", "cg", 19998)
        cm.close_conversation("track_test", "never_added")  # should not raise
        self._cleanup(tmpdir)

    def test_track_and_close_roundtrip(self):
        """追踪后关闭 → registry 中列表应更新。"""
        tmpdir, cm, mgr = self._setup()
        mgr.create_agent("tc_agent", "cg", 19997)
        # 模拟追踪
        cm._track_conversation("tc_agent", "conv_a")
        cm._track_conversation("tc_agent", "conv_b")
        reg = json.load(open(os.path.join(tmpdir, "registry.json")))
        assert "conv_a" in reg["agents"]["tc_agent"]["conversations"]
        assert "conv_b" in reg["agents"]["tc_agent"]["conversations"]
        # 关闭一个
        cm.close_conversation("tc_agent", "conv_a")
        reg = json.load(open(os.path.join(tmpdir, "registry.json")))
        assert "conv_a" not in reg["agents"]["tc_agent"]["conversations"]
        assert "conv_b" in reg["agents"]["tc_agent"]["conversations"]
        self._cleanup(tmpdir)

    def test_track_conversation_dedup(self):
        """重复追踪同一 conversation 应去重。"""
        tmpdir, cm, mgr = self._setup()
        mgr.create_agent("dedup_test", "cg", 19996)
        cm._track_conversation("dedup_test", "same_conv")
        cm._track_conversation("dedup_test", "same_conv")
        reg = json.load(open(os.path.join(tmpdir, "registry.json")))
        assert len(reg["agents"]["dedup_test"]["conversations"]) == 1
        self._cleanup(tmpdir)


# ════════════════════════════════════════════════════════
# ContextManager 边界
# ════════════════════════════════════════════════════════

class TestContextManagerEdgeCases:
    def _setup(self):
        tmpdir = os.path.join(tempfile.gettempdir(), "_test_ctx_edge")
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        return tmpdir, ap.ContextManager(tmpdir)

    def _cleanup(self, tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_set_phase_node_single_root(self):
        """len(path)==1 时应设置根节点 status。"""
        tmpdir, ctx = self._setup()
        ctx.set_phase_node(["项目顶层"], "wip")
        text = ctx.get_phase_text()
        assert "项目顶层" in text
        assert "wip" in text
        self._cleanup(tmpdir)

    def test_set_phase_node_root_title_override(self):
        """title 参数应覆盖根节点标题。"""
        tmpdir, ctx = self._setup()
        ctx.set_phase_node(["旧标题"], "done", title="新标题")
        text = ctx.get_phase_text()
        assert "新标题" in text
        assert "旧标题" not in text
        self._cleanup(tmpdir)

    def test_build_injection_all_keys_empty(self):
        """所有 key 都为空 → 返回空字符串。"""
        tmpdir, ctx = self._setup()
        result = ctx.build_injection(["background", "phase", "nonexistent"])
        assert result == ""
        self._cleanup(tmpdir)

    def test_build_injection_mixed(self):
        """部分 key 有值、部分无值 → 只组装有值的。"""
        tmpdir, ctx = self._setup()
        ctx.set_bg("p1", "v1")
        result = ctx.build_injection(["background", "phase", "nonexistent"])
        assert "v1" in result
        assert "进度" not in result
        self._cleanup(tmpdir)

    def test_set_ctx_overwrite(self):
        """覆盖已有 key 应更新值。"""
        tmpdir, ctx = self._setup()
        ctx.set_ctx("key", "old")
        ctx.set_ctx("key", "new")
        assert ctx.get_ctx("key") == "new"
        self._cleanup(tmpdir)

    def test_set_bg_empty_value(self):
        """空字符串作为 value 应正常保存。"""
        tmpdir, ctx = self._setup()
        ctx.set_bg("empty", "")
        assert ctx.get_bg("empty") == ""
        self._cleanup(tmpdir)


# ════════════════════════════════════════════════════════
# Config 边界
# ════════════════════════════════════════════════════════

class TestConfigEdgeCases:
    def _setup(self):
        tmpdir = os.path.join(tempfile.gettempdir(), "_test_cfg_edge")
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        cfg_path = os.path.join(tmpdir, "runtime_config.json")
        return tmpdir, ap.Config(cfg_path)

    def _cleanup(self, tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_get_not_in_defaults(self):
        """既不在文件也不在 DEFAULTS → 返回 None。"""
        tmpdir, cfg = self._setup()
        assert cfg.get("nonexistent_key_xyz") is None
        self._cleanup(tmpdir)

    def test_set_various_types(self):
        """支持 str / int / float / bool / list / dict 等类型。"""
        tmpdir, cfg = self._setup()
        cfg.set("str_val", "hello")
        cfg.set("int_val", 42)
        cfg.set("float_val", 3.14)
        cfg.set("bool_val", True)
        cfg.set("list_val", [1, 2, 3])
        cfg.set("dict_val", {"a": 1})
        assert cfg.get("str_val") == "hello"
        assert cfg.get("int_val") == 42
        assert cfg.get("float_val") == 3.14
        assert cfg.get("bool_val") is True
        assert cfg.get("list_val") == [1, 2, 3]
        assert cfg.get("dict_val") == {"a": 1}
        self._cleanup(tmpdir)

    def test_persistence_after_reload(self):
        """写入后重新实例化 Config 应读到相同值。"""
        tmpdir, cfg1 = self._setup()
        cfg1.set("persist_key", "persist_val")
        cfg2 = ap.Config(os.path.join(tmpdir, "runtime_config.json"))
        assert cfg2.get("persist_key") == "persist_val"
        self._cleanup(tmpdir)


# ════════════════════════════════════════════════════════
# AgentRuntime 边界
# ════════════════════════════════════════════════════════

class TestAgentRuntimeEdgeCases:
    def test_default_init(self):
        """无参数初始化应成功，使用默认路径。"""
        rt = ap.AgentRuntime()
        assert rt.runtime_dir is not None
        assert hasattr(rt, 'config')
        assert hasattr(rt, 'logger')
        assert hasattr(rt, 'agents')
        assert hasattr(rt, 'context')
        assert hasattr(rt, 'conversations')
        assert hasattr(rt, 'checkpoint')
        # 清理默认目录
        if os.path.exists(rt.runtime_dir):
            shutil.rmtree(rt.runtime_dir)

    def test_init_with_config_file(self):
        """传入配置文件应使用配置中的路径。"""
        tmpdir = os.path.join(tempfile.gettempdir(), "_test_rt_config")
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        cfg_path = os.path.join(tmpdir, "test_config.json")
        custom_pool = os.path.join(tmpdir, "my_pool")
        with open(cfg_path, "w") as f:
            json.dump({"runtime_dir": custom_pool, "hermes_home": tmpdir}, f)
        rt = ap.AgentRuntime(config_path=cfg_path)
        assert rt.runtime_dir == custom_pool
        shutil.rmtree(tmpdir)

    def test_load_config_not_found(self):
        """不存在的配置文件应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            ap.AgentRuntime._load_config("/nonexistent/path/config.json")

    def test_detect_hermes_home(self):
        """_detect_hermes_home 应返回非空字符串。"""
        detected = ap.AgentRuntime._detect_hermes_home()
        assert isinstance(detected, str)
        assert len(detected) > 0

    def test_load_config_bad_json(self):
        """内容损坏的配置文件应抛出 JSONDecodeError。"""
        tmp = os.path.join(tempfile.gettempdir(), "_bad_config.json")
        with open(tmp, "w") as f:
            f.write("{bad json}")
        with pytest.raises(json.JSONDecodeError):
            ap.AgentRuntime._load_config(tmp)
        os.remove(tmp)
