# 测试框架

> 基于 MockClient 的四层测试体系。LLM 调用用测试桩替换，不连 Hermes Gateway。

## 目录结构

```
test/
├── conftest.py              # MockClient + sys.path（全局 fixture）
├── static/                  # 第 1 层：静态校验 — 图结构、node_name 存在性
│   ├── __init__.py
│   └── test_graph_edges.py
├── unit/                    # 第 2 层：逐 node — 每个节点函数的 state/prompt 验证
│   ├── __init__.py
│   ├── test_conversation_client.py   # ConversationClient 接口 + MockClient 测试
│   ├── test_phase1.py
│   ├── test_phase2.py
│   ├── test_phase3.py
│   ├── test_phase4.py
│   └── test_flush.py
├── integration/             # 第 3 层：逐 phase 线性段 — 串联 3-5 个 node 的调用序列
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_phase0_flow.py
│   ├── test_phase1_flow.py
│   └── test_phase2_flow.py
└── e2e/                     # 第 4 层：全流程 — 完整图结构下的路径验证
    ├── __init__.py
    ├── conftest.py
    └── test_full_workflow.py
```

## MockClient

`ConversationClient` 的生产实现是 `HermesClient`（走 HTTP → Gateway），测试实现是 `MockClient`（返回预设文本）。

```python
mock_client.set_response("prompt 前缀", "模拟回复")
result = mock_client.call("master", "conv-1", "prompt 前缀 更多内容")
assert result.text == "模拟回复"
```

调用记录存在 `call_history` 中，每条为 `(agent, conversation, prompt)`。

## 测试编写规范

- 每个测试函数必须有 docstring 或单行注释，说明**测什么**和**为什么**。后续审阅者不依赖测试函数名理解意图。
- 一个测试函数只断言一个关注点。例外：校验 call_agent 参数时可在同一条中验证 agent + conversation + prompt。

## 四层测试

| 层级 | 目录 | 关注问题 | 用例数 |
|------|------|---------|--------|
| 静态校验 | static | graph edge → node_name 存在性 | 1-2 |
| 逐 node | unit | state 转换、prompt 构造、agent/conv 选择 | 每个 node 2-3 |
| 逐 phase | integration | context 传递、线性段调用序列 | 每个 phase 1-2 |
| 全流程 | e2e | 跨 phase 状态累积、中断恢复 | 2-3 |

### static — 静态校验

遍历 graph.py 中注册的所有 node，确保被 `add_edge()` 和 `add_conditional_edges()` 引用的 node_name 在图的 node 集合中存在。不需要 MockClient。

### unit — 逐 node 测试

每个 node 函数在隔离环境中运行，MockClient 拦截所有 `call_agent` 调用。步骤：

1. 创建 `AgentRuntime(conversation_client=mock_client)`
2. 注入 runtime：`NodeClass._runtime = rt`
3. 设好 context：`rt.context.set_ctx("master_conv", "...")`
4. 调用节点函数：`result = NodeClass.run(state)`
5. 断言 state、prompt、agent 选择、call 次数

```python
def test_node_returns_correct_state(self, mock_client):
    rt = AgentRuntime(config_path=None, conversation_client=mock_client)
    NodeClass._runtime = rt
    rt.context.set_ctx("master_conv", "conv-1")

    result = NodeClass.run({"phase": "start"})

    assert result["phase"] == "expected_end_state"
    assert "关键词" in mock_client.call_history[0][2]
```

#### 按 node 类型确定测试重点

| 类型 | 特征 | 用例数 | 关注点 |
|------|------|--------|--------|
| A 纯 call | 调 agent → 设 context → 返 state | 2-3 | agent/conv 选择、关键路径关键词 |
| B judge/路由 | judge_reply 返回值决定分支 | 3-4 | 每条分支至少 1 个用例 |
| C letter 读写 | 跨节点文件传递 | 3-4 | 路径字符串、删信时机 |
| D flush/checkpoint | 关/开对话、存恢复点 | 3-4 | conv 生命周期、checkpoint 写入 |

类型 A 的额外覆盖（prompt 中不同关键词的组合）留给集成测试。

### integration — 逐 phase 线性段

固定 judge 返回（如 "A"），验证多个 node 串联时 context 的传递和调用顺序。

### e2e — 全流程

构建完整 graph，设置完整的 mock 回复表，按预设的 judge 返回值走通指定路径。

## 运行

```bash
# 全部测试
python -m pytest test/

# 按层级
python -m pytest test/static/
python -m pytest test/unit/
python -m pytest test/integration/
python -m pytest test/e2e/

# 按文件
python -m pytest test/unit/test_phase4.py -v
```
