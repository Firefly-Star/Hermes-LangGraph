# 工作流机制设计

---

## 1. Conversation Flush

### 问题

工作流中 agent 的 conversation 会随流程推进持续累积，导致 input_tokens 单调增长。若不干预，长流程（如数十个 Dev step）将触达模型 context window 上限。

### 目的

flush（关闭当前 conversation，开启新 conversation 并重新注入上下文）有两个目的：

1. **防止超窗** — 长流程可能触达 context window 上限，flush 将输入量重置到可控水平
2. **重注约束** — Dev/Master 等 agent 在持续对话中可能逐渐忽略 system prompt 中的约束（如归档路径规则、review 不可跳过等），重新注入约束让其行为回到预期轨道

### 策略

#### Dev — 每 step PASS 后 flush + 执行前 flush

**触发时机 1**：计划审查通过、开始执行前（DevGitInit），关闭 align/design/plan 阶段的旧对话，开新对话准备 exec。

**触发时机 2**：每个 step 审查 PASS → git commit 后（DevCommit），关闭旧 conversation，下次 exec 开新对话。

**新对话注入内容：**

```
[system prompt — Hermes 自动注入]
[system prompt - workflow 定义的规范]

## 项目设计文档
{design.md}           ← 文件内容注入

## 执行计划
{plan.md}             ← 文件内容注入

## 已完成的工作
{compact_summary}     ← Dev 在 commit 前写的进度摘要
```

**为什么是文件内容而不是路径**：注入文件内容才能命中 DeepSeek prefix cache。system prompt + design.md + plan.md 构成稳定前缀，从第二步起全部命中 cache read。如果只传路径让 agent 自读，每次 prompt 都不同，无 cache 收益。

**终端整洁**：通过 `_resolve_file_refs` 的 `{路径}` 语法，终端显示路径字符串（简短），实际发给 LLM 的是文件内容。

#### Master — 每个 major phase 边界 flush

**触发时机**：需求澄清→PM 阶段、PM→Dev 阶段、Dev→QA 阶段、QA→交付，各阶段交接点。

**额外参数**：flush 时传入本阶段实际产出文件路径（`phase_summary_path`），Master 据此写准确的阶段总结，避免产生"尚未交付"类幻觉。

**新对话注入内容：**

```
[system prompt — Hermes 自动注入]
[system prompt - workflow 定义的规范]

## 项目需求（已确认）
{artifacts/project_context.md}

## 进度摘要
已完成：
- 需求澄清：已确认...
- PM 出方案：PRD 已定，MVP 范围为...
- Dev 步骤 1-6/12：完成了...

当前阶段：Dev 步骤 7，等待实施
```

Master 不需要 design.md 和 plan.md（那是 Dev 的上下文），但需要知道全局进展。

**Checkpoint**：每次 Master flush 同时保存 checkpoint，记录 resume_node 和阶段名，支持断线重连。

#### QA / Judge — 不 flush

QA 只参与一个独立的对齐回合，对话长度可控，不需要 flush。

Judge 每次调用都是一锤子分类，使用独立 conversation 名（`judge-{tag}-{ws}-{ts}`），调用即用即弃。不注册活跃，不写 registry，不走 close/track。

### compact_summary 模板

#### Dev — 每 step 提交前

由 Dev 在 commit 前自己撰写，模仿 Claude Code 的 compact 格式：

```
Summary:
1. Primary Request and Intent:
   - 当前 step 要实现什么功能
   - 涉及哪些模块／文件

2. Key Technical Concepts:
   - 本次实现中涉及的技术要点（框架、API、数据库等）
   - 配置变更（新依赖、环境变量、端口等）

3. Files and Code Sections:
   - 具体到文件路径和行号范围：`src/xxx.py:120-150`
   - 新增了什么文件、修改了什么文件
   - 关键函数/类的变更

4. Errors and fixes:
   - 踩了什么坑（编译错误、类型不匹配、依赖版本等）
   - 怎么解决的

5. Dependencies / Assumptions:
   - 当前 step 产出的东西依赖什么外部条件
   - 对后续步骤的假设

6. Current Status:
   - 已完成: Step N / total
   - 下一步要做什么
```

#### Master — 每个 phase 结束时

由 Workflow 在 phase 边界生成（通过 call_agent 让 Master 自己写），侧重全局而非单步：

