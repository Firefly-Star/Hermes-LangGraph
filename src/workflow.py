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
    "reviewer": {"profile": "cg", "port": 8642},
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
- 审核标准：{workspace}/criteria.md
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
        "你是一个流程裁判。以下是 {target_role} 的回复。\n\n"
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
            print(chunk, end="", flush=True)
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
    reply = call_agent(runtime, receiver, conv,
                       f"请阅读以下信件，然后{task}\n\n## 信件路径\n{letter_path}")
    os.remove(letter_path)
    return reply


def read_and_write_letter(runtime, receiver, conv,
                          input_letter_path, output_letter_path,
                          title, instruction, task):
    """读 input_letter（给路径让 agent 自读），让 receiver 按要求写回信到 output_letter_path。"""
    if not os.path.exists(input_letter_path):
        raise RuntimeError(f"信件不存在：{input_letter_path}")
    call_agent(runtime, receiver, conv,
               f"请阅读以下信件，然后{task}\n\n"
               f"## 信件路径\n{input_letter_path}\n\n"
               f"## 回复方式\n"
               f"请以 **{receiver}** 的身份写一封回信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{instruction}\n\n"
               f"请将信件完整写入文件：{output_letter_path}")
    if not os.path.exists(output_letter_path):
        raise RuntimeError(f"{receiver} 未生成回信：{output_letter_path}")
    os.remove(input_letter_path)


def setup_runtime(config_path: str = None) -> ap.AgentRuntime:
    """初始化 AgentRuntime，启动 Master Gateway。"""
    runtime = ap.AgentRuntime(config_path)

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
    runtime.context.set_bg("master_principles", MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())

    return runtime


