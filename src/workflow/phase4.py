"""Phase 4: 交付阶段 — 一致性审计 + 维护文档 + 交付总结。"""
import os

from .utils import conv_name, call_agent, ensure_write_file, register_nodes
from .checkpoint import clear_checkpoint


class ConsistencyAudit:
    """Master 做四方一致性审计 (1 call_agent)。"""

    entries = {"run": "consistency_audit"}
    exits = {"run": "consistency_audit"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = ConsistencyAudit._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        ws = runtime.paths.workspace
        pc_path = runtime.context.get_bg("project_context_path") or ""

        runtime.msg.phase("一致性审计")
        runtime.logger.log_event("phase_started", detail="一致性审计")

        audit_path = os.path.join(ws, "audit-report.md")

        artifacts = (
            f"- PRD: {ws}/PM/PRD.md\n"
            f"- 原型: {ws}/PM/prototype.html\n"
            f"- 详细设计: {ws}/Dev/design.md\n"
            f"- 代码目录: {ws}/Dev/\n"
            f"- 测试代码: {ws}/QA/tests/\n"
            f"- 测试报告: {ws}/QA/test-report.md\n"
        )
        if pc_path:
            artifacts += f"- 项目决策记录: {pc_path}\n"

        call_agent(runtime, "master", master_conv,
            "你即将对项目做一次全面的四方一致性审计。\n\n"
            "## 审计要求\n"
            "请先使用 read_file 工具逐一读取以下文件，然后检查：\n\n"
            "### 1. 需求 vs 方案\n"
            "- PRD 中的每个功能点在 design.md 中是否有对应的实现方案？\n\n"
            "### 2. 方案 vs 代码\n"
            "- design.md 中的每个组件/接口在 Dev/ 代码目录中是否有对应的实现？\n\n"
            "### 3. 代码 vs 测试\n"
            "- 核心功能路径是否有测试覆盖？测试是否通过？\n\n"
            "### 4. 配置一致性\n"
            "- runtime_config.json 与代码中的配置引用是否对应？\n\n"
            "## 审计范围文件\n"
            f"{artifacts}\n"
            "## 输出\n"
            "将审计报告完整写入文件，包含：\n"
            "1. 每项检查的结果\n"
            "2. 不一致项的严重程度（阻塞 / 建议）\n"
            "3. 针对不一致项的修复建议（仅建议，不要自动修改代码）\n\n"
            f"请将报告写入：{audit_path}")

        if not ensure_write_file(runtime, "master", master_conv, audit_path):
            call_agent(runtime, "master", master_conv,
                       f"将审计报告写入文件 {audit_path}，使用 write_file 工具。")

        clear_checkpoint(runtime)
        runtime.context.set_ctx("audit_path", audit_path)
        runtime.msg.ok(f"一致性审计报告已写入 {audit_path}")
        return {"phase": "audit_done", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"consistency_audit": cls.run})


class WriteMaintenanceDocs:
    """Dev 写维护文档 (1 call_agent)。"""

    entries = {"run": "write_maintenance_docs"}
    exits = {"run": "write_maintenance_docs"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = WriteMaintenanceDocs._runtime
        dev_doc_conv = conv_name("dev-doc")
        ws = runtime.paths.workspace
        dev_dir = os.path.join(ws, "Dev")
        pc_path = runtime.context.get_bg("project_context_path") or ""

        runtime.msg.phase("写维护文档")
        runtime.logger.log_event("phase_started", detail="写维护文档")

        readme_path = os.path.join(ws, "README.md")
        deploy_path = os.path.join(ws, "deployment-guide.md")

        call_agent(runtime, "dev", dev_doc_conv,
            "请编写项目的维护文档。\n\n"
            "## 参考文件\n"
            f"- 项目决策记录: {pc_path}\n"
            f"- PRD: {ws}/PM/PRD.md\n"
            f"- 详细设计: {ws}/Dev/design.md\n"
            f"- 代码目录: {dev_dir}\n\n"
            "## 产出要求\n"
            "请一次性编写以下两份文档：\n\n"
            "### 1. README.md\n"
            f"写入: {readme_path}\n"
            "内容：\n"
            "- 项目名称和简介\n"
            "- 技术栈\n"
            "- 快速启动指南（安装、配置、运行）\n"
            "- 项目结构说明\n\n"
            "### 2. deployment-guide.md\n"
            f"写入: {deploy_path}\n"
            "内容：\n"
            "- 环境要求\n"
            "- 部署步骤\n"
            "- 配置说明\n"
            "- 运维注意事项\n\n"
            "注意：直接编写，不需要出设计方案，不需要分步。")

        for path, name in [(readme_path, "README.md"), (deploy_path, "部署指南")]:
            if not ensure_write_file(runtime, "dev", dev_doc_conv, path):
                call_agent(runtime, "dev", dev_doc_conv,
                           f"请将 {name} 写入文件 {path}。")

        runtime.context.set_ctx("readme_path", readme_path)
        runtime.context.set_ctx("deploy_path", deploy_path)
        runtime.msg.ok(f"维护文档已写入 {readme_path}, {deploy_path}")
        return {"phase": "docs_written", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"write_maintenance_docs": cls.run})


