# nanobot 子 Agent 状态与审计闭环实施方案 V2

日期：2026-08-03

基线：`origin/main@4b94f587`

范围：统一子 Agent 任务状态、主子 Agent 协同、持久化审计、实时接口与 WebUI 运行轨迹

## 1. 为什么继续推进

当前 nanobot 已经能启动子 Agent，也已经具备 parent/child Run、独立进程执行、Goal required
barrier、终止裁决和结果回传。但“子 Agent 能运行”还不等于“子 Agent 是可管理的任务”。

这项工作的目标是让以下链路成为可验证的系统事实：

```text
主 Agent 创建任务
  -> 子 Agent 被接纳并执行
  -> 子 Agent 调用模型和工具
  -> 生成成功、失败、取消、超时或丢失终态
  -> 结果被领取并交付给主 Agent
  -> 主 Agent 消费结果并继续运行或完成 Goal
```

完成后主要支持这些场景：

- 并行分发多个独立调研、开发或验证任务，并准确汇总；
- 主 Agent 根据第一个子 Agent 的结果继续思考，再串行发起后续子 Agent；
- required 子任务未成功时阻止 owner Run 或 Goal 错误完成；
- background 子任务不阻塞当前回答，但重启后仍可查询其真实终态；
- 取消、超时、强杀、进程丢失和 late result 都有可审计裁决；
- 控制单次任务分片产生的子 Agent 总量、并发、深度、token、费用和时间；
- 前端实时展示与历史 Audit 回放使用同一套任务语义。

## 2. 准确的升级范围

本计划升级的是：

> 子 Agent 任务状态机 + 主子 Agent 协同状态机。

主体是统一 `SubagentTask` 生命周期。主 Agent 只改造与子任务相交的边界：spawn admission、
owner Run、required barrier、result claim/delivery、result injection、continuation、取消和预算门禁。

本计划不重写主 Agent 和子 Agent 两套 `AgentRunner`，也不把通用业务逻辑搬进
`nanobot/agent/loop.py` 或 WebUI。

## 3. 当前实现基线

截至上述基线，已经完成：

### 3.1 运行与终止

- `SubagentStatus` 记录 phase、iteration、tool events、usage、owner/child Run 和终止证据；
- Linux 上支持 `ProcessChildExecutor`、独立进程组、版本化 IPC 和进程身份校验；
- Child Worker 可重建 runtime、tools、workspace scope 和 Audit runtime；
- 支持 cooperative cancel、强杀、进程树回收和 `termination_failed` fail-closed 裁决；
- 迟到的进程结果不能覆盖已经确认的强杀或丢失状态。

### 3.2 Goal required 编排

- `GoalOrchestrationStore` schema V2 持久化 required task、group、attempt 和 replacement；
- 已有 durable UTC deadline、executor identity、termination state/evidence；
- 已有 result `claimed_at`、`claim_owner_run_id` 和 `delivery_phase`；
- `await_subagents` 提供 bounded wait，Goal completion 受 required gate 约束；
- gateway 启动时可将无法证明仍存活的 required task 诚实恢复为 `lost`。

### 3.3 Audit 与 WebUI

- Audit 已记录 parent/child Run、Model、Tool、Checkpoint、Input injection 和 Delivery；
- Graph 已识别 `child_agent` Run，并建立 `spawn_branch`、`parent_run`、`result_return`；
- WebUI 已展示 Run/Model/Tool 节点和工具 retry/continuation/recovery 关系。

这些能力是新版方案的基线，不应重复实现。

## 4. 当前剩余缺口

### 4.1 普通 background 子任务没有 durable task record

`required=false` 子任务主要依赖 `SubagentManager` 的进程内字典和有限终态缓存。重启后，运行句柄、
阶段、usage、终止和交付状态无法作为统一任务完整查询。

### 4.2 Goal required record 不是通用任务模型

Goal orchestration 只应负责 required obligation、group、replacement 和 barrier。目前它同时保存了
一部分执行与交付字段。若继续扩展，会形成“Goal 子任务”和“普通子任务”两套状态模型。

### 4.3 Audit 仍从 Run 推断任务

当前没有版本化的任务级 lifecycle event，也没有 `subagent_task` Graph node。Run 成功不能自动证明
任务已成功交付，Child Run 与 Task 也不是同一实体。

### 4.4 实时协议没有子任务状态

WebSocket 有 turn、goal、message 和 tool progress，但没有可恢复的 `subagent_snapshot` 与带 revision
的增量事件。前端刷新后无法先水合快照再合并实时变化。

### 4.5 输入输出与预算仍然较弱

