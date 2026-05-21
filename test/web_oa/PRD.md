# web_oa 产品需求文档（PRD）

- **项目代号**：web_oa
- **项目名称**：员工工时提交系统 Web 应用（第一阶段 MVP：用户认证模块）
- **版本**：v1.0
- **日期**：2026-05-21
- **作者**：PM Agent

---

## 1. 项目概述与背景

### 1.1 项目目标

构建一个员工工时提交系统的用户认证基础模块。本阶段（MVP）仅实现用户注册、登录、登出及受保护欢迎页功能，作为后续工时提交流程的前置基础设施。

### 1.2 业务范围

本 MVP 覆盖认证闭环的全流程：用户通过注册创建账号，通过登录获取认证令牌，在受保护页面中查看个人欢迎信息，通过登出主动结束会话。

### 1.3 用户群体

系统内部员工，无管理员或其他角色。

### 1.4 产出路径

前端和后端项目代码放置在 `C:\Users\温学周\Desktop\langgraph_test\test\web_oa` 目录下。

---

## 2. 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端框架 | Vue 3 | 用于构建单页面应用（SPA） |
| UI 组件库 | Element Plus | 使用现成组件美化界面，不涉及自定义设计稿 |
| 前端路由 | Vue Router | 管理三个独立路由：/login、/register、/welcome |
| HTTP 客户端 | Axios | 与后端 API 通信，配置请求拦截器自动携带 JWT |
| 后端框架 | Spring Boot | RESTful API 服务 |
| 数据库 | MySQL | 关系型数据库，存储用户数据与 JWT 黑名单 |
| 认证方案 | JWT + bcrypt | 密码使用 bcrypt 加密存储，JWT 用于认证 |
| 额外中间件 | 无 | 认证相关仅用 MySQL 和 JWT 库，不引入 Redis、缓存、消息队列等额外组件 |

---

## 3. 用户角色与权限

### 3.1 角色定义

仅存在一种角色：**用户（User）**。

### 3.2 角色能力

- 用户通过注册页面创建账号，注册即成为用户
- 已注册用户通过登录页面获取 JWT
- 已登录用户可以访问 `/welcome` 欢迎页面
- 已登录用户可以通过登出按钮结束会话

### 3.3 权限边界

- 无管理员角色
- 无角色/权限分级
- 登录后只能查看自己的欢迎页信息
- 无用户管理后台

---

## 4. 功能需求

### 4.1 登录页（/login）

#### 4.1.1 页面构成

- **用户名输入框**：Element Plus el-input 组件，文本框类型
- **密码输入框**：Element Plus el-input 组件，密码框类型（`type="password"`，输入内容以圆点掩码显示）
- **登录按钮**：Element Plus el-button 组件，类型 primary
- **链接**: 登录框下方提供一个"没有账号？去注册"的链接指向 `/register`

#### 4.1.2 输入校验

| 字段 | 校验规则 | 不通过时的提示 |
|------|---------|---------------|
| 用户名 | 非空 | "请输入用户名" |
| 密码 | 非空 | "请输入密码" |

校验触发时机：点击登录按钮时触发**一次性校验**，非实时校验。

#### 4.1.3 正常流程

1. 用户输入用户名和密码
2. 点击"登录"按钮
3. 前端执行非空校验，任一字段为空则显示对应错误提示并停止提交
4. 校验通过后，前端发起 `POST /api/auth/login` 请求
5. 后端返回 JWT，前端将 JWT 存入 `localStorage`，key 为 `token`
6. 前端使用 Vue Router 跳转至 `/welcome`

#### 4.1.4 错误状态

| 错误场景 | 交互表现 |
|---------|---------|
| 用户名或密码错误（后端返回 401） | 页面上方出现 Element Plus el-message 红色错误提示，文字为"用户名或密码错误" |
| 网络错误（请求无法到达服务端） | 页面上方出现 Element Plus el-message 红色错误提示，文字为"网络异常，请稍后重试" |
| 输入校验不通过 | 在对应输入框下方显示 el-form-item 的红色错误文字 |

