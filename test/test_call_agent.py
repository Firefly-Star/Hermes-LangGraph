"""
独立测试脚本 — 直接调 call_agent 看流式输出 + 工具调用展示。
用法：
  python test/test_call_agent.py <agent> [prompt]
  python test/test_call_agent.py pm "用 write_file 写一个 hello.txt"
  python test/test_call_agent.py master "你好"
"""
import os, sys, json, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import agent_runtime as ap


def setup_runtime():
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime_config.json")
    runtime = ap.AgentRuntime(config_path)
    for name, profile, port in [("master", "cg", 8642), ("pm", "pm", 8643)]:
        result = runtime.agents.create_agent(name, profile, port)
        if not result.success and "已存在" not in result.message:
            print(f"  [WARN] {name}: {result.message}")
    return runtime


def call_agent(runtime, agent, conversation, prompt, timeout=120):
    """和 workflow.py 中一样的 call_agent，方便独立测试。"""
    print(f"\n──── Request: {agent}/{conversation} ────")
    print(prompt)
    print(f"──── {agent}'s Response ────", flush=True)

    text_parts = []
    tool_names = []

    def on_tool(name, args):
        tool_names.append(name)
        print(f"\n  ── TOOL {name} ──")
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"     {k}: {v}", flush=True)

    def on_chunk(chunk):
        print(chunk, end="", flush=True)
        text_parts.append(chunk)

    t0 = time.time()
    result = runtime.conversations.call(
        agent, conversation, prompt, timeout=timeout,
        stream_callback=on_chunk, tool_callback=on_tool,
    )
    print()

    if not result.success:
        print(f"\n  [FAIL] ({time.time()-t0:.0f}s): {result.error}")
        return ""

    # 统计工具调用
    tool_names = []
    if result.raw_data:
        for item in result.raw_data.get("output", []):
            if item.get("type") == "function_call":
                tool_names.append(item.get("name", ""))

    elapsed = time.time() - t0
    tokens = result.input_tokens + result.output_tokens
    tool_info = f" tools:[{','.join(tool_names)}]" if tool_names else ""
    print(f"  ✓ ({elapsed:.0f}s, {tokens} tokens{tool_info})")
    return result.text


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    agent = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "你好，请简单回复。"
    conv = f"test-{int(time.time())}"

    runtime = setup_runtime()
    call_agent(runtime, agent, conv, prompt)
