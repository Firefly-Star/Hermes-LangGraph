# runtime_config.json 配置参考

> 各条目均标注代码引用位置，依据 `grep -rn` 实际结果，不含臆想。

---

## `paths` — 路径配置

所有路径均为绝对路径。

| Key | 默认值 | 用途 | 代码引用 |
|:----|:--------|:-----|:---------|
| `runtime_dir` | `.agent_runtime` | 运行时数据根目录（Logger/ContextManager/AgentManager 等模块的构造参数） | `agent_runtime.py:888` — runtime_dir 从配置提取；`agent_runtime.py:897` — `self.paths = PathsConfig(...)` |
| `workspace` | `os.getcwd()` | 项目工作目录，所有 agent 的文件产出（PM/Dev/QA 目录）挂在此下 | `phase0.py:32,61` — `project_context_path` 构建；各处 `os.path.join(runtime.paths.workspace, "Dev")` |
| `hermes_home` | 自动检测 | Hermes Gateway 安装目录 | `agent_runtime.py:921` — `self._hermes_home = hermes_home` → GatewayManager 构造参数 |
| `handoffs` | `{runtime_dir}/handoffs` | Agent 间通信的信件（markdown 文件）存放目录 | `utils.py:233` — `letter_path()` 生成路径；`checkpoint.py:157-242` — 各 resume 方法中 _clean_targets 清理；`phase3.py:188` — QA combined feedback 写入 |
| `phases` | `{runtime_dir}/phases` | 阶段总结文件（phase-summary-*.md）和 Dev compact-summary.md | `master_flush.py:85` — phase-summary 目录 `os.makedirs`；`checkpoint.py:77` — `_restore_dev_conv` 读取 compact-summary；`phase2.py:864,899,1169` — compact-summary 读写（行号可能偏移） |
| `artifacts` | `{runtime_dir}/artifacts` | 项目顶层决策文件（project_context.md）等持久产出 | `phase0.py:32` — artifacts 目录创建；`phase0.py:61` — `project_context_path` 构建 |
| `checkpoint` | `{runtime_dir}/checkpoint.json` | 断线重连检查点 JSON | `checkpoint.py:9` — `_cp_path()` 返回此路径供 save/load/clear |

---

## `agents` — Agent 注册

每个 agent 映射一个 profile + port。

```json
"master":   {"profile": "cg", "port": 8642}
```

- **profile**: Hermes Gateway 的 profile 名称（cg / pm / dev / qa）
- **port**: Gateway 端口。同 profile 可共享端口（master/judge/reviewer 共用 8642）
- **api_key**: 可选，默认 `"kaguya"`

| 代码引用 | 说明 |
|:---------|:-----|
| `utils.py:377` — `runtime.config.get("agents")` | `setup_runtime()` 获取 agent 配置 → `runtime.run_all(agent_configs)` |
| `agent_runtime.py:940` — `self.agents.register(name, cfg["profile"], cfg["port"], ...)` | 逐个注册 agent |
| `agent_runtime.py:940-944` — 按 port 分组启动 gateway | 同端口复用已启动的 gateway |

---

## `limits` — 阈值限制

> 读取方式：`runtime.limits.{key}`（通过 `LimitsConfig` 数据类），不再直接 `config.get()`。
> `Config` 内部通过 `_flatten_sections()` 将 `limits` 节的值拍平到顶层，因此 `config.get("call_timeout")` 也能读到。新代码请走 `runtime.limits`。