```
Summary:
1. Phase Completed:
   - 刚刚结束的阶段名称
   - 该阶段的核心产出物

2. Key Decisions Made:
   - 本阶段内所有 escalate/clarify 记录的关键决策
   - 每项决策的来源

3. Artifacts Produced:
   - 本阶段产出的文件清单（含路径）
   - 每个产出的状态（已定稿/待审查/需修改）

4. Open Issues / Risks:
   - 本阶段遗留的未解决问题
   - 可能影响后续阶段的风险点

5. Current Status:
   - 工作流整体进度
   - 下一阶段要做什么
```

### 软件设计

#### `_resolve_file_refs`

`ConversationManager.call()` 内部自动将 prompt 中的 `{文件路径}` 替换为文件内容。非文件路径的 `{}` 原样保留（见第 4 节）。

#### 对话生命周期设计

`call()` 不强制 close，通过 `_resolve_file_refs` 做文件注入。刷新对话通过 `close_conversation`（从 registry 移除）+ 新 `call_agent` 实现。

### 注意事项

- conversation 关闭后，agent 仍可通过文件系统和 runtime context 变量获取上下文
- flush 后需重新注入 work 目录、产出路径等关键约束，避免 agent 迷失上下文
- compact summary 由 agent 自己撰写，workflow 只负责存储和传递

---

## 2. Agent 调用中断（Ctrl+U）

### 问题

工作流运行中，Agent（如 Dev）可能陷入死循环或做错误的事（如反复重试 npm install），用户只能干等或强关程序。

### 机制

中断采用 **`interruptible` 装饰器 + 内联 `interrupt_dialog`** 的方案，不走 LangGraph 图路由。核心流程：

1. 后台线程（`_keyboard_listener`）监听 stdin，检测到热键时设全局标志 `_interrupt_requested = True`
2. `call_agent` 的 `on_chunk` / `on_tool` 回调检查该标志，触发 `WorkflowInterrupted` 异常
3. `call_agent` 在抛出异常前将 `(agent, conv)` 保存到 context
4. `interruptible` 装饰器捕获异常，直接内联调用 `interrupt_dialog(state)`——不走图节点
5. 用户在 `interrupt_dialog` 中与 agent 自由对话修改方向
6. 用户输入 EOF 后 `interrupt_dialog` 返回，装饰器**从头重入原函数** `return func(state)`

```python
def interruptible(func):
    @functools.wraps(func)
    def wrapper(state):
        try:
            return func(state)
        except WorkflowInterrupted:
            rt = wrapper._runtime
            rt.context.set_ctx("interrupted_node", func.__name__)
            interrupt_dialog._runtime = rt
            interrupt_dialog(state)       # 内联调用，不走 graph
            return func(state)            # 从头重入原节点
```

### 为什么不用图路由

中断如果走 graph 条件边，所有节点都需要有一条边连到 `interrupt_dialog`，再连回来——每条边都是一个条件判断，graph.py 的拓扑会膨胀一倍。更重要的是：graph 节点执行完后必须返回一个 phase 值决定下一步，但中断时原节点没执行完，phase 值是未知的。

内联方案不需要改 graph.py 的任何边，对所有节点零侵入。

### 中断检测

后台线程使用 `msvcrt.kbhit()`（Windows）每 50ms 轮询键盘，检测到热键时设置全局标志。支持的热键：

| 热键 | ASCII | 说明 |
|:-----|:------|:------|
| `ctrl+u` | 21 | 默认 |
| `ctrl+c` | 3 | 可选 |
| `ctrl+x` | 24 | 可选 |

从 `runtime_config.json` 的 `interaction.interrupt_hotkey` 读取配置。

### `interrupt_dialog` 行为

```
==============================
  [用户介入] 正在与 dev 对话 (conversation: dev-exec-...)
  输入你想说的内容，直接 EOF 返回「dev_exec_step」节点
==============================
【用户介入】
输入消息给 dev（EOF 返回）:
> 停一下，这个逻辑不对，改成...
── dev 回复 ──
好的，我重新实现...
【用户介入】
输入消息给 dev（EOF 返回）:
> （直接 EOF）
  → 返回 dev_exec_step 节点
```

### 边界情况

- **中断时 agent 正在执行工具调用**：工具结果已产生但被丢弃。重入后 agent 从对话历史看到之前的工具结果，可以继续使用。
- **多次中断**：每次中断都保存最新的 interrupted_node，后一次覆盖前一次。
- **在 interrupt_dialog 中再次中断**：`call_agent` 的 `WorkflowInterrupted` 被节点内的 `try/except` 捕获，只打断当前回复，不返回原节点。
- **非流式调用**：`call_agent(stream=False)` 的中断请求被忽略，打印警告。
- **中断标志残留**：进入 `interrupt_dialog` 时主动清除残留标志，避免刚进入就被中断。