`spawn` 仍以字符串 `task` 为主，结果以文本注入为主；缺少稳定的 `TaskSpec`、`TaskResult`、子任务
总量、递归深度、累计 token/费用和明确的 admission rejection。

## 5. 权威边界

采用“业务状态、审计证据、查询投影分离”的边界：

```text
SubagentTaskStore        任务当前状态与 revision 的业务真相
GoalOrchestrationStore   required obligation、group、replacement、barrier
Audit event log          append-only 生命周期与执行证据
Audit projection/index   历史查询和 Graph 构建，可重建
SubagentManager          当前进程调度句柄和短期缓存
MessageBus               结果通知与注入传输，不是持久化真相
WebSocket                实时传输，不是业务真相
```

状态转移与 Audit 事件不能采用不可靠的双写。推荐在任务记录中保存待发布 lifecycle outbox，由可靠
发布器写入 Audit 后确认；重复发布依靠 `task_id + revision + event_type` 幂等。Audit 暂时不可用时，
任务业务状态仍可提交，但必须明确标记 audit pending/degraded，并在恢复后补发。

迁移期间，现有 Goal orchestration V2 继续作为 required barrier 的兼容真相；统一 TaskStore 落地后，
Goal 只保留 obligation 引用和必要快照。不得一次性迁移并破坏活动 Goal。

## 6. 统一状态模型

### 6.1 四个正交维度

任务业务状态：

```text
created -> queued -> running -> succeeded | failed | cancelled | timed_out | lost
```

执行阶段，仅用于进度展示：

```text
initializing -> running_model -> awaiting_tools -> tools_completed
             -> final_response -> result_preparing
```

终止裁决沿用现有语义：

```text
none -> cancel_requested -> grace_waiting -> cooperatively_exited
                              |-> force_kill_requested -> force_killed
                              |-> termination_failed
```

结果交付状态：

```text
not_ready -> ready -> claimed_pending_delivery -> delivered
                    |-> delivery_failed
```

约束：

- `phase` 不替代业务终态；
- cancel request、timeout signal 和 task cancellation 都不等于执行器已经终止；
- 终态只能通过统一 transition service 写入，重复转移幂等；
- 终态不可被 late result 覆盖；
- `succeeded` 与 `delivered` 是不同事实；
- required 与 background 共用任务模型，仅等待和完成门禁不同。

### 6.2 推荐 SubagentTask V2

```json
{
  "schema_version": 2,
  "revision": 7,
  "task_id": "sub-123",
  "owner_session_key": "websocket:chat-1",
  "owner_run_id": "run-parent",
  "child_run_id": "run-child",
  "spawn_tool_call_id": "tool-spawn",
  "required": false,
  "task_group": "research",
  "attempt": 1,
  "replaces_task_id": null,
  "status": "running",
  "phase": "awaiting_tools",
  "termination": {"state": "none", "evidence": null},
  "delivery": {"phase": "not_ready", "claim_owner_run_id": null},
  "executor": {"backend": "process_group_v1", "process_instance_id": "..."},
  "progress": {"iteration": 3, "current_tool": "web_search"},
  "usage": {"prompt_tokens": 12000, "completion_tokens": 3000, "cost_usd": null},
  "budget": {"max_tokens": null, "max_cost_usd": null, "deadline_at": null},
  "created_at": "2026-08-03T00:00:00Z",
  "started_at": "2026-08-03T00:00:01Z",
  "finished_at": null,
  "error": null
}
```

所有持久化时间使用带时区 UTC。monotonic clock 仅用于当前进程等待和耗时测量。

## 7. Parent-to-child 与 child-to-parent 协议

保留字符串 `task` 兼容入口，同时定义版本化结构输入：

```json
{
  "schema_version": 1,
  "objective": "调研 PostgreSQL 连接池方案",
  "context": "仅包含完成任务所需的背景和引用",
  "constraints": ["只使用公开资料", "不要修改代码"],
  "deliverables": ["结论", "证据", "风险"],
  "acceptance_criteria": ["至少三个独立来源"],
  "dependencies": [],
  "output_mode": "structured_preferred"
}
```

结构化结果：

```json
{
  "schema_version": 1,
  "status": "succeeded",
  "summary": "...",
  "evidence": [],
  "artifacts": [],
  "files_changed": [],
  "tests": [],
  "risks": [],
  "error": null
}
```

兼容规则：

