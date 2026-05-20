"""
Workflow — AI Coding 工作流框架的 LangGraph 编排
================================================
严格遵循 workflow-design-v3.md 定义的 9 个 Phase。
"""

import os, sys, json, time, traceback
from typing import TypedDict, Literal, Optional, Any

# ── LangGraph ──────────────────────────────────────────
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# ── AgentPool ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_pool as ap

# ════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════

PRINCIPLES = """
## 核心原则（Master 必须遵守）
1. Review NEVER optional — 每个子 agent 输出必须审查，再小也不行
2. 执行与验证分离 — 写代码的 agent 不能自己验证自己
3. 每步可回滚 — 执行前提醒 agent 做 git commit
4. 约束反复注入 — 核心规则在每次委派时重述
5. UI 验证必须自动化 — 有 UI 就须有 Playwright 脚本
"""

SELF_CHECK_PROMPT = """
请逐条确认以下原则在你刚执行的操作中是否被遵守：

[ ] 1. Review NEVER optional
    → 刚才是否有需要审查但未审查的环节？
[ ] 2. 执行与验证分离
    → 刚才是否有 agent 自己验证了自己？
[ ] 3. 每步可回滚
    → 执行前是否提醒了 agent 做 git commit？
[ ] 4. 约束反复注入
    → 刚才的委派是否包含了核心约束？
[ ] 5. UI 验证必须自动化
    → 是否有 UI 层但没提 Playwright？

请对每条给出 ✅ 或 ❌。
如果有 ❌，说明违反了哪条、怎么纠正。
全部 ✅ 则回复 "PASS"。
"""

AGENT_CONFIGS = {
    "master": {"profile": "cg", "port": 8642},
    "pm":     {"profile": "pm", "port": 8643},
    "dev":    {"profile": "dev", "port": 8644},
    "qa":     {"profile": "qa", "port": 8645},
}

MAX_REVIEW_LOOP = 5

# ════════════════════════════════════════════════════════
# 状态定义
# ════════════════════════════════════════════════════════

class WorkflowState(TypedDict):
    # 阶段控制
    phase: str
    step_index: int
    loop_count: int
    conv_seq: int
    self_check_count: int

    # 产出审批
    pm_doc_approved: bool
    dev_design_approved: bool
    dev_plan_approved: bool
    qa_plan_approved: bool

    # 执行结果
    dev_results: dict
    qa_results: dict
    bug_loop_count: int


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def role_aware_prompt(
    role: str, upstream: str, upstream_doc: str,
    deliverable: str, downstream: str, downstream_needs: str,
) -> str:
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


def call_agent(pool, agent: str, conversation: str, prompt: str, timeout: int = 180) -> str:
    """调用 agent 并返回文本结果。失败时抛异常。"""
    print(f"  → 调 {agent}/{conversation}... ", end="", flush=True)
    t0 = time.time()
    result = pool.conversations.call(agent, conversation, prompt, timeout=timeout)
    elapsed = time.time() - t0
    if not result.success:
        print(f"❌ ({elapsed:.0f}s)")
        raise RuntimeError(f"[{agent}/{conversation}] 调用失败: {result.error}")

    # 提取工具调用信息
    tool_uses = []
    if result.raw_data:
        for msg in result.raw_data.get("output", []):
            if msg.get("type") == "function_call":
                tool_uses.append(f"{msg['name']}(...)")
    tool_info = f" tools:[{','.join(tool_uses)}]" if tool_uses else ""

    print(f"✓ ({elapsed:.0f}s, {result.input_tokens + result.output_tokens} tokens{tool_info})")
    return result.text


def write_criteria(pool, target_desc: str, conv_name: str) -> str:
    """写审核标准 + 自检。返回标准文本。"""
    prompt = (
        f"请为以下内容制定可执行的审核标准：{target_desc}\n\n"
        f"标准必须具体、可衡量，每一条都能通过检查代码/文档来判定通过/不通过。\n"
        f"每一条格式：【标准】内容"
    )
    # 只用 call_agent 写标准，跳过自检（自检在主对话 self_check 中做）
    result_text = call_agent(pool, "master", conv_name, prompt, timeout=120)
    return result_text


def archive_review(pool, target: str, round_num: int, criteria: str, verdict: str, reason: str):
    """审核结果存档。"""
    record = json.dumps({
        "target": target,
        "round": round_num,
        "criteria": criteria,
        "verdict": verdict,
        "reason": reason,
    }, ensure_ascii=False)
    pool.context.set_ctx(f"review_{target}_r{round_num}", record)


def self_check(pool, action_summary: str) -> str:
    """Master 自省。在主对话内 inline 执行。"""
    master_conv = pool.config.get("_master_conv") or "master-init-0"
    prompt = (
        f"你刚才执行的操作：{action_summary}\n\n"
        f"{SELF_CHECK_PROMPT}"
    )
    result = pool.conversations.call("master", master_conv, prompt, timeout=60)
    return result.text if result.success else "SELF_CHECK_FAILED"


def flush_master(pool, phase: str, seq: int) -> int:
    """刷新 Master 上下文。返回新 seq 并记录当前 conv 名。"""
    pool.conversations.close_conversation("master", f"master-{phase}-{seq}")
    keys = ["background", "phase"]
    for key in ["approved_pm_doc", "approved_dev_design", "approved_dev_plan", "qa_report"]:
        if pool.context.get_ctx(key):
            keys.append(key)
    injection = pool.context.build_injection(keys)
    if not injection.strip():
        injection = "会话已刷新。继续工作。"
    new_conv = f"master-{phase}-{seq + 1}"
    pool.conversations.init_conversation("master", new_conv, injection)
    pool.config.set("_master_conv", new_conv)   # ← 记录当前 conv 名
    return seq + 1


def handle_review_exhausted(pool, target_name: str, current_text: str, criteria: str) -> str:
    """
    审核循环达上限时，让用户决定下一步。
    返回: "override" | "retry" | "abort"
    """
    content = (
        f"【{target_name}】已反复审核 {MAX_REVIEW_LOOP} 次仍未通过。\n\n"
        f"最新内容：\n{current_text[:500]}\n\n"
        f"审核标准：\n{criteria[:500]}\n"
    )
    result = pool.checkpoint.wait(
        f"审核循环达上限 — {target_name}",
        content,
        prompt="输入 'override' 强制通过 / 'retry' 再试一轮 / 'abort' 终止工作流："
    )
    action = result.message.strip().lower()
    if action in ("override", "o"):
        return "override"
    elif action in ("retry", "r"):
        return "retry"
    else:
        return "abort"