#### 4.1.5 路由保护

- 已登录用户（`localStorage` 中有 token）访问 `/login` 时，应自动重定向至 `/welcome`
- 无 token 用户可以正常访问 `/login`

### 4.2 注册页（/register）

#### 4.2.1 页面构成

- **用户名输入框**：Element Plus el-input 组件，文本框类型
- **密码输入框**：Element Plus el-input 组件，密码框类型
- **注册按钮**：Element Plus el-button 组件，类型 primary
- **链接**：注册框下方提供一个"已有账号？去登录"的链接指向 `/login`

#### 4.2.2 输入校验

| 字段 | 校验规则 | 不通过时的提示 |
|------|---------|---------------|
| 用户名 | 非空；仅允许英文字母和数字组合 `[a-zA-Z0-9]` | 非空："请输入用户名"；格式不符："用户名仅允许英文字母和数字" |
| 密码 | 非空 | "请输入密码" |

校验触发时机：点击注册按钮时触发**一次性校验**。

#### 4.2.3 正常流程

1. 用户输入用户名和密码
2. 点击"注册"按钮
3. 前端执行非空校验和格式校验，任一不通过则显示对应错误提示并停止提交
4. 校验通过后，前端发起 `POST /api/auth/register` 请求
5. 后端校验用户名唯一性，通过后创建用户并返回 JWT
6. 前端将 JWT 存入 `localStorage`，key 为 `token`
7. 前端使用 Vue Router 自动跳转至 `/welcome`
8. 注册成功后不会弹出任何提示让用户手动去登录页，注册到欢迎页之间无用户额外操作环节

#### 4.2.4 错误状态

| 错误场景 | 交互表现 |
|---------|---------|
| 用户名已被注册（后端返回 409） | 页面上方出现 Element Plus el-message 红色错误提示，文字为"用户名已被注册" |
| 参数校验失败（用户名格式不符、密码为空等，后端返回 400） | 页面上方出现 Element Plus el-message 红色错误提示，文字为后端返回的具体错误信息 |
| 网络错误 | 页面上方出现 Element Plus el-message 红色错误提示，文字为"网络异常，请稍后重试" |

### 4.3 欢迎页（/welcome）

#### 4.3.1 页面构成

- **页面标题/欢迎语**：页面主体区域居中显示 `hello {username}`，其中 `{username}` 为当前登录用户的用户名。该文字为页面挂载后直接展示，不需要用户点击或其他操作
- **登出按钮**：位于页面右上角，Element Plus el-button 组件，类型 danger，文字为"退出登录"

#### 4.3.2 数据获取流程

1. 页面挂载（mounted）时，自动调用 `GET /api/user/me`，在请求 Header 的 `Authorization` 字段中携带 `Bearer {token}`
2. 后端校验 JWT 有效后，返回当前用户信息 `{ id, username }`
3. 前端将 `username` 展示为 `hello {username}`
4. 若 token 无效或过期（后端返回 401），前端清除 `localStorage` 中的 token，跳转至 `/login`

#### 4.3.3 正常流程

1. 用户持有有效的 JWT 访问 `/welcome`
2. 前端 Vue Router 导航守卫检查 `localStorage` 中有 token，允许访问
3. 页面挂载后自动调用 `GET /api/user/me`
4. 后端返回当前用户信息
5. 页面展示 `hello {username}`

#### 4.3.4 错误状态

| 错误场景 | 交互表现 |
|---------|---------|
| token 无效/过期（后端返回 401） | 前端清除 localStorage 中的 token，跳转至 /login |
| 网络错误导致无法获取用户信息 | 页面上方出现 Element Plus el-message 红色错误提示，文字为"获取用户信息失败" |
| 无 token 直接访问 | Vue Router 导航守卫拦截，重定向至 /login |

---

## 5. 认证与登录/登出流程说明

### 5.1 完整数据流

#### 流程一：注册

