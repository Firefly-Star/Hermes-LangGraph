"""工作流工具函数。"""
import os, sys, time, threading, functools
from typing import TypedDict

import agent_runtime as ap
from .config import (AGENT_CONFIGS, MASTER_SYSTEM_PROMPT, DEV_SYSTEM_PROMPT,
                     FLUSH_CONTINUATION_NOTE, HANDOFFS_DIR)

# ── Agent 中断机制 ──

_interrupt_requested = False
_interrupt_listener = None

HOTKEY_MAP = {
    "ctrl+u": 21,   # NAK
    "ctrl+c": 3,    # ETX
    "ctrl+x": 24,   # CAN
}


class WorkflowInterrupted(Exception):
    """用户在 call_agent 运行时按下了中断热键。由 interruptible 装饰器捕获。"""
    pass


def _keyboard_listener(hotkey_code: int):
    """后台线程：监听键盘，检测到热键时设置中断标志。"""
    import msvcrt
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if isinstance(ch, bytes) and len(ch) == 1 and ch[0] == hotkey_code:
                global _interrupt_requested
                _interrupt_requested = True
        time.sleep(0.05)


def start_interrupt_listener(hotkey: str):
    """启动键盘监听线程。hotkey 如 'ctrl+u'。"""
    global _interrupt_listener, _interrupt_requested
    _interrupt_requested = False
    code = HOTKEY_MAP.get(hotkey.lower())
    if code is None:
        print(f"  [中断] 未知热键 '{hotkey}'，中断功能禁用")
        return
    _interrupt_listener = threading.Thread(
        target=_keyboard_listener, args=(code,), daemon=True)
    _interrupt_listener.start()
    print(f"  [中断] 按 {hotkey} 中断当前 agent 调用")


def stop_interrupt_listener():
    """停止监听（daemon 线程自动退出）。"""
    global _interrupt_listener
    _interrupt_listener = None


def request_interrupt():
    """手动触发中断（供外部使用）。"""
    global _interrupt_requested
    _interrupt_requested = True


def interruptible(func):
    """装饰器：节点函数中 call_agent 抛出 WorkflowInterrupted 时路由到 user_intervention。"""
    @functools.wraps(func)
    def wrapper(state):
        if hasattr(wrapper, '_runtime'):
            func._runtime = wrapper._runtime
        try:
            return func(state)
        except WorkflowInterrupted:
            rt = wrapper._runtime
            rt.context.set_ctx("interrupted_node", func.__name__)
            # 内联调用用户介入，不走图路由（固定边无法路由到 interrupt_dialog）
            interrupt_dialog._runtime = rt
            interrupt_dialog(state)
            # 用户结束后从头重入当前节点
            return func(state)
    wrapper.__name__ = func.__name__
    return wrapper


def register_nodes(graph, runtime, nodes: dict):
    """批量注册 interruptible 节点到 LangGraph graph。

    nodes = {node_name: function, ...}
    每个函数的 __name__ 会被设为 node_name，自动用 interruptible 包装后注册。
    """
    for name, fn in nodes.items():
        fn.__name__ = name
        wrapped = interruptible(fn)
        wrapped.__wrapped__._runtime = runtime
        wrapped._runtime = runtime
        graph.add_node(name, wrapped)


