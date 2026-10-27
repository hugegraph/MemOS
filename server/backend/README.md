# Dashboard 后端

为 Issue #3 提供 FastAPI 适配服务，将现有 Dashboard 接入真实 MemOS REST 和 MCP 网关审计 JSONL。前端 src/、shared/ 和 server/mock/ 不变。

- 接口规格：仓库根目录 openapi.json

## 范围

已实现元信息、六标签记忆列表/详情、软删除与恢复/重试、调用日志、按日用量、项目生命周期、账号/会话、成员/邀请、抽取 Prompt、图谱/全文、模型清单、项目密钥及属性树模板接口。额度/订阅和 skillMD 按 Issue 约定返回固定数据。

知识库按请求者要求停止继续开发：保留实体 CRUD、项目关联、文件上传/列表/下载/删除及测试；**POST /api/dashboard/knowledgebase/memory/list 未实现**。此范围需要维护者审阅，不声明完整解决原 Issue。

## 架构与数据流

```text
浏览器 → 前端同源代理 → Dashboard FastAPI 后端
                         ├─ REST → MemOS → Neo4j / Qdrant
                         ├─ SQLite：账号、会话、项目、成员、邀请、审计索引
                         └─ 共享配置文件 → MCP 网关密钥自动重载

项目客户端 → MCP 网关 → MemOS
                └─ calls-*.jsonl → 增量审计索引 → 日志列表/详情、每日用量
```

后端按 OpenAPI 投影真实上游数据，先校验会话和项目权限，再访问对应 cube；控制面状态与审计索引持久化到本地 SQLite。日志与用量复用同一索引，避免重复解析及统计口径分叉。模型调用由上游执行，Dashboard 不保存模型服务凭据；网关密钥通过共享文件发布，下次鉴权请求检查并重载。

## 上游依赖

后端依赖 deployment-v2 的配套能力。使用该分支的独立工作区，基线为 8d6e231d7d95ea29b6a2716c9c34837644d3e7f6，在干净基线上应用本 PR 唯一的完整补丁：

```sh
git checkout 8d6e231d7d95ea29b6a2716c9c34837644d3e7f6
git apply --check /path/to/dashboard/server/backend/patches/deployment-v2-dashboard-support.patch
git apply /path/to/dashboard/server/backend/patches/deployment-v2-dashboard-support.patch
```

补丁包含记忆图/向量生命周期确认、画像字段读写状态、事件筛选、Prompt 运行时修复、模型提供方兼容、网关配置发布与自动重载，以及对应测试和上游 API schema。请按 deployment-v2 的说明配置模型、启动或重建 MemOS、Neo4j、Qdrant 和网关。不能只部署 Dashboard 适配器而忽略该补丁。

## 安装与启动

需要 Python 3.11+，以下命令在 Dashboard 仓库根目录运行：

```sh
python -m venv .venv
# 按当前 shell 的方式激活 .venv
python -m pip install -r server/backend/requirements-dev.txt
```

参考 [.env.example](.env.example) 导出进程环境变量；程序不会自动读取该文件。管理员初始化与后端服务必须使用相同配置：

```sh
export DASHBOARD_DATA_DIR="$PWD/.data"
export DASHBOARD_UPSTREAM_URL=http://127.0.0.1:8000/
export DASHBOARD_PROJECTS='demo:demo'
export DASHBOARD_DEFAULT_PROJECT_ID=demo
export DASHBOARD_AUDIT_GATEWAY_DIR=/absolute/path/to/gateway/audit
export DASHBOARD_GATEWAY_KEYS_FILE=/absolute/path/to/mcp-gateway/tenants.conf
python -m server.backend.bootstrap admin --bootstrap
python -m server.backend --host 127.0.0.1 --port 5281
```

PowerShell 使用 `$env:NAME = 'value'` 设置同名变量。初始化命令以隐藏输入提示设置密码，不将密码放入命令行。鉴权默认开启；后端不持有模型密钥。上游服务本身无鉴权，应置于受信网络中。

另开终端启动原有前端代理，不启动 Mock：

```sh
pnpm install --frozen-lockfile
MOCK_ORIGIN=http://127.0.0.1:5281 pnpm dev
```

PowerShell 先设置 `$env:MOCK_ORIGIN = 'http://127.0.0.1:5281'`，再运行 `pnpm dev`。访问 http://127.0.0.1:5274/__login 登录并选择项目。使用其他域名或端口时，通过 DASHBOARD_ALLOWED_ORIGINS 设置分号分隔的可信 Origin。

## 配置

