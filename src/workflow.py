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
    "judge":  {"profile": "cg", "port": 8642},
    "pm":     {"profile": "pm", "port": 8643},
}


def role_aware_prompt(role: str, upstream: str, upstream_doc: str,
                      deliverable: str, downstream: str,
                      downstream_needs: str) -> str:
    """角色上下文感知模板：让专业 agent 理解上下游关系。"""
    return (
        f"## 角色认知\n"
        f"你的角色是 **{role}**。\n\n"
        f"## 上游输入\n"
        f"上游角色 **{upstream}** 提供了以下上下文：\n"
        f"{upstream_doc}\n\n"
        f"## 你的任务\n"
        f"你需要产出 **{deliverable}**。\n\n"
        f"## 下游需求\n"
        f"下游角色 **{downstream}** 将使用你的产出做后续工作。\n"
        f"他们需要从你的产出中获得：{downstream_needs}\n\n"
        f"## 要求\n"
        f"确保你的产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
        f"请具体、可操作，避免空泛描述。"
    )

MASTER_SYSTEM_PROMPT = """
## 角色认知
你是项目的 **Master 分析师**。你只负责三件事：
1. **需求澄清阶段** — 与用户直接对话，理解需求
2. **审核与分析** — 被 workflow 引擎问到的时候，写审核标准、做判断
3. **决策输出** — 被要求时，将澄清结果整理为正式文档

## 你不做什么
你不直接调用或委托其他 agent。子 agent 的调度、什么时候调谁、传什么指令，
除了明确指明以外，你不直接上手完成任何东西的产出。
全部由 workflow 引擎处理。

## 工作流阶段（供你了解全局，但你不负责驱动）
1. 需求澄清 ← 你直接与用户对话
2. PM 出方案（由 workflow 调 pm agent）
3. Dev 出详细设计 + 实现（由 workflow 调 dev agent）
4. QA 测试（由 workflow 调 qa agent）
5. 交付

## 核心原则
1. **Review 不可跳过** — 每个专业 agent 的输出必须审查，再小也不能省
2. **执行与验证分离** — 写代码的 agent 不能自己验证自己
3. **每步可回滚** — 执行前提醒做 git commit
4. **约束反复注入** — 核心规则在每次委派时重述
5. **UI 验证必须自动化** — 有 UI 就须有 Playwright 脚本

## 当前阶段的工作方式
- 当你不清楚用户需求时，列出你的疑问，用 `## 疑问` 标题
- 当全部理解后，正常总结确认即可，无需特殊标记
"""


class WorkflowState(TypedDict):
    phase: str


def call_agent(runtime, agent: str, conversation: str, prompt: str,
               timeout: int = 180, stream: bool = True) -> str:
    """调用 agent 并返回文本。stream=True 时逐块打印输出。失败抛异常。"""
    print(f"  → 调 {agent}/{conversation}... ", end="", flush=True)
    t0 = time.time()

    print(f"\n──── Request ────\n{prompt}\n──── Response ────")

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


def handoff(file_path: str) -> str:
    """读取上游 agent 通过文件传递的 handoff 内容。"""
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


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

    started_ports = set()
    for name, cfg in AGENT_CONFIGS.items():
        result = runtime.agents.create_agent(name, cfg["profile"], cfg["port"])
        if not result.success and "已存在" not in result.message:
            print(f"  [WARN] {name} 注册: {result.message}")
        port = cfg["port"]
        if port not in started_ports:
            if result.status != "running":
                sr = runtime.agents.run_gateway(name)
                if not sr.success:
                    print(f"  [WARN] {name} gateway: {sr.message}")
                else:
                    print(f"  {name} gateway 就绪")
            started_ports.add(port)

    runtime.logger.log_event("workflow_started")

    # 持久化 Master system prompt，供后续 flush 重建对话时注入
    runtime.context.set_bg("master_principles", MASTER_SYSTEM_PROMPT.strip())

    return runtime


