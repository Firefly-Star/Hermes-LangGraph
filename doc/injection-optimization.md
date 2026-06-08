# Flush 注入优化：索引 + 摘要 + 按需查阅

> 解决大文件（design.md 70KB、plan.md 88KB）全文注入导致 token 浪费的问题。

## 思路

从「推送全文」改为「推送概要 + 索引，按需 read_file 查阅」。

每次 Dev flush 时注入：
- **design-summary**（~3KB）— 全局架构、技术选型、核心设计决策
- **design-index**（~2KB）— 文件/模块路径 → 一句话说明，供 agent 决定 read_file 目标
- **plan-index**（~1KB）— 全部步骤标题，标注当前步骤和已完成步骤
- **当前步骤 plan 全文**（~5KB）— 精确执行当前任务
- **指令** — 需要细节时查阅索引后 read_file，不要通读全文

## 新文件

由 agent 在 DevWriteDesign 完成后生成，存放在 Dev 工作目录：

| 文件 | 生成时机 | 格式要求 |
|:-----|:---------|:---------|
| `design-summary.md` | DevWriteDesign.read_design_letter 写完 design.md 后，额外写一份 | 自由格式，控制在 3KB 以内。描述整体架构、技术选型、核心约定 |
| `design-index.md` | 同上，同一轮 agent 调用写 | 每行 `路径/模块名: 一句话说明（不超过 20 字）` |

plan-index 不需要 agent 生成，用 regex 自动提取。

## 注入时机

三处 flush 统一使用同一套注入内容：

1. **DevGitInit.flush_context** — step 1 执行前
2. **DevCommit.flush_context** — 每步提交后
3. **_restore_dev_conv**（checkpoint.py）— 断线恢复

## 改动清单

### `utils.py` — 新增两个工具函数

```python
def extract_plan_index(plan_text: str, completed_steps: int) -> str:
    """从 plan.md 自动提取计划索引，已完成步骤标记 ✓，当前步骤标记 ←。"""

def extract_current_step(plan_text: str, step_idx: int) -> str:
    """从 plan.md 提取当前步骤的完整内容（## Step N 段落全文）。"""
```

### `phase2.py` — DevWriteDesign 追加输出

`DevWriteDesign.read_design_letter` 的 prompt 末尾加生成 design-summary + design-index 的要求。

### `phase2.py` — 两处 flush_context + checkpoint

统一替换为：

```python
injected = (
    f"## 项目设计概要\n{design_summary}\n\n"
    f"## 设计文件索引\n{design_index}\n\n"
    f"## 计划进度\n{plan_index}\n\n"
    f"## 当前步骤详细内容\n{current_step}\n\n"
    "注意：设计文件和计划文件体积较大，需要了解具体模块时"
    "请根据索引找到对应位置后用 read_file 定向读取，不要通读全文。"
)
```

### `prompt.py` — 约束计划格式

在 plan 写作 prompt 末尾加：`## Step N: 步骤名称` 格式约束，禁止非数字编号。

## 不做的

- 不用 LLM 做 plan 分段（regex 足够，格式由 prompt 约束）
- 第一次 flush 不做特殊处理（design-summary + design-index 已提供全局认知）
- design.md 不做"按章节分层"，统一用索引 + 按需 read_file