```text
用户在 /register 输入用户名+密码
  → 前端非空校验 + 格式校验（[a-zA-Z0-9]）
  → POST /api/auth/register { username, password }
  → 后端校验用户名唯一性（MySQL 唯一约束 + 应用层校验）
  → bcrypt 加密密码，写入 user 表
  → 签发 JWT，返回 { token } 给前端
  → 前端将 token 存入 localStorage
  → Vue Router 跳转至 /welcome
```

#### 流程二：登录

```text
用户在 /login 输入用户名+密码
  → 前端非空校验
  → POST /api/auth/login { username, password }
  → 后端查询 user 表，bcrypt 比对密码
  → 密码匹配则签发 JWT（有效期 24h），返回 { token }
  → 前端将 token 存入 localStorage
  → Vue Router 跳转至 /welcome
```

#### 流程三：访问受保护页面

```text
用户访问 /welcome
  → Vue Router 导航守卫检查 localStorage 中是否存在 token
    → 无 token：重定向至 /login
    → 有 token：允许访问
  → 页面挂载，自动调用 GET /api/user/me（Authorization: Bearer {token}）
  → 后端 Filter/Interceptor 校验 JWT
    → JWT 无效/过期/在黑名单中：返回 401
    → JWT 有效：从 JWT 中解析出 userId，查询 user 表，返回 { id, username }
  → 前端展示 hello {username}
```

#### 流程四：登出

```text
用户在 /welcome 点击"退出登录"按钮
  → 前端发起 POST /api/auth/logout（Authorization: Bearer {token}）
  → 后端从 JWT 中提取 jti（JWT ID），将 jti 和过期时间写入 jwt_blacklist 表
  → 后端返回 200 OK
  → 前端清除 localStorage 中的 token
  → Vue Router 跳转至 /login
  → 即使 POST /api/auth/logout 调用失败（网络错误、服务端错误），
    前端仍清除 localStorage 中的 token 并跳转至 /login
```

### 5.2 JWT 结构说明

- **签名算法**：HMAC-SHA256（HS256）
- **Payload 包含字段**：
  - `sub`：用户 ID（user.id）
  - `username`：用户名
  - `jti`：JWT 唯一标识（UUID），用于登出时加入黑名单
  - `iat`：签发时间
  - `exp`：过期时间（签发时间 + 24h）
- **有效期**：24 小时（86400 秒）
- **密钥**：服务端配置的密钥字符串，使用 `application.yml` 或环境变量配置
- **无 refresh token 机制**

---

## 6. API 接口定义

### 6.1 通用约定

- **基础路径**：`/api`
- **请求/响应格式**：JSON（`Content-Type: application/json`）
- **认证方式**：除注册和登录接口外，其他接口需在请求头 `Authorization` 字段携带 `Bearer {token}`
- **统一响应格式**：

```json
// 成功响应
{
  "code": 200,
  "message": "success",
  "data": { ... }
}

// 错误响应
{
  "code": 400,
  "message": "参数校验失败",
  "data": null
}
```

---

### 6.2 POST /api/auth/register

**说明**：用户注册。注册成功后直接返回 JWT，实现注册即登录。

#### 请求

```json
{
  "username": "zhangsan",
  "password": "123456"
}
```

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|---------|
| username | string | 是 | 非空，仅允许英文字母和数字 [a-zA-Z0-9]，长度 1-50 |
| password | string | 是 | 非空，长度 1-100 |

#### 成功响应（201 Created）

```json
{
  "code": 201,
  "message": "注册成功",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIs..."
  }
}
```

#### 错误响应

| HTTP 状态码 | 场景 | 响应体 |
|------------|------|--------|
| 400 | 参数校验失败（用户名为空/格式不符、密码为空） | `{ "code": 400, "message": "用户名仅允许英文字母和数字", "data": null }` |
| 409 | 用户名已被注册 | `{ "code": 409, "message": "用户名已被注册", "data": null }` |

---

### 6.3 POST /api/auth/login

**说明**：用户登录。成功返回 JWT。

#### 请求

```json
{
  "username": "zhangsan",
  "password": "123456"
}
```

