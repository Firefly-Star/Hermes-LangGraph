# Conversation Flush 设计

## 问题

工作流中 agent 的 conversation 会随流程推进持续累积，导致 input_tokens 单调增长。若不干预，长流程（如数十个 Dev step）将触达模型 context window 上限。

## 目的

flush（关闭当前 conversation，开启新 conversation 并重新注入上下文）有两个目的：

1. **防止超窗** — 长流程可能触达 context window 上限，flush 将输入量重置到可控水平
2. **重注约束** — Dev/Master 等 agent 在持续对话中可能逐渐忽略 system prompt 中的约束（如归档路径规则、review 不可跳过等），重新注入约束让其行为回到预期轨道

## 策略

| Agent | flush 时机 | 策略 |
|-------|-----------|------|
| Dev | 每个 step PASS 后（与 git commit 一起） | 关闭当前 conversation，下次 exec 开启新 conversation，注入 design.md + plan.md |
| Master | 每个 major phase 边界 | 写入摘要到上下文，重启 conversation |
| PM/QA | 不涉及 | 单次对齐对话，自然结束 |

## 注意事项

- conversation 关闭后，agent 仍可通过文件系统和 runtime context 变量获取上下文
- flush 后需重新注入 work 目录、产出路径等关键约束，避免 agent 迷失上下文
- 依赖 Hermes system prompt 在同一 profile 的多个 gateway 实例间稳定一致
