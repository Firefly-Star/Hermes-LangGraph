# Hermes-LangGraph

An AI coding workflow framework built with LangGraph + Hermes Gateway, enabling multi-agent collaborative code generation with precise interrupt handling and conversation context management.

## Architecture

```
src/workflow/
├── __init__.py       # Package marker
├── __main__.py       # Entry point: python -m src.workflow
├── graph.py          # LangGraph StateGraph construction and entry
├── prompt.py         # Constants and prompt templates
├── utils.py          # Utilities: call_agent, register_nodes, clarify_loop, etc.
├── phase0.py         # PreFlightClarify: requirement clarification
├── phase1.py         # PMHandoff ~ HumanReview: PM produces PRD + prototype
├── phase2.py         # DevHandoff ~ DevEscalate: Dev design, code, commit
├── phase3.py         # QA full pipeline: criteria → plan → code → run → fix loop
├── phase4.py         # ConsistencyAudit → WriteMaintenanceDocs → DeliverySummary
├── flush.py          # MasterFlushClarify/PM/Dev/QA: phase boundary context flush
└── checkpoint.py     # ResumeRouter: checkpoint save/load and resume routing
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
ResumeRouter → [Phase 0: Clarify] → Flush → [Phase 1: PM] → Flush → [Phase 2: Dev] → Flush → [Phase 3: QA] → Flush → [Phase 4: Delivery] → END
```

- **Phase 0**: User ↔ Master clarification, writes project_context.md
- **Phase 1**: PM writes PRD + prototype, reviewed by Reviewer and human
- **Phase 2**: Dev designs, codes, commits per step plan; includes rollback and escalation
- **Phase 3**: QA full pipeline: criteria → test plan → test code → run → bug fix loop
- **Phase 4**: Consistency audit → maintenance docs → delivery summary

## Key Features

- **One call_agent per node**: Each LangGraph node contains exactly one agent call for precise Ctrl+U interrupt recovery
- **Context flush**: Master conversation flushed at phase boundaries; Dev conversation flushed after each step
- **Checkpoint/Resume**: Save checkpoint at phase boundaries, resume from interruption
- **Ctrl+U interrupt**: Interrupt agent response mid-stream, enter dialog, then continue
- **Letter communication**: Agents communicate via markdown letter files in `handoffs/`
- **Output routing**: Console and/or file output via `sys.stdout` replacement (OutputLayer)
- **Node organization**: Class-based pattern with `entries`/`exits`/`register` for clean topology

## Requirements

- Python 3.10+
- Hermes Gateway (agent gateway service)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure runtime (edit runtime_config.json)
# Run the workflow
python -m src.workflow
```

Configuration is managed in `runtime_config.json`. See [config-reference.md](doc/config-reference.md) for details.