| 字段 | 类型 | 必填 | 校验规则 |
|------|------|------|---------|
| username | string | 是 | 非空 |
| password | string | 是 | 非空 |

#### 成功响应（200 OK）

```json
{
  "code": 200,
  "message": "登录成功",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIs..."
  }
}
```

#### 错误响应

| HTTP 状态码 | 场景 | 响应体 |
|------------|------|--------|
| 400 | 参数校验失败（用户名为空、密码为空） | `{ "code": 400, "message": "用户名和密码不能为空", "data": null }` |
| 401 | 用户名或密码错误 | `{ "code": 401, "message": "用户名或密码错误", "data": null }` |

---

### 6.4 POST /api/auth/logout

**说明**：用户登出，将当前 JWT 加入黑名单使其失效。**调用失败时前端仍应主动清除本地 token 并跳转登录页**。

#### 请求头

| 字段 | 值 |
|------|-----|
| Authorization | Bearer {token} |

#### 请求体

无。

#### 成功响应（200 OK）

```json
{
  "code": 200,
  "message": "登出成功",
  "data": null
}
```

#### 错误响应

| HTTP 状态码 | 场景 | 响应体 |
|------------|------|--------|
| 401 | token 无效/已过期（前端仍应清理本地 token 并跳转） | `{ "code": 401, "message": "token 无效或已过期", "data": null }` |

---

### 6.5 GET /api/user/me

**说明**：获取当前登录用户信息。需要 JWT 认证。

#### 请求头

| 字段 | 值 |
|------|-----|
| Authorization | Bearer {token} |