def interrupt_dialog(state) -> dict:
    """用户介入节点：用户可直接向中断时的 agent 对话发消息，EOF 后返回原节点。"""
    global _interrupt_requested
    _interrupt_requested = False  # 清掉残留 flag，避免刚进来就被中断

    runtime = getattr(interrupt_dialog, "_runtime", None)
    agent = runtime.context.get_ctx("interrupted_agent") or "master"
    conv = runtime.context.get_ctx("interrupted_conv") or ""
    return_node = runtime.context.get_ctx("interrupted_node") or "pre_flight_init"

    end_word = runtime.config.get("input_end_word") or None

    print(f"\n{'='*60}")
    print(f"  [用户介入] 正在与 {agent} 对话 (conversation: {conv})")
    print(f"  输入你想说的内容，直接 EOF 返回「{return_node}」节点")
    print(f"{'='*60}")

    while True:
        cp = runtime.checkpoint.wait(
            "用户介入",
            f"输入消息给 {agent}（EOF 返回）:",
            prompt="> ", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            print(f"  → 返回 {return_node} 节点")
            break

        try:
            reply = call_agent(runtime, agent, conv, user_input)
            print(f"\n── {agent} 回复 ──\n{reply}\n")
        except WorkflowInterrupted:
            # 用户在 agent 回复时再次按 Ctrl+U，忽略这次回复，继续等新输入
            print("\n  [中断] 已中断 agent 回复，可重新输入或 EOF 返回")

    runtime.context.set_ctx("interrupted_agent", "")
    runtime.context.set_ctx("interrupted_conv", "")
    runtime.context.set_ctx("interrupted_node", "")
    return {"phase": return_node}


class WorkflowState(TypedDict):
    phase: str
    judge_result: str


def conv_name(base: str) -> str:
    """生成带时间戳和工作目录的对话名，避免跨运行冲突。"""
    ws = os.path.basename(os.getcwd())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{base}-{ws}-{ts}"


def call_agent(runtime, agent: str, conversation: str, prompt: str,
               timeout: int = 180, stream: bool = True) -> str:
    """调用 agent 并返回文本。stream=True 时逐块打印输出。失败抛异常。"""
    print(f"  → 调 {agent}/{conversation}... ", end="", flush=True)
    t0 = time.time()

    def on_tool(name, args):
        global _interrupt_requested
        if _interrupt_requested:
            _interrupt_requested = False
            raise WorkflowInterrupted()
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
            global _interrupt_requested
            if _interrupt_requested:
                _interrupt_requested = False
                raise WorkflowInterrupted()
            try:
                print(chunk, end="", flush=True)
            except UnicodeEncodeError:
                enc = sys.stdout.encoding or "utf-8"
                sys.stdout.buffer.write(chunk.encode(enc, errors="replace"))
                sys.stdout.flush()
            text_parts.append(chunk)
        try:
            result = runtime.conversations.call(
                agent, conversation, prompt, timeout=timeout,
                stream_callback=on_chunk, tool_callback=on_tool)
        except WorkflowInterrupted:
            runtime.context.set_ctx("interrupted_agent", agent)
            runtime.context.set_ctx("interrupted_conv", conversation)
            raise
        print()
    else:
        global _interrupt_requested
        result = runtime.conversations.call(agent, conversation, prompt, timeout=timeout)
        if result.text:
            print(result.text)
        if _interrupt_requested:
            print("\n  [中断] 非流式调用无法中断，请求已忽略")
            _interrupt_requested = False

    elapsed = time.time() - t0
    if not result.success:
        print(f"  [FAIL] ({elapsed:.0f}s)")
        raise RuntimeError(f"[{agent}/{conversation}] 调用失败: {result.error}")

    tool_names = []
    if result.raw_data:
        for item in result.raw_data.get("output", []):
            if item.get("type") == "function_call":
                tool_names.append(item.get("name", ""))

    tool_info = f" tools:[{','.join(tool_names)}]" if tool_names else ""
    print(f"  ✓ ({elapsed:.0f}s, {result.input_tokens + result.output_tokens} tokens{tool_info})")
    return result.text


def letter_path(runtime, name: str) -> str:
    """生成 handoff 信件路径。"""
    handoff_dir = os.path.join(runtime.runtime_dir, HANDOFFS_DIR)
    os.makedirs(handoff_dir, exist_ok=True)
    ws = os.path.basename(os.getcwd())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(handoff_dir, f"{name}-{ws}-{ts}.md")


def ensure_write_file(runtime, receiver, conv, file_path, max_retry=None):
    """检查文件是否存在，不存在则提醒 agent 写入。max_retry 默认从 config 读。"""
    max_retry = int(runtime.config.get("write_retry", "2")) if max_retry is None else max_retry
    for attempt in range(max_retry):
        if os.path.exists(file_path):
            return True
        call_agent(runtime, receiver, conv,
                   f"（提醒）文件尚未被创建：{file_path}\n\n"
                   "请使用 write_file 工具将你的回复内容完整写入该文件，不要只在对话中回复。", stream = False)
    return os.path.exists(file_path)


def write_letter(runtime, sender, conv, letter_path, title, prompt):
    """sender 在 conv 对话中写一封信到 letter_path。重跑时删旧文件让 agent 重写。"""
    if os.path.exists(letter_path):
        os.remove(letter_path)
    call_agent(runtime, sender, conv,
               f"请以 **{sender}** 的身份写一封信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{prompt}\n\n"
               f"请将信件完整写入文件：{letter_path}")
    if not ensure_write_file(runtime, sender, conv, letter_path):
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
                          inputletter_path, outputletter_path,
                          title, instruction, task, keep=False):
    """读 input_letter（支持单路径或列表），让 receiver 按要求写回信。默认读完删信。"""
    paths = _resolve_paths(inputletter_path)
    for p in paths:
        if not os.path.exists(p):
            raise RuntimeError(f"信件不存在：{p}")

    # 重跑保护：如果输出文件已存在（来自中断前的写操作），删掉让 agent 重写
    out_paths = _resolve_paths(outputletter_path)
    for p in out_paths:
        if os.path.exists(p):
            os.remove(p)

    paths_text = "\n".join(f"- {p}" for p in paths)
    call_agent(runtime, receiver, conv,
               f"请阅读以下信件，然后{task}\n\n"
               f"## 信件路径\n{paths_text}\n\n"
               f"## 回复方式\n"
               f"请以 **{receiver}** 的身份写一封回信。\n\n"
               f"## 信件标题\n{title}\n\n"
               f"## 要求\n{instruction}\n\n"
               f"请将信件完整写入文件：{outputletter_path}")
    if not ensure_write_file(runtime, receiver, conv, outputletter_path):
        raise RuntimeError(f"{receiver} 仍未生成回信：{outputletter_path}")
    if not keep:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)


