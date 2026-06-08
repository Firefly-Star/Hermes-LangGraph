"""断线重连 — checkpoint 保存/加载 + resume 节点。"""
import os, json, time, shutil, subprocess
from typing import Optional

from .prompt import FLUSH_CONTINUATION_NOTE, FILE_READING_RULES
from .utils import (conv_name, call_agent, open_master_conv,
                    register_nodes, extract_plan_index,
                    extract_current_step)


def _cp_path(runtime) -> str:
    return runtime.paths.checkpoint


def save_checkpoint(runtime, resume_node, phase_name, step_idx=0, summary_path=""):
    """在 phase 边界 / dev step 完成后保存断点。"""
    cp = {
        "version": 1,
        "resume_node": resume_node,
        "phase_name": phase_name,
        "step_idx": step_idx,
        "summary_path": summary_path,
        "timestamp": time.time(),
    }
    path = _cp_path(runtime)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=2)
    step_info = f" Step {step_idx}" if step_idx else ""
    runtime.msg.ok(f"Checkpoint 已保存: {resume_node}（{phase_name}）{step_info}")
    runtime.logger.log_event("checkpoint_saved",
                             detail=f"resume_at={resume_node}, phase={phase_name}")


def load_checkpoint(runtime) -> Optional[dict]:
    path = _cp_path(runtime)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def clear_checkpoint(runtime):
    path = _cp_path(runtime)
    if os.path.exists(path):
        os.remove(path)


# ── helpers ──────────────────────────────────────────────

def _clean_targets(runtime, targets):
    """删除 targets 中的文件/目录（Windows 兼容）。"""
    def _rmtree_win(path):
        def _onerror(func, p, exc_info):
            # Windows 上 node_modules 等文件可能只读，重试一次
            try:
                os.chmod(p, 0o777)
                func(p)
            except Exception:
                pass
        shutil.rmtree(path, onerror=_onerror)

    for t in targets:
        if os.path.isdir(t):
            _rmtree_win(t)
            runtime.msg.ok(f"清理目录: {t}")
        elif os.path.isfile(t):
            try:
                os.remove(t)
            except PermissionError:
                os.chmod(t, 0o777)
                os.remove(t)
            runtime.msg.ok(f"清理文件: {t}")


def _restore_dev_conv(runtime, step_idx):
    """重新创建 Dev 执行对话 + git reset + 重置步进状态。"""
    dev_principles = runtime.context.get_bg("dev_principles")
    dev_dir = os.path.join(runtime.paths.workspace, "Dev")

    # 编排层直接执行 git reset，避免 agent 触达红线操作
    git_dir = os.path.join(dev_dir, ".git")
    if os.path.exists(git_dir):
        subprocess.run(["git", "-C", dev_dir, "reset", "--hard", "HEAD"],
                       capture_output=True, timeout=30)

    def _read(p):
        fp = os.path.join(dev_dir, p)
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    summary_text = ""
    summary_path = os.path.join(runtime.paths.phases, "compact-summary.md")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            summary_text = f.read()

    design_summary = _read("design-summary.md")
    design_index = _read("design-index.md")
    plan_text = _read("plan.md")
    plan_index = extract_plan_index(plan_text, step_idx)
    current_step = extract_current_step(plan_text, step_idx)

    injected = (
        f"{dev_principles}{FLUSH_CONTINUATION_NOTE}"
        f"你的工作目录：{dev_dir}\n\n"
        "请先按顺序执行以下操作，**不要询问确认，直接执行命令**:\n"
        "1. 阅读以下内容了解已完成的工作和计划：\n\n"
        f"## 已完成的工作\n{summary_text}\n\n"
        f"## 项目设计概要\n{design_summary}\n\n"
        f"## 设计文件索引\n{design_index}\n\n"
        f"## 计划进度\n{plan_index}\n\n"
        f"## 当前步骤详细内容\n{current_step}\n\n"
        f"{FILE_READING_RULES}"
        "在Master给你下达命令之前，你只能阅读上下文，不能进行任何产"
        "出，包括修改、创建任何文件，后续Master会给你下达任务。"
        "不要询问你是否要执行这些操作，直接去做。"
    )
    new_conv = conv_name("dev-exec")
    call_agent(runtime, "dev", new_conv, injected)
    runtime.context.set_ctx("dev_conv", new_conv)

    runtime.context.set_ctx("dev_step_index", str(step_idx))
    runtime.context.set_ctx("dev_step_fail_count", "0")
    runtime.context.set_ctx("dev_step_has_failed", "false")
    runtime.context.set_ctx("dev_step_review_feedback", "")
    runtime.context.set_ctx("dev_escalation_decision", "")
    runtime.msg.ok(f"步进状态已重置（step_idx={step_idx}, fail_count=0）")