| 环境变量 | 默认值/用途 |
|---|---|
| DASHBOARD_UPSTREAM_URL | http://127.0.0.1:8000/ |
| DASHBOARD_UPSTREAM_TIMEOUT_SECONDS | 3；普通上游请求截止时间，范围 0–60 秒，不含 0 |
| DASHBOARD_MODEL_TIMEOUT_SECONDS | 120；Prompt 生成/调试截止时间，不超过 180 秒 |
| DASHBOARD_DATA_DIR | .data；控制面、会话与审计索引持久化目录 |
| DASHBOARD_PROJECTS | projectId:cubeId，以分号分隔的初始项目映射 |
| DASHBOARD_DEFAULT_PROJECT_ID | 初始项目，配置种子映射时必须指定 |
| DASHBOARD_AUDIT_GATEWAY_DIR | 网关 calls-*.jsonl 所在目录；缺失/不可读返回明确错误 |
| DASHBOARD_AUDIT_CONSOLE_DIR | 可选；默认位于数据目录下 |
| DASHBOARD_GATEWAY_KEYS_FILE | 与网关 MEMOS_TENANTS_FILE 指向同一绝对路径 |
| DASHBOARD_AUTH_ENABLED | true；正式验收保持开启 |
| DASHBOARD_SESSION_TTL_SECONDS | 86400 |
| DASHBOARD_ALLOWED_ORIGINS | 默认允许本地 5274/5276/5281；生产部署应显式配置 |

首次部署升级后的网关需要重启一次。之后密钥新增/撤销成功发布后，下一个鉴权请求自动生效，无需重启；改名不影响调用。共享配置目录须支持发布锁。损坏、丢失、冲突或 pending 配置暂时拒绝文件密钥，确认发布后自动恢复；独立环境变量密钥保留。环境变量变更仍需重启，已授权执行中的请求不会被取消。

## 数据与接口约定

- 统一响应为 `{code,data,message}`；列表键、分页参数及字段命名按 OpenAPI，不改前端来迁就后端。
- 记忆列表/统计仅计算授权项目、指定类型及筛选范围内的 activated 数据；先筛选和计数，再分页。不透传 embedding、sources、history，列表正文限制 500 字，详情保留全文。
- 六标签映射集中在 memory_models.py；UserMemory 属于事实列表，ProfileMemory 对应用户画像字段。图谱保持原始类型和字段 ID，不伪造关系。超出节点/字节限制时返回 truncated。
- 展示时间固定 UTC+8；存储与机器查询不被展示转换覆盖。日期筛选按 UTC 日边界，带时区时间戳按明确时刻处理。
- 删除为软删除，恢复/重试/回执通过接口；普通记忆检查图与向量状态，画像字段使用字段状态语义。
- 日志缺正文时返回 JSON 字符串 null，缺耗时为 null、token 为 0、时间为空串、状态为 UNKNOWN。无法可靠归属租户的记录隔离，不猜测归属。
- SQLite 与共享文件锁适用于当前单机部署，不承诺分布式 HA；控制面数据库和网关明文凭据文件应限制目录权限，不提交到仓库。

## 验证

```sh
python -m pytest server/backend/tests -q
python -m ruff check server/backend
pnpm verify
pnpm --package=@redocly/cli dlx redocly lint openapi.json
git diff origin/dashboard-v2 -- src/ shared/ server/mock/
python -m server.backend.acceptance.smoke --base http://127.0.0.1:5281 --project demo
```

smoke 命令提示输入账号密码，也支持 DASHBOARD_TEST_USERNAME/DASHBOARD_TEST_PASSWORD；不创建、修改或删除业务资源，不调用对话模型。额外提供 `--native http://127.0.0.1:8000 --cube demo` 可按同 ID 对照上游正文。只输出检查结果、数量和大小，不打印密钥或记忆正文。

性能验收目标：记忆单页响应小于 200 KB，至少 1 万行日志首屏小于 1 秒。可用 acceptance/ 中的脚本复验；其依赖限制见下文。上游完整补丁应在指定干净基线上执行 `git apply --check`。私有运行日志、凭据、数据库和截图不随代码提交。

### 已执行验收摘要

以下为开发阶段已执行结果，不表示本次文档整理重新运行了全量测试；不同批次的测试数量不相加。

| 范围 | 已记录结果 |
|---|---|
| 后端完整测试 | 318 passed |
| 后续授权策略回归 | 23 passed |
| 网关自动重载 / 后端密钥回归 | 56 passed / 27 passed |
| 打包与交付检查 | 12 passed |
| 前端检查 | pnpm verify 通过；src/、shared/、server/mock/ 相对基线零改动 |
| OpenAPI 检查 | Redocly 0 错误、4 警告：四个表单接口成功返回 303 |
| 1 万行日志冷首屏 | 三次 805.350 / 779.142 / 795.091 ms（本机测试） |
| 10 条记忆列表响应 | 3,688 字节（该样本，不代表所有页） |
| 真实链路与故障 | 停 Mock、同 ID REST 对照、软删除/恢复、JSONL 对照、重启持久化及上游故障提示已验证 |

未宣称上游全仓测试通过：曾复现 18 项上游基线失败。知识库未完成部分、属性树前端占位及其他限制见下节。私有验收日志不提交；复核以本节命令、人工审核步骤及配套测试代码为入口。

## 已知限制

