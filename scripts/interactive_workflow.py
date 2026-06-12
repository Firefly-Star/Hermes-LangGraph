"""交互式全工作流调试 — 每步 call_agent 等用户输入回复。"""
import os, sys, json, tempfile

_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agent_runtime import AgentRuntime
from test.conftest import InteractiveClient

# ── Patch 文件 I/O — 交互调试只关注对话流程 ──
import workflow.utils as wf_utils
import workflow.phase1 as wf_phase1
import workflow.phase2 as wf_phase2
import workflow.phase3 as wf_phase3
import workflow.phase4 as wf_phase4
import workflow.subgraphs.master_flush as wf_flush

_ALL_MODS = [wf_utils, wf_phase1, wf_phase2, wf_phase3, wf_phase4, wf_flush]


def _patch(name, func):
    for mod in _ALL_MODS:
        if hasattr(mod, name):
            setattr(mod, name, func)


# ── ensure_write_file: 创建文件并返回 True ──
def _mock_ensure(runtime, receiver, conv, file_path, max_retry=None):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not (os.path.exists(file_path) and os.path.getsize(file_path) > 0):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("（交互式调试 — mock 文件）\n")
    return True


_patch("ensure_write_file", _mock_ensure)

# ── read_letter: 文件不存在时自动创建 ──
_orig_read_letter = wf_utils.read_letter


def _mock_read_letter(runtime, receiver, conv, letter_path, task, keep=False):
    paths = [letter_path] if isinstance(letter_path, str) else list(letter_path)
    for p in paths:
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("（交互式调试 — mock 信件）\n")
    return _orig_read_letter(runtime, receiver, conv, letter_path, task, keep=keep)


_patch("read_letter", _mock_read_letter)

# ── read_and_write_letter: 输入文件不存在时自动创建 ──
_orig_read_write = wf_utils.read_and_write_letter


def _mock_read_write(runtime, receiver, conv, inputletter_path, outputletter_path,
                      title, instruction, task, keep=False):
    paths = [inputletter_path] if isinstance(inputletter_path, str) else list(inputletter_path)
    for p in paths:
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("（交互式调试 — mock 输入信件）\n")
    return _orig_read_write(runtime, receiver, conv, inputletter_path, outputletter_path,
                             title, instruction, task, keep=keep)


_patch("read_and_write_letter", _mock_read_write)

# ── write_criteria: 直接写文件，跳过 call_agent ──
def _mock_write_criteria(runtime, master_conv, title, file_path, prompt, context_key):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n（交互式调试 — mock 审核标准）\n")
    runtime.context.set_ctx(f"{context_key}_path", file_path)
    runtime.msg.ok(f"审核标准已写入 {file_path}")
    runtime.logger.log_event("criteria_defined", detail=f"{title}——已写入 {file_path}")


_patch("write_criteria", _mock_write_criteria)

# ── 生成临时测试配置 ──
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, ".agent_runtime")
cfg = {
    "paths": {
        "runtime_dir": p,
        "workspace": os.path.join(tmp, "workspace"),
        "handoffs": os.path.join(p, "handoffs"),
        "phases": os.path.join(p, "phases"),
        "artifacts": os.path.join(p, "artifacts"),
        "checkpoint": os.path.join(p, "checkpoint.json"),
    },
    "fail_rollback_threshold": 3,
    "fail_escalation_threshold": 5,
}
config_path = os.path.join(tmp, "test_config.json")
json.dump(cfg, open(config_path, "w", encoding="utf-8"))

# ── 交互式 client ──
client = InteractiveClient()
rt = AgentRuntime(config_path=config_path, conversation_client=client)

# ── 构建完整图 ──
from workflow.graph import build_graph, _init_state

app = build_graph(rt)
state = _init_state()
stream_config = {"configurable": {"thread_id": "workflow-1"}}

# ── 运行 ──
print("=" * 60)
print("  交互式工作流调试")
print("  每遇到 [交互输入] 请手动输入 agent 的回复")
print("  文件 I/O 已自动 patch，专注测试对话逻辑即可")
print("=" * 60)

try:
    for event in app.stream(state, stream_config):
        for node_name, node_state in event.items():
            phase = node_state.get("phase", "?") if node_state else "?"
            print(f"\n  → [{node_name}] phase={phase}")
except KeyboardInterrupt:
    print("\n  用户中断")
