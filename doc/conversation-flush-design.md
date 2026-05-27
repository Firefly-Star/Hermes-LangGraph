# Conversation Flush 设计

## 问题

工作流中 agent 的 conversation 会随流程推进持续累积，导致 input_tokens 单调增长。若不干预，长流程（如数十个 Dev step）将触达模型 context window 上限。

## 目的

flush（关闭当前 conversation，开启新 conversation 并重新注入上下文）有两个目的：

1. **防止超窗** — 长流程可能触达 context window 上限，flush 将输入量重置到可控水平
2. **重注约束** — Dev/Master 等 agent 在持续对话中可能逐渐忽略 system prompt 中的约束（如归档路径规则、review 不可跳过等），重新注入约束让其行为回到预期轨道

## 策略

### Dev — 每 step PASS 后 flush

**触发时机**：每个 step 审查 PASS → git commit 后，关闭旧 conversation，下次 exec 开新对话。

**新对话注入内容：**

```
[system prompt — Hermes 自动注入]
## 已完成的工作
{compact_summary}     ← Dev 在 commit 前写的进度摘要

## 项目设计文档
{design.md}           ← 文件内容注入

## 执行计划
{plan.md}             ← 文件内容注入
```

**compact_summary 内容**：由 Dev 在 commit 前自己撰写，包含：
- 已完成的步骤列表
- 每步的关键产出（文件、函数、配置变更）
- 遇到的坑和已解决的问题
- 当前进度（第 N / total 步）

**为什么是文件内容而不是路径**：注入文件内容才能命中 DeepSeek prefix cache。system prompt + design.md + plan.md 构成稳定前缀，从第二步起全部命中 cache read（0.02元/M）。如果只传路径让 agent 自读，每次 prompt 都不同，无 cache 收益。

**终端整洁**：通过 `_resolve_file_refs` 的 `{路径}` 语法，终端显示路径字符串（简短），实际发给 LLM 的是文件内容。

### Master — 每个 major phase 边界 flush

**触发时机**：需求澄清→PM 阶段、PM→Dev 阶段、Dev→QA 阶段、QA→交付，各阶段交接点。

**新对话注入内容：**

```
[system prompt — Hermes 自动注入]
## 项目需求（已确认）
{project_context.md}   ← 需求澄清阶段产出的决策记录

## 项目决策日志
{decision_log}         ← 各阶段 escalate/clarify 记录的关键决策

## 进度摘要
已完成：
- 需求澄清：已确认...
- PM 出方案：PRD 已定，MVP 范围为...
- Dev 步骤 1-6/12：完成了...

当前阶段：Dev 步骤 7，等待实施
```

Master 不需要 design.md 和 plan.md（那是 Dev 的上下文），但需要知道全局进展。

### PM — 不 flush

PM 只参与一个独立的对齐回合（`pm-align` 对话），自然结束。对话长度可控，不需要 flush。

### QA — 不 flush

QA 只参与一个独立的对齐回合，对话长度可控，不需要 flush。

### Judge — 无状态，不追踪

Judge 每次调用都是一锤子分类，使用独立 conversation 名（`judge-{tag}-{ws}-{ts}`），调用即用即弃。不注册活跃，不写 registry，不走 begin/close。

## 软件设计

### _resolve_file_refs

`ConversationManager.call()` 内部自动将 prompt 中的 `{文件路径}` 替换为文件内容。非文件路径的 `{}` 原样保留。

```python
# workflow 中写：
call_agent(runtime, "dev", conv,
    f"下面是上下文：{design_path}\n{plan_path}")

# 终端显示（短）：
──── Request: dev/conv ────
下面是上下文：C:/work/Dev/design.md
C:/work/Dev/plan.md

# LLM 实际收到（长）：
下面是上下文：<design.md 的全部内容>
<plan.md 的全部内容>
```

### 对话生命周期设计（回退方案）

~~曾尝试引入 RAII 风格的 begin/close 显式生命周期管理：开始对话前必须 begin() 注册为活跃，否则 call() 报错。但在实践中发现：~~
- ~~judge 等无状态调用每次用新 conv，begin 完马上 close 纯属多余~~
- ~~大量函数需要追溯 conversation 创建点补 begin()，改动面太大~~
- ~~Python 缺乏 RAII 的自动析构，显式 close 容易被遗漏，最终选择回退~~

当前方案：`call()` 不强制 begin/close，通过 `_resolve_file_refs` 做文件注入。flush 通过 `close_conversation`（从 registry 移除）+ 新 `init_conversation` 实现。

## 注意事项

- conversation 关闭后，agent 仍可通过文件系统和 runtime context 变量获取上下文
- flush 后需重新注入 work 目录、产出路径等关键约束，避免 agent 迷失上下文
- 依赖 Hermes system prompt 在同一 profile 的多个 gateway 实例间稳定一致
- compact summary 由 agent 自己撰写，workflow 只负责存储和传递
