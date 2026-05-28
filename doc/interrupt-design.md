# Agent 调用中断（Ctrl+U）设计

## 问题

工作流运行中，Agent（如 Dev）可能陷入死循环或做错误的事（如反复重试 npm install），用户只能干等或强关程序。

## 决策过程

### 探索过的方案

**方案 A：冻结函数调用栈，1:1 恢复现场**
- Python 无此能力。`call_agent` 是同步阻塞调用，退出后所在 node 的局部变量和程序计数器全部丢失，不可能从离开的位置继续。

**方案 B：中断后不重入原 node，发「继续」指令推进到下一个节点**
- 中断后 user 和 agent 对话，EOF 后向 agent 发「继续你刚才的工作」。
- 但原 node 中 `call_agent` 之后的处理代码（写文件、设 context、判断返回值）不会被执行，导致状态不一致。
- 对多 call 的 node，无法确定「下一个节点」是哪个。

**方案 C：中断后从头重入原 node（选定方案）**
- 中断时保存 `(agent, conv, interrupted_node, return_phase)`。
- 用户对话结束后，graph 重新进入 `interrupted_node`，node 函数从头执行。
- 代价：重复发送之前已发送过的 prompt。但 agent conversation 中有历史记录，agent 能理解这是重入，不会产生不良后果。

### 方案 C 的可行性分析

| 场景 | 重入行为 | 是否安全 |
|------|----------|----------|
| 单 call node（如 `pm_write_doc`） | 重发 prompt → agent 看到历史 + 用户指导，以新指引回复 | 安全 |
| 多 call node（如 `dev_escalation` 有 4 个 call） | 已完成 call 重发 → agent 看到相同指令，快速略过/确认状态 | 安全（幂等） |
| 含包装函数的 node（`read_letter`、`write_letter`） | 重新读信/写信 → 信件文件还在，操作幂等 | 安全 |
| 循环（dev step loop） | context 中 `step_idx` 不变，重新进入同一 step | 安全 |
| `ensure_write_file` 类检查 | 文件已存在，检查通过或提醒重写 | 安全 |

## 实验验证

实现前对最核心的技术风险——「客户端断开流式连接后，Gateway 是否阻塞同一 conversation 的新请求」——做了独立验证。

### 实验脚本

`scripts/test_abort_agent.py`：

1. 向 Dev agent（port 8644）发一个长输出请求（写 500 字作文），使用流式 SSE
2. 读取 5 块输出后，客户端主动关闭 HTTP 连接（`resp.close()`）
3. 立即向同一 agent、同一 conversation 发第二个请求
4. 观察第二个请求是否被阻塞

### 实验结果

```
[1] 发送第一个请求（长输出）...
[2] 读取输出中...
春天来了，万物复苏...（收到 5 块 SSE）
[中断] 主动断开...
[3] 第一个请求的连接已关闭
[4] 测试同一 conversation 是否可用...
[第二个请求] 回复内容: 你好
[第二个请求] 完成，收到 2 字
结论: OK 中断后同一 conversation 可立即复用，gateway 没有阻塞
```

### 结论

Hermes Gateway 正确处理了客户端断连：断开后同一 conversation 可立即接受新请求，无需等待超时。中断功能的核心技术风险已排除。

## 决策结果

**采用方案 C：中断后保存现场，用户对话结束后从头重入原 node。**

保存的现场信息：

| 字段 | 说明 |
|------|------|
| `interrupted_agent` | 中断时正在调用哪个 agent |
| `interrupted_conv` | 中断时正在使用哪个 conversation |
| `interrupted_node` | 中断时在哪个 node，用户 EOF 后重入此 node |
| `return_phase` | node 完成后原本要去的 phase（备用） |

重复发送 prompt 的 token 浪费在可接受范围内，正确性优先。

## 实现方法

### 0. 配置

中断热键从 `runtime_config.json` 或 `src/workflow/config.py` 中读取，不硬编码。默认 Ctrl+U（ASCII 21）。

