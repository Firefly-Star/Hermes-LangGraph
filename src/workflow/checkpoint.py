"""断线重连 — checkpoint 保存/加载 + resume 节点。"""
import os, json, time, shutil
from typing import Optional

from .config import FLUSH_CONTINUATION_NOTE, CHECKPOINT_FILE, HANDOFFS_DIR
from .utils import conv_name, call_agent, open_master_conv


def _cp_path(runtime) -> str:
    return os.path.join(runtime.runtime_dir, CHECKPOINT_FILE)


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
    print(f"  ── Checkpoint 已保存: {resume_node}（{phase_name}）{step_info}")
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
    """删除 targets 中的文件/目录。"""
    for t in targets:
        if os.path.isdir(t):
            shutil.rmtree(t)
            print(f"  清理目录: {t}")
        elif os.path.isfile(t):
            os.remove(t)
            print(f"  清理文件: {t}")


def _restore_dev_conv(runtime, step_idx):
    """重新创建 Dev 执行对话 + git reset + 重置步进状态。"""
    dev_principles = runtime.context.get_bg("dev_principles")
    dev_dir = os.path.join(runtime.workspace, "Dev")

    try:
        import subprocess
        subprocess.run(["git", "reset", "--hard", "HEAD"],
                       cwd=dev_dir, capture_output=True, timeout=15)
        print(f"  → git reset --hard HEAD (clean)")
    except Exception:
        print(f"  → git reset 跳过（{dev_dir} 可能不是 git 仓库）")

    def _read(p):
        fp = os.path.join(dev_dir, p)
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    summary_text = _read("compact-summary.md")
    design_text = _read("design.md")
    plan_text = _read("plan.md")

    injected = (f"{dev_principles}{FLUSH_CONTINUATION_NOTE}"
                f"## 已完成的工作\n{summary_text}\n\n"
                f"## 项目设计文档\n{design_text}\n\n"
                f"## 执行计划\n{plan_text}\n"
                "在Master给你下达命令之前，你只能阅读上下文，不能进行任何产出，包括修改、创建任何文件，"
                "后续Master会给你下达任务。")
    new_conv = conv_name("dev-exec")
    call_agent(runtime, "dev", new_conv, injected)
    runtime.context.set_ctx("dev_conv", new_conv)

    runtime.context.set_ctx("dev_step_index", str(step_idx))
    runtime.context.set_ctx("dev_step_fail_count", "0")
    runtime.context.set_ctx("dev_step_has_failed", "false")
    runtime.context.set_ctx("dev_step_review_feedback", "")
    runtime.context.set_ctx("dev_escalation_decision", "")
    print(f"  → 步进状态已重置（step_idx={step_idx}, fail_count=0）")


# ── resume 节点 ──────────────────────────────────────────

def resume_router(state) -> dict:
    """入口节点：仅检查 checkpoint + 询问用户，路由到对应 resume 节点或从头开始。"""
    runtime = getattr(resume_router, "_runtime", None)
    cp = load_checkpoint(runtime)

    if cp is None:
        return {"phase": "pre_flight"}

    step_info = f"（第 {cp['step_idx'] + 1} 步）" if "step_idx" in cp else ""
    print(f"\n{'='*60}")
    print(f"  检测到上次运行中断于「{cp['phase_name']}」{step_info}")
    print(f"{'='*60}")

    cp_obj = runtime.checkpoint.wait(
        "重连确认",
        f"输入 y 从「{cp['phase_name']}」继续，直接 EOF 从头开始：",
        prompt="> ",
    )
    if cp_obj.message.strip().lower() in ("y", "yes"):
        return {"phase": f"resume_{cp['resume_node']}"}

    clear_checkpoint(runtime)
    print(f"  → 从头开始")
    return {"phase": "pre_flight"}


def resume_pm_handoff(state) -> dict:
    """恢复 pm 阶段：清产出 + 重建 Master 对话。"""
    runtime = getattr(resume_pm_handoff, "_runtime", None)
    ws = runtime.workspace
    _clean_targets(runtime, [
        os.path.join(runtime.runtime_dir, HANDOFFS_DIR),
        os.path.join(ws, "PM"),
        os.path.join(ws, "criteria-pm.md"),
    ])
    cp = load_checkpoint(runtime)
    open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
    print(f"  → 从 PM 阶段继续")
    return {"phase": "pm_handoff"}


def resume_dev_handoff(state) -> dict:
    """恢复 dev 阶段：清产出 + 重建 Master 对话。"""
    runtime = getattr(resume_dev_handoff, "_runtime", None)
    ws = runtime.workspace
    _clean_targets(runtime, [
        os.path.join(runtime.runtime_dir, HANDOFFS_DIR),
        os.path.join(ws, "Dev"),
    ])
    cp = load_checkpoint(runtime)
    open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
    print(f"  → 从 Dev 阶段继续")
    return {"phase": "dev_handoff"}


def resume_qa_handoff(state) -> dict:
    """恢复 qa 阶段：清产出 + 重建 Master 对话。"""
    runtime = getattr(resume_qa_handoff, "_runtime", None)
    ws = runtime.workspace
    _clean_targets(runtime, [
        os.path.join(runtime.runtime_dir, HANDOFFS_DIR),
        os.path.join(ws, "QA"),
    ])
    cp = load_checkpoint(runtime)
    open_master_conv(runtime, cp.get("summary_path", "") if cp else "")
    print(f"  → 从 QA 阶段继续")
    return {"phase": "qa_handoff"}


def resume_dev_exec_step(state) -> dict:
    """恢复 dev 执行：清 handoffs + git reset + 重建 Dev 对话，不碰 Master。"""
    runtime = getattr(resume_dev_exec_step, "_runtime", None)
    _clean_targets(runtime, [
        os.path.join(runtime.runtime_dir, HANDOFFS_DIR),
    ])
    cp = load_checkpoint(runtime)
    _restore_dev_conv(runtime, cp.get("step_idx", 0) if cp else 0)
    print(f"  → 从 Dev 执行继续")
    return {"phase": "dev_exec_step"}
