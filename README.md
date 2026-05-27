# Hermes-LangGraph

An AI coding workflow framework built with LangGraph, enabling automated code generation and agent orchestration.

## Description

Hermes-LangGraph is a workflow orchestration framework that uses LangGraph to coordinate multiple AI agents for automated coding tasks. It features agent registration, conversation management, and checkpoint-based human-in-the-loop interaction.

## Structure

```
src/
  agent_runtime.py    — Agent registration, lifecycle, and conversation management
  workflow.py         — LangGraph-based workflow orchestration
```

## Tech Stack

- Python
- LangGraph (state machine orchestration)
- LangChain
- Claude API

## Features

- Multi-agent orchestration with LangGraph state graphs
- Conversation management with timestamp-based naming
- Checkpoint system for human review
- Extensible agent registration