# ── ResumeRouter 类 ──────────────────────────────────────


class ResumeRouter:
    """断线重连路由及恢复节点组。入口 + 4 恢复节点 + 1 空节点。"""

    entries = {"router": "resume_router"}
    exits = {"to_pre_flight": "resume_to_pre_flight",
             "resume_pm": "resume_pm_handoff",
             "resume_dev": "resume_dev_handoff",
             "resume_qa": "resume_qa_handoff",
             "resume_dev_exec": "resume_dev_exec_step",
             "resume_phase4": "resume_phase4_handoff"}

    _runtime = None

    @staticmethod
    def router(state) -> dict:
        """入口节点：检查 checkpoint + 询问用户，路由到对应恢复节点或从头开始。"""
        runtime = ResumeRouter._runtime
        cp = load_checkpoint(runtime)

        if cp is None:
            return {"phase": "pre_flight"}

        step_info = f"（第 {cp['step_idx'] + 1} 步）" if "step_idx" in cp else ""
        runtime.msg.phase(f"检测到上次运行中断于「{cp['phase_name']}」{step_info}")

        cp_obj = runtime.checkpoint.wait(
            "重连确认",
            f"输入 y 从「{cp['phase_name']}」继续，直接 EOF 从头开始：",
            prompt="> ",
        )
        if cp_obj.message.strip().lower() in ("y", "yes"):
            return {"phase": f"resume_{cp['resume_node']}"}

        clear_checkpoint(runtime)
        runtime.msg.ok("从头开始")
        return {"phase": "pre_flight"}

    @staticmethod
    def to_pre_flight(state) -> dict:
        """空节点：路由到 PreFlightClarify。"""
        return state

    @staticmethod
    def resume_pm(state) -> dict:
        """恢复 pm 阶段：清产出 + 清 context + 重建 Master 对话。"""
        runtime = ResumeRouter._runtime
        ws = runtime.paths.workspace
        _clean_targets(runtime, [
            runtime.paths.handoffs,
            os.path.join(ws, "PM"),
            os.path.join(ws, "criteria-pm.md"),
        ])
        # 清理 PM 阶段 context 残留，避免拿着上一轮的路径/轮次去找已被删除的信件
        for key in ("pm_align_round", "masterletter_path", "pmletter_path",
                     "pm_reply_path", "pm_reply_text", "pm_conv", "master_reply",
                     "clarify_reason"):
            runtime.context.set_ctx(key, "")
        cp = load_checkpoint(runtime)
        open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
        runtime.msg.ok("从 PM 阶段继续")
        return {"phase": "pm_handoff"}

    @staticmethod
    def resume_dev(state) -> dict:
        """恢复 dev 阶段：清产出 + 清 context + 重建 Master 对话。"""
        runtime = ResumeRouter._runtime
        ws = runtime.paths.workspace
        _clean_targets(runtime, [
            runtime.paths.handoffs,
            os.path.join(ws, "Dev"),
        ])
        # 清理 Dev 阶段所有 context 残留
        for key in ("devletter_path", "dev_conv", "dev_reply_path", "pm_conv",
                     "pm_reply_text", "pm_reply_path", "dev_feedback_path",
                     "master_reply_path", "dev_criteria_feedback_path",
                     "review_text", "design_feedback_path", "designletter_path",
                     "design_path", "plan_feedback_path", "planletter_path",
                     "plan_path", "dev_step_index", "dev_total_steps",
                     "dev_step_fail_count", "dev_step_has_failed",
                     "dev_git_dir", "dev_exec_dir", "exec_letter_path",
                     "dev_step_review_feedback", "commit_step_idx",
                     "commit_summary_path", "commit_design_path", "commit_plan_path",
                     "dev_escalation_decision"):
            runtime.context.set_ctx(key, "")
        cp = load_checkpoint(runtime)
        open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
        runtime.msg.ok("从 Dev 阶段继续")
        return {"phase": "dev_handoff"}

    @staticmethod
    def resume_qa(state) -> dict:
        """恢复 qa 阶段：清产出 + 清 context + 重建 Master 对话。"""
        runtime = ResumeRouter._runtime
        ws = runtime.paths.workspace
        _clean_targets(runtime, [
            runtime.paths.handoffs,
            os.path.join(ws, "QA"),
        ])
        # 清理 QA 阶段 context 残留
        for key in ("qaletter_path", "qa_feedback_path", "qa_understanding_path",
                     "qa_plan_path", "qa_plan_feedback_path", "qa_plan_review",
                     "qa_code_path", "qa_code_feedback_path", "qa_code_review",
                     "qa_test_report_path", "qa_bug_report_path", "qa_conv"):
            runtime.context.set_ctx(key, "")
        # 同时也清理 Dev/PM 对齐阶段的 pm_conv/dev_conv，避免 QA 拿到旧对话
        for key in ("pm_conv", "dev_conv"):
            runtime.context.set_ctx(key, "")
        cp = load_checkpoint(runtime)
        open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
        runtime.msg.ok("从 QA 阶段继续")
        return {"phase": "qa_handoff"}

    @staticmethod
    def resume_dev_exec(state) -> dict:
        """恢复 dev 执行：清 handoffs + git reset + 重建 Dev 对话，不碰 Master。"""
        runtime = ResumeRouter._runtime
        _clean_targets(runtime, [
            runtime.paths.handoffs,
        ])
        cp = load_checkpoint(runtime)
        _restore_dev_conv(runtime, cp.get("step_idx", 0) if cp else 0)
        runtime.msg.ok("从 Dev 执行继续")
        return {"phase": "dev_exec_step"}

    @staticmethod
    def resume_phase4(state) -> dict:
        """恢复交付阶段：清 handoffs + 重建 Master 对话。"""
        runtime = ResumeRouter._runtime
        _clean_targets(runtime, [
            runtime.paths.handoffs,
        ])
        cp = load_checkpoint(runtime)
        open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
        clear_checkpoint(runtime)
        runtime.msg.ok("从交付阶段继续")
        return {"phase": "consistency_audit"}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {
            "resume_router": cls.router,
            "resume_to_pre_flight": cls.to_pre_flight,
            "resume_pm_handoff": cls.resume_pm,
            "resume_dev_handoff": cls.resume_dev,
            "resume_qa_handoff": cls.resume_qa,
            "resume_dev_exec_step": cls.resume_dev_exec,
            "resume_phase4_handoff": cls.resume_phase4,
        })
        graph.add_conditional_edges("resume_router", lambda s: s.get("phase", ""), {
            "pre_flight": "resume_to_pre_flight",
            "resume_pm_handoff": "resume_pm_handoff",
            "resume_dev_handoff": "resume_dev_handoff",
            "resume_qa_handoff": "resume_qa_handoff",
            "resume_dev_exec_step": "resume_dev_exec_step",
            "resume_consistency_audit": "resume_phase4_handoff",
        })
