# Hermes-LangGraph

An AI coding workflow framework built with LangGraph + Hermes Gateway, enabling multi-agent collaborative code generation with precise interrupt handling and conversation context management.

## Architecture

```
src/workflow/
├── graph.py          — LangGraph StateGraph construction and entry
├── config.py         — Constants and prompt templates
├── utils.py          — Utilities: call_agent, register_nodes, clarify_loop, etc.
├── phase0.py         — PreFlightClarify: requirement clarification
├── phase1.py         — PMHandoff ~ HumanReview: PM produces PRD + prototype
├── phase2.py         — DevHandoff ~ DevEscalate: Dev design, code, commit
├── phase3.py         — qa_handoff, qa_align: QA alignment
├── flush.py          — MasterFlushClarify/PM/Dev: phase boundary context flush
└── checkpoint.py     — ResumeRouter: checkpoint save/load and resume routing
```

Each logical node group follows a class-based pattern with `entries`/`exits` dicts, one `call_agent` per node for precise interrupt recovery.

## Agents

| Agent | Profile | Port | Role |
|:------|:--------|:-----|:-----|
| Master | cg | 8642 | Orchestration, decision-making, state management |
| Judge | cg | 8642 | Reply classification (A/B/C/D routing) |
| Reviewer | cg | 8642 | Output review against criteria |
| PM | pm | 8643 | Requirements analysis, PRD, HTML prototype |
| Dev | dev | 8644 | Detailed design, coding, git operations |
| QA | qa | 8645 | Test planning and execution |

## Workflow

```
ResumeRouter → PreFlightClarify → MasterFlush → [PM Phase] → MasterFlush → [Dev Phase] → MasterFlush → [QA Phase] → END
```

- **Phase 0**: User ↔ Master clarification, writes project_context.md
- **Phase 1**: PM writes PRD + prototype, reviewed by Reviewer, human review
- **Phase 2**: Dev designs, codes, commits per step plan; includes rollback and escalation
- **Phase 3**: QA aligns with PM, Dev, Master; test plan and execution

## Key Features

- **One call_agent per node**: Each LangGraph node contains exactly one agent call for precise Ctrl+U interrupt recovery
- **Context flush**: Master conversation flushed at phase boundaries; Dev conversation flushed after each step
- **Checkpoint/Resume**: Save checkpoint at phase boundaries, resume from interruption
- **Ctrl+U interrupt**: Interrupt agent response mid-stream, enter dialog, then continue
- **Letter communication**: Agents communicate via markdown letter files in `handoffs/`
- **Node organization**: Class-based pattern with `entries`/`exits`/`register` for clean topology

## Requirements

- Python 3.10+
- Hermes Gateway (agent gateway service)
- LangGraph 1.2.0+

## Quick Start

```bash
python -m src.workflow
```

Configure runtime settings in `runtime_config.json`.