#### 成功响应（200 OK）

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 1,
    "username": "zhangsan"
  }
}
```

#### 错误响应

| HTTP 状态码 | 场景 | 响应体 |
|------------|------|--------|
| 401 | token 无效 | `{ "code": 401, "message": "token 无效", "data": null }` |
| 401 | token 在黑名单中 | `{ "code": 401, "message": "token 已失效", "data": null }` |
| 401 | token 已过期 | `{ "code": 401, "message": "token 已过期", "data": null }` |

---

## 7. 数据库表设计

### 7.1 user 表

存储注册用户信息。

```sql
CREATE TABLE `user` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT  COMMENT '主键',
  `username`    VARCHAR(50)  NOT NULL                 COMMENT '用户名，英文字母和数字组合',
  `password_hash` VARCHAR(255) NOT NULL                COMMENT 'bcrypt 密码哈希',
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='用户表';
```

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT | PRIMARY KEY, AUTO_INCREMENT | 用户唯一标识 |
| username | VARCHAR(50) | NOT NULL, UNIQUE, utf8mb4_bin | 用户名，仅允许 [a-zA-Z0-9]，区分大小写 |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt 加密后的密码哈希值 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 行创建时间 |
| updated_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP, ON UPDATE CURRENT_TIMESTAMP | 行更新时间 |

### 7.2 jwt_blacklist 表

存储已登出/失效的 JWT 黑名单，用于实现服务端 JWT 失效。

```sql
CREATE TABLE `jwt_blacklist` (
  `id`         BIGINT       NOT NULL AUTO_INCREMENT  COMMENT '主键',
  `jti`        VARCHAR(128) NOT NULL                 COMMENT 'JWT 的唯一标识（JWT ID）',
  `expired_at` DATETIME     NOT NULL                 COMMENT '原始 JWT 的过期时间',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入黑名单的时间',
  PRIMARY KEY (`id`),
  KEY `idx_jti` (`jti`),
  KEY `idx_expired_at` (`expired_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='JWT 黑名单表';
```

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT | PRIMARY KEY, AUTO_INCREMENT | 主键 |
| jti | VARCHAR(128) | NOT NULL, INDEX | JWT 的唯一标识，登出时从此表中查询 |
| expired_at | DATETIME | NOT NULL, INDEX | JWT 原始过期时间，用于定时清理过期记录 |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | 记录创建时间 |

### 7.3 清理策略

后端应定期（如每小时）删除 `jwt_blacklist` 表中 `expired_at` 早于当前时间的记录，避免表数据无限增长。此功能可以使用 Spring Boot `@Scheduled` 注解实现，无需引入额外组件。

### 7.4 索引说明

- `user.username`：唯一索引，保证用户名全局唯一
- `jwt_blacklist.jti`：普通索引，加快登出校验时的查询速度
- `jwt_blacklist.expired_at`：普通索引，加快定时清理的查询速度

---

## 8. 前端路由设计

### 8.1 路由表

| 路径 | 组件 | 是否需要登录 | 说明 |
|------|------|-------------|------|
| `/login` | LoginPage | 否（但已登录用户访问时自动跳转 /welcome） | 登录页 |
| `/register` | RegisterPage | 否 | 注册页 |
| `/welcome` | WelcomePage | 是 | 欢迎页 |

三个页面为独立路由，非 tab 切换。

### 8.2 导航守卫

#### 全局前置守卫（beforeEach）

```text
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token');

  if (to.path === '/welcome' && !token) {
    // 访问受保护页面但无 token，重定向至登录页
    next('/login');
  } else if ((to.path === '/login') && token) {
    // 已登录用户访问登录页，重定向至欢迎页
    next('/welcome');
  } else {
    next();
  }
});
```

#### 后端接口保护

Spring Boot 实现一个 `AuthInterceptor` 或 `OncePerRequestFilter`，对所有 `/api/**` 路径（除 `/api/auth/register` 和 `/api/auth/login` 外）进行 JWT 验证：
- 检查请求头 `Authorization` 字段是否存在且格式为 `Bearer {token}`
- 解析 JWT，校验签名和过期时间
- 检查 JWT 的 `jti` 是否在 `jwt_blacklist` 表中
- 以上任一校验失败，返回 HTTP 401

---

## 9. MVP 边界（不做事项）

本 MVP 明确**不包含**以下功能：

1. 工时提交、工时管理、审批流程
2. 邮箱注册或手机号注册
3. 密码重置功能
4. refresh token 机制
5. 用户管理后台
6. 角色或权限分级
7. 第三方登录（OAuth）
8. 记住我或自动登录

以上各项不在本阶段实现范围之内，PRD 中任何地方不包含以上功能的描述或暗示。

---

## 10. 非功能性约束

| 编号 | 约束项 | 具体要求 |
|------|--------|---------|
| 1 | JWT 有效期 | 24 小时（86400 秒），从签发时间算起 |
| 2 | 密码安全 | 使用 bcrypt 加密存储，不存明文密码 |
| 3 | 用户名规则 | 仅允许英文字母和数字 `[a-zA-Z0-9]`，不允许特殊字符，不允许中文字符 |
| 4 | 用户名唯一性 | 数据库级别唯一约束（utf8mb4_bin），区分大小写。即 `Admin` 和 `admin` 视为不同用户 |
| 5 | 密码复杂度 | 无复杂度要求，非空即可。后续版本可增加 |
| 6 | 前端 UI 标准 | 使用 Element Plus 组件库构建界面，保证输入校验和错误提示的用户体验 |
| 7 | token 存储 | 前端在 `localStorage` 中存储 token，key 为 `token` |
| 8 | 请求拦截器 | Axios 请求拦截器自动从 `localStorage` 读取 token 并注入 `Authorization: Bearer {token}` 请求头 |
| 9 | 响应拦截器 | Axios 响应拦截器检测 HTTP 401 状态码时，自动清除 `localStorage` 中的 token 并跳转 `/login` |
| 10 | 方案克制 | 不引入 Redis、缓存、消息队列、NoSQL 等额外中间件，仅使用 MySQL + JWT 库 |

---

## 11. 验收标准

### 11.1 注册功能

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-REG-01 | 用 username=zhangsan, password=123456 注册 | 返回 201，响应体中包含有效 JWT，前端自动存入 localStorage 并跳转至 /welcome |
| TC-REG-02 | 用 username=a, password=1 注册（最短边界） | 返回 201，注册成功 |
| TC-REG-03 | 用 username=Test123, password=abc 注册，再用 username=test123, password=def 注册 | 两者均注册成功（大小写敏感，视为不同用户） |
| TC-REG-04 | 用 username=zhangsan（已存在）, password=任意密码 注册 | 返回 409，错误信息为"用户名已被注册" |
| TC-REG-05 | 用 username=张三（含中文）, password=123 注册 | 返回 400，错误信息为"用户名仅允许英文字母和数字" |
| TC-REG-06 | 用 username=hello world（含空格）, password=123 注册 | 返回 400，错误信息为"用户名仅允许英文字母和数字" |
| TC-REG-07 | 用 username=user@name（含特殊字符）, password=123 注册 | 返回 400，错误信息为"用户名仅允许英文字母和数字" |
| TC-REG-08 | 用户名为空，密码为 123456 注册 | 前端校验阻止提交，显示"请输入用户名" |
| TC-REG-09 | 用户名为 zhangsan，密码为空注册 | 前端校验阻止提交，显示"请输入密码" |
| TC-REG-10 | 注册成功后，直接刷新页面进入 /welcome | 欢迎页正常显示 hello zhangsan（token 已自动携带） |

### 11.2 登录功能

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-LOG-01 | 用已注册的用户名和正确密码登录 | 返回 200，响应体包含有效 JWT，前端存入 localStorage 并跳转 /welcome |
| TC-LOG-02 | 用已注册的用户名和错误密码登录 | 返回 401，页面显示红色错误提示"用户名或密码错误" |
| TC-LOG-03 | 用不存在的用户名登录 | 返回 401，页面显示红色错误提示"用户名或密码错误"（不区分是用户名不存在还是密码错误） |
| TC-LOG-04 | 用户名为空，密码为 123456 登录 | 前端校验阻止提交，显示"请输入用户名" |
| TC-LOG-05 | 用户名为 zhangsan，密码为空登录 | 前端校验阻止提交，显示"请输入密码" |
| TC-LOG-06 | 已登录用户访问 /login | 自动重定向至 /welcome |

### 11.3 欢迎页与认证保护

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-WEL-01 | 持有有效 JWT 访问 /welcome | 页面挂载后自动调用 GET /api/user/me，展示 hello {username} |
| TC-WEL-02 | 无 token 直接访问 /welcome | 前端路由守卫拦截，重定向至 /login |
| TC-WEL-03 | 持有已过期的 JWT 访问 /welcome | GET /api/user/me 返回 401，前端清除 token，跳转 /login |
| TC-WEL-04 | 持有已登出的 JWT（在黑名单中）访问 /welcome | GET /api/user/me 返回 401，前端清除 token，跳转 /login |

### 11.4 登出功能

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-LOGOUT-01 | 已登录用户点击"退出登录"按钮 | 调 POST /api/auth/logout，后端将 jti 加入黑名单，前端清除 localStorage 中的 token，跳转 /login |
| TC-LOGOUT-02 | 登出后直接访问 /welcome | 路由守卫检测不到 token，重定向至 /login |
| TC-LOGOUT-03 | 登出后使用旧 token 调用 GET /api/user/me | 返回 401（token 在黑名单中） |
| TC-LOGOUT-04 | 登出 API 调用失败（如断开网络后点击登出） | 前端仍清除 localStorage 中的 token，跳转 /login |

### 11.5 JWT 有效期

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-JWT-01 | 登录后检查 JWT 的 exp 字段 | exp = iat + 86400（秒），即 24 小时有效期 |

### 11.6 前后端双重保护

| 编号 | 测试步骤 | 预期结果 |
|------|---------|---------|
| TC-DUAL-01 | 清空 localStorage 后直接访问 /welcome | 前端路由守卫拦截，重定向至 /login |
| TC-DUAL-02 | 使用有效 token 访问 /welcome，但后端数据库 jwt_blacklist 中已包含该 token 的 jti | 后端返回 401，前端清除 token 并跳转 /login |
