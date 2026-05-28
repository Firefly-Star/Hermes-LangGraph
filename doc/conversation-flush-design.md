# Conversation Flush 设计

## 问题

工作流中 agent 的 conversation 会随流程推进持续累积，导致 input_tokens 单调增长。若不干预，长流程（如数十个 Dev step）将触达模型 context window 上限。

## 目的

flush（关闭当前 conversation，开启新 conversation 并重新注入上下文）有两个目的：

1. **防止超窗** — 长流程可能触达 context window 上限，flush 将输入量重置到可控水平
2. **重注约束** — Dev/Master 等 agent 在持续对话中可能逐渐忽略 system prompt 中的约束（如归档路径规则、review 不可跳过等），重新注入约束让其行为回到预期轨道

## 策略

### Dev — 每 step PASS 后 flush

**触发时机**：每个 step 审查 PASS → git commit 后，关闭旧 conversation，下次 exec 开新对话。

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

**为什么是文件内容而不是路径**：注入文件内容才能命中 DeepSeek prefix cache。system prompt + design.md + plan.md 构成稳定前缀，从第二步起全部命中 cache read（0.02元/M）。如果只传路径让 agent 自读，每次 prompt 都不同，无 cache 收益。

**终端整洁**：通过 `_resolve_file_refs` 的 `{路径}` 语法，终端显示路径字符串（简短），实际发给 LLM 的是文件内容。

### Master — 每个 major phase 边界 flush

**触发时机**：需求澄清→PM 阶段、PM→Dev 阶段、Dev→QA 阶段、QA→交付，各阶段交接点。

**新对话注入内容：**

```
[system prompt — Hermes 自动注入]

[system prompt - workflow 定义的规范]

## 项目需求（已确认）
{project_context.md}   ← 需求澄清阶段产出的决策记录

## 项目决策日志
{decision_log}         ← 各阶段 escalate/clarify 记录的关键决策

## 进度摘要
已完成：
- 需求澄清：已确认...
- PM 出方案：PRD 已定，MVP 范围为...
- Dev 步骤 1-6/12：完成了...

当前阶段：Dev 步骤 7，等待实施
```

Master 不需要 design.md 和 plan.md（那是 Dev 的上下文），但需要知道全局进展。

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
   - 对后续步骤的假设（"表单组件已就绪，下一步可以直接引用"）

6. Current Status:
   - 已完成: Step N / total
   - 下一步要做什么
```

**示例：**

```
Summary:
1. Primary Request and Intent:
   实现登录页面的前端表单组件 + 基础校验逻辑。属于 Step 2/12。

2. Key Technical Concepts:
   - 使用 Vue 3 + TypeScript + `<script setup>` 写法
   - 表单校验用内联响应式 watch，未引入第三方校验库
   - CSS 模块化，样式写在同文件 `<style scoped>`

3. Files and Code Sections:
   - **`Dev/src/components/LoginForm.vue`** (new) — 登录表单组件，含
     - `validateForm()`: 校验 username 非空、password ≥ 6 位
     - `handleSubmit()`: 调用 POST /api/auth/login
   - **`Dev/src/App.vue`**: 引入 LoginForm，替换占位内容
   - **`Dev/src/types.ts`** (new): `LoginRequest`, `LoginResponse` 接口

4. Errors and fixes:
   - Vue 模板中 `v-model` 绑定到 `ref` 属性时需用 `.value`，修复
   - CORS 预检请求 403：后端 `@CrossOrigin` 未配置 allowCredentials，已在后端修复

5. Dependencies / Assumptions:
   - 后端 `/api/auth/login` 端点已就绪（Step 1 实现）
   - 下一步（Step 3）可直接在后端添加 JWT token 校验中间件

6. Current Status:
   - 已完成: Step 2/12（登录页面表单组件 + 前端校验）
   - 下一步: Step 3 — 添加 JWT 中间件，保护 /api/home 路由
```

#### Master — 每个 phase 结束时

由 Workflow 在 phase 边界生成（通过 call_agent 让 Master 自己写），而非 Master 的 conversation 内部自然产生。结构不同，侧重全局而非单步：

```
Summary:
1. Phase Completed:
   - 刚刚结束的阶段名称（需求澄清 / PM 出方案 / Dev 执行 / QA 对齐）
   - 该阶段的核心产出物

