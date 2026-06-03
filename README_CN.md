# Hermes-LangGraph

基于 LangGraph + Hermes Gateway 的 AI 编码工作流框架，通过多 Agent 协作实现自动化代码生成，支持精确中断恢复和对话上下文管理。

## 架构

```
src/workflow/
├── graph.py          — LangGraph StateGraph 图构建与入口
├── config.py         — 常量与 prompt 模板
├── utils.py          — 工具函数：call_agent, register_nodes, clarify_loop 等
├── phase0.py         — PreFlightClarify：需求澄清
├── phase1.py         — PMHandoff ~ HumanReview：PM 出方案
├── phase2.py         — DevHandoff ~ DevEscalate：Dev 设计、编码、提交
├── phase3.py         — qa_handoff, qa_align：QA 对齐
├── flush.py          — MasterFlushClarify/PM/Dev：阶段边界上下文刷新
└── checkpoint.py     — ResumeRouter：检查点保存/加载与恢复路由
```

每个逻辑节点组采用 class + register 模式，通过 `entries`/`exits` 声明对外连接，每个节点只包含一次 `call_agent` 调用，确保精确中断恢复。

## Agent 列表

| Agent | Profile | 端口 | 职责 |
|:------|:--------|:-----|:-----|
| Master | cg | 8642 | 编排决策、维护状态、回答问题 |
| Judge | cg | 8642 | 回复分类（A/B/C/D 路由） |
| Reviewer | cg | 8642 | 按标准审查产出 |
| PM | pm | 8643 | 需求分析、PRD、HTML 原型 |
| Dev | dev | 8644 | 详细设计、编码、git 操作 |
| QA | qa | 8645 | 测试计划和执行 |

## 工作流

```
ResumeRouter → PreFlightClarify → MasterFlush → [PM 阶段] → MasterFlush → [Dev 阶段] → MasterFlush → [QA 阶段] → END
```

- **Phase 0**：用户 ↔ Master 需求澄清，产出 project_context.md
- **Phase 1**：PM 产出 PRD + prototype，经 Reviewer 审查和人工确认
- **Phase 2**：Dev 按计划分步设计、编码、提交，支持回滚和升级人工
- **Phase 3**：QA 与 PM/Dev/Master 对齐，生成测试计划

## 核心功能

- **一节点一 call_agent**：每个 graph 节点只调用一次 agent，Ctrl+U 中断恢复时只重放一个调用
- **上下文刷新**：Master 对话在阶段边界刷新，Dev 对话每步提交后刷新
- **断点续跑**：阶段边界保存 checkpoint，支持从中断处恢复
- **Ctrl+U 中断**：流式输出中按热键中断，进入对话后继续工作流
- **信件通信**：Agent 间通过 `handoffs/` 目录下的 markdown 信件交换信息
- **节点组织规范**：class + register 模式，entries/exits 声明拓扑连接

## 环境要求

- Python 3.10+
- Hermes Gateway（Agent 网关服务）
- LangGraph 1.2.0+

## 快速开始

```bash
python -m src.workflow
```

运行配置在 `runtime_config.json` 中设置。