---

## 3. 节点组织约定

### 问题

原工作流中一个 node 函数可能包含多次 `call_agent` 调用，无法精确中断。拆分后 node 变多，需要保持内聚。

### 约定

#### 每个逻辑分组是一个类

原 node 函数 `snake_case_name` 拆分为 PascalCase 类：

```python
class PreFlightClarify:
    """原 pre_flight_clarify 拆分后的逻辑分组。"""
```

#### 类内节点以 @staticmethod 表示

每个 `call_agent` 对应一个 `@staticmethod`，方法名取能表达其职责的短名称。

#### _runtime 通过类属性传递

```python
class PreFlightClarify:
    _runtime = None

    @staticmethod
    def init(state):
        runtime = PreFlightClarify._runtime
```

#### entries / exits 声明对外连接

- **`entries`**: 本组的入口节点，dict，键为方法名，值为图节点名
- **`exits`**: 本组的出口节点，dict，键为方法名，值为图节点名

```python
class PreFlightClarify:
    entries = {"init": "pre_flight_init"}
    exits = {"close": "clarify_close"}
```

具体连线在 `graph.py` 中完成（不跨组边在 register 内处理）。

#### register 类方法注册节点和内部边

```python
@classmethod
def register(cls, graph, runtime):
    cls._runtime = runtime
    register_nodes(graph, runtime, {
        "pre_flight_init": cls.init,
        ...
    })
    graph.add_edge("pre_flight_init", "clarify_ask")
```

#### graph.py 的责任

1. 调用各组的 register 注册节点和内部边
2. 通过 entries / exits 引用对外接线
3. 维护全局的路由拓扑（跨组边、条件边）

### 子图架构（subgraphs/）

当同一图结构在多个 phase 重复出现时，抽取为配置驱动的子图。子图采用三层分离架构：

```
┌─ 工厂类（Factory）─────┐
│  define(config) → Def  │  ← 只创建闭包
└──────────┬─────────────┘
           ▼
┌─ Def 类 ───────────────┐
│  SubgraphDef(ABC)      │  ← 掌握图拓扑
│  register(graph, rt)   │  ← 做所有接线
│  → SubgraphResult      │
└──────────┬─────────────┘
           ▼
┌─ ABC 基类 ─────────────┐
│  nodes / entries/exits │  ← 定义契约
│  abstract register()   │
└────────────────────────┘
```

#### SubgraphDef（ABC 基类）

```python
class SubgraphDef(ABC):
    nodes: dict[str, Callable] = {}       # define 时填充
    entries: dict | None = None           # register 前为 None
    exits: dict | None = None             # register 前为 None

    @abstractmethod
    def register(self, graph, runtime) -> SubgraphResult:
        ...
```

entries/exits 在 register 调用前为 None，因为节点函数尚未注入 runtime。register 时才确定图节点名。

#### 工厂类只 define，不 register

```python
class HandoffSubgraph:
    @staticmethod
    def define(config: HandoffConfig) -> HandoffDef:
        # 创建闭包，返回 Def 实例
        ...
        return HandoffDef(node_name=node_name, run=run)
```

工厂对图结构零了解，只负责创建节点闭包。

#### graph.py 中的调用链

```python
# phase1.py — 定义配置 + define
PM_HANDOFF_DEF = HandoffSubgraph.define(PM_HANDOFF_CONFIG)

# graph.py — register
pm_handoff = PM_HANDOFF_DEF.register(graph, runtime)
graph.add_edge(pm_handoff.exits["run"], PMAlign.entries["read"])
```

#### 已实现的子图

| 子图 | 工厂 | Def | 配置 | 节点数 |
|:-----|:-----|:----|:-----|:-------|
| Handoff | `HandoffSubgraph` | `HandoffDef` | `HandoffConfig` | 1 |
| CriteriaDefinition | `CriteriaDefinitionSubgraph` | `CriteriaDefinitionDef` | `CriteriaDefinitionConfig` | 4 |

详细设计见 `doc/subgraph-extraction.md`。

---

## 4. 文件引用注入

### 问题

工作流中 node 函数需要让 agent 读取项目文件（design.md、plan.md、代码文件等）。常规做法是在 prompt 中告诉 agent "去读 X 文件"，但 agent 需要通过 tool call（read_file）自己去读，多一轮 tool call 既慢又浪费 token。

