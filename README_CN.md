# Hermes-LangGraph

基于 LangGraph 的 AI 编码工作流框架，实现自动化代码生成与智能体编排。

## 描述

Hermes-LangGraph 是一个工作流编排框架，使用 LangGraph 协调多个 AI 智能体完成自动化编码任务。支持智能体注册、对话管理和基于检查点的人机协作。

## 结构

```
src/
  agent_runtime.py    — 智能体注册、生命周期和对话管理
  workflow.py         — 基于 LangGraph 的工作流编排
```

## 技术栈

- Python
- LangGraph（状态机编排）
- LangChain
- Claude API

## 功能

- 基于 LangGraph 的多智能体编排
- 带时间戳的对话管理
- 人工审核检查点系统
- 可扩展的智能体注册