def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 交互式需求澄清。"""
    runtime = getattr(pre_flight_clarify, "_runtime", None)
    conv = _conv_name("clarify")
    end_word = runtime.config.get("input_end_word") or None

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_context_path = os.path.join(root, ".agent_runtime", "project_context.md")

    runtime.logger.log_event("phase_started", detail="需求澄清")
    runtime.conversations.init_conversation("master", conv, MASTER_SYSTEM_PROMPT.strip())

    def _judge_clarify(reply: str) -> str:
        """判读 Master 回复是已确认还是有疑问。"""
        judge_prompt = (
            "你是一个流程裁判。以下是 Master 的回复。\n\n"
            f"## Master 的回复\n{reply}\n\n"
            "判定当前状态是以下哪一种：\n"
            "A. 需求已明确，可以进入下一阶段\n"
            "B. Master 有疑问需要用户继续回答\n\n"
            "回复 A 或 B 即可，不要输出其他内容。"
        )
        result = call_agent(runtime, "judge", _conv_name("judge-clarify"), judge_prompt)
        return result.strip()

    def _close_clarify(reason: str):
        """通知 Master 写 project_context.md。"""
        call_agent(runtime, "master", conv,
                   "需求澄清阶段已结束。\n"
                   f"请将所有已确认的决策整理为完整的项目顶层决策记录，"
                   f"写入文件 {project_context_path}。\n"
                   "需包含：用户角色与权限、技术栈、功能范围、MVP 边界、"
                   "约束条件、页面结构等所有被确认过的信息。\n"
                   "勿包含对话中的口头语，只写正式的文档内容。\n"
                   "后续所有 agent 将通过这个文件了解项目。")
        runtime.logger.log_event("clarification_done", detail=reason)
        runtime.context.set_bg("project_context_path", project_context_path)
        runtime.context.set_bg("clarification", f"项目顶层决策文件：{project_context_path}")

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

        reply = call_agent(runtime, "master", conv, 
                           f'''{user_input}
                           不要产出任何东西，说出你的理解，如果有疑问就问就行。'''
                           )

        # 确认子循环：judge 判读 → 用户确认 → 修正 → 再 judge
        while True:
            judge = _judge_clarify(reply)
            if judge != "A":
                break  # Master 仍有疑问，回到外层让用户回答

            cp = runtime.checkpoint.wait(
                f"需求澄清 — 第 {round_num} 轮 (确认)",
                "Master 已确认理解需求。认可的话输入 CONFIRMED 进入下一阶段；不认可则说明哪里不对：",
                prompt="输入内容后按 Enter：", end_word=end_word,
            )
            confirm_input = cp.message.strip()
            if confirm_input.upper() == "CONFIRMED":
                _close_clarify("用户确认 Master 理解正确")
                return {"phase": "done"}

            round_num += 1
            reply = call_agent(runtime, "master", conv,
                              f"用户认为你的理解有偏差，请重新理解需求：\n{confirm_input}")
            # 继续子循环：再 judge → 再确认


def pm_write_doc(state: WorkflowState) -> dict:
    """Phase 1: PM 产出 PRD.md + prototype.html。"""
    runtime = getattr(pm_write_doc, "_runtime", None)
    conv = _conv_name("pm-doc")

    # 让 Master 针对 PM 写 handoff 委托信
    project_context_path = runtime.context.get_bg("project_context_path")
    project_context = handoff(project_context_path)
    if not project_context:
        raise RuntimeError(f"project_context.md 不存在：{project_context_path}")

    master_conv = _conv_name("master-to-pm")
    handoff_dir = os.path.join(os.path.dirname(project_context_path), "handoffs")
    master_to_pm_path = os.path.join(handoff_dir, "master_to_pm.md")
    os.makedirs(handoff_dir, exist_ok=True)

    runtime.conversations.init_conversation("master", master_conv,
                                            MASTER_SYSTEM_PROMPT.strip())
    call_agent(runtime, "master", master_conv,
               f"以下是项目顶层决策记录：\n{project_context}\n\n"
               f"现在请以 Master 的身份写一封给 PM agent 的信，"
               f"写入文件 {master_to_pm_path}。\n"
               "信件的目的是让 PM 理解项目上下文并确认信息是否足够，而不是直接派活。\n\n"
               "信件需包含：\n"
               "1. 开宗明义：这是 Master 给 PM 的信\n"
               "2. 项目概况和核心需求（简要描述即可）\n"
               f"3. 告知 PM 详细内容在项目顶层决策文件中，路径：{project_context_path}，让 PM 自行阅读\n"
               "4. 要求 PM：先汇报你对项目的理解和疑问，得到 Master 明确许可后才能动手产出\n"
               "5. 强调：在确认之前，不得开始写 PRD 或原型\n\n"
               "信件要有 Master 的口吻，是上级对下级的沟通与任务委派。")

    # 读取 handoff 信件（一次性，用完即删）
    upstream_doc = handoff(master_to_pm_path)
    if not upstream_doc:
        raise RuntimeError(f"handoff 文件不存在：{master_to_pm_path}")
    os.remove(master_to_pm_path)

    runtime.logger.log_event("phase_started", detail="PM 出方案")
    print(f"\n  ── PM 出方案 ──")

    # Call 1 — PM 对齐理解（不产出任何文档）
    call_agent(runtime, "pm", conv, role_aware_prompt(
        role="PM",
        upstream="Master",
        upstream_doc=upstream_doc,
        deliverable="对项目需求的理解汇报和疑问清单",
        downstream="Master",
        downstream_needs="确认 PM 正确理解了项目范围和目标，如果有疑问需要解答后再开始工作",
    ) + "\n\n阅读 Master 给你的信以及项目顶层决策文件。请汇报你对项目的理解，并列出你的疑问。"
      "在 Master 明确许可之前，不要开始写 PRD 或原型。")

    # Call 2 — PM 写 PRD
    prd_text = call_agent(runtime, "pm", conv, role_aware_prompt(
        role="PM",
        upstream="Master",
        upstream_doc=upstream_doc,
        deliverable="PRD.md 需求文档",
        downstream="Dev（开发工程师）",
        downstream_needs="清晰的功能列表、验收标准、页面结构描述、MVP 边界定义",
    ) + "\n\n请输出 PRD.md 的完整内容，包含：项目概述、功能需求、MVP 范围、页面结构、验收标准。")

    # Call 3 — PM 写 prototype
    prototype_text = call_agent(runtime, "pm", conv, role_aware_prompt(
        role="PM",
        upstream="Master",
        upstream_doc=upstream_doc,
        deliverable="prototype.html 静态原型文件",
        downstream="Dev（开发工程师）",
        downstream_needs="可直接在浏览器中打开运行的 HTML 原型，展示页面布局和核心交互",
    ) + "\n\n请基于 PRD 生成一个完整的 prototype.html。要求：单文件自包含（CSS/JS 内嵌），可双击在浏览器中直接打开，包含核心交互和布局。")

    # 写文件到 test/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "..", "test")
    os.makedirs(output_dir, exist_ok=True)

    prd_path = os.path.join(output_dir, "PRD.md")
    with open(prd_path, "w", encoding="utf-8") as f:
        f.write(prd_text)
    print(f"  ✓ 已写入 {prd_path}")

    proto_path = os.path.join(output_dir, "prototype.html")
    with open(proto_path, "w", encoding="utf-8") as f:
        f.write(prototype_text)
    print(f"  ✓ 已写入 {proto_path}")

    # 持久化到 context
    runtime.context.set_ctx("pm_prd", prd_text)
    runtime.context.set_ctx("pm_prototype", prototype_text)
    runtime.context.set_phase_node(["PM 出方案"], "done")

    runtime.logger.log_event("phase_completed", detail="PM 方案完成")
    return {"phase": "done"}


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    pre_flight_clarify._runtime = runtime
    pm_write_doc._runtime = runtime

    graph = StateGraph(WorkflowState)
    graph.add_node("pre_flight_clarify", pre_flight_clarify)
    graph.add_node("pm_write_doc", pm_write_doc)
    graph.set_entry_point("pre_flight_clarify")
    graph.add_edge("pre_flight_clarify", "pm_write_doc")
    graph.add_edge("pm_write_doc", END)

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
