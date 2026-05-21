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


def _judge_clarify(runtime, reply: str) -> str:
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


def _clarify_loop(runtime, conv, first_hint: str, on_done):
    """通用澄清循环。用户↔Master↔judge↔确认，直到 CONFIRMED。"""
    end_word = runtime.config.get("input_end_word") or None

    round_num = 0
    while True:
        round_num += 1
        hint = first_hint if round_num == 1 \
            else "请回答 Master 的疑问，或输入 CONFIRMED 直接结束："

        cp = runtime.checkpoint.wait(
            f"需求澄清 — 第 {round_num} 轮", hint,
            prompt="输入内容后按 Enter：", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            continue

        if user_input.upper() == "CONFIRMED":
            on_done("用户直接确认")
            return

        reply = call_agent(runtime, "master", conv,
                           f"{user_input}\n不要产出任何东西，说出你的理解，如果有疑问就问。")

        # 确认子循环：judge 判读 → 用户确认或纠正
        while True:
            judge = _judge_clarify(runtime, reply)
            if judge != "A":
                break

            cp = runtime.checkpoint.wait(
                f"需求澄清 — 第 {round_num} 轮 (确认)",
                "Master 已确认理解需求。认可的话输入 CONFIRMED 进入下一阶段；"
                "不认可则说明哪里不对：",
                prompt="输入内容后按 Enter：", end_word=end_word,
            )
            confirm_input = cp.message.strip()
            if confirm_input.upper() == "CONFIRMED":
                on_done("用户确认 Master 理解正确")
                return

            round_num += 1
            reply = call_agent(runtime, "master", conv,
                              f"用户认为你的理解有偏差，请重新理解需求：\n{confirm_input}")


class WorkflowState(TypedDict):
    phase: str
    judge_result: str


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


def _letter_path(name: str) -> str:
    """生成 handoff 信件路径。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    handoff_dir = os.path.join(root, ".agent_runtime", "handoffs")
    os.makedirs(handoff_dir, exist_ok=True)
    ws = os.path.basename(os.getcwd())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(handoff_dir, f"{name}-{ws}-{ts}.md")


def write_letter(runtime, sender, conv, letter_path, title, prompt):
    """sender 在 conv 对话中写一封信到 letter_path。"""
    call_agent(runtime, sender, conv,
               f"请以 **{sender}** 的身份写一封信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{prompt}\n\n"
               f"请将信件完整写入文件：{letter_path}")
    if not os.path.exists(letter_path):
        raise RuntimeError(f"{sender} 未生成信件：{letter_path}")


def read_letter(runtime, receiver, conv, letter_path, task):
    """receiver 读 letter_path 后执行 task。读完删信。返回 receiver 回复。"""
    if not os.path.exists(letter_path):
        raise RuntimeError(f"信件不存在：{letter_path}")
    with open(letter_path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(letter_path)
    return call_agent(runtime, receiver, conv,
                      f"请阅读以下信件，然后{task}\n\n## 信件内容\n{content}")


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

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_context_path = os.path.join(root, ".agent_runtime", "project_context.md")

    runtime.logger.log_event("phase_started", detail="需求澄清")
    runtime.conversations.init_conversation("master", conv, MASTER_SYSTEM_PROMPT.strip())
    runtime.context.set_ctx("conv_clarify", conv)

    def _close(reason: str):
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

    _clarify_loop(runtime, conv, "请描述你的需求", _close)
    return {"phase": "done"}


def pm_handoff(state: WorkflowState) -> dict:
    """Phase 1a: Master 写 handoff 信给 PM。"""
    runtime = getattr(pm_handoff, "_runtime", None)

    project_context_path = runtime.context.get_bg("project_context_path")
    if not project_context_path or not os.path.exists(project_context_path):
        raise RuntimeError(f"project_context.md 不存在：{project_context_path}")

    master_conv = _conv_name("master-to-pm")
    runtime.conversations.init_conversation("master", master_conv,
                                            MASTER_SYSTEM_PROMPT.strip())

    letter_path = _letter_path("master-to-pm")
    write_letter(runtime, "master", master_conv, letter_path,
                 "Master 给 PM 的信",
                 f"介绍项目上下文。信件需包含：\n"
                 "1. 开宗明义：这是 Master 给 PM 的信\n"
                 "2. 项目概况和核心需求（简要描述即可）\n"
                 f"3. 告知 PM 详细内容在项目顶层决策文件中，路径：{project_context_path}，让 PM 自行阅读\n"
                 "4. 要求 PM：先汇报你对项目的理解和疑问，得到 Master 明确许可后才能动手产出\n"
                 "5. 强调：在确认之前，不得开始写 PRD 或原型\n\n"
                 "信件要有 Master 的口吻，是上级对下级的沟通与任务委派。")

    runtime.context.set_ctx("pm_letter_path", letter_path)
    print(f"\n  ── Master 给 PM 的信件已就绪 ──")
    return {"phase": "pm_handoff_done"}


def pm_align(state: WorkflowState) -> dict:
    """Phase 1b: PM 读信，汇报理解 + 列出疑问。

    首次调用时读 handoff 信；循环中 Master 先写信，PM 再读。"""
    runtime = getattr(pm_align, "_runtime", None)

    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = _conv_name("pm-align")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="PM 对齐理解")
    print(f"\n  ── PM 对齐理解 ──")

    master_reply = runtime.context.get_ctx("master_reply")
    if master_reply:
        # 循环：Master 先写正式答复信，PM 再读
        conv_clarify = runtime.context.get_ctx("conv_clarify")
        master_letter_path = _letter_path("master-to-pm-reply")
        write_letter(runtime, "master", conv_clarify, master_letter_path,
                     "Master 给 PM 的答复",
                     "你在刚才的分析中已核对了 PM 的理解并回答了疑问。"
                     "请将你的结论写成正式信件给 PM。\n"
                     "逐一核对 PM 的理解是否正确，回答所有疑问。"
                     "如果 PM 的理解完全正确且无疑问，也请告知 PM。")
        reply = read_letter(runtime, "pm", pm_conv, master_letter_path,
                            "仔细阅读 Master 的答复，确认是否清楚所有疑问。"
                            "如有新的疑问也一并提出。"
                            "在 Master 明确许可之前，不得开始写 PRD 或原型。")
    else:
        # 首次：读 handoff 信
        letter_path = runtime.context.get_ctx("pm_letter_path")
        if not letter_path:
            raise RuntimeError("没有 handoff 信件路径")
        reply = read_letter(runtime, "pm", pm_conv, letter_path,
                            "写一封回信汇报你对项目的理解和疑问。"
                            "在 Master 明确许可之前，不得开始写 PRD 或原型。")

    runtime.context.set_ctx("pm_reply", reply)
    return {"phase": "pm_align_done"}


def master_reply_pm(state: WorkflowState) -> dict:
    """Master 回答 PM 的疑问，复用 clarify conversation。"""
    runtime = getattr(master_reply_pm, "_runtime", None)
    pm_reply = runtime.context.get_ctx("pm_reply")
    conv_clarify = runtime.context.get_ctx("conv_clarify")

    if not conv_clarify:
        raise RuntimeError("clarify conversation 不存在")

    prompt = (
        f"这是 PM 对项目的理解和疑问：\n{pm_reply}\n\n"
        "请逐一检查以下内容：\n"
        "1. PM 的理解是否正确？如有误，逐一指出\n"
        "2. PM 的疑问中，你能回答的全部回答\n"
        "3. 如果遇到你无从判定的问题（涉及顶层决策、技术选型、使用场景等），"
        "不要猜测，明确写出需要向用户确认的具体问题\n\n"
        "你的回复中需明确区分两部分：\n"
        "- 你对 PM 的答复/纠正\n"
        "- 需要向用户确认的问题（如无则说'无需向用户提问'）"
    )

    print(f"\n  ── Master 回复 PM ──")
    reply = call_agent(runtime, "master", conv_clarify, prompt)

    runtime.context.set_ctx("master_reply", reply)
    return {"phase": "master_reply_done"}


def judge_master_reply(state: WorkflowState) -> dict:
    """判读 Master 能否独立回答 PM，还是需要问用户。"""
    runtime = getattr(judge_master_reply, "_runtime", None)
    master_reply = runtime.context.get_ctx("master_reply")

    print("  ── judge: Master 回复 ──")
    judge_prompt = (
        "你是一个流程裁判。以下是 Master 的回复。\n\n"
        f"## Master 的回复\n{master_reply}\n\n"
        "判定当前状态是以下哪一种：\n"
        "A. Master 确认 PM 理解 100% 正确，无需再问用户任何问题且无需纠正 PM 的任何错误且无需回复 PM 的任何问题 → 进入下一阶段\n"
        "B. Master 有对 PM 的答复或对 PM 指出的问题，需要转发给 PM 继续确认 → 回 PM\n"
        "C. Master 有无法判定的问题，需要向用户确认\n\n"
        "回复 A / B / C 即可，不要输出其他内容。"
    )
    result = call_agent(runtime, "judge", _conv_name("judge-master-reply"), judge_prompt)
    return {"judge_result": result.strip()}


def route_master_reply(state: WorkflowState) -> str:
    r = state.get("judge_result", "")
    if r.startswith("A"):
        return "pm_write_doc"
    elif r.startswith("B"):
        return "pm_align"
    return "clarify_inject"


def clarify_inject(state: WorkflowState) -> dict:
    """向用户提问 Master 无法判定的问题，更新 project_context.md。"""
    runtime = getattr(clarify_inject, "_runtime", None)
    conv_clarify = runtime.context.get_ctx("conv_clarify")
    master_reply = runtime.context.get_ctx("master_reply")

    print(f"\n  ── Master 需要向用户确认 ──\n{master_reply}")

    def _close(reason: str):
        project_context_path = runtime.context.get_bg("project_context_path")
        call_agent(runtime, "master", conv_clarify,
                   f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{project_context_path}")
        runtime.logger.log_event("clarification_done", detail=reason)

    _clarify_loop(runtime, conv_clarify, "请回答 Master 的疑问", _close)
    return {"phase": "clarify_done"}


def pm_write_doc(state: WorkflowState) -> dict:
    """Phase 1e: Master 写信指令 → PM 产出 PRD.md + prototype.html。"""
    runtime = getattr(pm_write_doc, "_runtime", None)
    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = _conv_name("pm-doc")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="PM 出方案")
    print(f"\n  ── PM 出方案 ──")

    conv_clarify = runtime.context.get_ctx("conv_clarify")
    if not conv_clarify:
        raise RuntimeError("clarify conversation 不存在")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "..", "test")
    os.makedirs(output_dir, exist_ok=True)

    # Call 1 — Master 写信要求 PRD，PM 直接写入 prd_path
    prd_path = os.path.join(output_dir, "PRD.md")
    prd_letter = _letter_path("master-prd")
    write_letter(runtime, "master", conv_clarify, prd_letter,
                 "PRD 编写说明",
                 "请以 Master 的身份给 PM 写信，要求 PM 输出 PRD.md 并写入指定文件。\n"
                 "需包含：项目概述、功能需求、MVP 范围、页面结构、验收标准。")
    read_letter(runtime, "pm", pm_conv, prd_letter,
                f"按信中的要求编写 PRD.md，写入文件 {prd_path}。")

    # Call 2 — Master 写信要求原型，PM 直接写入 proto_path
    proto_path = os.path.join(output_dir, "prototype.html")
    proto_letter = _letter_path("master-prototype")
    write_letter(runtime, "master", conv_clarify, proto_letter,
                 "原型编写说明",
                 "请以 Master 的身份给 PM 写信，要求基于 PRD 产出 prototype.html 并写入指定文件。\n"
                 "单文件自包含（CSS/JS 内嵌），可双击在浏览器中直接打开，包含核心交互和布局。")
    read_letter(runtime, "pm", pm_conv, proto_letter,
                f"按信中要求编写 prototype.html，写入文件 {proto_path}。")

    print(f"  ✓ {prd_path}")
    print(f"  ✓ {proto_path}")

    runtime.context.set_phase_node(["PM 出方案"], "done")
    runtime.logger.log_event("phase_completed", detail="PM 方案完成")
    return {"phase": "done"}


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    for f in [pre_flight_clarify, pm_handoff, pm_align,
              master_reply_pm, judge_master_reply, clarify_inject,
              pm_write_doc, route_master_reply]:
        f._runtime = runtime

    graph = StateGraph(WorkflowState)
    graph.add_node("pre_flight_clarify", pre_flight_clarify)
    graph.add_node("pm_handoff", pm_handoff)
    graph.add_node("pm_align", pm_align)
    graph.add_node("master_reply_pm", master_reply_pm)
    graph.add_node("judge_master_reply", judge_master_reply)
    graph.add_node("clarify_inject", clarify_inject)
    graph.add_node("pm_write_doc", pm_write_doc)

    graph.set_entry_point("pre_flight_clarify")
    graph.add_edge("pre_flight_clarify", "pm_handoff")
    graph.add_edge("pm_handoff", "pm_align")
    graph.add_edge("pm_align", "master_reply_pm")
    graph.add_edge("master_reply_pm", "judge_master_reply")
    graph.add_conditional_edges("judge_master_reply", route_master_reply, {
        "pm_write_doc": "pm_write_doc",
        "pm_align": "pm_align",
        "clarify_inject": "clarify_inject",
    })
    graph.add_edge("clarify_inject", "master_reply_pm")
    graph.add_edge("pm_write_doc", END)

    return graph.compile(checkpointer=MemorySaver())


def _init_state() -> WorkflowState:
    return {"phase": "pre_flight", "judge_result": ""}


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
            print(f"  [{node_name}] phase={node_state.get('phase', '?')}, "
                  f"judge={node_state.get('judge_result', '')[:20]}")

    print("\n✅ 框架就绪")


if __name__ == "__main__":
    main()
