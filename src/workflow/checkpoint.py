"""断线重连 — checkpoint 保存/加载/resume_router。"""
import os, json, time, shutil
from typing import Optional

from .config import FLUSH_CONTINUATION_NOTE, CHECKPOINT_FILE, HANDOFFS_DIR
from .utils import conv_name, open_master_conv


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


def _restore_dev_conv(runtime):
    """重新创建 Dev 执行对话。"""
    dev_principles = runtime.context.get_bg("dev_principles")
    ws = runtime.workspace

    def _read(p):
        fp = os.path.join(ws, "Dev", p)
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
                f"## 执行计划\n{plan_text}")
    new_conv = conv_name("dev-exec")
    runtime.conversations.begin("dev", new_conv, injected)
    runtime.context.set_ctx("dev_conv", new_conv)


def _clean_next_phase(runtime, resume_node):
    """清理下一阶段的产出目录 + handoff 信件，避免上次失败的残留干扰重连。"""
    ws = runtime.workspace
    targets = [os.path.join(runtime.runtime_dir, HANDOFFS_DIR)]
    if resume_node == "pm_handoff":
        targets.append(os.path.join(ws, "PM"))
        targets.append(os.path.join(ws, "criteria-pm.md"))
    elif resume_node == "dev_handoff":
        targets.append(os.path.join(ws, "Dev"))
    elif resume_node == "qa_handoff":
        targets.append(os.path.join(ws, "QA"))
    # dev_exec_step: 不清，git 管理代码
    for t in targets:
        if os.path.isdir(t):
            shutil.rmtree(t)
            print(f"  清理目录: {t}")
        elif os.path.isfile(t):
            os.remove(t)
            print(f"  清理文件: {t}")


def resume_router(state) -> dict:
    """Graph 入口节点：检查 checkpoint 并路由到继续或从头开始。"""
    runtime = getattr(resume_router, "_runtime", None)
    cp = load_checkpoint(runtime)

    if cp is None:
        return {"phase": "pre_flight"}

    step_info = f"（第 {cp['step_idx'] + 1} 步）" if cp.get("step_idx") else ""
    print(f"\n{'='*60}")
    print(f"  检测到上次运行中断于「{cp['phase_name']}」{step_info}")
    print(f"{'='*60}")

    cp_obj = runtime.checkpoint.wait(
        "重连确认",
        f"输入 y 从「{cp['phase_name']}」继续，直接 EOF 从头开始：",
        prompt="> ",
    )
    user_input = cp_obj.message.strip().lower()

    if user_input in ("y", "yes"):
        resume_node = cp["resume_node"]
        _clean_next_phase(runtime, resume_node)
        open_master_conv(runtime, cp.get("summary_path", ""))
        if resume_node == "dev_exec_step":
            _restore_dev_conv(runtime)

        print(f"  → 从 {cp['phase_name']} 继续")
        runtime.logger.log_event("workflow_resumed",
                                 detail=f"resume_at={resume_node}")
        return {"phase": resume_node}

    clear_checkpoint(runtime)
    print(f"  → 从头开始")
    return {"phase": "pre_flight"}