def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 交互式需求澄清。"""
    runtime = getattr(pre_flight_clarify, "_runtime", None)
    conv = _conv_name("master")

    print(f"\n{'='*50}\n  ==> Phase 0: 需求澄清\n{'='*50}")

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

    runtime.logger.log_event("phase_started", detail="PM 对齐理解")
    print(f"\n  ── PM 对齐理解 ──")

    master_reply = runtime.context.get_ctx("master_reply")
    pm_reply_path = _letter_path(runtime, "pm-reply")
    if master_reply:
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
            "- 需要向用户确认的问题（如无则说'无需向用户提问'）")

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
    """判读 Master 能否独立回答 PM，还是需要问用户。"""
    runtime = getattr(judge_master_reply, "_runtime", None)
    master_reply = runtime.context.get_ctx("master_reply")

    print("  ── judge: Master 回复 ──")
    result = judge_reply(runtime, "Master", master_reply, [
        "A. Master 确认 PM 理解 100% 正确，无需再问用户任何问题且无需纠正 PM 的任何错误且无需回复 PM 的任何问题 → 进入下一阶段",
        "B. Master 有对 PM 的答复或对 PM 指出的问题，需要转发给 PM 继续确认 → 回 PM",
        "C. Master 有无法判定的问题，需要向用户确认",
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


def pm_write_criteria(state: WorkflowState) -> dict:
    """Master 制定 PM 产出的审核标准（PRD + prototype）。循环直至自检通过。"""
    runtime = getattr(pm_write_criteria, "_runtime", None)
    master_conv = runtime.context.get_ctx("master_conv")

    project_context_path = runtime.context.get_bg("project_context_path")
    project_context = ""
    if project_context_path and os.path.exists(project_context_path):
        with open(project_context_path, "r", encoding="utf-8") as f:
            project_context = f.read()

    runtime.logger.log_event("phase_started", detail="PM 审核标准制定")
    print(f"\n  ── Master 制定 PM 审核标准 ──")

    # 如有上一轮自检反馈，注入帮助改进
    prev_check = runtime.context.get_ctx("pm_criteria_self_check")
    feedback = ""
    if prev_check and "FAIL" in prev_check:
        feedback = "\n上一轮自检发现的问题：\n" + prev_check + "\n请针对性改进后重新制定标准。"

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
        "每条标准必须具体、可衡量，且写明审查方法（如何使用tool或者skill判断通过/不通过）。\n"
        "确保标准不是模板化的文字堆砌，而是真正能为审查提供 actionable 的判断依据。\n"
        "请具体、可操作，避免空泛描述。"
    )
    criteria = call_agent(runtime, "master", master_conv, prompt + feedback)

    # 自检
    self_check = call_agent(runtime, "master", master_conv,
        "逐条确认以上标准每一条你都能实际执行检查"
        "（通过查看 PRD 或 prototype 文件）。"
        "依次回复每条是 ✓ 还是 ✗，如 ✗ 说明缺什么。\n"
        "如果全部 ✓，最后一行回复 == PASS ==。"
        "如果有 ✗，最后一行回复 == FAIL ==")

    runtime.context.set_ctx("pm_criteria", criteria)
    runtime.context.set_ctx("pm_criteria_self_check", self_check)

    # 写入审核标准文件，供 PM 和后续 reviewer 使用
    criteria_path = os.path.join(runtime.workspace, "criteria.md")
    os.makedirs(os.path.dirname(criteria_path), exist_ok=True)
    with open(criteria_path, "w", encoding="utf-8") as f:
        f.write("# PM 产出审核标准\n\n" + criteria)
    runtime.context.set_ctx("pm_criteria_path", criteria_path)
    print(f"  ✓ 审核标准已写入 {criteria_path}")

    last_line = self_check.strip().split("\n")[-1].strip()
    passed = "PASS" in last_line
    runtime.logger.log_event("criteria_defined",
        detail=f"PM 审核标准已制定，自检{'通过' if passed else '不通过'}")
    return {"phase": "criteria_done", "judge_result": "pm_write_doc" if passed else "pm_write_criteria"}


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
                 "5. 在这个阶段中，只要求它产出PRD.md，原型需要等你进一步下达指令后再进行产出。"
                 + criteria_ref + feedback_ref)
    read_letter(runtime, "pm", pm_conv, prd_letter_path,
                f"按信中的要求编写 PRD.md，写入文件 {prd_path}。")

    # Call 2 — Master 写信要求原型，PM 直接写入 proto_path
    proto_path = os.path.join(pm_dir, "prototype.html")
    proto_letter_path = _letter_path(runtime, "master-prototype")
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
                f"按信中要求编写 prototype.html，写入文件 {proto_path}。")

    print(f"  ✓ {prd_path}")
    print(f"  ✓ {proto_path}")

    runtime.context.set_phase_node(["PM 出方案"], "done")
    runtime.logger.log_event("phase_completed", detail="PM 方案完成")
    return {"phase": "done", "judge_result": "pass"}


def review_pm_output(state: WorkflowState) -> dict:
    """Reviewer 对照审核标准和项目决策，审查 PM 产出。"""
    runtime = getattr(review_pm_output, "_runtime", None)
    print(f"\n{'='*60}\n  ==> Reviewer 审查 PM 产出\n{'='*60}")

    criteria_path = os.path.join(runtime.workspace, "criteria.md")
    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    project_context_path = runtime.context.get_bg("project_context_path") or ""

    human_feedback = runtime.context.get_ctx("human_feedback") or "(无人工反馈)"

    prompt = "你是一个项目审查员。请根据以下材料审查 PM 的产出。\n"
    prompt += f"## 审核标准在：{criteria_path}\n"
    prompt += f"## 项目顶层决策在：{project_context_path}\n"
    if human_feedback:
        prompt += f"## 人工反馈（需优先处理）\n{human_feedback}\n\n"
    prompt += f"PM的产出：\n PRD 在：{prd_path}\n\n"
    prompt += f"Prototype 在：{proto_path}\n\n"

    prompt += ("逐条对照审核标准检查，输出审查结果。\n"
               "明确列出每个不通过项及其原因。\n"
               "如果全部通过，最后一行回复 == PASS ==\n"
               "如果有不通过项，最后一行回复 == FAIL ==")

    conv = _conv_name("reviewer")
    reply = call_agent(runtime, "reviewer", conv, prompt)

    last_line = reply.strip().split("\n")[-1].strip()
    passed = "PASS" in last_line

    runtime.context.set_ctx("review_result", reply)
    if passed:
        runtime.context.set_ctx("human_feedback", "")

    runtime.logger.log_event("review_completed", detail=f"审查{'通过' if passed else '不通过'}")
    print(f"  {'✓ Reviewer 审查通过' if passed else '✗ Reviewer 审查不通过'}")
    return {"phase": "review_done", "judge_result": "human_review" if passed else "pm_write_doc"}


def human_review(state: WorkflowState) -> dict:
    """人工审核 PM 产出。展出文件路径，让人确认或提意见。"""
    runtime = getattr(human_review, "_runtime", None)

    prd_path = os.path.join(runtime.workspace, "PM", "PRD.md")
    proto_path = os.path.join(runtime.workspace, "PM", "prototype.html")
    criteria_path = os.path.join(runtime.workspace, "criteria.md")

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

    runtime.context.set_ctx("human_feedback", feedback)
    runtime.logger.log_event("human_review_rejected", detail=feedback)
    print(f"  ⚠ 人工审核不通过，反馈已记录")
    return {"phase": "human_review_rejected", "judge_result": "review_pm_output"}


def build_graph(runtime) -> StateGraph:
    """构建 LangGraph StateGraph。"""
    for f in [pre_flight_clarify, pm_handoff, pm_align,
              master_reply_pm, judge_master_reply, clarify_inject,
              pm_write_criteria, pm_write_doc,
              review_pm_output, human_review]:
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
    graph.add_conditional_edges("pm_write_criteria", lambda s: s.get("judge_result", ""))
    graph.add_edge("pm_write_doc", "review_pm_output")
    graph.add_conditional_edges("review_pm_output", lambda s: s.get("judge_result", ""))
    graph.add_conditional_edges("human_review", lambda s: s.get("judge_result", ""))

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
