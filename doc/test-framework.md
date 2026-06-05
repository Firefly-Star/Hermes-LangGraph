# 测试框架

> 基于 MockClient 的节点级单元测试。LLM 调用用测试桩替换，不连 Hermes Gateway。

## 目录结构

```
test/
├── conftest.py              # MockClient + fixture（全局共享）
├── unit/                    # 单元测试 — 单节点 / 单函数
│   ├── test_conversation_client.py   # ConversationClient 接口 + MockClient 测试
│   └── test_phase4.py                # 节点测试示例
└── integration/             # 集成测试 — 子图 / 管道（待填充）
```

## MockClient

`ConversationClient` 的生产实现是 `HermesClient`（走 HTTP → Gateway），测试实现是 `MockClient`（返回预设文本）。

```python
mock_client.set_response("prompt 前缀", "模拟回复")
result = mock_client.call("master", "conv-1", "prompt 前缀 更多内容")
assert result.text == "模拟回复"
```

调用记录存在 `call_history` 中，每条为 `(agent, conversation, prompt)`。

## 节点测试模式

每个节点测试的标准步骤：

1. 创建 `AgentRuntime(conversation_client=mock_client)`
2. 把 runtime 注入到被测试节点：`NodeClass._runtime = rt`
3. 设好 runtime context：`rt.context.set_ctx("master_conv", "...")`
4. 调用节点函数：`result = NodeClass.run(state)`
5. 断言 state、prompt、agent 选择、call 次数

```python
def test_node_returns_correct_state(self, mock_client):
    rt = AgentRuntime(config_path=None, conversation_client=mock_client)
    NodeClass._runtime = rt
    rt.context.set_ctx("master_conv", "conv-1")

    result = NodeClass.run({"phase": "start"})

    assert result["phase"] == "expected_end_state"
    assert "期望的关键词" in mock_client.call_history[0][2]  # prompt 验证
```

## 测试范围

### 能测
- state 转换是否正确
- prompt 构造（agent/conv/prompt 内容）
- call_agent 调用次数
- context 读写
- 多层 retry 路径

### 不测
- LLM 回复质量（MockClient 返回固定文本）
- 图路由（graph.py 的边不执行）
- 文件系统（ensure_write_file 在 mock 下永远 False）
- 中断机制（键盘监听线程不跑）
- Gateway 生命周期
- 节点间串联

## 运行

```bash
# 全部测试
python -m pytest test/

# 按类型
python -m pytest test/unit/
python -m pytest test/integration/

# 按文件
python -m pytest test/unit/test_phase4.py -v
```