| Key | 默认值 | 用途 | 代码引用 |
|:----|:--------|:-----|:---------|
| `call_timeout` | 120 | Hermes API 单次调用超时（秒） | `agent_runtime.py` — `HermesClient.call()` 传给 `requests.post(timeout=...)` |
| `max_retry` | 3 | Hermes API 调用失败时的重试次数 | `agent_runtime.py:323` — `for attempt in range(1 + max_retry)` |
| `max_plan_loop` | 5 | **未使用**。在 DEFAULTS 中定义但在代码中无任何引用 | — |
| `max_bug_loop` | 5 | **未使用**。同上 | — |
| `fail_rollback_threshold` | 3 | Dev 执行步骤连续失败达到此阈值 → 弹窗提醒用户关注 | `phase2.py:982` — 读取阈值；`phase2.py:986` — 弹窗 `win_popup`；均路由到 `step_retry` |
| `fail_escalation_threshold` | 5 | Dev 执行步骤连续失败达到此阈值 → 弹窗提醒用户关注 | `phase2.py:978` — 读取阈值；`phase2.py:981` — 弹窗 `win_popup`；均路由到 `step_retry` |
| `gateway_start_timeout` | 30 | Gateway 进程启动后等待 health check 就绪的超时（秒），冷启动需要更长时间 | `agent_runtime.py:241` — `for _ in range(timeout)`；`runtime_config.json` 设为 60 |

---

## `interaction` — 交互配置

| Key | 默认值 | 用途 | 代码引用 |
|:----|:--------|:-----|:---------|
| `input_end_word` | `"EOF"` | `runtime.checkpoint.wait()` 的结束词，用户输入此词视为空输入/结束 | `utils.py:98` — `interrupt_dialog`；`utils.py:341` — `clarify_loop`；`phase1.py:523` — `HumanReview` |
| `interrupt_hotkey` | `"ctrl+u"` | 中断 agent 调用的热键（弹出介入对话框） | `graph.py:195` — 传入 `start_interrupt_listener(hotkey, skip_hotkey)`；`utils.py:14` — `HOTKEY_MAP` 定义 |
| `skip_hotkey` | `""` | 跳过 agent 回复的热键（停止 agent 输出，改由用户手动输入回复） | `agent_runtime.py:980` — `InteractionConfig`；`utils.py:248` — `call_agent` 中处理 `_skip_requested` |

**中断 vs 跳过**：中断（Ctrl+U）弹出介入对话框，用户可与 agent 对话修改方向，然后回到原节点重新执行。跳过（Ctrl+K）停止 agent 回复，用户直接输入自己的回复作为 `call_agent` 的返回值——当前节点认为 agent 已正常回复，继续走下游流程。跳过不弹出对话框，不影响 node 执行路径。

---

## `output` — 输出路由

控制 `print()` 的输出目标。启用时 `AgentRuntime.__init__` 会创建 `OutputLayer` 替换 `sys.stdout`。

| Key | 类型 | 默认值 | 用途 | 代码引用 |
|:----|:-----|:--------|:-----|:---------|
| `targets` | `list` | `[{"type": "console", "enabled": true}]` | 输出目标列表 | `agent_runtime.py:935` — `sys.stdout = OutputLayer(output_targets)` |

每个 target 支持：

| 字段 | 类型 | 说明 |
|:-----|:-----|:------|
| `type` | `"console"` / `"file"` | 输出类型 |
| `enabled` | `bool` | 是否启用 |
| `path` | `str` | 仅 file 类型，输出文件路径 |

示例配置：
```json
"output": {
  "targets": [
    {"type": "console", "enabled": true},
    {"type": "file", "enabled": true, "path": "C:/path/to/output.log"}
  ]
}
```

- `console` 类型写入 `sys.__stdout__`（原始 stdout）
- `file` 类型以 UTF-8 追加写入指定文件，父目录不存在时自动创建
- 两种类型可同时启用，互不干扰
- 工作流退出时（`__exit__`）自动恢复 `sys.stdout = sys.__stdout__`

---

## 未纳入数据类的直读条目

| Key | 读法 | 用途 | 代码引用 |
|:----|:-----|:-----|:---------|
| `write_retry` | `runtime.config.get("write_retry") or "2"` | `ensure_write_file()` 重试次数，默认 2 | `utils.py:242` |