# ════════════════════════════════════════════════════════
# 初始化 AgentPool
# ════════════════════════════════════════════════════════

def setup_pool() -> ap.AgentPool:
    """初始化 AgentPool 并启动所有 Gateway。"""
    pool = ap.AgentPool()

    # 写入原则
    pool.context.set_bg("master_principles", PRINCIPLES)

    # 注册并启动各 agent
    for name, cfg in AGENT_CONFIGS.items():
        result = pool.agents.create_agent(name, cfg["profile"], cfg["port"])
        if not result.success and "已存在" not in result.message:
            print(f"  [WARN] {name} 注册: {result.message}")
        if result.status != "running":
            sr = pool.agents.run_gateway(name)
            if not sr.success:
                print(f"  [WARN] {name} gateway: {sr.message}")
            else:
                print(f"  {name} gateway 就绪")

    # 初始化 Master 对话
    pool.conversations.init_conversation(
        "master", "master-init-0",
        "工作流已启动。等待进入 Phase 0: Pre-Flight / Clarification。"
    )
    pool.config.set("_master_conv", "master-init-0")

    pool.logger.log_event("workflow_started", detail="AgentPool initialized")
    return pool


# ════════════════════════════════════════════════════════
# Node 函数
# ════════════════════════════════════════════════════════

def _init_state() -> WorkflowState:
    return {
        "phase": "pre_flight",
        "step_index": 0,
        "loop_count": 0,
        "conv_seq": 0,
        "self_check_count": 0,
        "pm_doc_approved": False,
        "dev_design_approved": False,
        "dev_plan_approved": False,
        "qa_plan_approved": False,
        "dev_results": {},
        "qa_results": {},
        "bug_loop_count": 0,
    }


# ── Phase 0: Pre-Flight / Clarification ────────────────

def pre_flight_clarify(state: WorkflowState) -> dict:
    """Phase 0: 和 Master 交互式澄清需求。"""
    pool = getattr(pre_flight_clarify, "_pool", None)
    if pool is None:
        return state

    pool.logger.log_event("phase_started", detail="Phase 0: 需求澄清")

    # 第一步：告诉 Master 准备接需求
    pool.conversations.init_conversation(
        "master", "master-clarify",
        "你现在是项目的 Master 编排者。请等待用户描述项目需求。\n"
        "当用户描述完后，你需要做两件事：\n"
        "1. 总结你对项目的理解\n"
        "2. 如果还有不清楚的地方，用 '## 疑问' 标题列出问题；\n"
        "   如果全部清楚了，用 '## 确认' 标题确认可以开始。"
    )
    pool.config.set("_master_conv", "master-clarify")

    # 循环：用户输入 → Master 回应 → 直到 CONFIRMED
    max_rounds = 5
    for round_num in range(max_rounds):
        # 问用户
        if round_num == 0:
            hint = "请描述你的项目需求（类型、技术栈、功能范围、验收标准等）："
        else:
            hint = "请回答 Master 的疑问，或输入 'CONFIRMED' 直接开始："

        cp = pool.checkpoint.wait(
            f"需求澄清 — 第 {round_num + 1} 轮",
            hint,
            prompt="输入内容后按 Enter："
        )
        user_input = cp.message.strip()
        if not user_input:
            continue

        # 用户说 CONFIRMED → 跳过 Master，直接 proceed
        if user_input.upper() == "CONFIRMED":
            pool.logger.log_event("clarification_done", detail="用户直接确认")
            pool.context.set_bg("clarification", "（用户确认后直接开始）")
            pool.context.set_bg("master_principles", PRINCIPLES)
            return {"phase": "pm_write_doc", "conv_seq": 0,
                    "self_check_count": state.get("self_check_count", 0)}

        # 把用户输入发给 Master
        result = pool.conversations.call("master", "master-clarify", user_input, timeout=120)
        reply = result.text if result.success else "（Master 无响应）"
        print(f"\n  Master 回应：{reply[:500]}")

        # 判断 Master 是否确认
        if "## 确认" in reply or "CONFIRMED" in reply.upper():
            pool.logger.log_event("clarification_done", detail="Master 确认理解")
            pool.context.set_bg("clarification", reply)
            pool.context.set_bg("master_principles", PRINCIPLES)
            return {"phase": "pm_write_doc", "conv_seq": 0,
                    "self_check_count": state.get("self_check_count", 0)}

        # 否则继续循环（Master 有疑问）
        pool.logger.log_event("clarification_round", detail=f"第{round_num + 1}轮有疑问")

    # 超限后强制通过
    pool.logger.log_event("clarification_done", detail="达到最大轮数，强制开始")
    pool.context.set_bg("clarification", "（达到最大澄清轮数后强制开始）")
    pool.context.set_bg("master_principles", PRINCIPLES)
    return {"phase": "pm_write_doc", "conv_seq": 0,
            "self_check_count": state.get("self_check_count", 0)}


# ── Phase 1: PM 出方案 ─────────────────────────────────