- 知识库入口禁用，且缺少知识库记忆列表；未完成范围如上，不作为完整交付。
- 属性树模板页面仍为占位页，已交付 API，不宣称有编辑 UI。
- 原前端缺少 ProfileMemory 中文图例；列表大类与图谱原始类型标签口径不同。本 PR 不改 src/，不通过伪造类型掩盖差异。
- 归档当前项目后表格状态可能需要刷新；恢复、重试等没有对应前端按钮。
- 用量无法按缺少可信审计来源的 apiKeyId 筛选，该请求明确拒绝。
- acceptance/task14_driver.py、task15_driver.py、task16_driver.py 和 start_live.ps1 是依赖原 Windows 私有夹具的历史工具；部署与跨平台验收请使用本文模块命令和 smoke，不需要本机 D:/MemOS/setup。

## 权限边界

平台账号管理要求平台管理员；项目管理按 viewer/editor/owner 授权。`/admin/` 前缀不等于全部接口都要求平台管理员。此策略已在 OpenAPI 标注，仍需维护者确认与 Issue 的预期一致。

| 接口 | 最低权限 | 范围/约束 |
|---|---|---|
| GET /api/auth/me | 已登录 | 只返回当前会话账号 |
| GET /admin/accounts | 平台管理员 | 全站账号列表 |
| POST /admin/accounts/{id} | 平台管理员 | 更新账号，保护最后有效管理员及 owner |
| DELETE /admin/accounts/{id} | 平台管理员 | 删除账号，保护最后有效管理员及 owner |
| GET /admin/tenants/{tenantId}/members | 项目 viewer | 仅目标团队项目 |
| GET /admin/tenants/{tenantId}/member-candidates | 项目 owner | 候选账号搜索，不授予全站账号管理权限 |
| POST /admin/tenants/{tenantId}/members | 项目 owner | 添加或修改成员；项目需可用，保护最后有效 owner |
| POST /admin/tenants/{tenantId}/members/{accountId}/remove | 项目 owner | 仅目标项目；保护最后有效 owner |
| POST /admin/tenants/{tenantId}/members/leave | 本项目成员 | 仅退出自己；保护最后有效 owner |
| GET /admin/tenants/{tenantId}/invites | 项目 owner | 仅目标团队项目 |
| POST /admin/tenants/{tenantId}/invites | 项目 owner | 目标项目需可用 |
| POST /admin/tenants/{tenantId}/invites/{inviteId}/role | 项目 owner | 邀请必须属于该项目 |
| POST /admin/tenants/{tenantId}/invites/{inviteId}/revoke | 项目 owner | 邀请必须属于该项目 |

- 平台管理员可覆盖项目角色检查，但个人空间、项目状态、最后有效管理员/owner 约束仍生效。
- 匿名、过期会话、禁用账号返回 401；可见资源权限不足返回 403；无权访问的项目或跨项目资源返回 404。
- 普通项目 owner 不能管理全站账号或其他项目。邀请接受独立校验令牌、过期、撤销和单次使用。
- 浏览器写请求校验 Origin/CSRF；正式部署保持鉴权开启。

## 人工审核

先停止 Mock，启动真实上游、网关、后端和前端代理。仅使用新建演示资源，不操作已有业务数据。

1. 登录后检查 `/api/meta` 的 `code=0`、`mode=live`，刷新仍能读取数据。
2. 用演示项目密钥调用网关 `add_message` 写入唯一标记，在记忆列表搜索；按同一 ID 用上游 REST 对照详情。检查六标签、筛选和分页，切换项目验证隔离。
3. 删除自己的测试记忆，确认列表、详情与原生读取不可见；通过恢复接口验证同 ID 与正文。恢复/重试没有前端按钮。
4. 同一会话多次写入后检查图谱分组、连线、缩放、筛选和节点详情，不要求上游未提供的关系出现。
5. 日志搜索该标记，对照原始 `calls-*.jsonl` 的事件、时间和耗时；详情请求/响应是 JSON 字符串。用量按相同日期范围与同一审计数据核对，缺失 token 显示 0 不代表没有消耗。
6. 创建测试项目，编辑、切换、停用/启用、归档/恢复，并在服务重启后检查持久化。
7. 使用普通账号和管理员分别验证上表权限、成员添加/变更/移除/退出，以及最后有效管理员/owner 保护。
8. 创建邀请，在独立浏览器会话打开 `/__login/invite/{token}`，登录或注册接受；已用、撤销或过期邀请不能再次加入。
9. 创建演示密钥直接调用网关；改名不影响凭据，撤销后下一请求应 401，其他密钥仍可用。
10. 在隔离项目保存 Prompt，通过真实写入验证唯一规则生效，结束后恢复配置；生成候选不保存不生效，快速调试不写入记忆。模型调用可能产生费用。
11. 模型清单应与真实配置一致且不包含凭据。属性树模板仅验 API，不能将前端占位页算作功能 UI；知识库不在本轮完整验收范围。
12. 仅在可控环境停止上游，检查页面错误提示与重试，随后恢复服务。清理自己的测试资源，不在提交或截图中公开有效凭据、邀请令牌及业务隐私。
