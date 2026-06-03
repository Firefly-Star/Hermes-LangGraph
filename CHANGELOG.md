# Changelog

## [Unreleased]

### Added
- Phase 4 交付阶段（ConsistencyAudit → WriteMaintenanceDocs → DeliverySummary）
- OutputLayer 替换 sys.stdout，支持 console/file 双目标输出路由
- QA 测试全流程：审核标准 → 测试计划 → 测试代码 → 运行 → 修 bug 循环
- Config 类型化数据类（PathsConfig/LimitsConfig/InteractionConfig）
- `checkpoint.json` 路径可配置，`gateway_start_timeout` 可配置
- Playwright 测试规范注入 Dev/PM/Reviewer prompt
- `diagram.py` 独立绘图脚本

### Changed
- Gateway 启动改为 threading 并行检测，串行写 registry，冷启动加速
- 配置分层：runtime_config.json 拆为 paths/agents/limits/interaction/output 五节
- `config.py` 重命名为 `prompt.py`，目录配置移入 runtime_config.json
- doc/ 整理：16 个文件合并精简为 4 个（v6 + mechanisms + api-ref + config-ref）
- AgentRuntime 改用类型化数据类，去掉平面属性

### Fixed
- `judge_reply` 只取首字母返回，去除多余字符
- `letter_path` 变量名冲突导致 UnboundLocalError
- phase summary 幻觉问题（Master 写总结时传入实际产出路径）
- dev_exec_step 恢复时 git reset + 重置步进状态
- 手动作业文件（handoff）未随 checkpoint 清理

## [v0.1] — 工作流骨架

### Added
- 初始工作流框架：LangGraph StateGraph + AgentRuntime
- Phase 0~3 节点：需求澄清 → PM 出方案 → Dev 编码 → QA 对齐
- ResumeRouter 断线重连（checkpoint save/load/resume）
- MasterFlush 上下文刷新机制（phase 边界 + step 提交后）
- Ctrl+U 中断机制（interruptible 装饰器 + 内联 interrupt_dialog）
- Handoff 信件机制（Agent 间 markdown 文件通信）
- 节点 class + register 模式（entries/exits 声明拓扑连接）
- Judge 路由（LLM 语义分类驱动条件边）
- `_resolve_file_refs` 文件引用注入