### 1. 中断检测

在 workflow 层起一个后台线程监听 stdin：

- 使用 `msvcrt.kbhit()`（Windows）/ `select.select`（Linux）轮询键盘
- 检测到 Ctrl+U（ASCII 21）时设置全局中断标志 `AgentRuntime.interrupt_requested = True`
- 只在工作流运行时启动该线程，结束后关闭

### 2. call_agent 配合中断

`call_agent` 的 `on_chunk` 回调（每收到一个 token 都会调用）检查中断标志：

```python
def on_chunk(chunk):
    if getattr(runtime, "interrupt_requested", False):
        runtime.interrupt_requested = False  # 消费标志
        raise WorkflowInterrupted()  # 自定义异常，终止流式输出
```

在 `_call_stream` 的 SSE 迭代循环中捕获该异常，关闭连接，向上抛出让 `call_agent` 退出。

### 3. Graph 路由

- 新增 `user_intervention` 节点
- `call_agent` 的 `WorkflowInterrupted` 异常传到 node 函数，被 `interruptible` 装饰器捕获
- node 函数将 `interrupted_node` 设为当前 phase 名称，`return_phase` 设为原本的 `return {"phase": "xxx"}` 中的值
- 路由到 `user_intervention` 节点

### 4. user_intervention 节点

```python
def user_intervention(state):
    # 读取 context 中的 interrupted_agent, interrupted_conv
    agent = ctx.get("interrupted_agent")
    conv = ctx.get("interrupted_conv")
    return_node = ctx.get("interrupted_node")

    while True:
        user_input = input("> ")
        if not user_input:  # EOF
            break
        reply = call_agent(runtime, agent, conv, user_input)
        print(reply)

    return {"phase": return_node, "judge_result": ""}
```

### 5. 改动文件

| 文件 | 改动 |
|------|------|
| `runtime_config.json` | 新增 `interrupt_hotkey` 配置项 |
| `src/workflow/utils.py` | 新增：`_interrupt_requested` 标志、`WorkflowInterrupted` 异常、`_keyboard_listener` 后台线程、`start/stop_interrupt_listener`、`interruptible` 装饰器、`user_intervention_node` 节点；修改：`call_agent` 的 `on_chunk` 中检测中断标志并保存现场 |
| `src/workflow/graph.py` | 所有节点包裹 `interruptible()`、新增 `user_intervention_node` 到图、条件边覆盖所有节点、`main()` 中 start/stop listener |
| `src/agent_runtime.py` | `_call_stream` 的 SSE 循环包裹 try/finally `resp.close()`，确保中断后连接释放 |

### 6. 边界情况

- **中断时 agent 正在执行工具调用**：工具调用的结果可能已经产生但被丢弃。重入后 agent 从对话历史看到之前的工具结果，可以继续使用。
- **多次中断**：每次中断都保存最新的 `interrupted_node`，后一次覆盖前一次。
- **在 user_intervention_node 中再次中断**：`call_agent` 的 `WorkflowInterrupted` 被节点内的 `try/except` 捕获，只打断当前回复，不返回原节点。
- **键盘监听与 input() 冲突**：后台线程检测到 Ctrl+U 时不应消费 stdin 字符，只设置标志。`input()` 读取用户输入由正常流程处理，不会抢占。中断标志会延迟到下一个 `call_agent` 的 `on_chunk` 才被消费。
- **中断标志残留**：进入 `user_intervention_node` 时主动清除残留标志，避免刚进入就被送回原节点。

## 后续规划

中断机制实现后，逐步重构节点函数，让每个 node 只包含一个 `call_agent` 调用，消除多 call 和循环。好处：

- 中断后重入的 token 浪费降至最低（只重复一个 call）
- 节点逻辑更清晰：一个节点 = 一个 agent 交互 + 结果处理
- 为未来可能的状态机细化铺路

循环逻辑（如 dev review fail loop）从节点代码中抽离，由 graph 边的条件判断替代。
