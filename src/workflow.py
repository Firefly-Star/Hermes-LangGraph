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
    "dev":    {"profile": "dev", "port": 8644},
    "reviewer": {"profile": "cg", "port": 8642},
    "qa":     {"profile": "qa", "port": 8645},
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

## 工作文件夹
项目工作目录：{workspace}
产出路径规则：
- PM 产出审核标准：{workspace}/criteria-pm.md
- Dev 设计审核标准：{workspace}/criteria-design.md
- Dev 代码审核标准：{workspace}/criteria-code.md
- PM 的产出:      {workspace}/PM/
- Dev 的产出:     {workspace}/Dev/
- QA 的产出:   {workspace}/QA/

## Agent 间通信机制
- **不要直接在对话里引用其他 agent 的对话内容** — 其他 agent 看不到你的对话

## 核心原则
1. **Review 不可跳过** — 每个专业 agent 的输出必须审查，再小也不能省
2. **执行与验证分离** — 写代码的 agent 不能自己验证自己
3. **每步可回滚** — 执行前提醒做 git commit
4. **约束反复注入** — 核心规则在每次委派时重述
5. **UI 验证必须自动化** — 有 UI 就须有 Playwright 脚本

## 当前阶段的工作方式

### 需求澄清阶段
- 当用户提出需求后，逐条确认关键信息：功能范围、目标用户、技术约束、交付标准
- 如果信息不足，列出你的疑问并用 `## 疑问` 标题
- 当所有关键信息已明确时，输出需求总结，用 `## 已确认的需求` 标题
- 判断「需求已明确」的标准：功能边界清晰、MVP 范围可定义、验收标准可写

### 审核与分析阶段
- 当被要求写审核标准时，先理解上游需求，再针对性产出
- 输出需结构化：列出各维度标准（功能、体验、兼容性、一致性、逻辑自洽）
- 每项标准必须可验证（通过/不通过），不能写模糊描述

### 决策输出阶段
- 整理澄清结果为结构化文档
- 必须标明：来源（用户原话 vs 你的推断）
"""


def judge_reply(runtime, target_role: str, reply: str, options: list[str],
                tag: str = None) -> str:
    """通用判读函数。judge agent 对 target_role 的回复进行分类路由。返回选项字母。"""
    options_text = "\n".join(f"{opt}" for opt in options)
    keys = "/".join(opt.strip()[0] for opt in options if opt.strip())
    conv = _conv_name(tag or f"judge-{target_role.lower()}")
    prompt = (
        f"你是一个流程裁判。以下是 {target_role} 的回复。\n\n"
        f"## {target_role} 的回复\n{reply}\n\n"
        "判定当前状态是以下哪一种：\n"
        f"{options_text}\n\n"
        f"回复 {keys} 即可，不要输出其他内容。"
    )
    result = call_agent(runtime, "judge", conv, prompt)
    return result.strip()


def _clarify_loop(runtime, conv, title: str, first_hint: str, on_done):
    """通用澄清循环。用户↔Master↔judge↔确认。空输入（直接 EOF）视为确认。"""
    end_word = runtime.config.get("input_end_word") or None

    round_num = 0
    while True:
        round_num += 1
        hint = first_hint if round_num == 1 \
            else "请回答 Master 的疑问，或直接 EOF 结束："

        cp = runtime.checkpoint.wait(
            title, hint,
            prompt="输入内容后按 Enter：", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            on_done("用户直接确认")
            return

        reply = call_agent(runtime, "master", conv,
                           f"{user_input}\n不要产出任何东西，也不要修改任何文件，只需要说出你的理解，以及对有疑问的地方提出问题。")

        # 确认子循环：judge 判读 → 用户确认或纠正
        while True:
            judge_result = judge_reply(runtime, "Master", reply, [
                "A. 需求已明确，可以进入下一阶段",
                "B. Master 有疑问需要用户继续回答",
            ], "judge-clarify")
            if judge_result != "A":
                break

            cp = runtime.checkpoint.wait(
                f"{title} (确认)",
                "Master 已确认理解需求。认可的话直接 EOF 进入下一阶段；"
                "不认可则说明哪里不对：",
                prompt="输入内容后按 Enter：", end_word=end_word,
            )
            confirm_input = cp.message.strip()
            if not confirm_input:
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

    def on_tool(name, args):
        print(f"\n  ── TOOL {name} ──")
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"     {k}: {v}", flush=True)

    print(f"\n──── Request: {agent}/{conversation} ────\n{prompt}\n──── {agent}'s Response ────")

    if stream:
        print(flush=True)
        text_parts = []
        def on_chunk(chunk):
            try:
                print(chunk, end="", flush=True)
            except UnicodeEncodeError:
                enc = sys.stdout.encoding or "utf-8"
                sys.stdout.buffer.write(chunk.encode(enc, errors="replace"))
                sys.stdout.flush()
            text_parts.append(chunk)
        result = runtime.conversations.call(
            agent, conversation, prompt, timeout=timeout,
            stream_callback=on_chunk, tool_callback=on_tool)
        print()
    else:
        result = runtime.conversations.call(agent, conversation, prompt, timeout=timeout)

    elapsed = time.time() - t0
    if not result.success:
        print(f"  [FAIL] ({elapsed:.0f}s)")
        raise RuntimeError(f"[{agent}/{conversation}] 调用失败: {result.error}")

    # 统计工具调用（只用于 token 统计显示）
    tool_names = []
    if result.raw_data:
        for item in result.raw_data.get("output", []):
            if item.get("type") == "function_call":
                tool_names.append(item.get("name", ""))

    tool_info = f" tools:[{','.join(tool_names)}]" if tool_names else ""
    print(f"  ✓ ({elapsed:.0f}s, {result.input_tokens + result.output_tokens} tokens{tool_info})")
    return result.text


def _letter_path(runtime, name: str) -> str:
    """生成 handoff 信件路径。"""
    handoff_dir = os.path.join(runtime.runtime_dir, "handoffs")
    os.makedirs(handoff_dir, exist_ok=True)
    ws = os.path.basename(os.getcwd())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(handoff_dir, f"{name}-{ws}-{ts}.md")


def _ensure_write_file(runtime, receiver, conv, file_path, max_retry=2):
    """检查文件是否存在，不存在则提醒 agent 写入。"""
    for attempt in range(max_retry):
        if os.path.exists(file_path):
            return True
        call_agent(runtime, receiver, conv,
                   f"（提醒）文件尚未被创建：{file_path}\n\n"
                   "请使用 write_file 工具将你的回复内容完整写入该文件，不要只在对话中回复。")
    return os.path.exists(file_path)


def write_letter(runtime, sender, conv, letter_path, title, prompt):
    """sender 在 conv 对话中写一封信到 letter_path。"""
    call_agent(runtime, sender, conv,
               f"请以 **{sender}** 的身份写一封信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{prompt}\n\n"
               f"请将信件完整写入文件：{letter_path}")
    if not _ensure_write_file(runtime, sender, conv, letter_path):
        raise RuntimeError(f"{sender} 仍未生成信件：{letter_path}")


def _resolve_paths(path):
    """统一处理单个路径或多个路径，返回列表。"""
    return [path] if isinstance(path, str) else list(path)


def read_letter(runtime, receiver, conv, letter_path, task, keep=False):
    """receiver 读 letter_path（支持单路径或列表）后执行 task。默认读完删信。"""
    paths = _resolve_paths(letter_path)
    for p in paths:
        if not os.path.exists(p):
            raise RuntimeError(f"信件不存在：{p}")
    paths_text = "\n".join(f"- {p}" for p in paths)
    reply = call_agent(runtime, receiver, conv,
                       f"请阅读以下信件，然后{task}\n\n## 信件路径\n{paths_text}")
    if not keep:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)
    return reply


def read_and_write_letter(runtime, receiver, conv,
                          input_letter_path, output_letter_path,
                          title, instruction, task, keep=False):
    """读 input_letter（支持单路径或列表），让 receiver 按要求写回信。默认读完删信。"""
    paths = _resolve_paths(input_letter_path)
    for p in paths:
        if not os.path.exists(p):
            raise RuntimeError(f"信件不存在：{p}")
    paths_text = "\n".join(f"- {p}" for p in paths)
    call_agent(runtime, receiver, conv,
               f"请阅读以下信件，然后{task}\n\n"
               f"## 信件路径\n{paths_text}\n\n"
               f"## 回复方式\n"
               f"请以 **{receiver}** 的身份写一封回信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{instruction}\n\n"
               f"请将信件完整写入文件：{output_letter_path}")
    if not _ensure_write_file(runtime, receiver, conv, output_letter_path):
        raise RuntimeError(f"{receiver} 仍未生成回信：{output_letter_path}")
    if not keep:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)


def setup_runtime(config_path: str = None) -> ap.AgentRuntime:
    """初始化 AgentRuntime，启动 Master Gateway。"""
    runtime = ap.AgentRuntime(config_path)
    runtime.run_all(AGENT_CONFIGS)
    runtime.logger.log_event("workflow_started")

    # 持久化 Master system prompt，供后续 flush 重建对话时注入
    runtime.context.set_bg("master_principles", MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())

    return runtime


def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 交互式需求澄清。"""
    runtime = getattr(pre_flight_clarify, "_runtime", None)
    conv = _conv_name("master")

    print(f"\n{'='*50}\n  ==> Phase 0: 需求澄清\n{'='*50}")

    # 清理上一轮运行的 session context，避免残留 key 干扰
    for key in ["master_reply", "pm_reply_text", "pm_reply_path", "pm_letter_path",
                "pm_criteria", "pm_criteria_self_check", "pm_criteria_path",
                "review_result", "human_feedback", "pm_align_round",
                "dev_conv", "dev_letter_path", "dev_feedback_path"]:
        runtime.context.set_ctx(key, "")

    project_context_path = os.path.join(runtime.runtime_dir, "project_context.md")

    runtime.logger.log_event("phase_started", detail="需求澄清")
    runtime.conversations.init_conversation("master", conv, MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())
    runtime.context.set_ctx("master_conv", conv)

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

    _clarify_loop(runtime, conv, "== 需求澄清 ==", "请描述你的需求", _close)
    return {"phase": "done"}