更关键的是：如果只是传路径，每次 prompt 都不同（路径字符串变化），无法命中 DeepSeek 的 prefix cache。将稳定内容（design.md、plan.md）直接注入 prompt 后，system prompt + design.md + plan.md 构成稳定前缀，后续 step 全部命中 cache。

### 机制

`ConversationManager.call()` 在发送请求前对 `input_text` 执行 `_resolve_file_refs`：

- 正则匹配 `{文件路径}` 模式
- 若路径对应真实文件 → 替换为文件全文
- 若非文件路径 → 保留原样

```python
# workflow 中写：
call_agent(runtime, "dev", conv,
    f"请参考设计文档：{design_path}")

# 终端显示（短）：
──── Request: dev/conv ────
请参考设计文档：C:/work/Dev/design.md

# LLM 实际收到（长）：
请参考设计文档：# 项目设计文档\n\n## 架构...（design.md 全部内容）
```

### 效果

- 省掉 agent 自己去 read_file 的一轮 tool call
- 稳定前缀命中 cache，后续调用大幅降低推理成本
- 终端日志保持可读性（只显示路径，不显示全文）

---

## 5. Handoff 信件模式

### 问题

工作流中 Master 需要向其他 Agent（PM/Dev/QA）派发任务并接收反馈。最初让 agent 直接在对话中回复，但 agent 的回复包含大量无关内容（"好的彩叶，我来分析你的需求～"、"没问题！以下是实现方案："等），这些内容混入 Master 的上下文中，浪费 token 且干扰判断。

### 机制

Agent 间不直接对话，通过 markdown 文件通信，称为"信件"。

```
发送方（如 Master）：
  write_letter(runtime, "master", master_conv, letter_path, "任务标题",
      "请完成以下工作：...")

  内部流程：
  1. 若信件文件已存在（中断重入残留），先删除让 agent 重写
  2. call_agent 让 Master 写一封信到 handoffs/{name}-{ws}-{ts}.md
  3. 信中只包含事实和指令，不含问候语或多于信息
  4. 信件格式约定：work_dir/任务/具体的任务内容

接收方（如 PM）：
  read_letter(runtime, "pm", pm_conv, letter_path, "理解需求")

  内部流程：
  1. call_agent 让 PM 读信并按要求执行
  2. PM 产出写入 workspace 对应目录
  3. 默认删除信件（keep=False）

读写合一（读信后写回信）：
  read_and_write_letter(runtime, "pm", pm_conv,
      inputletter_path, outputletter_path,
      "回信标题", "回信要求", "理解需求并回复")
```

信件路径由 `letter_path(runtime, name)` 生成，统一放在 `{runtime_dir}/handoffs/` 下。

### 特性

- **异步解耦**：发送方不依赖接收方实时在线，信件写入即完成通信
- **自包含**：每封信包含完整的任务上下文，接收方可独立理解
- **无闲聊**：信件格式约定只有事实和指令
- **幂等**：信件文件是纯文本 markdown，可重复读写
- **可审计**：信件内容可人工审查
- **即用即删**：checkpoint 恢复时会清理 handoffs 目录（见第 8 节）

---

## 6. Judge 路由

### 问题

LangGraph 的条件边（`add_conditional_edges`）需要根据 state 中的一个字段决定下一步走向。在复杂工作流中，路由决策往往不是简单的字段匹配，而是需要理解语义——比如判断 PM 的答复是"足够明确可以直接写标准"还是"还需要继续澄清"。

### 机制

用一个轻量 call_agent（不带 tool 调用）对回复做语义分类，称为 Judge：

```python
def judge_reply(runtime, target_role, reply, options, tag=None):
    """让 Judge 对回复做语义分类。"""
    options_text = "\n".join(f"{opt}" for opt in options)
    keys = "/".join(opt.strip()[0] for opt in options if opt.strip())
    conv = conv_name(tag or f"judge-{target_role.lower()}")
    prompt = (
        f"你是一个流程裁判。以下是 {target_role} 的回复。\n\n"
        f"## {target_role} 的回复\n{reply}\n\n"
        "判定当前状态是以下哪一种：\n"
        f"{options_text}\n\n"
        f"只回复单个字母（{keys}），不要包含标点或多余文字。"
    )
    result = call_agent(runtime, "judge", conv, prompt, stream=False)
    return result.strip()[0]  # 只取第一个字母
```