2. Key Decisions Made:
   - 本阶段内所有 escalate / clarify 记录的关键决策
   - 每项决策的来源（用户确认 / Master 自行决定 / 集体讨论）
   - project_context.md 的更新内容

3. Artifacts Produced:
   - 本阶段产出的文件清单（含路径）
   - 每个产出的状态（已定稿 / 待审查 / 需修改）

4. Open Issues / Risks:
   - 本阶段遗留的未解决问题
   - 可能影响后续阶段的风险点
   - 需要下游阶段特别关注的事项

5. Current Status:
   - 工作流整体进度
   - 下一阶段要做什么
```

**示例：**

```
Summary:
1. Phase Completed:
   PM 出方案阶段结束。PM 已确认需求理解无误，产出 PRD 和 prototype。
   Master 答复了 PM 全部 9 个问题，其中 Q4/Q5/Q7 由 Master 在授权范围内
   做决定并更新到 project_context.md。

2. Key Decisions Made:
   - 登出后前端清除 token，跳转登录页，显示 toast（用户授权，Master 决定）
   - 401 时前端清除 token，跳转登录页，显示"登录已过期"（用户授权，Master 决定）
   - JWT 黑名单表结构约定 id/jti/token/expire_at/created_at（用户授权，Master 决定）
   - 无需正式项目名，沿用"用户认证系统"（用户确认）

3. Artifacts Produced:
   - `{workspace}/criteria-pm.md` — 审核标准，已通过 Reviewer 审查
   - `{workspace}/PM/PRD.md` — PRD，已通过 human_review
   - `{workspace}/PM/prototype.html` — 原型，已通过 Reviewer Playwright 审查
   - `{workspace}/project_context.md` — 已补充三项新决策

4. Open Issues / Risks:
   - 无遗留问题。PM 的 9 个问题全部解决，无升级到用户的事项。

5. Current Status:
   - 已完成: 需求澄清 → PM 出方案 → 审查通过
   - 下一步: Dev 阶段（handoff → 对齐 → 设计 → 实现）
```

PM 只参与一个独立的对齐回合（`pm-align` 对话），自然结束。对话长度可控，不需要 flush。

### QA — 不 flush

QA 只参与一个独立的对齐回合，对话长度可控，不需要 flush。

### Judge — 无状态，不追踪

Judge 每次调用都是一锤子分类，使用独立 conversation 名（`judge-{tag}-{ws}-{ts}`），调用即用即弃。不注册活跃，不写 registry，不走 begin/close。

## 软件设计

### _resolve_file_refs

`ConversationManager.call()` 内部自动将 prompt 中的 `{文件路径}` 替换为文件内容。非文件路径的 `{}` 原样保留。

```python
# workflow 中写：
call_agent(runtime, "dev", conv,
    f"下面是上下文：{design_path}\n{plan_path}")

# 终端显示（短）：
──── Request: dev/conv ────
下面是上下文：C:/work/Dev/design.md
C:/work/Dev/plan.md

# LLM 实际收到（长）：
下面是上下文：<design.md 的全部内容>
<plan.md 的全部内容>
```

### 对话生命周期设计（回退方案）

~~曾尝试引入 RAII 风格的 begin/close 显式生命周期管理：开始对话前必须 begin() 注册为活跃，否则 call() 报错。但在实践中发现：~~
- ~~judge 等无状态调用每次用新 conv，begin 完马上 close 纯属多余~~
- ~~大量函数需要追溯 conversation 创建点补 begin()，改动面太大~~
- ~~Python 缺乏 RAII 的自动析构，显式 close 容易被遗漏，最终选择回退~~

当前方案：`call()` 不强制 begin/close，通过 `_resolve_file_refs` 做文件注入。flush 通过 `close_conversation`（从 registry 移除）+ 新 `init_conversation` 实现。

## 注意事项

- conversation 关闭后，agent 仍可通过文件系统和 runtime context 变量获取上下文
- flush 后需重新注入 work 目录、产出路径等关键约束，避免 agent 迷失上下文
- 依赖 Hermes system prompt 在同一 profile 的多个 gateway 实例间稳定一致
- compact summary 由 agent 自己撰写，workflow 只负责存储和传递