def pm_handoff(state: WorkflowState) -> dict:
    """Phase 1a: Master 写 handoff 信给 PM。"""
    runtime = getattr(pm_handoff, "_runtime", None)

    print(f"\n{'='*60}\n  ==> Phase 1a: Master 写信给 PM\n{'='*60}")

    project_context_path = runtime.context.get_bg("project_context_path")
    if not project_context_path or not os.path.exists(project_context_path):
        raise RuntimeError(f"project_context.md 不存在：{project_context_path}")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    letter_path = _letter_path(runtime, "master-to-pm")
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

    round_num = int(runtime.context.get_ctx("pm_align_round") or 0)

    runtime.logger.log_event("phase_started", detail="PM 对齐理解")
    print(f"\n  ── PM 对齐理解（第 {round_num + 1} 轮）──")

    pm_reply_path = _letter_path(runtime, "pm-reply")
    if round_num > 0:
        # 循环：Master 先写正式答复信，PM 读信后写回信
        master_conv = runtime.context.get_ctx("master_conv")
        master_letter_path = _letter_path(runtime, "master-to-pm-reply")
        write_letter(runtime, "master", master_conv, master_letter_path,
                     "Master 给 PM 的答复",
                     "你在刚才的分析中已核对了 PM 的理解并回答了疑问。"
                     "请将你的结论写成正式信件给 PM。\n"
                     "逐一核对 PM 的理解是否正确，回答所有疑问。"
                     "如果 PM 的理解完全正确且无疑问，也请告知 PM。"
                     "要求 PM 再次汇报它对项目的理解和疑问。\n"
                     "强调：不得许可 PM 写 PRD 或原型\n\n")
        read_and_write_letter(runtime, "pm", pm_conv,
                              master_letter_path, pm_reply_path,
                              "From PM, Re: 对 Master 的答复",
                              "逐一回应 Master 的答复，确认清楚所有疑问。"
                              "如有新的疑问也一并提出。如果已没有疑问，也需要明确说明没有疑问，并重新详细讲述自己对项目的了解。",
                              "在 Master 明确许可之前，不得开始写 PRD 或原型。")
    else:
        # 首次：读 handoff 信，写回信
        letter_path = runtime.context.get_ctx("pm_letter_path")
        if not letter_path:
            raise RuntimeError("没有 handoff 信件路径")
        read_and_write_letter(runtime, "pm", pm_conv,
                              letter_path, pm_reply_path,
                              "From PM, Re: Master 的委托",
                              "写一封回信汇报你对项目的理解和疑问。"
                              "列出不清楚或需要 Master 确认的地方。",
                              "在 Master 明确许可之前，不得开始写 PRD 或原型。")

    runtime.context.set_ctx("pm_align_round", str(round_num + 1))

    runtime.context.set_ctx("pm_reply_path", pm_reply_path)
    # 缓存回信内容，供 master_reply_pm 重读（文件可能被 read_letter 删除）
    if os.path.exists(pm_reply_path):
        with open(pm_reply_path, "r", encoding="utf-8") as f:
            runtime.context.set_ctx("pm_reply_text", f.read())
    return {"phase": "pm_align_done"}