Judge 的特点是：
- **无 tool**：`stream=False`，不需要文件读写、代码执行等能力，只做纯文本分类
- **独立 conversation**：每次调用使用新 conversation（`judge-{tag}-{ws}-{ts}`），用完即弃，不追踪
- **独立 agent 条目**：在 registry 中注册为 `judge` agent，走 cg profile、共享 8642 端口
- **语义理解**：能理解"虽然 PM 说了一堆但核心诉求还不明确"这类复杂判断

### 路由模式

```
Phase 1 示例：

MasterReplyPM → JudgeMasterReply ──A──→ PMWriteCriteria
                                 ├──B──→ PMAlign（继续对齐）
                                 └──C──→ ClarifyInject（向用户澄清）
```

三路语义路由：A=可以直接写标准了，B=还要再对齐，C=需要问用户。如果在 graph.py 中用正则或字符串匹配实现，既脆弱又难维护。

### 备注

这是 LLM 工作流中的标准路由模式。核心思想是"让 LLM 做自己的路由器"——既然已经有 LLM 在工作流中，用它做语义分类比任何规则引擎都灵活可靠。

---

## 7. 审核循环模式

### 现状

PM、Dev、QA 三个阶段共享同一审核骨架：写标准 → 审查 → 反馈循环 → 通过与继续。但三段的具体实现尚未完全抽象，每段各有定制逻辑。

### 通用骨架

```
Write{Target}Criteria → Review{Target}Criteria ──A──→ {Next}（通过）
                                           └──B──→ Write{Target}Criteria（重写）
```

A 路由条件：Reviewer 判定标准合格，无需修改。

### 各阶段差异

| 阶段 | 标准 | 审查对象 | 通过后去向 |
|:-----|:------|:---------|:-----------|
| PM | PMWriteCriteria | PM 是否理解需求 | PMWriteDoc（写 PRD） |
| Dev | DevWriteCriteria | 标准是否清晰 | DevWriteDesign → DevWritePlan |
| QA | QAWriteCriteria | 测试范围是否完备 | QAWriteTestPlan |

### 后续规划

等到完整工作流跑通无问题后，将 `write_criteria` / `judge_reply` / 审查循环抽成框架级工具函数，让新阶段加三五行配置即可接入审核循环。

---

## 8. Checkpoint 恢复策略

### 问题

工作流中断后恢复时，不能只恢复 state 了事。agent 对话已关闭、旧阶段产出文件还残留在 workspace 中、context 里还存着上一轮的轮次和路径——这些都会让恢复后的 agent 产生混乱。

### 策略

Checkpoint 恢复的核心原则：**清空旧上下文，重建干净环境**。

#### 恢复步骤

1. **清产出**：删除对应阶段的 workspace 目录（`Dev/`、`PM/`、`QA/`）和 handoffs 目录
2. **清 context**：删除该阶段相关的所有 context key（路径、轮次、对话名等）
3. **建对话**：关闭旧 conversation，开新 conversation，注入阶段总结（已完成的工作）
4. **重置状态**：step_idx、fail_count 等计数器归零

```python
# Dev 阶段恢复示例：
_clean_targets(runtime, [
    runtime.paths.handoffs,
    os.path.join(ws, "Dev"),
])
# 清理 Dev 阶段所有 context 残留
for key in ("devletter_path", "dev_conv", "step_idx", "fail_count", ...):
    runtime.context.set_ctx(key, "")
# 重建 Dev 对话，注入 design.md + plan.md + compact_summary
open_master_conv(runtime, summary_path)
_restore_dev_conv(runtime, step_idx)
```

#### 各阶段恢复入口

| checkpoint 位置 | resume_node | 恢复动作 |
|:----------------|:------------|:---------|
| 需求澄清完成 | pm_handoff | 清 PM 产出 + 开新 Master 对话 |
| PM 方案完成 | dev_handoff | 清 Dev 产出 + 开新 Master 对话 |
| Dev 实现完成 | qa_handoff | 清 QA 产出 + 开新 Master 对话 |
| Dev 开始执行前 | dev_exec_step | 清 handoffs + git reset + 重建设 Dev 对话 |
| QA 完成 | consistency_audit | 清 handoffs + 开新 Master 对话 |

#### 设计意图

清产出再重建的策略看似"暴力"——删掉 agent 做过的文件再让它们重做——但这是正确的选择：

- agent 重入时看到的文件必须是干净的和当前 context 一致的
- 残留的旧轮次信件或部分产出会让 agent 困惑（"这个文件已经有了，我是不是该跳过？"）
- 重建对话注入的 phase_summary 足够让 agent 理解已完成的工作
- exec step 级别恢复会回到具体 step_idx，不丢进度
