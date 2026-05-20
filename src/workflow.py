"""
Workflow — AI Coding 工作流框架的 LangGraph 编排
"""
import os, sys, time, json
from typing import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_runtime as ap


def _conv_name(base: str) -> str:
    """生成带时间戳和工作目录的对话名，避免跨运行冲突。"""
    ws = os.path.basename(os.getcwd())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{base}-{ws}-{ts}"


AGENT_CONFIGS = {
    "master": {"profile": "cg", "port": 8642},
}


class WorkflowState(TypedDict):
    phase: str


def call_agent(runtime, agent: str, conversation: str, prompt: str,
               timeout: int = 180, stream: bool = True) -> str:
    """调用 agent 并返回文本。stream=True 时逐块打印输出。失败抛异常。"""
    print(f"  → 调 {agent}/{conversation}... ", end="", flush=True)
    t0 = time.time()

    if stream:
        print(flush=True)
        text_parts = []
        def on_chunk(chunk):
            print(chunk, end="", flush=True)
            text_parts.append(chunk)
        result = runtime.conversations.call(
            agent, conversation, prompt, timeout=timeout, stream_callback=on_chunk)
        print()
    else:
        result = runtime.conversations.call(agent, conversation, prompt, timeout=timeout)

    elapsed = time.time() - t0
    if not result.success:
        print(f"  [FAIL] ({elapsed:.0f}s)")
        raise RuntimeError(f"[{agent}/{conversation}] 调用失败: {result.error}")

    tool_info = ""
    if result.raw_data:
        tools = [i["name"] for i in result.raw_data.get("output", []) if i.get("type") == "function_call"]
        if tools:
            tool_info = f" tools:[{','.join(tools)}]"

    print(f"  ✓ ({elapsed:.0f}s, {result.input_tokens + result.output_tokens} tokens{tool_info})")
    return result.text


def setup_runtime(config_path: str = None) -> ap.AgentRuntime:
    """初始化 AgentRuntime，启动 Master Gateway。"""
    runtime = ap.AgentRuntime(config_path)

    # 将工作流级配置项写入 Config
    if config_path and os.path.exists(config_path):
        cfg = json.load(open(config_path, "r", encoding="utf-8"))
        for key in ("call_timeout", "max_retry", "max_plan_loop", "max_bug_loop",
                     "input_end_word"):
            if key in cfg:
                runtime.config.set(key, cfg[key])

    for name, cfg in AGENT_CONFIGS.items():
        result = runtime.agents.create_agent(name, cfg["profile"], cfg["port"])
        if not result.success and "已存在" not in result.message:
            print(f"  [WARN] {name} 注册: {result.message}")
        if result.status != "running":
            sr = runtime.agents.run_gateway(name)
            if not sr.success:
                print(f"  [WARN] {name} gateway: {sr.message}")
            else:
                print(f"  {name} gateway 就绪")

    runtime.logger.log_event("workflow_started")
    return runtime


def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 交互式需求澄清。"""
    runtime = getattr(pre_flight_clarify, "_runtime", None)
    conv = _conv_name("clarify")
    end_word = runtime.config.get("input_end_word") or None

    runtime.logger.log_event("phase_started", detail="需求澄清")

    runtime.conversations.init_conversation(
        "master", conv,
        "你现在是项目的 Master 编排者。请等待用户描述项目需求。\n"
        "当用户描述完后，请总结你对项目的理解。\n"
        "如果还有不清楚的地方，用 '## 疑问' 标题列出问题；\n"
        "如果全部清楚了，用 '## 确认' 标题确认可以开始。"
    )

    def _close_clarify(reason: str):
        """通知 Master 澄清结束，然后写 context。"""
        call_agent(runtime, "master", conv,
                   "用户已确认需求结束。如有遗留疑问，后续阶段中再提出。")
        bg = f"（{reason}）"
        runtime.logger.log_event("clarification_done", detail=reason)
        runtime.context.set_bg("clarification", bg)

    round_num = 0
    while True:
        round_num += 1
        hint = "请描述你的需求" if round_num == 1 \
            else "请回答 Master 的疑问，或输入 CONFIRMED 直接开始："

        cp = runtime.checkpoint.wait(
            f"需求澄清 — 第 {round_num} 轮", hint,
            prompt="输入内容后按 Enter：", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            continue

        if user_input.upper() == "CONFIRMED":
            _close_clarify("用户直接确认")
            return {"phase": "done"}

        reply = call_agent(runtime, "master", conv, user_input)

        if "## 确认" in reply:
            runtime.logger.log_event("clarification_done", detail="Master 确认理解")
            runtime.context.set_bg("clarification", reply)
            return {"phase": "done"}

    return {"phase": "done"}


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    pre_flight_clarify._runtime = runtime

    graph = StateGraph(WorkflowState)
    graph.add_node("pre_flight_clarify", pre_flight_clarify)
    graph.set_entry_point("pre_flight_clarify")
    graph.add_edge("pre_flight_clarify", END)

    return graph.compile(checkpointer=MemorySaver())


def _init_state() -> WorkflowState:
    return {"phase": "pre_flight"}


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="AI Coding 工作流框架")
    p.add_argument("--config", default=None,
                   help="配置文件路径（默认: 项目根目录/runtime_config.json）")
    return p.parse_args()


def main():
    print("=" * 60)
    print("  AI Coding 工作流框架 — 骨架")
    print("=" * 60)

    args = parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config or os.path.join(script_dir, "..", "runtime_config.json")

    print("\n[1/2] 初始化 AgentRuntime...")
    runtime = setup_runtime(config_path)

    print("\n[2/2] 构建并运行 LangGraph...")
    app = build_graph(runtime)
    state = _init_state()
    config = {"configurable": {"thread_id": "workflow-1"}}

    for event in app.stream(state, config):
        for node_name, node_state in event.items():
            if node_state is None:
                print(f"  [{node_name}] 完成")
                continue
            print(f"  [{node_name}] phase={node_state.get('phase', '?')}")

    print("\n✅ 框架就绪")


if __name__ == "__main__":
    main()