- 旧字符串 task 转换为 `objective`；
- 旧文本结果转换为 `summary`，不得伪造 evidence、tests 或 files_changed；
- 主 Agent 只接收限长、脱敏的结果信封，完整 artifact 通过引用按需读取；
- 不自动复制主 Agent 完整历史、完整 system prompt、思维链或秘密到子 Agent；
- prompt 对齐通过稳定公共 policy、明确 TaskSpec 和能力声明完成，不追求字节级 system prompt 相同。

## 8. 生命周期事件与关系契约

新增版本化任务事件：

```text
subagent_created
subagent_admitted
subagent_phase_changed
subagent_usage_updated
subagent_cancel_requested
subagent_termination_decided
subagent_result_ready
subagent_result_claimed
subagent_result_delivered
subagent_delivery_failed
subagent_terminal
subagent_recovered
subagent_lost
```

每个事件至少包含：`task_id`、`revision`、trace/turn/owner Run/child Run/spawn tool call ID、前后
状态、安全摘要、时间和 schema version。

关系要求：

- `spawn_branch` 必须锚定真实 spawn tool call 与 child Run；
- `result_return` 必须锚定真实 result claim/delivery 与 input injection；
- replacement/retry 必须引用真实 `replaces_task_id`；
- recovery 必须引用 checkpoint、executor identity 或 startup recovery 证据；
- 禁止按时间邻近、同名工具或文本相似度猜测关系；
- 旧 Trace 缺少 task event 时可标记 `legacy_inferred`，但不得伪装成 recorded evidence。

## 9. 成本与防止过度分片

预算分为 task、owner Run、session/Goal 三层：

- `max_children`：一次 owner Run 可创建的总子任务数；
- `max_concurrent_children`：同时运行上限；
- `max_child_depth`：禁止无界递归委派；
- `max_total_tokens`、`max_cost_usd`：累计模型预算；
- `max_wall_time_seconds`：任务或 Goal 墙钟预算；
- 可选 `min_task_value` 或 admission policy：过小、重复、强依赖任务不应拆分。

spawn admission 必须原子执行“读取预算 -> 预留额度 -> 创建任务”。拒绝时返回结构化 reason，例如
`concurrency_limit`、`child_count_limit`、`depth_limit`、`token_budget_exhausted`。模型应缩小范围、等待
现有任务或由主 Agent 直接处理，不能通过不断换 label 绕过限制。

先上线观测和告警，再启用默认拒绝门禁；但并发限制和递归深度应始终有硬上限。

## 10. API、实时协议与 WebUI

### 10.1 查询与实时协议

提供按 session/trace/task 查询的脱敏 DTO：

- 任务列表与单任务详情；
- 当前快照及 `revision`；
- lifecycle timeline；
- Task 对应的 owner Run、child Run、Tool、Delivery；
- 聚合 usage、预算和 admission rejection。

WebSocket 推荐采用：

```text
subagent_snapshot       订阅或重连后的有界任务快照
subagent_status_changed 携带 task_id、revision 和变化字段
```

前端按 `task_id + revision` 合并，忽略重复和旧 revision；发现 revision 跳跃时重新拉取快照。

### 10.2 前端运行轨迹

前端应区分五层：

```text
Task      任务目标、required/background、状态、预算、结果交付
Run       主/子/continuation Run、iteration、stop reason
Model     provider、model、tokens、attempt
Tool      工具输入安全摘要、结果、错误、timeout、恢复
Delivery  result ready、claim、注入、投递结果
```

TraceGraph 增加 `subagent_task` 节点或明确的 Task 容器，不再把 child Run 直接等同于任务。支持：

- spawn -> Task -> child Run -> result -> continuation 双向定位；
- 并行子任务独立泳道和串行二次委派；
- running、waiting、succeeded、failed、cancelled、timed_out、lost；
- termination evidence、delivery pending/failed、usage 和预算；
- 实时变化、刷新水合、历史回放和旧 Trace 降级展示；
- 桌面与移动端不重叠、不跳位，未知字段不导致崩溃。

## 11. 分阶段实施

### 阶段 0：差距审计与契约冻结

- 以当前代码和测试为事实基线，建立“已实现/部分实现/缺失”矩阵；
- 冻结四维状态枚举、合法转移、revision、幂等 key、脱敏 DTO；
- 决定 TaskStore/outbox 持久化位置和活动 Goal 兼容策略；
- 先补 schema、非法转移、late result 和兼容 fixture 测试。

交付门：契约评审通过，尚不大规模修改 UI。

### 阶段 1：统一 durable SubagentTask

- 为 required 和 background 创建同一种任务记录；
- 将 `SubagentStatus` 降为运行时镜像，不作为公开 DTO 或跨重启真相；
- 接入现有 ProcessChildExecutor、checkpoint 和终止证据；
- 对 Goal orchestration V2 做 read-through/dual-read 兼容，避免破坏活动 Goal；
- 实现 startup reconciliation 与 terminal record retention。