def master_reply_pm(state: WorkflowState) -> dict:
    """Master 读取 PM 回信，回答疑问，复用 clarify conversation。"""
    runtime = getattr(master_reply_pm, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")

    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    task = ("逐一检查以下内容：\n"
            "1. PM 的理解是否正确？如有误，逐一指出\n"
            "2. PM 的疑问中，你能回答的全部回答。"
            "如果你修改了项目顶层决策文件，你需要答复 PM "
            "让它从顶层决策文件中获取更新，不能假设 PM 已经得知了你对文件的修改\n"
            "3. 如果遇到你无从判定的问题（涉及顶层决策、技术选型、使用场景等），"
            "不要猜测，明确写出需要向用户确认的具体问题\n\n"
            "你的回复中需明确区分两部分：\n"
            "- 你对 PM 的答复/纠正\n"
            "- 需要向用户确认的问题（如无则说'无需向用户提问'）\n"
            "4. 如果 PM 已经没有疑问且确保 PM 没有任何理解错误，则告诉 PM 它的理解已经完全正确。无需再向用户汇报以及请求许可，但也别立刻让 PM 编写相关产出.\n"
            "后续用户会主动指示让你编写对 PM 产出的审核标准以及对 PM 的prompt信件。")

    pm_reply_path = runtime.context.get_ctx("pm_reply_path")
    if pm_reply_path and os.path.exists(pm_reply_path):
        reply = read_letter(runtime, "master", master_conv, pm_reply_path, task)
    else:
        # 文件已被 read_letter 删除，用缓存的文本
        pm_reply = runtime.context.get_ctx("pm_reply_text")
        if not pm_reply:
            raise RuntimeError("PM 回信缺失，既无文件也无缓存")
        reply = call_agent(runtime, "master", master_conv,
                          f"请阅读以下 PM 的回信，然后{task}\n\n"
                          f"## PM 回信内容\n{pm_reply}")

    runtime.context.set_ctx("master_reply", reply)
    return {"phase": "master_reply_done"}


def judge_master_reply(state: WorkflowState) -> dict:
    """判读 Master 的回复，路由到下一步。"""
    runtime = getattr(judge_master_reply, "_runtime", None)
    master_reply = runtime.context.get_ctx("master_reply")

    print("  ── judge: Master 回复 ──")
    result = judge_reply(runtime, "Master", master_reply, [
        "A. Master 已明确确认 PM 理解完全正确，且无任何需要再向PM说明或向用户提问的内容 → 进入下一阶段",
        "B. Master 有对 PM 的答复或纠正，需要转发给 PM 继续对齐 → 回 pm_align",
        "C. Master 有无法判定的问题，需要向用户确认 → 进入 clarify_inject",
    ], "judge-master-reply")
    return {"judge_result": result.strip()}


def clarify_inject(state: WorkflowState) -> dict:
    """向用户提问 Master 无法判定的问题，更新 project_context.md。"""
    runtime = getattr(clarify_inject, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    master_reply = runtime.context.get_ctx("master_reply")

    print(f"\n  ── Master 需要向用户确认 ──\n{master_reply}")

    def _close(reason: str):
        project_context_path = runtime.context.get_bg("project_context_path")
        call_agent(runtime, "master", master_conv,
                   f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{project_context_path}")
        runtime.logger.log_event("clarification_done", detail=reason)

    _clarify_loop(runtime, master_conv, "== 向用户确认 ==", "请回答 Master 的疑问", _close)
    return {"phase": "clarify_done"}


def _write_criteria(runtime, master_conv, title: str, file_path: str,
                     prompt: str, context_key: str):
    """通用审核标准编写。告诉 Master 路径让 Master 自己写入文件。"""
    print(f"\n  ── {title} ──")

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    call_agent(runtime, "master", master_conv,
               f"{prompt}\n\n请将审核标准完整写入文件：{file_path}")

    if not _ensure_write_file(runtime, "master", master_conv, file_path):
        raise RuntimeError(f"Master 未生成审核标准文件：{file_path}")

    runtime.context.set_ctx(f"{context_key}_path", file_path)
    print(f"  ✓ 审核标准已写入 {file_path}")

    runtime.logger.log_event("criteria_defined",
        detail=f"{title}——已写入 {file_path}")


def pm_write_criteria(state: WorkflowState) -> dict:
    """Master 制定 PM 产出的审核标准（PRD + prototype）。循环直至自检通过。"""
    runtime = getattr(pm_write_criteria, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")

    runtime.logger.log_event("phase_started", detail="PM 审核标准制定")

    # 如果有上一轮审查反馈信，让 Master 先读
    feedback_path = runtime.context.get_ctx("pm_criteria_feedback_path") or ""
    if feedback_path and os.path.exists(feedback_path):
        read_letter(runtime, "master", master_conv, feedback_path,
                    "根据反馈意见重新制定审核标准")
        runtime.context.set_ctx("pm_criteria_feedback_path", "")

    prompt = (
        "你即将为 PM 产出的 PRD 和 prototype 制定审核标准。\n\n"
        "## 上游约束\n"
        "项目决策是你要考虑的上游上下文，标准必须与之对齐：\n"
        f"项目决策记录的文件地址为：{project_context_path or '（无项目决策记录）'}\n\n"
        "## 标准覆盖维度\n"
        "1. 需求完整性 — PRD 是否覆盖了所有已确认的功能？\n"
        "2. MVP 边界 — 范围是否控制在 MVP 内？有无超额？\n"
        "3. 逻辑自洽性 — 功能描述是否完整无矛盾？数据流是否有断点？\n"
        "4. 一致性 — 功能定义、用户角色、技术假设是否与项目决策文件冲突？\n"
        "5. 原型质量 — prototype 是否体现了核心交互和页面结构？\n"
        "   - 页面要素完整（输入框、按钮、链接等）\n"
        "   - 交互行为正确（表单校验触发、错误提示展示、页面切换、登出流程等）\n"
        "   - 边界情况体现（空输入拦截、重复注册检测、非法字符过滤等）\n"
        "   - 数据流一致性（注册后可登录、大小写区分、密码错误提示等信息流是否自洽）\n"
        "   - 视觉风格统一\n"
        "## 下游需求\n"
        "- PM 将按这些标准撰写 PRD 和 prototype\n"
        "- Reviewer 将按这些标准审查 PM 产出\n\n"
        "## 要求\n"
        "文件中只需要写测什么以及怎么样算是测试完成，不需要写审查方法（reviewer 自己知道怎么测）。\n"
        "（对于原型的审核，优先考虑Playwright可以验收的标准，不需要你编写playwright标准，但是需要体现playwright脚本可审核的标准）。\n"
        "确保标准不是模板化的文字堆砌，而是真正能为审查提供 actionable 的判断依据。\n"
        "请具体、可操作，避免空泛描述。"
    )

    _write_criteria(
        runtime, master_conv,
        title="Master 制定 PM 审核标准",
        file_path=os.path.join(runtime.workspace, "criteria-pm.md"),
        prompt=prompt,
        context_key="pm_criteria",
    )
    return {"phase": "criteria_done", "judge_result": "review_pm_criteria"}


def review_pm_criteria(state: WorkflowState) -> dict:
    """Reviewer 审查 PM 审核标准是否具体可执行。"""
    runtime = getattr(review_pm_criteria, "_runtime", None)
    criteria_path = runtime.context.get_ctx("pm_criteria_path") or ""
    print(f"\n{'='*60}\n  ==> Reviewer 审查 PM 审核标准\n{'='*60}")

    if not criteria_path or not os.path.exists(criteria_path):
        print(f"  ✗ PM 审核标准文件不存在：{criteria_path}")
        return {"phase": "review_criteria_fail", "judge_result": "pm_write_criteria"}

    review = call_agent(runtime, "reviewer", _conv_name("review-pm-criteria"),
        "请审查以下审核标准。\n\n"
        "逐条检查：\n"
        "1. 每条标准是否具体、可衡量(审核标准不能带有\"恰当\"，\"合理\"等主观判断)？\n"
        "2. 每条标准是否写明了审查方法？(agent可以使用tool如file_read等方法进行审查)\n"
        "3. 标准是否覆盖了所有应覆盖的维度？\n"
        f"审核标准文件在：{criteria_path}\n\n"
        "逐条给出评价，最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。",
        stream=True)

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "PASS. 审查通过，满足所有条件。",
        "FAIL. 审查不通过，存在问题需要修正。",
    ], tag="judge-pm-criteria")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("pm_criteria_feedback_path", "")
    else:
        feedback_path = _letter_path(runtime, "reviewer-pm-criteria-feedback")
        write_letter(runtime, "reviewer", _conv_name("review-pm-criteria-feedback"),
                     feedback_path, "PM 审核标准审查反馈",
                     f"以下是你在上一轮审查中给出的评审意见，请整理成一封反馈信。\n\n"
                     f"## 你的审查意见\n{review}")
        runtime.context.set_ctx("pm_criteria_feedback_path", feedback_path)

    runtime.logger.log_event("criteria_reviewed",
        detail=f"PM 审核标准审查{'通过' if passed else '不通过'}")
    return {
        "phase": "review_pm_criteria_done" if passed else "review_pm_criteria_fail",
        "judge_result": "pm_write_doc" if passed else "pm_write_criteria",
    }


def pm_write_doc(state: WorkflowState) -> dict:
    """Phase 1e: Master 写信指令 → PM 产出 PRD.md + prototype.html。"""
    runtime = getattr(pm_write_doc, "_runtime", None)
    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = _conv_name("pm-doc")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="PM 出方案")
    print(f"\n  ── PM 出方案 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("clarify conversation 不存在")

    pm_dir = os.path.join(runtime.workspace, "PM")
    os.makedirs(pm_dir, exist_ok=True)

    # 审查反馈注入（循环进入时）
    prev_review = runtime.context.get_ctx("review_result") or ""
    human_feedback = runtime.context.get_ctx("human_feedback") or ""
    feedback_ref = ""
    if prev_review:
        feedback_ref += f"\n\n## 上一轮审查发现的问题\n{prev_review}"
    if human_feedback:
        feedback_ref += f"\n\n## 人工反馈（需优先处理）\n{human_feedback}"

    # Call 1 — Master 写信要求 PRD，PM 直接写入 prd_path
    prd_path = os.path.join(pm_dir, "PRD.md")
    criteria_path = runtime.context.get_ctx("pm_criteria_path") or ""
    criteria_ref = ""
    if criteria_path and os.path.exists(criteria_path):
        criteria_ref = f"\n审核标准文件（PM 需对着这些标准写，Reviewer 将用来审查）：{criteria_path}"
    prd_letter_path = _letter_path(runtime, "master-prd")
    write_letter(runtime, "master", master_conv, prd_letter_path,
                 "PRD 编写说明",
                 "请以 Master 的身份给 PM 写信，要求 PM 输出 PRD.md 并写入指定文件。\n"
                 "需包含：项目概述、功能需求、MVP 范围、页面结构、验收标准。\n"
                 "需要告知 PM ，在它写文档之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁，给了它哪些上下文，这些上下文该如何约束它进行文档的编写。\n"
                 "2. 它的下游是谁，会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
                 "4. 确保具体、可操作，避免空泛描述\n"
                 "5. 在这个阶段中，只要求它产出PRD.md，原型需要等你进一步下达指令后再进行产出。\n"
                 "6. 数据流描述必须覆盖每个角色的完整链路。例如不能只写「前端解析」，"
                 "而要写「前端解析 JWT payload 中的哪个字段、做什么用」。\n"
                 "7. 异常状态的 UI 描述必须和你将要产出的 prototype 的实际设计保持一致。"
                 + criteria_ref + feedback_ref)
    read_letter(runtime, "pm", pm_conv, prd_letter_path,
                f"按信中的要求编写 PRD.md，写入文件 {prd_path}。")

    # Call 2 — Master 写信要求原型，PM 直接写入 proto_path
    proto_path = os.path.join(pm_dir, "prototype.html")
    proto_letter_path = _letter_path(runtime, "master-prototype")
    pm_agent_dir = os.path.join(runtime.workspace, "pm")
    pm_script_dir = os.path.join(pm_agent_dir, "tests")
    write_letter(runtime, "master", master_conv, proto_letter_path,
                 "原型编写说明",
                 "请以 Master 的身份给 PM 写信，要求 PM 基于 PRD 产出 prototype.html 并写入指定文件。\n"
                 "需包含：核心交互、页面布局、导航流程。\n"
                 "单文件自包含（CSS/JS 内嵌），可双击在浏览器中直接打开。\n"
                 "需要告知 PM，在它写原型之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁，给了它哪些上下文（PRD），这些上下文该如何约束它进行原型的编写。\n"
                 "2. 它的下游是谁，会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的原型。\n"
                 "4. 确保具体、可操作，避免空泛占位符。")
    read_letter(runtime, "pm", pm_conv, proto_letter_path,
                f"按信中要求编写 prototype.html，写入文件 {proto_path}。\n\n"
                "编写完成后，对照 PRD 自检：所有 PRD 中定义的 UI 状态（包括异常状态）"
                "是否都有对应的页面展示。\n\n"
                "编写完成后如果需要进行自测，使用 Playwright 脚本测试，不要使用 Playwright MCP 交互式测试。\n"
                f"Playwright 环境搭建在 {pm_agent_dir}，脚本保存到 {pm_script_dir}。\n"
                f"  a. 首次运行：cd \"{pm_agent_dir}\" && npm init -y && cd \"{pm_agent_dir}\" && npm install playwright\n"
                "  b. 检查 package.json 是否已存在，如已存在则跳过 npm init\n"
                "  c. 脚本命名格式：pm-test.spec.js\n"
                "  d. 运行脚本验证 prototype 行为是否符合预期\n"
                "  e. 系统已预装兼容的 Chrome 无头浏览器，无需 npx playwright install\n"
                "  f. 测试失败时，先诊断是测试脚本的问题还是原型本身的问题：\n"
                "     - 页面交互与预期不符（如点按纽触发错误行为） → 检查原型 HTML/CSS/JS 逻辑\n"
                "     - 测试脚本选择器或交互方式不当 → 修正测试脚本\n"
                "     - 明确说明本轮修复的是什么问题\n"
                "  g. 每次只修复一个根因，不要同时改脚本又改原型\n"
                "  h. 同一问题连续调试 3 轮仍未通过，使用 Playwright MCP 工具确认问题，不要继续改脚本")

    print(f"  ✓ {prd_path}")
    print(f"  ✓ {proto_path}")

    runtime.context.set_phase_node(["PM 出方案"], "done")
    runtime.logger.log_event("phase_completed", detail="PM 方案完成")
    return {"phase": "done", "judge_result": "pass"}


def review_pm_output(state: WorkflowState) -> dict:
    """Reviewer 对照审核标准和项目决策，审查 PM 产出。"""
    runtime = getattr(review_pm_output, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 PM 产出\n{'='*60}")

    criteria_path = os.path.join(runtime.workspace, "criteria-pm.md")
    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    project_context_path = runtime.context.get_bg("project_context_path") or ""

    human_feedback = runtime.context.get_ctx("human_feedback") or "(无人工反馈)"
    reviewer_dir = os.path.join(runtime.workspace, "reviewer")
    script_dir = os.path.join(reviewer_dir, "pm")

    prompt = "你是一个项目审查员。请根据以下材料审查 PM 的产出。\n"
    prompt += f"## 审核标准在：{criteria_path}\n"
    prompt += f"## 项目顶层决策在：{project_context_path}\n"
    if human_feedback:
        prompt += f"## 人工反馈（需优先处理）\n{human_feedback}\n\n"
    prompt += f"PM的产出：\n PRD 在：{prd_path}\n\n"
    prompt += f"Prototype 在：{proto_path}\n\n"

    prompt += (
        "## 审查步骤\n"
        "1. 先阅读 PRD，对照审核标准中的需求完整性、MVP 边界、逻辑自洽性等维度检查，输出结论\n"
        f"2. 针对 prototype，在以下目录编写 Playwright 脚本并执行。所有脚本保存到：{script_dir}\n"
        "   a. 首次执行 Playwright 前，先初始化运行环境：\n"
        f"      cd \"{reviewer_dir}\" && npm init -y\n"
        f"      cd \"{reviewer_dir}\" && npm install playwright\n"
        "   b. 检查 package.json 是否已存在，如已存在则跳过 npm init\n"
        "   c. 脚本必须逐条覆盖审核标准中所有交互/UI 相关的条目，包括但不限于：\n"
        "      - 页面结构：登录页、注册页、主页面要素是否完整\n"
        "      - 表单校验：空输入、非法字符、密码长度等\n"
        "      - 交互流程：注册 → 自动登录 → 登出 → 重新登录\n"
        "      - 边界情况：重复注册、密码错误、未登录访问保护页面\n"
        "      - 数据一致性：注册后可用新账号登录、大小写用户名区分\n"
        "   d. 命名格式：pm-prototype.spec.js\n"
        "   e. 运行脚本验证 prototype 行为是否符合预期\n"
        "   f. 系统已经npx playwright install chrome预安装过兼容的chrome无头浏览器\n"
        "   g. 测试失败时，先诊断是测试脚本的问题还是原型本身的问题：\n"
        "      - 页面交互与预期不符 → 检查原型 HTML/CSS/JS 逻辑\n"
        "      - 脚本选择器或交互方式不当 → 修正测试脚本\n"
        "      - 明确说明本轮修复的是什么问题\n"
        "   h. 每次只修复一个根因，不要同时改脚本又改原型\n"
        "   i. 同一问题连续调试 3 轮仍未通过，使用 Playwright MCP 工具确认问题\n"
        "3. 综合 PRD 审查结论和 Playwright 脚本执行结果，逐条输出审查结论。\n"
        "明确列出每个不通过项及其原因。\n"
        "如果全部通过，最后一行回复 == PASS ==\n"
        "如果有不通过项，最后一行回复 == FAIL ==")

    conv = _conv_name("reviewer")
    reply = call_agent(runtime, "reviewer", conv, prompt)

    judge_result = judge_reply(runtime, "Reviewer", reply, [
        "PASS. 审查通过，满足所有条件。",
        "FAIL. 审查不通过，存在问题需要修正。",
    ], tag="judge-pm-output")
    passed = judge_result.strip() == "P"

    runtime.context.set_ctx("review_result", reply)

    runtime.logger.log_event("review_completed", detail=f"审查{'通过' if passed else '不通过'}")
    print(f"  {'✓ Reviewer 审查通过' if passed else '✗ Reviewer 审查不通过'}")
    return {"phase": "review_done", "judge_result": "human_review" if passed else "pm_write_doc"}


def human_review(state: WorkflowState) -> dict:
    """人工审核 PM 产出。展出文件路径，让人确认或提意见。"""
    runtime = getattr(human_review, "_runtime", None)

    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    criteria_path = os.path.join(runtime.workspace, "criteria-pm.md")

    print(f"\n{'='*60}\n  ==> 人工审核 PM 产出\n{'='*60}")
    print(f"  PM 产出位置：")
    print(f"    PRD:       {prd_path}")
    print(f"    Prototype: {proto_path}")
    print(f"    审核标准:   {criteria_path}")
    print()

    end_word = runtime.config.get("input_end_word") or None
    cp = runtime.checkpoint.wait(
        "人工审核 PM 产出",
        f"请查看以上文件，确认 PM 产出符合要求。\n"
        f"直接 EOF 通过审核；如有问题请说明：",
        prompt="输入内容后按 Enter：", end_word=end_word,
    )
    feedback = cp.message.strip()

    if not feedback:
        print("  ✓ 人工审核通过")
        runtime.logger.log_event("human_review_passed")
        return {"phase": "done", "judge_result": END}

    round_num = runtime.context.get_ctx("human_feedback_round") or 0
    round_num += 1
    runtime.context.set_ctx("human_feedback_round", round_num)
    entry = f"第 {round_num} 次人工反馈:\n{feedback}"
    prev = runtime.context.get_ctx("human_feedback") or ""
    runtime.context.set_ctx("human_feedback",
                            prev + "\n\n---\n\n" + entry if prev else entry)
    runtime.logger.log_event("human_review_rejected", detail=feedback)
    print(f"  ⚠ 人工审核不通过，反馈已记录")
    return {"phase": "human_review_rejected", "judge_result": "pm_write_doc"}


def dev_handoff(state: WorkflowState) -> dict:
    """Phase 2a: Master 写 handoff 信给 Dev。"""
    runtime = getattr(dev_handoff, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Phase 2a: Master 写信给 Dev\n{'='*60}")

    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")
    ws = runtime.workspace

    letter_path = _letter_path(runtime, "master-to-dev")
    write_letter(runtime, "master", master_conv, letter_path,
                 "Master 给 Dev 的信",
                 f"介绍项目上下文。信件需包含：\n"
                 "1. 开宗明义：这是 Master 给 Dev 的信\n"
                 "2. 项目概况和核心需求（简要描述即可）\n"
                 f"3. 告知 Dev 详细内容在以下文件：\n"
                 f"   项目顶层决策：{project_context_path}\n"
                 f"   PRD：{ws}/PM/PRD.md\n"
                 f"   原型：{ws}/PM/prototype.html\n"
                 "4. 要求 Dev：先阅读以上所有文档，然后写出你对需求的理解总结和疑问清单\n"
                 "5. 你的直接对接人是 PM，PM 无法回答的问题会由 Master 处理\n"
                 "6. 在 PM 明确许可之前，不得开始写详细设计\n\n"
                 "信件要有 Master 的口吻，是上级对下级的沟通与任务委派。")

    runtime.context.set_ctx("dev_letter_path", letter_path)
    print(f"\n  ── Master 给 Dev 的信件已就绪 ──")
    return {"phase": "dev_handoff_done"}


def dev_align(state: WorkflowState) -> dict:
    """Phase 2b: Dev↔PM/Master 对齐循环。

    Dev 写信（理解+疑问），PM 审答疑，需升级则 Master 介入。"""
    runtime = getattr(dev_align, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    ws = runtime.workspace

    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = _conv_name("dev-align")
        runtime.context.set_ctx("dev_conv", dev_conv)

    pm_conv = runtime.context.get_ctx("pm_conv")
    if not pm_conv:
        pm_conv = _conv_name("pm-align")
        runtime.context.set_ctx("pm_conv", pm_conv)

    runtime.logger.log_event("phase_started", detail="Dev 对齐")
    print(f"\n{'='*60}\n  ==> Phase 2b: Dev 对齐（Dev ↔ PM / Master）\n{'='*60}")

    is_first = True
    while True:
        if is_first:
            # 首次：Dev 读 handoff 信，写理解+疑问
            handoff_path = runtime.context.get_ctx("dev_letter_path")
            if not handoff_path:
                raise RuntimeError("没有 handoff 信件路径")
            dev_reply_path = _letter_path(runtime, "dev-understanding")
            read_and_write_letter(runtime, "dev", dev_conv,
                                  handoff_path, dev_reply_path,
                                  "From Dev, Re: Master 的委托",
                                  "阅读所有项目文档后，"
                                  "写出你对项目需求的理解总结，"
                                  "以及不清楚或有疑问的地方的清单。",
                                  "在 PM 明确许可之前，不得开始写详细设计")
            is_first = False
        else:
            # 后续：Dev 读反馈信（来自 PM 或 Master），修订理解
            feedback_path = runtime.context.get_ctx("dev_feedback_path")
            if not feedback_path or not os.path.exists(feedback_path):
                raise RuntimeError("Dev 反馈信缺失")
            dev_reply_path = _letter_path(runtime, "dev-understanding")
            read_and_write_letter(runtime, "dev", dev_conv,
                                  feedback_path, dev_reply_path,
                                  "From Dev, Re: 修订后的理解",
                                  "根据上轮反馈修订你的理解总结，"
                                  "如果有新的疑问也一并提出。"
                                  "如果已经没有疑问，明确说明已无疑问。",
                                  "在 PM 明确许可之前，不得开始写详细设计")

        # PM 读 Dev 的信，审理解 + 答疑问
        pm_reply_path = _letter_path(runtime, "pm-reply-dev")
        read_and_write_letter(runtime, "pm", pm_conv,
                              dev_reply_path, pm_reply_path,
                              "From PM, Re: Dev 的理解与疑问",
                              "逐一检查 Dev 的理解是否正确，有误则纠正。"
                              "回答 Dev 的所有疑问。"
                              "有无法回答的问题，在对应处标记 ❓需要升级。"
                              "如果 Dev 的理解完全正确且无疑问，也请明确说明。"
                              "不得许可 Dev 写详细设计。"
                              "注意：如果需要升级到 Master，你的回信中必须包含"
                              "Dev 对项目的全部理解和全部疑问清单"
                              "（包括你已解答的和需要升级给 Master 的），"
                              "以便 Master 掌握完整上下文。",
                              "审 Dev 的理解并回答问题")

        # 缓存 PM 回信内容
        pm_reply = ""
        if os.path.exists(pm_reply_path):
            with open(pm_reply_path, "r", encoding="utf-8") as f:
                pm_reply = f.read()
            runtime.context.set_ctx("pm_reply_text", pm_reply)

        # judge 判读 + marker 双重检查
        judge_result = judge_reply(runtime, "PM", pm_reply, [
            "A. Dev 理解完全正确且无疑问，无需修改",
            "B. PM 有反馈需要 Dev 修改或回答疑问",
            "C. PM 有需要升级到 Master 的问题",
        ], "judge-dev-align")
        needs_upgrade = "❓" in pm_reply

        # 升级条件：judge 说 C 或 marker 检测到 ❓
        if judge_result in ("C",) or needs_upgrade:
            print(f"\n  ── 升级到 Master ──")
            # PM 的回复已包含升级请求，转发给 Master
            master_reply_path = _letter_path(runtime, "master-reply-dev")
            read_and_write_letter(runtime, "master", master_conv,
                                  pm_reply_path, master_reply_path,
                                  "From Master, Re: Dev 对齐中的争议",
                                  "阅读 PM 的报告，逐条回答 PM 无法解决的问题。"
                                  "如果 PM 报告中有你无法判定的问题，明确写出需要向用户确认。"
                                  "你的回复中将包含 Dev 对项目的全部理解和全部疑问清单"
                                  "（包括 PM 已解答的和需要升级给你的），"
                                  "确保 Dev 收到后掌握完整的对齐结论。",
                                  "回答 Dev 对齐中升级上来的问题")

            # 判读 Master 回复
            master_reply = ""
            if os.path.exists(master_reply_path):
                with open(master_reply_path, "r", encoding="utf-8") as f:
                    master_reply = f.read()

            master_judge = judge_reply(runtime, "Master", master_reply, [
                "A. Master 已解决所有问题",
                "B. Master 还有疑问需要向用户确认",
            ], "judge-dev-master")

            if master_judge == "B" or "❓" in master_reply:
                # Master 需要向用户确认
                print(f"\n  ── Master 需要向用户确认 ──")

                def _close(reason: str):
                    pc_path = runtime.context.get_bg("project_context_path")
                    call_agent(runtime, "master", master_conv,
                               f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{pc_path}")

                _clarify_loop(runtime, master_conv, "== 向用户确认（Dev 对齐）==",
                             "Master 需要向用户确认 Dev 对齐中的争议问题", _close)

                # Master 写最终答复给 Dev
                final_path = _letter_path(runtime, "master-final-dev")
                write_letter(runtime, "master", master_conv, final_path,
                            "Master 给 Dev 的最终答复",
                            "根据用户确认的决策以及你的分析，"
                            "写出对 Dev 对齐中所有问题的最终答复。")
                runtime.context.set_ctx("dev_feedback_path", final_path)
            else:
                runtime.context.set_ctx("dev_feedback_path", master_reply_path)

        elif judge_result == "B":
            # PM 有反馈，直接回 Dev
            runtime.context.set_ctx("dev_feedback_path", pm_reply_path)

        else:  # A
            # 理解正确 + 无疑问，对齐完成
            runtime.logger.log_event("phase_completed", detail="Dev 对齐完成")
            print(f"\n  ✓ Dev 对齐完成")
            if os.path.exists(pm_reply_path):
                os.remove(pm_reply_path)
            return {"phase": "dev_align_done"}


def dev_write_criteria(state: WorkflowState) -> dict:
    """Master 制定 Dev 详细设计的审核标准。"""
    runtime = getattr(dev_write_criteria, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    project_context_path = runtime.context.get_bg("project_context_path")
    ws = runtime.workspace

    runtime.logger.log_event("phase_started", detail="Dev 设计审核标准制定")

    # 如果有上一轮审查反馈信，让 Master 先读
    feedback_path = runtime.context.get_ctx("dev_criteria_feedback_path") or ""
    if feedback_path and os.path.exists(feedback_path):
        read_letter(runtime, "master", master_conv, feedback_path,
                    "根据反馈意见重新制定审核标准")
        runtime.context.set_ctx("dev_criteria_feedback_path", "")

    prompt = (
        "你即将为 Dev 的详细设计方案制定审核标准。\n\n"
        "## 上游约束\n"
        "标准必须与以下已确认的内容对齐：\n"
        f"- 项目决策记录：{project_context_path or '（无项目决策记录）'}\n"
        f"- PRD：{ws}/PM/PRD.md\n"
        f"- 原型：{ws}/PM/prototype.html\n\n"
        "## 标准覆盖维度\n"
        "1. 架构合理性 — 设计方案是否与 PRD 一致？技术选型是否合理？\n"
        "2. 功能完整性 — 设计方案是否覆盖了 PRD 中所有功能点？\n"
        "3. 数据流正确性 — 数据流转路径是否清晰？前后端接口定义是否完整？\n"
        "4. 可实现性 — 在当前技术栈和约束下是否可行？\n"
        "5. 可测试性 — 设计是否考虑了如何验证每个功能？\n"
        "6. 边界与异常 — 是否涵盖了错误处理、空状态、异常场景等边界情况？\n"
        "## 下游需求\n"
        "- Dev 将按这些标准撰写详细设计方案\n"
        "- Reviewer 将按这些标准审查 Dev 的设计\n\n"
        "## 要求\n"
        "文件中只需要写测什么以及怎么样算是测试完成，不需要写审查方法（reviewer 自己知道怎么测）。\n"
        "请具体、可操作，避免空泛描述。"
    )

    _write_criteria(
        runtime, master_conv,
        title="Master 制定 Dev 设计审核标准",
        file_path=os.path.join(ws, "criteria-design.md"),
        prompt=prompt,
        context_key="dev_criteria",
    )
    return {"phase": "dev_criteria_done", "judge_result": "review_dev_criteria"}


def review_dev_criteria(state: WorkflowState) -> dict:
    """Reviewer 审查 Dev 审核标准是否具体可执行。"""
    runtime = getattr(review_dev_criteria, "_runtime", None)
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Dev 审核标准\n{'='*60}")

    if not criteria_path or not os.path.exists(criteria_path):
        print(f"  ✗ Dev 审核标准文件不存在：{criteria_path}")
        return {"phase": "review_criteria_fail", "judge_result": "dev_write_criteria"}

    review = call_agent(runtime, "reviewer", _conv_name("review-dev-criteria"),
        "请审查以下审核标准。\n\n"
        "逐条检查：\n"
        "1. 每条标准是否具体、可衡量(审核标准不能带有“恰当”，“合理”等主观判断)？\n"
        "2. 每条标准是否写明了审查方法？(agent可以使用tool如file_read等方法进行审查)\n"
        "3. 标准是否覆盖了所有应覆盖的维度？\n"
        f"审核标准文件在：{criteria_path}\n\n"
        "逐条给出评价，如果完全没有任何问题，且没有任何可以提高的建议，则最后一行输出 == PASS =="
        "如果有任何问题或有任何建议则输出 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。",
        stream=True)

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "PASS. 审查通过，所有标准具体可衡量。",
        "FAIL. 审查不通过，标准需要修正。",
    ], tag="judge-dev-criteria")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("dev_criteria_feedback_path", "")
    else:
        feedback_path = _letter_path(runtime, "reviewer-dev-criteria-feedback")
        write_letter(runtime, "reviewer", _conv_name("review-dev-criteria-feedback"),
                     feedback_path, "Dev 审核标准审查反馈",
                     f"以下是你在上一轮审查中给出的评审意见，请整理成一封反馈信。\n\n"
                     f"## 你的审查意见\n{review}")
        runtime.context.set_ctx("dev_criteria_feedback_path", feedback_path)

    runtime.logger.log_event("criteria_reviewed",
        detail=f"Dev 审核标准审查{'通过' if passed else '不通过'}")
    return {
        "phase": "review_dev_criteria_done" if passed else "review_dev_criteria_fail",
        "judge_result": "dev_write_design" if passed else "dev_write_criteria",
    }


def dev_write_design(state: WorkflowState) -> dict:
    """Master 写信指令 → Dev 产出详细设计方案。"""
    runtime = getattr(dev_write_design, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = _conv_name("dev-design")
        runtime.context.set_ctx("dev_conv", dev_conv)

    runtime.logger.log_event("phase_started", detail="Dev 出详细设计")
    print(f"\n  ── Dev 出详细设计 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("master conversation 不存在")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""
    criteria_ref = ""
    if criteria_path and os.path.exists(criteria_path):
        criteria_ref = f"\n审核标准文件（Dev 需对着这些标准写，Reviewer 将用来审查）：{criteria_path}"

    design_path = os.path.join(dev_dir, "design.md")
    design_letter_path = _letter_path(runtime, "master-design")
    write_letter(runtime, "master", master_conv, design_letter_path,
                 "详细设计编写说明",
                 "请以 Master 的身份给 Dev 写信，要求 Dev 输出详细设计方案并写入指定文件。\n"
                 "需包含：系统架构、数据流设计、路由/API 定义、组件结构、关键实现逻辑。\n"
                 "需要告知 Dev，在它写详细设计之前，需要考虑以下问题：\n"
                 "1. 它的上游是谁（PM），给了它哪些上下文（PRD、原型），"
                 "这些上下文该如何约束它进行详细设计的编写。\n"
                 "2. 它的下游是谁（QA），会如何从它的产出中获得约束和信息。\n"
                 "3. 确保产出不是模板化的文字堆砌，而是真正能为下游提供 actionable 的信息。\n"
                 "4. 确保具体、可操作，避免空泛描述。\n"
                 "5. 在这个阶段中，只要求产出详细设计文档，"
                 "代码实现需要等进一步指令后再进行。"
                 + criteria_ref)
    read_letter(runtime, "dev", dev_conv, design_letter_path,
                f"按信中的要求编写详细设计方案，写入文件 {design_path}。")

    print(f"  ✓ {design_path}")

    runtime.context.set_phase_node(["Dev 出详细设计"], "done")
    runtime.logger.log_event("phase_completed", detail="Dev 详细设计完成")
    return {"phase": "dev_design_done", "judge_result": "pass"}


def dev_write_plan(state: WorkflowState) -> dict:
    """Master 写信指令 → Dev 产出分步实现计划，每步含可执行验收标准。"""
    runtime = getattr(dev_write_plan, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    if not dev_conv:
        dev_conv = _conv_name("dev-plan")
        runtime.context.set_ctx("dev_conv", dev_conv)

    runtime.logger.log_event("phase_started", detail="Dev 出实现计划")
    print(f"\n  ── Dev 出实现计划 ──")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("master conversation 不存在")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    design_path = os.path.join(dev_dir, "design.md")
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""

    plan_path = os.path.join(dev_dir, "plan.md")
    plan_letter_path = _letter_path(runtime, "master-plan")
    write_letter(runtime, "master", master_conv, plan_letter_path,
                 "分步实现计划编写说明",
                 "请以 Master 的身份给 Dev 写信，要求 Dev 输出分步实现的计划并写入指定文件。\n"
                 "告知 Dev，它的详细设计方案在：\n"
                 f"{design_path}\n\n"
                 "## 计划模板格式\n"
                 "每个 Step 必须按以下模板编写：\n"
                 "```markdown\n"
                 "## Step N: <简短标题>\n"
                 "### 改动文件\n"
                 "- 列出需要新增或修改的文件路径\n"
                 "### 验收方法\n"
                 "```bash\n"
                 "<一条可执行的命令，能独立验证此步骤完成>\n"
                 "```\n"
                 "### 前置条件\n"
                 "- 列出需要上一步已完成的前提（如果有）\n"
                 "```\n\n"
                 "## 要求\n"
                 "1. 每个 Step 的改动不超过 3-5 个文件\n"
                 "2. 验收方法必须是可直接运行的命令（pytest、curl、python -c 等），"
                 "或是可使用tools或playwright脚本进行e2e验证的方法(如需要此方法验收，需要细致地写明验证方式)，"
                 "如果有必要的话，鼓励编写测试代码来进行验收，"
                 "每一步的验收需要覆盖这一 Step 中的所有改动。"
                 "不允许主观描述（如'确认代码正确'、'检查逻辑'）\n"
                 "3. 步骤必须按依赖顺序排列\n"
                 "4. 覆盖设计文档中的所有功能点\n"
                 "5. 这个阶段只要求产出计划文档，"
                 "代码实现需要等进一步指令后再进行。\n"
                 f"Plan需要约束未来所有代码的产出至{dev_dir}\n"
                 f"审核标准文件参考：{criteria_path}")
    read_letter(runtime, "dev", dev_conv, plan_letter_path,
                f"按信中的要求编写分步实现计划，写入文件 {plan_path}。")

    print(f"  ✓ {plan_path}")

    runtime.context.set_phase_node(["Dev 出实现计划"], "done")
    runtime.logger.log_event("phase_completed", detail="Dev 实现计划完成")
    return {"phase": "dev_plan_done", "judge_result": "pass"}


def dev_review_plan(state: WorkflowState) -> dict:
    """Reviewer 审查 Dev 的实现计划。"""
    runtime = getattr(dev_review_plan, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Dev 实现计划\n{'='*60}")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    plan_path = os.path.join(dev_dir, "plan.md")
    design_path = os.path.join(dev_dir, "design.md")
    criteria_path = runtime.context.get_ctx("dev_criteria_path") or ""

    if not os.path.exists(plan_path):
        print(f"  ✗ 计划文件不存在：{plan_path}")
        return {"phase": "review_plan_fail", "judge_result": "dev_write_plan"}

    prompt = (
        "你是一个项目审查员。请审查 Dev 的分步实现计划。\n\n"
        "## 审查标准\n"
        "逐条检查以下维度，每一条回复 ✓ 或 ✗：\n\n"
        "1. **步骤完整性** — 计划是否覆盖了设计文档中的所有功能点？\n"
        f"   设计文档在：{design_path}\n"
        "2. **验收可执行性** — 每个 Step 的验收方法是否为可运行的命令、\n"
        "   Playwright 脚本、或测试代码？不允许'确认代码正确'、'检查逻辑'这类主观描述。\n"
        "3. **步骤粒度** — 每个 Step 的改动是否不超过 3-5 个文件？\n"
        "4. **步骤顺序** — 步骤是否按依赖关系排列？\n"
        "5. **可验证性** — 每个 Step 完成后是否可独立验证？\n"
        "6. **验收覆盖度** — 每个 Step 的验收方法是否覆盖了该步骤的所有改动？\n"
        "   如果用了 Playwright/E2E 方式，是否写明了具体的验证步骤和预期结果？\n\n"
        "## Dev 的实现计划\n"
        f"计划文件在：{plan_path}\n\n"
        f"## 审核标准参考\n{criteria_path}\n\n"
        "## 输出格式\n"
        "逐条给出评价，最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题。"
    )

    review = call_agent(runtime, "reviewer", _conv_name("review-plan"),
                        prompt, stream=True)
    print(f"\n── Reviewer 审查结果 ──\n{review}\n")

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "PASS. 计划审查通过。",
        "FAIL. 计划审查不通过，需要修改。",
    ], tag="judge-dev-plan")
    passed = judge_result.strip() == "P"

    runtime.logger.log_event("plan_reviewed",
        detail=f"Dev 计划审查{'通过' if passed else '不通过'}")

    if passed:
        plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
        total = _count_steps(plan_path)
        runtime.context.set_ctx("dev_step_index", "0")
        runtime.context.set_ctx("dev_total_steps", str(total))
        runtime.context.set_ctx("dev_step_fail_count", "0")
        runtime.context.set_ctx("dev_step_has_failed", "false")

    return {
        "phase": "plan_review_done" if passed else "plan_review_fail",
        "judge_result": "dev_exec" if passed else "dev_write_plan",
    }


def dev_git_init(state: WorkflowState) -> dict:
    """Dev 在 Dev/ 目录下初始化 Git 仓库。"""
    runtime = getattr(dev_git_init, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv") or _conv_name("dev-git-init")
    runtime.context.set_ctx("dev_conv", dev_conv)

    dev_dir = os.path.join(runtime.workspace, "Dev")
    print(f"\n  ── Dev 初始化 Git 仓库 ──")

    call_agent(runtime, "dev", dev_conv,
        f"请在 {dev_dir} 目录下初始化 Git 仓库：\n"
        "1. cd 到该目录\n"
        "2. git init\n"
        "3. git config user.name 'Dev Agent'\n"
        "4. git config user.email 'dev@agent.local'\n"
        "5. git commit --allow-empty -m 'Initial empty commit'\n\n"
        "以上所有操作都完成后回复确认。")

    runtime.logger.log_event("phase_completed", detail="Dev Git 仓库初始化完成")
    return {"phase": "git_initted", "judge_result": "pass"}


def _get_step_from_plan(plan_path: str, step_idx: int) -> str:
    """从 plan.md 中提取第 step_idx 步的内容（0-indexed）。"""
    if not os.path.exists(plan_path):
        return ""
    with open(plan_path, "r", encoding="utf-8") as f:
        text = f.read()
    sections = text.split("## Step ")
    if step_idx + 1 >= len(sections):
        return ""
    return "## Step " + sections[step_idx + 1].strip()


def _count_steps(plan_path: str) -> int:
    """统计 plan.md 中的总步数。"""
    if not os.path.exists(plan_path):
        return 0
    with open(plan_path, "r", encoding="utf-8") as f:
        text = f.read()
    return text.count("## Step ")


def dev_exec_step(state: WorkflowState) -> dict:
    """依次执行 Dev plan 中的每一步。Master 写信 → Dev 实现。"""
    runtime = getattr(dev_exec_step, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    dev_conv = runtime.context.get_ctx("dev_conv") or _conv_name("dev-exec")
    runtime.context.set_ctx("dev_conv", dev_conv)

    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
    design_path = os.path.join(runtime.workspace, "Dev", "design.md")

    step_content = _get_step_from_plan(plan_path, step_idx)
    if not step_content:
        print(f"\n  ✗ 未找到 Step {step_idx + 1}，计划文件：{plan_path}")
        return {"phase": "dev_exec_error", "judge_result": "dev_exec_step"}

    print(f"\n{'='*60}\n  ==> Dev 执行 Step {step_idx + 1}\n{'='*60}")
    runtime.logger.log_event("phase_started", detail=f"Dev 执行 Step {step_idx + 1}")

    # 如有上一轮审查反馈，注入帮助改进
    prev_review = runtime.context.get_ctx("dev_step_review_feedback")
    feedback = ""
    if prev_review:
        feedback = f"\n\n## 上一轮审查反馈（需修复）\n{prev_review}"

    # 如有升级人工决策的指令，注入
    escalation_decision = runtime.context.get_ctx("dev_escalation_decision")
    if escalation_decision:
        feedback += f"\n\n## 人工决策\n{escalation_decision}"
        runtime.context.set_ctx("dev_escalation_decision", "")

    dev_dir = os.path.join(runtime.workspace, "Dev")
    os.makedirs(dev_dir, exist_ok=True)

    letter_path = _letter_path(runtime, f"master-step-{step_idx + 1}")
    write_letter(runtime, "master", master_conv, letter_path,
                 f"Step {step_idx + 1} 实现说明",
                 f"请以 Master 的身份给 Dev 写信，要求 Dev 实现以下步骤。\n\n"
                 f"## 待实现的步骤\n{step_content}\n\n"
                 f"## 上下文\n"
                 f"这是第 {step_idx + 1} 步。\n"
                 f"详细设计方案：{design_path}\n"
                 f"所有代码文件必须放在 {dev_dir} 目录下。\n"
                 f"所有之前的步骤已完成，请在此基础上继续开发。\n"
                 f"完成实现后自行验证验收方法。"
                 + feedback)
    read_letter(runtime, "dev", dev_conv, letter_path,
                "按信中要求实现当前步骤。所有代码产出必须放在 Dev/ 目录下，"
                "不要将文件生成到项目根目录或其他地方。"
                "完成实现后，运行该步骤的验收方法确认通过。\n\n"
                "## Git 操作限制\n"
                "没有允许不要做任何 git 操作（包括 git add、git commit、git push 等），"
                "代码只需要写在文件中即可。")

    return {"phase": "dev_exec", "judge_result": "dev_review_step"}


def dev_review_step(state: WorkflowState) -> dict:
    """Reviewer 按验收标准审查当前 Step 的实现。"""
    runtime = getattr(dev_review_step, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 Step\n{'='*60}")

    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")
    plan_path = os.path.join(runtime.workspace, "Dev", "plan.md")
    design_path = os.path.join(runtime.workspace, "Dev", "design.md")

    step_content = _get_step_from_plan(plan_path, step_idx)
    if not step_content:
        return {"phase": "review_step_error", "judge_result": "dev_exec_step"}

    review = call_agent(runtime, "reviewer", _conv_name(f"review-step-{step_idx + 1}"),
        "请审查 Dev 的最新实现。\n\n"
        "## 验收标准\n"
        f"来自计划的当前步骤：\n{step_content}\n\n"
        f"## 参考设计文档\n{design_path}\n\n"
        "逐条检查：\n"
        "1. 实现是否满足该步骤的验收方法？\n"
        "2. 实现是否与详细设计方案一致？\n"
        "3. 代码质量和错误处理是否合理？\n\n"
        "最后一行输出 == PASS == 或 == FAIL ==。\n"
        "如果 FAIL，写明需要修正的具体问题和原因。",
        stream=True)

    print(f"\n── Reviewer 审查结果 ──\n{review}\n")

    judge_result = judge_reply(runtime, "Reviewer", review, [
        "PASS. 实现满足所有验收标准。",
        "FAIL. 实现存在问题，需要修正。",
    ], tag=f"judge-step-{step_idx + 1}")
    passed = judge_result.strip() == "P"

    if passed:
        runtime.context.set_ctx("dev_step_fail_count", "0")
        runtime.context.set_ctx("dev_step_has_failed", "false")

        new_idx = step_idx + 1
        runtime.context.set_ctx("dev_step_index", str(new_idx))
        runtime.context.set_ctx("dev_step_review_feedback", "")
        runtime.logger.log_event("step_completed",
            detail=f"Step {step_idx + 1} 通过（{new_idx}/{total}）")

        if new_idx >= total:
            print(f"\n  ✓ 所有步骤完成！")
            runtime.logger.log_event("phase_completed", detail="Dev 执行全部完成")
        else:
            print(f"\n  ✓ Step {step_idx + 1} 通过，进入 Step {new_idx + 1}")
        return {"phase": "dev_exec_done" if new_idx >= total else "step_pass",
                "judge_result": "dev_commit"}
    else:
        runtime.context.set_ctx("dev_step_review_feedback", review)

        # 计数逻辑：第一次 FAIL 不计数，后续每次 +1
        has_failed_before = runtime.context.get_ctx("dev_step_has_failed") == "true"
        if not has_failed_before:
            runtime.context.set_ctx("dev_step_has_failed", "true")
            count = 0
        else:
            count = int(runtime.context.get_ctx("dev_step_fail_count") or "0") + 1
            runtime.context.set_ctx("dev_step_fail_count", str(count))

        # 阈值检查
        rollback_threshold = runtime.config.get("fail_rollback_threshold")
        escalation_threshold = runtime.config.get("fail_escalation_threshold")
        if rollback_threshold is None:
            raise RuntimeError("config 中缺少 fail_rollback_threshold")
        if escalation_threshold is None:
            raise RuntimeError("config 中缺少 fail_escalation_threshold")

        runtime.logger.log_event("step_failed",
            detail=f"Step {step_idx + 1} 未通过（fail_count={count}）")

        if count >= escalation_threshold:
            print(f"\n  ⚠ Step {step_idx + 1} 失败 {count} 次，升级人工决策")
            return {"phase": "step_escalate", "judge_result": "dev_escalate"}
        elif count >= rollback_threshold:
            print(f"\n  ⚠ Step {step_idx + 1} 失败 {count} 次，触发回滚")
            return {"phase": "step_rollback", "judge_result": "dev_rollback"}
        else:
            print(f"\n  ✗ Step {step_idx + 1} 未通过（fail_count={count}），重新执行")
            return {"phase": "step_fail", "judge_result": "step_retry"}


def dev_commit(state: WorkflowState) -> dict:
    """Dev 审查通过后提交代码到 Git。"""
    runtime = getattr(dev_commit, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")

    print(f"\n  ── Dev 提交 Step {step_idx} 的代码 ──")

    call_agent(runtime, "dev", dev_conv,
        f"你的 Step {step_idx} 已通过审查，请将改动提交到 Git：\n"
        "1. cd 到 Dev/ 目录\n"
        "2. git add 相关文件——不要将测试中间产物、缓存文件等无关内容 add 进去\n"
        "3. git commit -m \"Step {step_idx}: <提交说明>\"\n\n"
        "完成后回复确认。")

    runtime.logger.log_event("phase_completed", detail=f"Dev Step {step_idx} 代码已提交")

    if step_idx >= total:
        return {"phase": "dev_commit_done", "judge_result": "done"}
    else:
        return {"phase": "dev_commit_done", "judge_result": "dev_exec_step"}


def dev_rollback(state: WorkflowState) -> dict:
    """Dev 失败次数过多，回滚到上一个 commit 重新开始。"""
    runtime = getattr(dev_rollback, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")

    print(f"\n{'='*60}\n  ==> Dev Step {step_idx + 1} 回滚中...\n{'='*60}")

    call_agent(runtime, "dev", dev_conv,
        f"当前 Step {step_idx + 1} 已失败多次，执行以下操作：\n"
        "1. 注意：你的改动将被回滚至上一个提交，重新开始实现这个 Step\n"
        "2. cd Dev/ && git reset --hard HEAD\n"
        "3. 确认工作区已清理干净\n"
        "4. 重新实现 Step\n\n"
        "完成后回复确认。")

    runtime.logger.log_event("phase_started", detail=f"Dev Step {step_idx + 1} 回滚重来")
    return {"phase": "step_rollback", "judge_result": "dev_exec_step"}


def dev_escalate(state: WorkflowState) -> dict:
    """Dev 失败次数过多，升级到用户对话。Dev 简述 → 用户对话 → Dev 总结。"""
    runtime = getattr(dev_escalate, "_runtime", None)
    dev_conv = runtime.context.get_ctx("dev_conv")
    step_idx = int(runtime.context.get_ctx("dev_step_index") or "0")
    total = int(runtime.context.get_ctx("dev_total_steps") or "0")
    end_word = runtime.config.get("input_end_word") or None

    print(f"\n{'='*50}")
    print(f"【Dev Step {step_idx + 1}/{total} 多次失败，进入人工对话】")
    print(f"{'='*50}")

    # Step 1: Dev 简述 plan、当前 step、问题
    dev_summary = call_agent(runtime, "dev", dev_conv,
        "请用简短的篇幅向用户说明以下信息：\n"
        f"1. 整体计划概述\n"
        f"2. 当前 Step {step_idx + 1} 的内容和进展\n"
        "3. 最近一次审查反馈中指出的问题\n"
        "4. 你认为可能的原因是什么\n\n"
        "用户将与你对话帮助你解决问题。保持简洁。")
    print(f"\n── Dev 的简述 ──\n{dev_summary}\n")

    # Step 2: 对话循环，用户 EOF 结束
    print("进入对话模式。输入你的意见／修改要求，Dev 将回应。直接 EOF 结束对话。\n")

    round_num = 0
    while True:
        round_num += 1
        hint = "输入你的意见（直接 EOF 结束）："
        cp = runtime.checkpoint.wait(
            f"与 Dev 对话（Step {step_idx + 1}）",
            hint,
            prompt="", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            print("对话结束。\n")
            break

        call_agent(runtime, "dev", dev_conv,
            f"用户说：{user_input}\n\n"
            "请回应用户的意见。如果需要修改计划或其他文档，可以直接修改。\n"
            "保持对话简洁、有建设性。")

    # Step 3: Dev 总结决策
    decision = call_agent(runtime, "dev", dev_conv,
        "对话已结束。请总结用户最终的决策：\n"
        "1. 计划需要如何调整？是否已修改 Dev/plan.md？\n"
        "2. 下一步应该怎么做？\n"
        "3. 是否需要修改其他文档？\n\n"
        "输出决策总结。")

    print(f"\n── Dev 决策总结 ──\n{decision}\n")
    runtime.context.set_ctx("dev_escalation_decision", decision)

    runtime.logger.log_event("phase_escalated",
        detail=f"Dev Step {step_idx + 1} 升级人工对话")
    return {"phase": "step_escalated", "judge_result": "dev_exec_step"}


def qa_handoff(state: WorkflowState) -> dict:
    """Phase 3a: Master 写 handoff 信给 QA。"""
    runtime = getattr(qa_handoff, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Phase 3a: Master 写信给 QA\n{'='*60}")

    master_conv = runtime.context.get_ctx("master_conv")
    if not master_conv:
        raise RuntimeError("master conversation 不存在")

    ws = runtime.workspace
    qa_dir = os.path.join(ws, "QA")
    os.makedirs(qa_dir, exist_ok=True)

    letter_path = _letter_path(runtime, "master-to-qa")
    write_letter(runtime, "master", master_conv, letter_path,
                 "Master 给 QA 的信",
                 f"介绍项目上下文。信件需包含：\n"
                 "1. 开宗明义：这是 Master 给 QA 的信\n"
                 "2. 项目概况和核心需求（简要描述）\n"
                 f"3. 项目决策记录：{runtime.context.get_bg('project_context_path')}\n"
                 f"4. PRD：{ws}/PM/PRD.md\n"
                 f"5. 详细设计：{ws}/Dev/design.md\n"
                 f"6. 实现计划：{ws}/Dev/plan.md\n"
                 "7. 要求 QA：先阅读所有文档，写出你对项目的理解"
                 "和初步测试思路大纲（测什么、怎么测），"
                 "得到 PM 和 Dev 确认后才能开始写详细测试计划\n"
                 "8. 强调：在确认之前，不得开始写测试用例或执行测试")

    runtime.context.set_ctx("qa_letter_path", letter_path)
    print(f"\n  ── Master 给 QA 的信件已就绪 ──")
    return {"phase": "qa_handoff_done"}


def qa_align(state: WorkflowState) -> dict:
    """Phase 3b: QA↔PM/Dev/Master 对齐循环。

    QA 写理解+测试思路，PM 审范围，Dev 审技术可行性，需升级则 Master 介入。"""
    runtime = getattr(qa_align, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")
    ws = runtime.workspace

    qa_conv = _conv_name("qa-align")
    pm_conv = runtime.context.get_ctx("pm_conv") or _conv_name("pm-align")
    dev_conv = runtime.context.get_ctx("dev_conv") or _conv_name("dev-align")

    runtime.logger.log_event("phase_started", detail="QA 对齐")
    print(f"\n{'='*60}\n  ==> Phase 3b: QA 对齐（QA ↔ PM / Dev / Master）\n{'='*60}")

    is_first = True
    last_qa_reply = ""  # 缓存 QA 最后一版理解，避免文件被删后读不到
    while True:
        if is_first:
            handoff_path = runtime.context.get_ctx("qa_letter_path")
            if not handoff_path:
                raise RuntimeError("没有 handoff 信件路径")
            qa_reply_path = _letter_path(runtime, "qa-understanding")
            read_and_write_letter(runtime, "qa", qa_conv,
                                  handoff_path, qa_reply_path,
                                  "From QA, Re: Master 的委托",
                                  "阅读所有项目文档后，"
                                  "写出你对项目的理解总结，"
                                  "以及初步的测试思路大纲。\n\n"
                                  "测试思路大纲需覆盖：\n"
                                  "- 测试范围（功能模块、边界场景）\n"
                                  "- 每个模块的测试方法（E2E / API / 单元）\n"
                                  "- 不清楚或有疑问的地方\n\n"
                                  "你的信件会被 PM 和 Dev 查看(PM 检查测试的范围，Dev 检查测试的可行性)"
                                  "并且由他们回复你的问题"
                                  "注意：这是大纲阶段，不要写详细测试用例。",
                                  "在 PM 和 Dev 明确许可之前，不得开始写测试用例")
            is_first = False
        else:
            feedback_path = runtime.context.get_ctx("qa_feedback_path")
            if not feedback_path or not os.path.exists(feedback_path):
                raise RuntimeError("QA 反馈信缺失")
            qa_reply_path = _letter_path(runtime, "qa-understanding")
            read_and_write_letter(runtime, "qa", qa_conv,
                                  feedback_path, qa_reply_path,
                                  "From QA, Re: 修订后的理解与测试思路",
                                  "根据上轮反馈修订你的理解总结和测试思路大纲，"
                                  "如果有新的疑问也一并提出。"
                                  "如果已经没有疑问，明确说明已无疑问。",
                                  "在 PM 和 Dev 明确许可之前，不得开始写测试用例")
        # 缓存 QA 理解内容（PM/Dev 读信后会删信）
        if os.path.exists(qa_reply_path):
            with open(qa_reply_path, "r", encoding="utf-8") as f:
                last_qa_reply = f.read()

        # PM 审范围（keep=True 保留信给 Dev 读）
        pm_review_path = _letter_path(runtime, "pm-review-qa")
        read_and_write_letter(runtime, "pm", pm_conv,
                              qa_reply_path, pm_review_path,
                              "From PM, Re: QA 的理解与测试思路",
                              instruction="逐一检查 QA 的理解是否正确，测试范围是否覆盖了所有功能点。\n"
                                          "回答 QA 的疑问。\n"
                                          "有无法回答的问题，在对应处标记 ❓需要升级。\n"
                                          "如果 QA 的理解完全正确且测试范围无遗漏，也请明确说明。\n"
                                          "注意：如果需要升级到 Master，你的回信中必须包含"
                                          "QA 的全部理解和全部疑问清单"
                                          "（包括你已解答的和需要升级给 Master 的），"
                                          "以便 Master 掌握完整上下文。",
                              task="审 QA 的理解和测试范围",
                              keep=True)

        # Dev 审技术可行性
        dev_review_path = _letter_path(runtime, "dev-review-qa")
        read_and_write_letter(runtime, "dev", dev_conv,
                              qa_reply_path, dev_review_path,
                              "From Dev, Re: QA 的测试思路大纲",
                              "逐一检查 QA 的测试方法在技术实现上是否可行。\n"
                              "如果测试方案涉及当前未实现的接口或功能点，需明确指出。\n"
                              "如果测试环境配置有问题，也请指出。\n"
                              "回答 QA 的疑问。\n"
                              "有无法回答的问题，在对应处标记 ❓需要升级。\n"
                              "如果 QA 的测试思路完全可行，也请明确说明。\n"
                              "注意：如果需要升级到 Master，你的回信中必须包含"
                              "QA 的全部理解和全部疑问清单"
                              "（包括你已解答的和需要升级给 Master 的），"
                              "以便 Master 掌握完整上下文。",
                              "审 QA 测试思路的技术可行性")

        # 合并 PM 和 Dev 的反馈，判读是否需要升级
        pm_review = ""
        if os.path.exists(pm_review_path):
            with open(pm_review_path, "r", encoding="utf-8") as f:
                pm_review = f.read()
        dev_review = ""
        if os.path.exists(dev_review_path):
            with open(dev_review_path, "r", encoding="utf-8") as f:
                dev_review = f.read()

        combined_review = f"## PM 的审查\n{pm_review}\n\n## Dev 的审查\n{dev_review}"
        needs_upgrade = "❓" in combined_review

        # 用 judge 判读 PM 和 Dev 的综合审查结果
        judge_result = judge_reply(runtime, "PM/Dev", combined_review, [
            "A. QA 理解完全正确且测试范围无遗漏，无需修改",
            "B. PM 和 Dev 均没有需要升级到 Master 的问题，但有反馈需要 QA 修改",
            "C. PM 或 Dev 有需要升级到 Master 的问题",
        ], "judge-qa-align")

        if judge_result in ("C",) or needs_upgrade:
            print(f"\n  ── 升级到 Master ──")
            master_reply_path = _letter_path(runtime, "master-reply-qa")
            read_and_write_letter(runtime, "master", master_conv,
                                  [pm_review_path, dev_review_path], master_reply_path,
                                  "From Master, Re: QA 对齐中的争议",
                                  "阅读 PM 和 Dev 的审查报告，逐条回答他们无法解决的问题。\n"
                                  "如果报告中有你无法判定的问题，明确写出需要向用户确认。\n"
                                  "你的回复中将包含 QA 的全部理解和全部疑问清单，"
                                  "确保 QA 收到后掌握完整的对齐结论。",
                                  "回答 QA 对齐中升级上来的问题")

            master_reply = ""
            if os.path.exists(master_reply_path):
                with open(master_reply_path, "r", encoding="utf-8") as f:
                    master_reply = f.read()

            master_judge = judge_reply(runtime, "Master", master_reply, [
                "A. Master 已解决所有问题",
                "B. Master 还有疑问需要向用户确认",
            ], "judge-qa-master")

            if master_judge == "B" or "❓" in master_reply:
                print(f"\n  ── Master 需要向用户确认 ──")

                def _close(reason: str):
                    pc_path = runtime.context.get_bg("project_context_path")
                    call_agent(runtime, "master", master_conv,
                               f"请将本轮确认的决策记录到项目顶层决策记录文件的合适位置中：{pc_path}")

                _clarify_loop(runtime, master_conv, "== 向用户确认（QA 对齐）==",
                             "Master 需要向用户确认 QA 对齐中的争议问题", _close)

                final_path = _letter_path(runtime, "master-final-qa")
                write_letter(runtime, "master", master_conv, final_path,
                            "Master 给 QA 的最终答复",
                            "根据用户确认的决策以及你的分析，"
                            "写出对 QA 对齐中所有问题的最终答复。")
                runtime.context.set_ctx("qa_feedback_path", final_path)
            else:
                runtime.context.set_ctx("qa_feedback_path", master_reply_path)

        elif judge_result == "B":
            # 合并 PM 和 Dev 的反馈作为 QA 修改依据
            feedback_dir = os.path.join(runtime.runtime_dir, "handoffs")
            combined_path = os.path.join(feedback_dir, f"qa-combined-feedback-{int(time.time())}.md")
            with open(combined_path, "w", encoding="utf-8") as f:
                f.write(combined_review)
            runtime.context.set_ctx("qa_feedback_path", combined_path)

        else:  # A
            # 对齐完成，保存 QA 理解文件
            qa_dir = os.path.join(ws, "QA")
            os.makedirs(qa_dir, exist_ok=True)
            understanding_path = os.path.join(qa_dir, "understanding.md")
            with open(understanding_path, "w", encoding="utf-8") as f:
                f.write(last_qa_reply)

            runtime.context.set_ctx("qa_understanding_path", understanding_path)
            runtime.logger.log_event("phase_completed", detail="QA 对齐完成")
            print(f"\n  ✓ QA 对齐完成，理解已写入 {understanding_path}")
            return {"phase": "qa_align_done"}


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    for f in [pre_flight_clarify, pm_handoff, pm_align,
              master_reply_pm, judge_master_reply, clarify_inject,
              pm_write_criteria, pm_write_doc,
              review_pm_output, human_review,
              dev_handoff, dev_align, dev_write_criteria, dev_write_design,
              dev_write_plan, dev_review_plan,
              review_pm_criteria, review_dev_criteria,
              dev_git_init, dev_exec_step, dev_review_step,
              dev_commit, dev_rollback, dev_escalate,
              qa_handoff, qa_align]:
        f._runtime = runtime

    graph = StateGraph(WorkflowState)
    graph.add_node("pre_flight_clarify", pre_flight_clarify)
    graph.add_node("pm_handoff", pm_handoff)
    graph.add_node("pm_align", pm_align)
    graph.add_node("master_reply_pm", master_reply_pm)
    graph.add_node("judge_master_reply", judge_master_reply)
    graph.add_node("clarify_inject", clarify_inject)
    graph.add_node("pm_write_criteria", pm_write_criteria)
    graph.add_node("pm_write_doc", pm_write_doc)
    graph.add_node("review_pm_output", review_pm_output)
    graph.add_node("human_review", human_review)
    graph.add_node("dev_handoff", dev_handoff)
    graph.add_node("dev_align", dev_align)
    graph.add_node("dev_write_criteria", dev_write_criteria)
    graph.add_node("dev_write_design", dev_write_design)
    graph.add_node("dev_write_plan", dev_write_plan)
    graph.add_node("dev_review_plan", dev_review_plan)
    graph.add_node("review_pm_criteria", review_pm_criteria)
    graph.add_node("review_dev_criteria", review_dev_criteria)
    graph.add_node("dev_git_init", dev_git_init)
    graph.add_node("dev_exec_step", dev_exec_step)
    graph.add_node("dev_review_step", dev_review_step)
    graph.add_node("dev_commit", dev_commit)
    graph.add_node("dev_rollback", dev_rollback)
    graph.add_node("dev_escalate", dev_escalate)
    graph.add_node("qa_handoff", qa_handoff)
    graph.add_node("qa_align", qa_align)

    graph.set_entry_point("pre_flight_clarify")
    graph.add_edge("pre_flight_clarify", "pm_handoff")
    graph.add_edge("pm_handoff", "pm_align")
    graph.add_edge("pm_align", "master_reply_pm")
    graph.add_edge("master_reply_pm", "judge_master_reply")
    graph.add_conditional_edges("judge_master_reply", lambda s: s.get("judge_result", ""), {
        "A": "pm_write_criteria",
        "B": "pm_align",
        "C": "clarify_inject",
    })
    graph.add_edge("clarify_inject", "master_reply_pm")
    graph.add_conditional_edges("pm_write_criteria", lambda s: s.get("judge_result", ""), {
        "review_pm_criteria": "review_pm_criteria",
        "pm_write_criteria": "pm_write_criteria",
    })
    graph.add_conditional_edges("review_pm_criteria", lambda s: s.get("judge_result", ""), {
        "pm_write_doc": "pm_write_doc",
        "pm_write_criteria": "pm_write_criteria",
    })
    graph.add_edge("pm_write_doc", "review_pm_output")
    graph.add_conditional_edges("review_pm_output", lambda s: s.get("judge_result", ""), {
        "human_review": "human_review",
        "pm_write_doc": "pm_write_doc",
    })
    graph.add_conditional_edges("human_review", lambda s: s.get("judge_result", ""), {
        END: "dev_handoff",
        "review_pm_output": "review_pm_output",
    })
    graph.add_edge("dev_handoff", "dev_align")
    graph.add_edge("dev_align", "dev_write_criteria")
    graph.add_conditional_edges("dev_write_criteria", lambda s: s.get("judge_result", ""), {
        "review_dev_criteria": "review_dev_criteria",
        "dev_write_criteria": "dev_write_criteria",
    })
    graph.add_conditional_edges("review_dev_criteria", lambda s: s.get("judge_result", ""), {
        "dev_write_design": "dev_write_design",
        "dev_write_criteria": "dev_write_criteria",
    })
    graph.add_edge("dev_write_design", "dev_write_plan")
    graph.add_edge("dev_write_plan", "dev_review_plan")
    graph.add_conditional_edges("dev_review_plan", lambda s: s.get("judge_result", ""), {
        "dev_exec": "dev_git_init",
        "dev_write_plan": "dev_write_plan",
    })
    graph.add_edge("dev_git_init", "dev_exec_step")
    graph.add_edge("dev_exec_step", "dev_review_step")
    graph.add_conditional_edges("dev_review_step", lambda s: s.get("judge_result", ""), {
        "dev_commit": "dev_commit",
        "step_retry": "dev_exec_step",
        "dev_rollback": "dev_rollback",
        "dev_escalate": "dev_escalate",
    })
    graph.add_conditional_edges("dev_commit", lambda s: s.get("judge_result", ""), {
        "dev_exec_step": "dev_exec_step",
        "done": "qa_handoff",
    })
    graph.add_edge("dev_rollback", "dev_exec_step")
    graph.add_edge("dev_escalate", "dev_exec_step")
    graph.add_edge("qa_handoff", "qa_align")
    graph.add_edge("qa_align", END)

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
    if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "gb18030"):
        sys.stdout.reconfigure(errors="replace")
    print("=" * 60)
    print("  AI Coding 工作流框架 — 骨架")
    print("=" * 60)

    args = parse_args()
    config_path = args.config or os.path.join(os.getcwd(), "runtime_config.json")

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