def pm_write_doc(state: WorkflowState) -> dict:
    pool = getattr(pm_write_doc, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 1a: PM 写方案")

    bg = pool.context.get_bg("clarification") or "（无澄清信息）"
    prompt = role_aware_prompt(
        role="PM（产品经理）",
        upstream="用户",
        upstream_doc=bg,
        deliverable="需求文档（PRD）+ HTML 静态原型界面",
        downstream="Dev（开发工程师）",
        downstream_needs="清晰的功能列表、页面结构、交互流程、验收标准，以及可直接打开的 HTML 原型文件",
    )
    prompt += (
        "\n\n## 产出要求\n"
        "请将以下文件实际写入磁盘到 workspace 目录：\n"
        "1. PRD.md — 需求文档（功能列表、用户故事、验收标准）\n"
        "2. prototype.html — 可直接双击浏览器打开的 HTML 静态原型\n\n"
        "写完后在回复中总结你创建了哪些文件以及它们的内容概要。"
    )

    # 如果是重入（审查没通过回来的），注入审查反馈
    lc = state.get("loop_count", 0)
    if lc > 0:
        prev_review = pool.context.get_ctx(f"review_pm_doc_r{lc - 1}") or pool.context.get_ctx("review_pm_doc_r0")
        if prev_review:
            prompt += (
                f"\n\n## 上一轮审查反馈\n"
                f"Reviewer 给出的结论是不通过，以下是需要修改的问题：\n"
                f"{prev_review[:1500]}\n\n"
                f"请根据反馈修改文件，不要重复说'文件已在磁盘上'。"
            )

    text = call_agent(pool, "pm", "pm-doc", prompt)
    pool.context.set_ctx("pm_doc", text)
    pool.logger.log_event("pm_doc_written", detail=f"PM 文档长度: {len(text)}")

    # 自省
    sc = self_check(pool, "Phase 1a: PM 写方案")
    return {"loop_count": lc, "self_check_count": state.get("self_check_count", 0) + 1}


def pm_write_criteria(state: WorkflowState) -> dict:
    pool = getattr(pm_write_criteria, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 1b: PM 审核标准")

    criteria = write_criteria(pool, "PM 的产出（需求文档 + HTML 原型）", "pm-criteria")
    pool.context.set_ctx("pm_review_criteria", criteria)
    return {}


def pm_review_doc(state: WorkflowState) -> dict:
    pool = getattr(pm_review_doc, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 1c: 评审 PM 方案")

    criteria = pool.context.get_ctx("pm_review_criteria") or "（无审核标准）"
    pm_doc = pool.context.get_ctx("pm_doc") or "（无文档）"
    prompt = (
        f"请按以下审核标准审查 PM 的产出：\n\n"
        f"【审核标准】\n{criteria}\n\n"
        f"【待审查内容】\n{pm_doc}\n\n"
        f"请逐条判断通过/不通过，并给出理由。"
        f"最终结论：PASS 或 FAIL"
    )
    text = call_agent(pool, "master", "review-pm-doc", prompt)

    # 判断结论
    is_pass = "PASS" in text.upper() and "FAIL" not in text.upper().split("PASS")[0] if "PASS" in text.upper() else False
    verdict = "pass" if is_pass else "fail"
    archive_review(pool, "pm_doc", state.get("loop_count", 0), criteria, verdict, text)

    if is_pass:
        pool.context.set_ctx("approved_pm_doc", pm_doc)
        pool.context.set_phase_node(["PM 方案评审"], "done")
        pool.logger.log_event("pm_doc_approved")
        sc = self_check(pool, "Phase 1c: PM 方案评审通过")
        return {"pm_doc_approved": True, "phase": "align_pm_dev", "loop_count": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "PM 方案评审", pm_doc, criteria)
            if decision == "override":
                pool.context.set_ctx("approved_pm_doc", pm_doc)
                pool.context.set_phase_node(["PM 方案评审"], "done")
                pool.logger.log_event("pm_doc_approved", detail="user override")
                return {"pm_doc_approved": True, "phase": "align_pm_dev", "loop_count": 0}
            elif decision == "abort":
                return {"phase": "done"}
            pool.logger.log_event("pm_doc_rejected", detail=f"第{lc}次未通过，用户选择 retry")
            return {"pm_doc_approved": False, "loop_count": 0}


# ── Phase 2: Cross-Agent Alignment PM→Dev ─────────────

def align_pm_dev(state: WorkflowState) -> dict:
    pool = getattr(align_pm_dev, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 2: Align PM→Dev")

    pm_doc = pool.context.get_ctx("approved_pm_doc") or "（无 PM 文档）"
    prompt = (
        f"你是 Dev。上游 PM 给你提供了以下方案文档：\n\n"
        f"{pm_doc}\n\n"
        f"请仔细阅读。然后列出你的问题和疑点——任何会影响你后续设计/实现的模糊之处。\n"
        f"如果没有问题，请回复 'NO_QUESTIONS'。"
    )
    text = call_agent(pool, "dev", "align-pm-dev", prompt)

    if "NO_QUESTIONS" in text.upper():
        pool.context.set_phase_node(["PM→Dev 对齐"], "done")
        pool.logger.log_event("alignment_pm_dev_done")
        sc = self_check(pool, "Phase 2: PM→Dev 对齐完成，无问题")
        return {"phase": "dev_design", "loop_count": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        # 将问题路由回 PM
        route_prompt = (
            f"Dev 读了你的方案后提出了以下问题：\n\n{text}\n\n"
            f"请逐一解答。如果需要修改方案，直接给出修改后的版本。"
        )
        answer = call_agent(pool, "pm", "align-pm-dev-answer", route_prompt)
        pool.context.set_ctx("align_pm_dev_qa", f"Q: {text}\nA: {answer}")
        # 回到对齐循环（loop_count 防死循环）
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "PM→Dev 对齐", text, "Dev 需理解 PM 方案")
            if decision == "override":
                pool.context.set_phase_node(["PM→Dev 对齐"], "done")
                return {"phase": "dev_design", "loop_count": 0}
            elif decision == "abort":
                return {"phase": "done"}
            return {"loop_count": 0}
        pool.logger.log_event("alignment_pm_dev_cycle", detail=f"第{lc}轮")
        return {"loop_count": lc}


# ── Phase 3: Dev 出详细设计 ────────────────────────────

def dev_design(state: WorkflowState) -> dict:
    pool = getattr(dev_design, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 3a: Dev 出详细设计")

    pm_doc = pool.context.get_ctx("approved_pm_doc") or "（无 PM 文档）"
    prompt = role_aware_prompt(
        role="Dev（开发工程师）",
        upstream="PM",
        upstream_doc=pm_doc,
        deliverable="详细设计文档（模块划分、函数边界、数据流、接口定义、类图/ER图描述）",
        downstream="QA（测试工程师）+ Dev（自己后续实现用）",
        downstream_needs="清晰的模块边界、函数签名、数据流向、接口契约，以及内聚/耦合分析",
    )
    prompt += (
        "\n\n## 设计要求\n"
        "- 每个模块的职责必须单一\n"
        "- 明确函数边界：每个函数的输入/输出/副作用\n"
        "- 分析模块间耦合度，避免循环依赖\n"
        "- 描述数据流：数据从入口到持久化的完整路径"
    )

    # 重入时注入审查反馈
    lc = state.get("loop_count", 0)
    if lc > 0:
        prev_review = pool.context.get_ctx(f"review_dev_design_r{lc - 1}") or pool.context.get_ctx("review_dev_design_r0")
        if prev_review:
            prompt += (
                f"\n\n## 上一轮审查反馈\n"
                f"Reviewer 给出的结论是不通过，以下是需要修改的问题：\n"
                f"{prev_review[:1500]}\n\n"
                f"请根据反馈修改设计文档。"
            )

    text = call_agent(pool, "dev", "dev-design", prompt)
    pool.context.set_ctx("dev_design", text)
    pool.logger.log_event("dev_design_written", detail=f"设计文档长度: {len(text)}")
    sc = self_check(pool, "Phase 3a: Dev 出详细设计")
    return {"loop_count": lc, "self_check_count": state.get("self_check_count", 0) + 1}


def dev_design_criteria(state: WorkflowState) -> dict:
    pool = getattr(dev_design_criteria, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 3b: Dev 设计审核标准")

    criteria = write_criteria(
        pool,
        "Dev 的详细设计文档（重点关注：函数边界是否清晰、模块内聚性、模块间耦合度、数据流完整性）",
        "dev-design-criteria",
    )
    pool.context.set_ctx("dev_design_criteria", criteria)
    return {}


def dev_design_review(state: WorkflowState) -> dict:
    pool = getattr(dev_design_review, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 3c: 评审 Dev 详细设计")

    criteria = pool.context.get_ctx("dev_design_criteria") or "（无审核标准）"
    design = pool.context.get_ctx("dev_design") or "（无设计文档）"
    prompt = (
        f"请按以下审核标准审查 Dev 的详细设计：\n\n"
        f"【审核标准】\n{criteria}\n\n"
        f"【待审查内容】\n{design}\n\n"
        f"重点检查：\n"
        f"1. 函数边界：每个函数职责是否单一？CRUD 是否分离？\n"
        f"2. 内聚性：模块内部逻辑是否自洽？\n"
        f"3. 耦合性：模块间依赖是否合理？有无循环依赖？\n"
        f"4. 数据流：数据链路是否完整闭环？\n\n"
        f"逐条判断。最终结论：PASS 或 FAIL"
    )
    text = call_agent(pool, "master", "review-dev-design", prompt)

    is_pass = "PASS" in text.upper() and ("FAIL" not in text.upper().split("PASS")[0] if "PASS" in text.upper() else False)
    verdict = "pass" if is_pass else "fail"
    archive_review(pool, "dev_design", state.get("loop_count", 0), criteria, verdict, text)

    if is_pass:
        pool.context.set_ctx("approved_dev_design", design)
        pool.context.set_phase_node(["Dev 详细设计评审"], "done")
        pool.logger.log_event("dev_design_approved")
        sc = self_check(pool, "Phase 3c: Dev 详细设计评审通过")
        return {"dev_design_approved": True, "phase": "dev_plan", "loop_count": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "Dev 详细设计评审", design, criteria)
            if decision == "override":
                pool.context.set_ctx("approved_dev_design", design)
                pool.context.set_phase_node(["Dev 详细设计评审"], "done")
                pool.logger.log_event("dev_design_approved", detail="user override")
                return {"dev_design_approved": True, "phase": "dev_plan", "loop_count": 0}
            elif decision == "abort":
                return {"phase": "done"}
            return {"dev_design_approved": False, "loop_count": 0}


# ── Phase 4: Dev 出实现计划 ────────────────────────────

def dev_plan(state: WorkflowState) -> dict:
    pool = getattr(dev_plan, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 4a: Dev 出实现计划")

    design = pool.context.get_ctx("approved_dev_design") or "（无设计文档）"
    prompt = role_aware_prompt(
        role="Dev（开发工程师）",
        upstream="PM（需求）+ 自己的详细设计",
        upstream_doc=design,
        deliverable="实现计划（每步 = 一个可验证的动作，每步 3~8 个文件）",
        downstream="QA + Master（用于跟踪进度）",
        downstream_needs="明确的步骤分解、每步的文件清单、每步的预期产出和验证方法",
    )
    prompt += (
        "\n\n## 计划要求\n"
        "- 每步必须是一个可验证的动作（如'实现用户注册 API'，而非'完成后端'）\n"
        "- 每步覆盖 3~8 个文件\n"
        "- 每步明确：要创建/修改的文件、预期产出、验证方式\n"
        "- 用 JSON 数组格式输出步骤列表"
    )

    # 重入时注入审查反馈
    lc = state.get("loop_count", 0)
    if lc > 0:
        prev_review = pool.context.get_ctx(f"review_dev_plan_r{lc - 1}") or pool.context.get_ctx("review_dev_plan_r0")
        if prev_review:
            prompt += (
                f"\n\n## 上一轮审查反馈\n"
                f"Reviewer 给出的结论是不通过，以下是需要修改的问题：\n"
                f"{prev_review[:1500]}\n\n"
                f"请根据反馈修改计划。"
            )

    text = call_agent(pool, "dev", "dev-plan", prompt)
    pool.context.set_ctx("dev_plan_text", text)
    pool.logger.log_event("dev_plan_written")
    sc = self_check(pool, "Phase 4a: Dev 出实现计划")
    return {"loop_count": lc, "step_index": 0,
            "self_check_count": state.get("self_check_count", 0) + 1}


def dev_plan_criteria(state: WorkflowState) -> dict:
    pool = getattr(dev_plan_criteria, "_pool", None)
    criteria = write_criteria(
        pool,
        "Dev 的实现计划（每步是否可验证、粒度是否合适、文件清单是否完整）",
        "dev-plan-criteria",
    )
    pool.context.set_ctx("dev_plan_criteria", criteria)
    return {}


def dev_plan_review(state: WorkflowState) -> dict:
    pool = getattr(dev_plan_review, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 4c: 评审 Dev 实现计划")

    criteria = pool.context.get_ctx("dev_plan_criteria") or "（无审核标准）"
    plan = pool.context.get_ctx("dev_plan_text") or "（无计划）"
    prompt = (
        f"请按以下审核标准审查 Dev 的实现计划：\n\n"
        f"【审核标准】\n{criteria}\n\n"
        f"【待审查内容】\n{plan}\n\n"
        f"逐条判断。最终结论：PASS 或 FAIL"
    )
    text = call_agent(pool, "master", "review-dev-plan", prompt)

    is_pass = "PASS" in text.upper() and ("FAIL" not in text.upper().split("PASS")[0] if "PASS" in text.upper() else False)
    verdict = "pass" if is_pass else "fail"
    archive_review(pool, "dev_plan", state.get("loop_count", 0), criteria, verdict, text)

    if is_pass:
        pool.context.set_ctx("approved_dev_plan", plan)
        pool.context.set_phase_node(["Dev 计划评审"], "done")
        pool.logger.log_event("dev_plan_approved")
        sc = self_check(pool, "Phase 4c: Dev 实现计划评审通过")
        return {"dev_plan_approved": True, "phase": "dev_exec", "loop_count": 0, "step_index": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "Dev 实现计划评审", plan, criteria)
            if decision == "override":
                pool.context.set_ctx("approved_dev_plan", plan)
                pool.context.set_phase_node(["Dev 计划评审"], "done")
                pool.logger.log_event("dev_plan_approved", detail="user override")
                return {"dev_plan_approved": True, "phase": "dev_exec", "loop_count": 0, "step_index": 0}
            elif decision == "abort":
                return {"phase": "done"}
            return {"dev_plan_approved": False, "loop_count": 0}


# ── Phase 5: Dev 执行循环 ─────────────────────────────

def dev_exec_step(state: WorkflowState) -> dict:
    pool = getattr(dev_exec_step, "_pool", None)

    plan = pool.context.get_ctx("approved_dev_plan") or "（无计划）"
    si = state.get("step_index", 0)
    prompt = (
        f"以下是已批准的实现计划：\n{plan}\n\n"
        f"你现在执行第 {si + 1} 步。\n\n"
        f"要求：\n"
        f"1. 执行前先 git add + git commit\n"
        f"2. 只做这一步，不要做后续步骤\n"
        f"3. 完成后报告做了什么"
    )
    text = call_agent(pool, "dev", f"dev-impl-{si}", prompt, timeout=300)
    pool.context.set_ctx(f"dev_step_{si}_result", text)
    pool.logger.log_event("dev_step_done", detail=f"Step {si + 1} 完成")

    # 判断是否需要 flush：step + self_check 合计达到阈值
    updates = {"step_index": si + 1}
    total_ops = (si + 1) + state.get("self_check_count", 0)
    if total_ops > 0 and total_ops % 5 == 0:
        seq = state.get("conv_seq", 0)
        new_seq = flush_master(pool, "dev_exec", seq)
        pool.logger.log_event("master_flush", detail=f"dev_exec flush: seq {seq} → {new_seq}")
        updates["conv_seq"] = new_seq
    return updates


def dev_review_step(state: WorkflowState) -> dict:
    pool = getattr(dev_review_step, "_pool", None)
    si = state.get("step_index", 0) - 1  # 上一步的结果
    result = pool.context.get_ctx(f"dev_step_{si}_result") or "（无结果）"
    plan = pool.context.get_ctx("approved_dev_plan") or "（无计划）"

    prompt = (
        f"审查 Dev 第 {si + 1} 步的产出：\n\n"
        f"【计划中的这一步】\n（从实现计划中提取）\n"
        f"【实际产出】\n{result}\n\n"
        f"判断：这个产出是否符合计划？代码是否可编译？功能是否完整？\n"
        f"最终结论：PASS 或 FAIL（附理由）"
    )
    text = call_agent(pool, "master", f"review-dev-step-{si}", prompt)

    is_pass = "PASS" in text.upper() and ("FAIL" not in text.upper().split("PASS")[0] if "PASS" in text.upper() else False)
    archive_review(pool, f"dev_step_{si}", 0, "（见审核标准记录）", "pass" if is_pass else "fail", text)

    if not is_pass:
        result_msg = f"Dev 第 {si + 1} 步未通过审查，需要回滚重做"
        pool.logger.log_event("dev_step_failed", detail=result_msg)
        decision = handle_review_exhausted(pool, f"Dev 第 {si + 1} 步审查", result, "步骤需通过审查")
        if decision == "override":
            sc = self_check(pool, f"Phase 5: Dev 第 {si + 1} 步用户 override")
            return {"self_check_count": state.get("self_check_count", 0) + 1}
        elif decision == "abort":
            return {"phase": "done"}

    sc = self_check(pool, f"Phase 5: Dev 第 {si + 1} 步审查通过")
    return {"self_check_count": state.get("self_check_count", 0) + 1}


def dev_exec_router(state: WorkflowState) -> str:
    """Dev 执行循环路由器。"""
    if state.get("phase") == "done":
        return "end_workflow"
    si = state.get("step_index", 0)

    # 估算总步数：从 plan 文本中粗略估算
    pool = getattr(dev_exec_step, "_pool", None)
    plan_str = pool.context.get_ctx("approved_dev_plan") if pool else ""
    # 按常见的 JSON 步骤条目数估算
    step_count = plan_str.lower().count('"step"') + plan_str.lower().count('"title"')
    total_steps = max(1, step_count // 2) if step_count > 0 else 5

    if si >= total_steps:
        return "align_dev_qa"
    return "dev_exec_step"


# ── Phase 6: Cross-Agent Alignment Dev→QA ─────────────

def align_dev_qa(state: WorkflowState) -> dict:
    pool = getattr(align_dev_qa, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 6: Align Dev→QA")

    design = pool.context.get_ctx("approved_dev_design") or "（无设计）"
    dev_results = "\n".join(
        f"Step {k}: {v[:200]}"
        for k, v in (pool.context.get_ctx(key) and {key: pool.context.get_ctx(key) for key in pool.context._data.get("contexts", {}) if key.startswith("dev_step_")} or {}).items()
    )

    # 收集 dev 执行结果
    ctx_data = pool.context._data.get("contexts", {})
    dev_steps = {k: v for k, v in ctx_data.items() if k.startswith("dev_step_")}
    dev_summary = "\n".join(f"{k}: {v[:200]}" for k, v in sorted(dev_steps.items()))

    prompt = (
        f"你是 QA。Dev 已完成以下实现：\n\n"
        f"【设计文档】\n{design[:1000]}\n\n"
        f"【执行结果摘要】\n{dev_summary}\n\n"
        f"请仔细阅读。列出你在编写测试计划前需要 Dev 澄清的问题。\n"
        f"如果没有问题，请回复 'NO_QUESTIONS'。"
    )
    text = call_agent(pool, "qa", "align-dev-qa", prompt)

    if "NO_QUESTIONS" in text.upper():
        pool.context.set_phase_node(["Dev→QA 对齐"], "done")
        pool.logger.log_event("alignment_dev_qa_done")
        sc = self_check(pool, "Phase 6: Dev→QA 对齐完成，无问题")
        return {"phase": "qa_plan", "loop_count": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        route_prompt = (
            f"QA 读了你的实现后提出了以下问题：\n\n{text}\n\n"
            f"请逐一解答。"
        )
        answer = call_agent(pool, "dev", "align-dev-qa-answer", route_prompt)
        pool.context.set_ctx("align_dev_qa_record", f"Q: {text}\nA: {answer}")
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "Dev→QA 对齐", text, "QA 需理解 Dev 实现")
            if decision == "override":
                pool.context.set_phase_node(["Dev→QA 对齐"], "done")
                return {"phase": "qa_plan", "loop_count": 0}
            elif decision == "abort":
                return {"phase": "done"}
            return {"loop_count": 0}
        pool.logger.log_event("alignment_dev_qa_cycle", detail=f"第{lc}轮")
        return {"loop_count": lc}


# ── Phase 7: QA 出测试计划 ────────────────────────────

def qa_plan(state: WorkflowState) -> dict:
    pool = getattr(qa_plan, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 7a: QA 出测试计划")

    pm_doc = pool.context.get_ctx("approved_pm_doc") or "（无方案）"
    design = pool.context.get_ctx("approved_dev_design") or "（无设计）"
    combined = f"## PM 方案\n{pm_doc[:500]}\n\n## Dev 设计\n{design[:1000]}"

    prompt = role_aware_prompt(
        role="QA（测试工程师）",
        upstream="PM（需求）+ Dev（实现）",
        upstream_doc=combined,
        deliverable="测试计划（黑盒 Playwright E2E + 白盒 API 测试）",
        downstream="Master + Dev（用于 bug 修复参考）",
        downstream_needs="覆盖所有业务路径的测试用例、每个用例的输入/预期输出、Playwright 脚本大纲",
    )
    prompt += (
        "\n\n## 测试计划要求\n"
        "- 黑盒测试：Playwright E2E，模拟用户操作，覆盖关键用户流程\n"
        "- 白盒测试：API 调用（curl/requests），覆盖边界条件、错误处理、权限\n"
        "- 首轮全部测试，后续轮次只测上次未通过的\n"
        "- 输出格式：JSON 数组，每项含 id、title、type(blackbox/whitebox)、steps、expected"
    )
    # 重入时注入审查反馈
    lc = state.get("loop_count", 0)
    if lc > 0:
        prev_review = pool.context.get_ctx(f"review_qa_plan_r{lc - 1}") or pool.context.get_ctx("review_qa_plan_r0")
        if prev_review:
            prompt += (
                f"\n\n## 上一轮审查反馈\n"
                f"Reviewer 给出的结论是不通过，以下是需要修改的问题：\n"
                f"{prev_review[:1500]}\n\n"
                f"请根据反馈修改测试计划。"
            )
    text = call_agent(pool, "qa", "qa-plan", prompt, timeout=240)
    pool.context.set_ctx("qa_plan_text", text)
    pool.logger.log_event("qa_plan_written")
    sc = self_check(pool, "Phase 7a: QA 出测试计划")
    return {"loop_count": lc, "self_check_count": state.get("self_check_count", 0) + 1}


def qa_plan_criteria(state: WorkflowState) -> dict:
    pool = getattr(qa_plan_criteria, "_pool", None)
    criteria = write_criteria(
        pool,
        "QA 的测试计划（是否覆盖全部业务路径、Playwright 脚本是否可行、边界条件是否覆盖）",
        "qa-plan-criteria",
    )
    pool.context.set_ctx("qa_plan_criteria", criteria)
    return {}


def qa_plan_review(state: WorkflowState) -> dict:
    pool = getattr(qa_plan_review, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 7c: 评审 QA 测试计划")

    criteria = pool.context.get_ctx("qa_plan_criteria") or "（无审核标准）"
    plan = pool.context.get_ctx("qa_plan_text") or "（无计划）"
    prompt = (
        f"请按以下审核标准审查 QA 的测试计划：\n\n"
        f"【审核标准】\n{criteria}\n\n"
        f"【待审查内容】\n{plan}\n\n"
        f"逐条判断。最终结论：PASS 或 FAIL"
    )
    text = call_agent(pool, "master", "review-qa-plan", prompt)

    is_pass = "PASS" in text.upper() and ("FAIL" not in text.upper().split("PASS")[0] if "PASS" in text.upper() else False)
    verdict = "pass" if is_pass else "fail"
    archive_review(pool, "qa_plan", state.get("loop_count", 0), criteria, verdict, text)

    if is_pass:
        pool.context.set_ctx("approved_qa_plan", plan)
        pool.context.set_phase_node(["QA 计划评审"], "done")
        pool.logger.log_event("qa_plan_approved")
        sc = self_check(pool, "Phase 7c: QA 测试计划评审通过")
        return {"qa_plan_approved": True, "phase": "qa_exec", "loop_count": 0, "step_index": 0,
                "self_check_count": state.get("self_check_count", 0) + 1}
    else:
        lc = state.get("loop_count", 0) + 1
        if lc >= MAX_REVIEW_LOOP:
            decision = handle_review_exhausted(pool, "QA 测试计划评审", plan, criteria)
            if decision == "override":
                pool.context.set_ctx("approved_qa_plan", plan)
                pool.context.set_phase_node(["QA 计划评审"], "done")
                pool.logger.log_event("qa_plan_approved", detail="user override")
                return {"qa_plan_approved": True, "phase": "qa_exec", "loop_count": 0, "step_index": 0}
            elif decision == "abort":
                return {"phase": "done"}
            return {"qa_plan_approved": False, "loop_count": 0}


# ── Phase 8: QA 测试循环 ──────────────────────────────

def qa_exec_test(state: WorkflowState) -> dict:
    pool = getattr(qa_exec_test, "_pool", None)
    pool.logger.log_event("phase_started", detail=f"Phase 8a: QA 测试 (round {state.get('bug_loop_count', 0)})")

    plan = pool.context.get_ctx("approved_qa_plan") or "（无测试计划）"
    prev_report = pool.context.get_ctx("qa_report")

    if state.get("bug_loop_count", 0) == 0:
        # 首轮：全部测试
        scope = "请执行测试计划中的所有测试用例。"
    else:
        # 后续轮次：只测上次未通过的
        scope = f"以下是上次测试报告，只执行其中 FAIL 的用例：\n\n{prev_report}\n\n只重测 FAIL 的用例，已通过的不需要重测。"

    prompt = (
        f"【测试计划】\n{plan}\n\n"
        f"{scope}\n\n"
        f"要求：\n"
        f"1. 黑盒测试：写 Playwright 脚本并运行\n"
        f"2. 白盒测试：用 curl/requests 调 API，记录 HTTP 状态码和响应体\n"
        f"3. 输出测试报告：JSON 格式，每项含 id、title、result(pass/fail)、actual_output"
    )
    text = call_agent(pool, "qa", f"qa-exec-{state.get('bug_loop_count', 0)}", prompt, timeout=600)
    pool.context.set_ctx(f"qa_raw_result_r{state.get('bug_loop_count', 0)}", text)
    return {}


def qa_write_report(state: WorkflowState) -> dict:
    pool = getattr(qa_write_report, "_pool", None)
    raw = pool.context.get_ctx(f"qa_raw_result_r{state.get('bug_loop_count', 0)}") or "（无数据）"

    prompt = (
        f"整理以下 QA 原始输出为结构化测试报告：\n\n{raw}\n\n"
        f"报告格式：\n"
        f"- 总用例数、通过数、失败数\n"
        f"- 每个失败用例：ID、名称、期望结果、实际结果、HTTP 响应体/Playwright 输出\n"
        f"- 最终结论：ALL_PASS 或 HAS_FAILURES"
    )
    text = call_agent(pool, "master", "qa-report-compile", prompt, timeout=120)
    pool.context.set_ctx("qa_report", text)
    pool.logger.log_event("qa_report_written")
    return {}


def qa_report_router(state: WorkflowState) -> str:
    """根据 QA 报告决定下一步。"""
    pool = getattr(qa_write_report, "_pool", None)
    if pool:
        report = pool.context.get_ctx("qa_report") or ""
        if "ALL_PASS" in report.upper():
            return "deliver"
    return "dev_fix_bug"


def dev_fix_bug(state: WorkflowState) -> dict:
    pool = getattr(dev_fix_bug, "_pool", None)
    pool.logger.log_event("phase_started", detail=f"Phase 8b: Dev 修 bug (round {state.get('bug_loop_count', 0)})")

    report = pool.context.get_ctx("qa_report") or "（无报告）"

    prompt = (
        f"以下是 QA 测试报告中的失败用例（需要修复的 bug）：\n\n{report}\n\n"
        f"对每个 FAIL 的用例：\n"
        f"1. 分析原因\n"
        f"2. 修改代码\n"
        f"3. git add + git commit\n"
        f"4. 跑对应的 Playwright 脚本验证\n"
        f"5. 如果通过 → 标记已修；如果仍失败 → 继续改\n\n"
        f"输出修复结果摘要。"
    )
    text = call_agent(pool, "dev", f"dev-fix-{state.get('bug_loop_count', 0)}", prompt, timeout=300)
    pool.context.set_ctx(f"dev_fix_r{state.get('bug_loop_count', 0)}", text)
    sc = self_check(pool, f"Phase 8b: Dev 修 bug 第{state.get('bug_loop_count', 0) + 1}轮")
    return {"bug_loop_count": state.get("bug_loop_count", 0) + 1,
            "self_check_count": state.get("self_check_count", 0) + 1}


def qa_verify_fix(state: WorkflowState) -> dict:
    pool = getattr(qa_verify_fix, "_pool", None)
    pool.logger.log_event("phase_started", detail=f"Phase 8c: QA 验证 fix (round {state.get('bug_loop_count', 0)})")

    report = pool.context.get_ctx("qa_report") or "（无报告）"

    prompt = (
        f"Dev 已修复以下 bug，请验证修复是否有效：\n\n"
        f"【之前的测试报告】\n{report}\n\n"
        f"请重新执行报告中 FAIL 的用例。\n"
        f"全部通过回复 ALL_PASS，仍有失败回复 HAS_FAILURES。"
    )
    text = call_agent(pool, "qa", f"qa-verify-{state.get('bug_loop_count', 0)}", prompt, timeout=300)
    pool.context.set_ctx(f"qa_verify_r{state.get('bug_loop_count', 0)}", text)
    return {}


def qa_verify_router(state: WorkflowState) -> str:
    pool = getattr(qa_verify_fix, "_pool", None)
    if pool:
        bl = state.get("bug_loop_count", 0)
        verify_text = pool.context.get_ctx(f"qa_verify_r{bl}") or ""
        if "ALL_PASS" in verify_text.upper():
            return "deliver"
    return "dev_fix_bug"


# ── Phase 9: 交付 ──────────────────────────────────────

def end_workflow(state: WorkflowState) -> dict:
    """终止工作流（用户选择 abort 或正常结束）。"""
    return state  # 不做任何事，只是让图走到 END

def deliver(state: WorkflowState) -> dict:
    pool = getattr(deliver, "_pool", None)
    pool.logger.log_event("phase_started", detail="Phase 9: 交付")

    # 汇总信息
    bg_info = pool.context._data.get("background", {})
    phase_tree = pool.context.get_phase_text()
    qa_report = pool.context.get_ctx("qa_report") or "（无测试报告）"

    summary = (
        f"## 项目信息\n"
        + "\n".join(f"  {k}: {v}" for k, v in bg_info.items()) +
        f"\n\n## 进度\n{phase_tree}"
        f"\n\n## 测试报告摘要\n{qa_report[:1500]}"
    )

    # 等用户确认
    cp = pool.checkpoint.wait(
        "交付确认",
        summary,
        "请确认交付。直接按 Enter 完成，或输入修改意见："
    )
    pool.logger.log_event("delivery_checkpoint", detail=cp.action)

    if cp.action == "continue":
        pool.logger.log_event("workflow_completed")
        return {"phase": "done"}
    else:
        pool.logger.log_event("delivery_modify_requested", detail=cp.message)
        # 将用户意见保存到 context
        pool.context.set_ctx("delivery_feedback", cp.message)
        return {"phase": "deliver"}  # 重试交付


# ════════════════════════════════════════════════════════
# 图构建
# ════════════════════════════════════════════════════════

def build_graph(pool) -> StateGraph:
    """构建 LangGraph StateGraph。"""

    # 将 pool 注入到所有 node 函数中
    node_funcs = [
        pre_flight_clarify, pm_write_doc, pm_write_criteria, pm_review_doc,
        align_pm_dev,
        dev_design, dev_design_criteria, dev_design_review,
        dev_plan, dev_plan_criteria, dev_plan_review,
        dev_exec_step, dev_review_step,
        align_dev_qa,
        qa_plan, qa_plan_criteria, qa_plan_review,
        qa_exec_test, qa_write_report,
        dev_fix_bug, qa_verify_fix,
        deliver, end_workflow,
    ]
    for fn in node_funcs:
        fn._pool = pool

    # 设置 self_check 函数也能访问 pool
    self_check.__wrapped__ = lambda: None  # 标记
    # 注意：self_check 通过 pool 参数传入，不需要注入

    graph = StateGraph(WorkflowState)

    # ── 注册节点 ────────────────────────────────────────
    graph.add_node("pre_flight_clarify", pre_flight_clarify)

    # Phase 1
    graph.add_node("pm_write_doc", pm_write_doc)
    graph.add_node("pm_write_criteria", pm_write_criteria)
    graph.add_node("pm_review_doc", pm_review_doc)

    # Phase 2
    graph.add_node("align_pm_dev", align_pm_dev)

    # Phase 3
    graph.add_node("dev_design", dev_design)
    graph.add_node("dev_design_criteria", dev_design_criteria)
    graph.add_node("dev_design_review", dev_design_review)

    # Phase 4
    graph.add_node("dev_plan", dev_plan)
    graph.add_node("dev_plan_criteria", dev_plan_criteria)
    graph.add_node("dev_plan_review", dev_plan_review)

    # Phase 5
    graph.add_node("dev_exec_step", dev_exec_step)
    graph.add_node("dev_review_step", dev_review_step)

    # Phase 6
    graph.add_node("align_dev_qa", align_dev_qa)

    # Phase 7
    graph.add_node("qa_plan", qa_plan)
    graph.add_node("qa_plan_criteria", qa_plan_criteria)
    graph.add_node("qa_plan_review", qa_plan_review)

    # Phase 8
    graph.add_node("qa_exec_test", qa_exec_test)
    graph.add_node("qa_write_report", qa_write_report)
    graph.add_node("dev_fix_bug", dev_fix_bug)
    graph.add_node("qa_verify_fix", qa_verify_fix)

    # Phase 9
    graph.add_node("deliver", deliver)
    graph.add_node("end_workflow", end_workflow)

    # ── 边 ──────────────────────────────────────────────
    graph.set_entry_point("pre_flight_clarify")
    graph.add_edge("end_workflow", END)

    # Phase 0 → Phase 1
    graph.add_edge("pre_flight_clarify", "pm_write_doc")

    # Phase 1: PM 方案
    graph.add_edge("pm_write_doc", "pm_write_criteria")
    graph.add_edge("pm_write_criteria", "pm_review_doc")
    graph.add_conditional_edges(
        "pm_review_doc",
        lambda s: s.get("phase", ""),
        {
            "align_pm_dev": "align_pm_dev",
            "pm_write_doc": "pm_write_doc",
            "done": "end_workflow",
        },
    )

    # Phase 2: Align PM→Dev
    graph.add_conditional_edges(
        "align_pm_dev",
        lambda s: s.get("phase", ""),
        {
            "dev_design": "dev_design",
            "align_pm_dev": "align_pm_dev",
            "done": "end_workflow",
        },
    )

    # Phase 3: Dev 详细设计
    graph.add_edge("dev_design", "dev_design_criteria")
    graph.add_edge("dev_design_criteria", "dev_design_review")
    graph.add_conditional_edges(
        "dev_design_review",
        lambda s: s.get("phase", ""),
        {
            "dev_plan": "dev_plan",
            "dev_design": "dev_design",
            "done": "end_workflow",
        },
    )

    # Phase 4: Dev 实现计划
    graph.add_edge("dev_plan", "dev_plan_criteria")
    graph.add_edge("dev_plan_criteria", "dev_plan_review")
    graph.add_conditional_edges(
        "dev_plan_review",
        lambda s: s.get("phase", ""),
        {
            "dev_exec": "dev_exec_step",
            "dev_plan": "dev_plan",
            "done": "end_workflow",
        },
    )

    # Phase 5: Dev 执行循环
    graph.add_edge("dev_exec_step", "dev_review_step")
    graph.add_conditional_edges(
        "dev_review_step",
        dev_exec_router,
        {
            "dev_exec_step": "dev_exec_step",
            "align_dev_qa": "align_dev_qa",
            "end_workflow": "end_workflow",
        },
    )

    # Phase 6: Align Dev→QA
    graph.add_conditional_edges(
        "align_dev_qa",
        lambda s: s.get("phase", ""),
        {
            "qa_plan": "qa_plan",
            "align_dev_qa": "align_dev_qa",
            "done": "end_workflow",
        },
    )

    # Phase 7: QA 测试计划
    graph.add_edge("qa_plan", "qa_plan_criteria")
    graph.add_edge("qa_plan_criteria", "qa_plan_review")
    graph.add_conditional_edges(
        "qa_plan_review",
        lambda s: s.get("phase", ""),
        {
            "qa_exec": "qa_exec_test",
            "qa_plan": "qa_plan",
            "done": "end_workflow",
        },
    )

    # Phase 8: QA 测试循环
    graph.add_edge("qa_exec_test", "qa_write_report")
    graph.add_conditional_edges(
        "qa_write_report",
        qa_report_router,
        {
            "deliver": "deliver",
            "dev_fix_bug": "dev_fix_bug",
            "done": "end_workflow",
        },
    )
    graph.add_edge("dev_fix_bug", "qa_verify_fix")
    graph.add_conditional_edges(
        "qa_verify_fix",
        qa_verify_router,
        {
            "deliver": "deliver",
            "dev_fix_bug": "dev_fix_bug",
            "done": "end_workflow",
        },
    )

    # Phase 9: 交付
    graph.add_conditional_edges(
        "deliver",
        lambda s: s.get("phase", ""),
        {
            "done": END,
            "deliver": "deliver",
        },
    )

    return graph.compile(checkpointer=MemorySaver())


# ════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  AI Coding 工作流框架 — Workflow Engine")
    print("=" * 60)

    # 1. 初始化 AgentPool
    print("\n[1/3] 初始化 AgentPool...")
    pool = setup_pool()
    print("  AgentPool 就绪")

    # 2. 构建图
    print("\n[2/3] 构建 LangGraph...")
    app = build_graph(pool)
    print("  图编译完成")

    # 3. 运行
    print("\n[3/3] 启动工作流...")
    state = _init_state()
    config = {"configurable": {"thread_id": "workflow-run-1"}}
    try:
        for event in app.stream(state, config):
            for node_name, node_state in event.items():
                if node_state is None:
                    print(f"  [{node_name}] 完成（无状态更新）")
                    continue
                phase = node_state.get("phase", "?")
                si = node_state.get("step_index", 0)
                lc = node_state.get("loop_count", 0)
                print(f"  [{node_name}] phase={phase} step={si} loop={lc}")
        print("\n✅ 工作流完成！")
    except Exception as e:
        print(f"\n❌ 工作流出错: {e}")
        traceback.print_exc()
    finally:
        print("\n清理资源...")
        pool.logger.log_event("workflow_ended")
        # AgentPool __exit__ 会自动 stop_gateway


if __name__ == "__main__":
    main()