交付门：普通 background 子任务重启后也能查询诚实终态。

### 阶段 2：统一转移服务、outbox 与 Audit

- 所有状态变化经统一 transition service 校验并递增 revision；
- 写入 lifecycle outbox，可靠发布任务级 Audit event；
- 实现 result ready/claim/delivery 的原子幂等规则；
- Graph projection 能区分 recorded 与 legacy inferred evidence；
- 验证 Audit degraded/recovered 时不丢业务状态和待补事件。

交付门：任务状态、Audit evidence 和结果交付可重放对账。

### 阶段 3：TaskSpec、TaskResult 与预算观测

- 增加结构化协议及字符串/文本适配层；
- 记录 task/owner/session/Goal usage、耗时、子任务数和深度；
- 实现 admission reservation、结构化拒绝原因和预算释放；
- 先观测告警，再开启 token/费用门禁；并发和深度保留硬限制。

交付门：能解释为什么允许或拒绝一次任务分片。

### 阶段 4：查询 API 与 WebSocket

- 增加脱敏任务 DTO、snapshot 和 timeline 查询；
- 增加 `subagent_snapshot`、`subagent_status_changed`；
- 支持订阅水合、乱序、重复、revision gap 和重连；
- 保持旧 WebSocket 客户端、Payload 权限、认证和限长兼容。

交付门：前端无需读取 `SubagentManager` 内存即可得到当前与历史状态。

### 阶段 5：Audit Graph Task 语义

- 增加 Task node/region、summary 和 lifecycle timeline；
- 用真实 ID 连接 spawn、child Run、replacement、recovery、delivery、continuation；
- 保留现有 Run/Tool 关系和 builder version 兼容；
- 对旧 Trace 提供清晰的证据降级标识。

交付门：Graph 可以回答“创建了什么任务、怎么执行、结果是否被主 Agent 消费”。

### 阶段 6：WebUI 闭环与真实验收

- 实现任务总览、并行泳道、状态 badge、预算和 Delivery Inspector；
- 实时状态和历史 Audit 共用枚举、文案与合并逻辑；
- 验证并行、串行二次委派、Goal barrier、background、取消、超时、重启、预算拒绝；
- 使用 Vitest、build 和真实 Gateway Playwright 验证桌面/移动端。

交付门：刷新、重连和历史回放均能还原同一条主子 Agent 链路。

## 12. 验收矩阵

后端必须覆盖：

- 一个 owner Run 并发创建多个子任务；
- 主 Agent 消费第一个结果后再创建第二个子任务；
- required 成功、失败、替换和 barrier；
- background 不阻塞 owner Run，但仍持久化和交付；
- cooperative cancel、force kill、termination failed、timeout、lost 和 late result；
- 重启前后 result claim/delivery 不重复；
- 并发、总量、深度、token/费用预算拒绝；
- Audit 写入降级后的 outbox 补发和 projection 重建；
- 旧 Goal、旧 Trace、旧字符串 task 和旧文本 result 兼容；
- 默认接口不泄露 prompt、思维链、秘密和完整外部内容。

前端必须覆盖：

- 实时看到每个子任务状态而不是只有主 Turn loading；
- 从 spawn 定位 Task/Child，从 result 定位主 Agent continuation；
- 并行与串行任务布局明确；
- 刷新和重连后先水合快照，再合并增量；
- 状态异常、delivery pending 和预算拒绝有明确文案；
- 历史与实时对同一 revision 显示一致；
- 旧数据和未知枚举可降级，不崩溃、不误报成功。

## 13. 安全与兼容约束

- 不持久化完整 prompt、思维链、secret、provider object 或未脱敏工具参数；
- provider/runtime 不 pickle 到任务记录或 worker；
- 任务摘要、错误和外部内容必须限长；
- workspace scope 和工具安全边界必须原样传递并校验；
- 不使用 PID 本身作为恢复存活证据，沿用 process identity 校验；
- 不改写已有 Audit JSONL 和 Session 历史；
- 新 API、WebSocket 字段和协议先 additive，再经过迁移期移除旧路径；
- 修改 `loop.py`、`runner.py` 仅限必要协调点，并补聚焦集成测试。

首个可执行工作单元应是“阶段 0 契约冻结 + 阶段 1 最小 durable TaskStore 骨架”，而不是再次实现
ProcessChildExecutor、强杀、Goal deadline 或现有 Audit Run 关系。