def judge_reply(runtime, target_role: str, reply: str, options: list[str],
                tag: str = None) -> str:
    """通用判读函数。judge agent 对 target_role 的回复进行分类路由。返回选项字母。"""
    options_text = "\n".join(f"{opt}" for opt in options)
    keys = "/".join(opt.strip()[0] for opt in options if opt.strip())
    conv = conv_name(tag or f"judge-{target_role.lower()}")
    prompt = (
        f"你是一个流程裁判。以下是 {target_role} 的回复。\n\n"
        f"## {target_role} 的回复\n{reply}\n\n"
        "判定当前状态是以下哪一种：\n"
        f"{options_text}\n\n"
        f"只回复单个字母（{keys}），不要包含标点或多余文字。"
    )
    result = call_agent(runtime, "judge", conv, prompt, False)
    # 只取第一个字母，防止 agent 返回 "PASS" 而非 "P"
    for ch in result.strip():
        if ch.isalpha():
            return ch
    return result.strip()


def clarify_loop(runtime, conv, title: str, first_hint: str) -> str:
    """通用澄清循环。用户输入 → Master 处理 → 用户决定继续或 EOF。

    每轮迭代一个 call_agent，中断后不回放整轮。空输入（直接 EOF）视为确认结束。
    返回结束原因字符串。
    """
    global _interrupt_requested
    _interrupt_requested = False

    end_word = runtime.config.get("input_end_word") or None

    round_num = 0
    while True:
        round_num += 1
        hint = first_hint if round_num == 1 \
            else "继续对话，或直接 EOF 结束："

        cp = runtime.checkpoint.wait(
            title, hint,
            prompt="输入内容后按 Enter：", end_word=end_word,
        )
        user_input = cp.message.strip()
        if not user_input:
            return "用户直接确认"

        try:
            call_agent(runtime, "master", conv,
                f"{user_input}\n不要产出任何东西，也不要修改任何文件，"
                "只需要说出你的理解，以及对有疑问的地方提出问题。")
        except WorkflowInterrupted:
            print("\n  [中断] 已中断 agent 回复，可重新输入或 EOF 返回")


def setup_runtime(config_path: str = None) -> ap.AgentRuntime:
    """初始化 AgentRuntime，启动所有 Gateway。"""
    runtime = ap.AgentRuntime(config_path)
    runtime.run_all(AGENT_CONFIGS)
    runtime.logger.log_event("workflow_started")

    runtime.context.set_bg("master_principles", MASTER_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())
    runtime.context.set_bg("dev_principles", DEV_SYSTEM_PROMPT.format(workspace=runtime.workspace).strip())

    return runtime


def write_criteria(runtime, master_conv, title: str, file_path: str,
                     prompt: str, context_key: str):
    """通用审核标准编写。告诉 Master 路径让 Master 自己写入文件。"""
    print(f"\n  ── {title} ──")

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    call_agent(runtime, "master", master_conv,
               f"{prompt}\n\n请将审核标准完整写入文件：{file_path}")

    if not ensure_write_file(runtime, "master", master_conv, file_path):
        raise RuntimeError(f"Master 未生成审核标准文件：{file_path}")

    runtime.context.set_ctx(f"{context_key}_path", file_path)
    print(f"  ✓ 审核标准已写入 {file_path}")

    runtime.logger.log_event("criteria_defined",
        detail=f"{title}——已写入 {file_path}")


def get_step_from_plan(plan_path: str, step_idx: int) -> str:
    """从 plan.md 中提取第 step_idx 步的内容（0-indexed）。"""
    if not os.path.exists(plan_path):
        return ""
    with open(plan_path, "r", encoding="utf-8") as f:
        text = f.read()
    sections = text.split("## Step ")
    if step_idx + 1 >= len(sections):
        return ""
    return "## Step " + sections[step_idx + 1].strip()


def count_steps(plan_path: str) -> int:
    """统计 plan.md 中的总步数。"""
    if not os.path.exists(plan_path):
        return 0
    with open(plan_path, "r", encoding="utf-8") as f:
        text = f.read()
    return text.count("## Step ")


def open_master_conv(runtime, summary_path=""):
    """创建新的 Master 对话并注入上下文。供 flush 和断线重连共用。"""
    master_principles = runtime.context.get_bg("master_principles")
    project_context_path = runtime.context.get_bg("project_context_path")

    pc_text = ""
    if project_context_path and os.path.exists(project_context_path):
        with open(project_context_path, "r", encoding="utf-8") as f:
            pc_text = f.read()

    summary_text = ""
    if summary_path and os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            summary_text = f.read()

    injected = master_principles
    if pc_text or summary_text:
        injected += FLUSH_CONTINUATION_NOTE
    if pc_text:
        injected += f"\n## 项目需求（已确认）\n{pc_text}"
    if summary_text:
        injected += f"\n\n## 进度摘要\n{summary_text}"

    new_conv = conv_name("master")
    old_conv = runtime.context.get_ctx("master_conv")
    if old_conv:
        runtime.conversations.close("master", old_conv)

    call_agent(runtime, "master", new_conv, injected)
    runtime.context.set_ctx("master_conv", new_conv)
    return new_conv