class DeliverySummary:
    """Master 写交付总结 (1 call_agent)。"""

    entries = {"run": "delivery_summary"}
    exits = {"run": "delivery_summary"}

    _runtime = None

    @staticmethod
    def run(state) -> dict:
        runtime = DeliverySummary._runtime
        master_conv = runtime.context.get_ctx("master_conv")
        ws = runtime.paths.workspace

        runtime.msg.phase("交付总结")
        runtime.logger.log_event("phase_started", detail="交付总结")

        summary_path = os.path.join(ws, "delivery-summary.md")

        audit_path = runtime.context.get_ctx("audit_path") or ""
        readme_path = runtime.context.get_ctx("readme_path") or ""
        deploy_path = runtime.context.get_ctx("deploy_path") or ""

        artifacts = (
            f"- PRD: {ws}/PM/PRD.md\n"
            f"- 原型: {ws}/PM/prototype.html\n"
            f"- 审核标准: {ws}/criteria-pm.md, {ws}/criteria-qa.md\n"
            f"- 设计文档: {ws}/Dev/design.md\n"
            f"- 实现计划: {ws}/Dev/plan.md\n"
            f"- 代码: {ws}/Dev/\n"
            f"- 测试计划: {ws}/QA/test-plan.md\n"
            f"- 测试代码: {ws}/QA/tests/\n"
            f"- 测试报告: {ws}/QA/test-report.md\n"
        )
        if audit_path:
            artifacts += f"- 审计报告: {audit_path}\n"
        if readme_path:
            artifacts += f"- README: {readme_path}\n"
        if deploy_path:
            artifacts += f"- 部署指南: {deploy_path}\n"

        call_agent(runtime, "master", master_conv,
            "请编写项目交付总结。\n\n"
            "## 参考文件\n"
            "请先使用 read_file 工具逐一读取关键文件了解项目全貌，"
            "然后编写总结。\n\n"
            "## 项目产出物清单\n"
            f"{artifacts}\n"
            "## 总结内容要求\n"
            f"请将总结写入 {summary_path}，包含：\n"
            "1. 项目概述\n"
            "2. 各阶段产出物清单\n"
            "3. 审计结论摘要\n"
            "4. 已知问题 / 风险\n"
            "5. 后续维护建议\n"
            "6. 交付物清单（所有文件路径）")

        if not ensure_write_file(runtime, "master", master_conv, summary_path):
            call_agent(runtime, "master", master_conv,
                       f"将交付总结写入文件 {summary_path}。")

        runtime.context.set_ctx("delivery_summary_path", summary_path)
        runtime.msg.ok(f"交付总结已写入 {summary_path}")
        runtime.msg.ok("项目交付完成")
        runtime.msg.sep()
        return {"phase": "delivery_done", "judge_result": ""}

    @classmethod
    def register(cls, graph, runtime):
        cls._runtime = runtime
        register_nodes(graph, runtime, {"delivery_summary": cls.run})
