# nanobot 子 Agent 强制终止、Deadline 恢复与真实 WebUI 双端定位修复方案

日期：2026-08-02
依据：`docs/subagent-lifecycle-three-issues-research.md`、当前 `codex/subagent-tool-recovery` 分支源码与测试。
目标：修复三个相互依赖的运行时缺口，同时保持 Goal、Audit、安全和兼容边界可审查、可回退。

## 1. 目标与非目标

### 1.1 目标

1. 对 Child 的取消建立可证明的状态机，严格区分取消请求、协作退出、强制终止和无法确认退出。
2. required Child 的 deadline 在 continuation、checkpoint、服务重启和容器重建后不重置。
3. Child terminal/result 通过 durable state 和 `subagent_task_id` 幂等恢复，不依赖内存 MessageBus 存活。
4. `tool_recovery` 在真实 Audit Graph、Events API、Gateway dist 和 WebUI 下支持失败端与恢复端双向定位。
5. Payload 始终默认关闭，只有用户显式操作后才通过认证、有界、脱敏接口加载。

### 1.2 非目标

- 不修改 `caused_by_event_id` 表达 Tool 恢复。
- 不按 basename、同名 Tool、时间邻近、资源字符串或前端文本推断恢复关系。
- 不把 `required=false` 全部改成同步任务。
- 不修改历史 Audit JSONL、Payload JSONL、catalog 或旧 Session 历史。
- 不承诺能够回滚已经发生的外部副作用。
- 不用线程池冒充可杀执行器；Python 线程不能被可靠强制终止。
- 不把合成 fixture 的通过结果表述成真实生产验收通过。

## 2. 已确认的既有契约

以下契约沿用当前分支，不重新设计：

- required join deadline 默认 300 秒。
- deadline 触发后业务终态映射为 `timed_out`，但只有确认执行已经退出时才能写入。
- 不可协作 Child 在没有可杀 backend 时 fail-closed。
- `required=false` 保持 `background_notify`。
- 使用最小 `required=true => join_current_run`，不增加 `completion_policy`。
- runtime auto-barrier 只写 metadata，不伪造新 Event。
- 旧客户端将未知 `tool_recovery` 降级为普通边。
- 不增加 selected edge URL 深链。
- 有未完成 required Child 的 guarded Run 关闭 final streaming，guard 默认启用。

## 3. 核心状态模型

### 3.1 业务任务状态

保持现有 task status 的主要集合：

```text
running -> succeeded | failed | cancelled | timed_out | lost
```

终态不可被晚到结果覆盖。replacement task 使用新 task ID，旧任务保持原终态。

### 3.2 终止状态

新增与业务状态正交的 `termination_state`，避免扩散公开 task status：

```text
none
cancel_requested
grace_waiting
cooperatively_exited
force_kill_requested
force_killed
termination_failed
```

规则：

- `cancel_requested` 和 `force_kill_requested` 都不是终止证明。
- 只有 `cooperatively_exited` 或 `force_killed` 可以确认执行已停止。
- `termination_failed` 对 required task 映射为业务 `lost`，completion guard fail-closed。
- 正常 `succeeded`/`failed` 可使用 `termination_state=none`，因为执行已经自然退出。
- timeout 只有在 `cooperatively_exited` 或 `force_killed` 后才写 `timed_out`；否则写 `lost`，并保留 `deadline_expired=true`。

### 3.3 推荐 durable record

在现有 Goal orchestration task record 上向后兼容增加：

```json
{
  "task_id": "...",
  "owner_run_id": "...",
  "child_run_id": "...",
  "required": true,
  "status": "running",
  "created_at": "2026-08-02T00:00:00Z",
  "deadline_at": "2026-08-02T00:05:00Z",
  "cancel_requested_at": null,
  "grace_deadline_at": null,
  "ended_at": null,
  "deadline_expired": false,
  "termination_state": "none",
  "termination_evidence": null,
  "executor": {
    "backend": "asyncio",
    "executor_id": null,
    "process_instance_id": null
  },
  "result": {
    "available": false,
    "summary": null,
    "claimed_at": null,
    "claim_owner_run_id": null
  }
}
```

禁止持久化 PID 作为唯一身份凭据。增强 backend 应保存不可复用的 `executor_id` 和 process instance token；PID 只能作为诊断字段。

## 4. Deadline 与时钟设计

### 4.1 权威时间

- durable 真相使用带时区的绝对 UTC `deadline_at`。
- 单次进程内等待使用 `time.monotonic()`，等待前由 UTC remaining 计算本轮单调 deadline。
- 不持久化 monotonic timestamp，不跨进程比较 monotonic 值。
- 所有恢复入口执行 `remaining = deadline_at - now_utc`；`remaining <= 0` 不得重新等待 300 秒。
- 发现严重 wall-clock 回拨时记录 degraded 状态并 fail-closed，不延长原 deadline。

### 4.2 Deadline 继承

- 初始 required Child：由 spawn 时确定 `deadline_at`。
- `await_subagents`：调用者的 `timeout_seconds` 只能缩短本次等待，不能延长 task durable deadline。
- Goal continuation、checkpoint restore、Child-result continuation：继承原 task deadline。
- replacement Child：默认获得新的 300 秒 deadline，但必须是新的 task/attempt，并保留 `resolved_by_task_id`；不能改写旧任务 deadline。
- `required=false`：不进入 owner Run completion deadline；工具自己的 timeout 保持现状。

## 5. Cancel、宽限和强制终止流程

### 5.1 最小可行实现：诚实 fail-closed

第一阶段不引入新进程架构，先修复错误声明：

1. deadline 到达或 `/stop` 后原子写 `cancel_requested`。
2. 调用 `asyncio.Task.cancel()`。
3. 在短宽限期内 `gather(return_exceptions=True)`。
4. Task 确认退出：写 `cooperatively_exited`，再写 `cancelled` 或 `timed_out`。
5. 宽限期内未退出：写 `termination_failed` + `lost`，不能写 `cancelled/timed_out`，不能让 required barrier 满足。
6. late result 只写 suppressed/late evidence；不得重写 terminal 或再次进入 history/outbound。

该阶段修复的是状态真实性，不代表不可协作代码已被强杀。

### 5.2 增强实现：killable Child executor

真正不可协作的 Child 必须运行在父进程可监管的独立进程中。推荐 process-per-child，避免一个卡死 worker 污染同池其他任务。

组件建议：

- `nanobot/agent/child_executor.py`：backend 接口、状态和 supervisor。
- `nanobot/agent/child_worker.py`：最小 worker 入口和结构化 IPC。
- `SubagentManager`：只负责 orchestration、owner、结果和 backend 调用，不自行实现平台 kill 细节。

建议接口：

```python
class ChildExecutor(Protocol):
    async def start(self, spec: ChildExecutionSpec) -> ChildHandle: ...
    async def request_cancel(self, handle: ChildHandle) -> None: ...
    async def wait(self, handle: ChildHandle, timeout: float) -> ChildExit | None: ...
    async def force_kill(self, handle: ChildHandle) -> ChildExit | None: ...
    async def close(self) -> None: ...
```

实现约束：

- IPC 只传结构化、最小化 execution spec 和 result envelope；不得把 Provider 对象直接 pickle。
- Runtime rehydration 必须复用现有配置/provider factory，并证明动态 model/runtime override 能重建；无法重建的 Provider capability 标为 `cooperative_only`，required task fail-closed。
- secret 不写命令行、Audit、临时文件或持久化 state。父子进程通过受控 pipe 传递必要凭据，worker 退出后关闭 pipe。
- Unix worker 创建独立进程组；Windows 使用可证明的进程树控制。平台不支持时不得显示 `force_killed`。
- 强杀后等待 root process exit、reap，并关闭 stdout/stderr/stdin、reader tasks 和文件描述符。
- worker 再派生 Shell 子进程时必须加入可控的后代树；无法保证时标记 capability degraded。
- `force_killed` evidence 至少包括 executor ID、backend、exit observation 和时间；不包含任务正文或凭据。

### 5.3 二次取消

二次取消不是另一次完整 300 秒等待：

- 第一次：协作 cancel + 短宽限。
- 第二次：若 backend 可杀，立即 force kill；若不可杀，保持 `termination_failed/lost`。
- 重复 `/stop`、重复 watchdog 和恢复扫描必须幂等，不重复发通知或 continuation。

## 6. Checkpoint 与服务重启恢复

### 6.1 存储边界

- Session history/metadata：现有原子保存路径。
- Goal orchestration：required task、owner、deadline、replacement、terminal、claim。
- runtime checkpoint：LLM/tool 上下文恢复，不作为 Child 存活真相。
- MessageBus/pending queue/SubagentManager maps：进程内缓存，重启后不可作为依据。

### 6.2 启动恢复算法

在 AgentLoop/runtime 初始化完成且 Session 可读后执行幂等扫描：

1. 读取 active Goal orchestration，不修改损坏 blob。
2. 校验 schema version、task ownership、replacement 无环、timestamp 可解析。
3. 对每个 `running` task 查询 executor supervisor。
4. executor 仍可证明存活：重新注册 watchdog，使用原 deadline。
5. executor 已自然结束且 durable result 可用：原子 finish，然后尝试 claim。
6. executor 不存在或身份无法证明：写 `lost/termination_failed`，required guard 保持关闭。
7. deadline 已过：直接走 cancel/kill 状态机，不能重新等待。
8. 对未 claim terminal result 原子 claim；claim 成功才排入一次 continuation/notification。

### 6.3 Result claim 与重复抑制

- durable finish 必须先于 MessageBus publish。
- claim key 使用 `subagent_task_id`；建议记录 `claimed_at` 和 `claim_owner_run_id`。
- required 和 required=false 最终统一 durable claim；兼容旧 background task 时可扫描 history，但不能成为新任务的主路径。
- MessageBus 重复、恢复扫描重复、late result、worker retry 都调用同一 claim API。
- continuation 另有确定性 idempotency key，例如 `subagent-result:{task_id}`，避免 claim 后进程崩溃造成重复 continuation。
- 若发生“claim 已写但消息未入队”，恢复器根据 claim delivery phase 补发；因此 claim 建议使用 `unclaimed -> claimed_pending_delivery -> delivered`，而不只是布尔值。

### 6.4 Durable state 损坏

- parse/validation 失败不自动清空、不猜测成功。
- Goal 进入可解释的 blocked/degraded 状态；保留原 blob 的安全 hash/错误码，不记录正文。
- 写 Audit recovery failure；提供人工恢复入口作为后续增强，不在本任务自动修 JSON。

## 7. Runner、AgentLoop 和工具行为

### 7.1 Runner

`AgentRunSpec.completion_guard` 必须覆盖：

- 正常 final；
- tool error/fatal 收口；
- Provider/LLM error；
- empty final/finalization retry；
- max iterations/no-tools finalization；
- Goal internal continuation；
- stream abort、stop 和 shutdown。

guard 在 durable barrier 未满足时：不保存候选 assistant final、不发送 final stream，只注入 bounded runtime instruction。guard 自身异常 fail-closed。

### 7.2 AgentLoop

- completion guard 按 `owner_run_id` 查询，不能检查整个 Session。
- 等待预算为 `min(本轮允许等待, durable remaining)`。
- deadline 到达后执行 cancel -> grace -> force kill/unknown；只有确认退出后写 `timed_out`。
- startup recovery、checkpoint restore 和 result continuation 共享 durable orchestration helper，不复制状态逻辑。
- runtime auto-barrier 只写 metadata/decision evidence，不生成虚假模型决策 Event。

### 7.3 `await_subagents`

- 仍是 Goal scoped、task IDs 或完整 group 二选一的一次性有界等待。
- `timeout_seconds` 不延长 durable deadline。
- 本次等待耗尽但 durable deadline 未到：`waiting=true`。
- durable deadline 到且退出已确认：`timed_out`。
- durable deadline 到但无法确认退出：`lost` + termination failure，`barrier_satisfied=false`。

## 8. Audit 事件与 Graph/API

### 8.1 事件

优先复用现有 typed event；确需新增时只增加状态型事件：

- cancellation requested；
- cancellation grace expired；
- force kill requested；
- execution exit observed；
- termination failed；
- orchestration recovery started/completed/failed；
- duplicate/late result suppressed。

如果避免扩充公开 EventType，可先把 bounded evidence 放入相关 Run/Checkpoint 的安全 metadata；但请求和终止证据必须可区分。不得写完整 Tool 参数、result、Payload、凭据或绝对路径。

### 8.2 `tool_recovery`

- 继续只消费 `ToolFinished.recovery_of_tool_call_ids`。
- source：同 Trace、同 Run、失败 terminal Tool semantic node。
- target：同 Trace、同 Run、成功 terminal Tool semantic node。
- anchor：两端实际 `tool_finished.event_id`。
- 多对多逐条构边并去重；dangling/malformed/跨 Trace/跨 Run不构边。
- 不修改 `caused_by_event_id`。
- Graph builder version 和 ETag 同步提升。

### 8.3 API 一致性

- Graph response 增加可选 `evidence_count`、`schema_version` 或明确 degradation warning；保持旧客户端兼容。
- Graph 和 Events 响应都返回 index revision；前端只有同 revision 时把 anchor 视为稳定。
- MVP 继续使用有界 cursor 扫描定位 Event。增强方案可新增 `GET /api/audit/traces/{trace_id}/events/{event_id}`，但必须认证、校验 trace ownership、只返回脱敏 metadata，不自动返回 Payload。
- Event 缺失、延迟、cursor stale、旧 schema 和 dangling anchor 返回明确错误/降级，不让 Graph 构建或 React 崩溃。

## 9. WebUI 修改

### 9.1 关系检查器

从 `TraceWorkbench` 中抽出或形成可复用的 edge inspector，显示：

- 关系类型；
- 失败端 node label/status 和 Event ID；
- 恢复端 node label/status 和 Event ID；
- 显式 recovery evidence count；
- 两个独立的 Timeline 定位操作；
- 缺失/dangling/revision mismatch 状态。

不得把边伪装成 node，也不根据前端字符串重算关系。

### 9.2 Timeline 定位

- 复用 `useAuditTimeline.ensureEvent()`。
- 保持最多 5 页、1000 Event、10 秒上限和 event_id 去重。
- cursor stale 后提示并允许从第一页刷新；不能静默继续旧 cursor。
- 定位成功后打开 Timeline、选择 Event、滚动到可见位置。
- 失败端定位失败不应阻止恢复端定位，反之亦然。

### 9.3 Payload

- 初始加载 Graph、选择 focus、点击 edge、定位 Event 都不得请求 Payload API。
- 只有用户点击明确的 Payload 按钮后加载。
- 保持 2 秒后端查询 timeout、1 MiB 单行上限、20 万字符渲染上限、脱敏和 `no-store`。

## 10. 真实环境 Chromium 验收

### 10.1 数据准备

使用真实 Audit emitter/indexer 写入脱敏合成数据，而不是 React 内存 fixture。至少准备：

1. 同 Run 的 failed Tool -> explicit recovery -> succeeded Tool。
2. 同 basename 但 unrelated 的成功 Tool，不得构边。
3. dangling recovery ID。
4. 1001+ Event 的分页 Trace。
5. 可触发 cursor stale 的 index revision 更新。
6. 带 Payload ID 的两端 Event，内容已脱敏。

### 10.2 Dist 一致性

- 先运行 `bun run build`，确认产物写入 Gateway 实际服务目录。
- 计算 dist manifest/hash，并在测试日志记录。
- 启动的 Gateway 必须从该目录提供静态资源；禁止另一个已运行进程或旧容器提供旧 dist。
- Playwright 请求 `/api/audit/...` 必须经过真实 Gateway 认证和路由。

### 10.3 自动验收步骤

在 1440x900 和 390x844 两个 viewport：

1. 登录/注入测试 token，打开真实 Trace。
2. 选择“恢复链路”，断言 2 节点/1 边。
3. 点击恢复边，检查双端 label/status/Event ID/evidence count。
4. 定位失败端并验证 Timeline 选中/滚动。
5. 定位恢复端并验证 Timeline 选中/滚动。
6. 从两端 Node Inspector 打开原始 Event 导航。
7. 验证此前 Payload API 请求数为 0。
8. 显式打开 Payload，验证脱敏、截断和 no-store。
9. 验证 cursor stale、not found、5 页/1000 Event/10 秒 limit 提示。
10. 验证 dangling、旧 schema、collapse/filter 后一端失败时另一端仍可用。
11. 断言 console error 和 page error 为 0。

125%/150% 浏览器 zoom、trackpad 和原生 scrollbar 作为人工补充，不用 CSS zoom 冒充。

## 11. 实施阶段、影响文件与回退

### 阶段 0：契约和失败测试

影响：

- `nanobot/session/goal_orchestration.py`
- `nanobot/agent/subagent.py`
- `tests/agent/test_subagent_lifecycle.py`
- `tests/agent/tools/test_goal_orchestration.py`

先写 deterministic tests：取消请求不等于终止、deadline 不重置、claim 幂等。回退点是只增加测试和兼容字段，不启用新 backend。

### 阶段 1：诚实 fail-closed 与 deadline 恢复

影响：

- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/subagent.py`
- `nanobot/agent/tools/await_subagents.py`
- `nanobot/session/goal_orchestration.py`
- `nanobot/session/goal_state.py`
- `nanobot/session/turn_continuation.py`

断言所有 final 出口 fail-closed，重启不重置 deadline。回退时关闭自动恢复，但保留 `lost` 阻断完成。

### 阶段 2：killable executor

影响：

- 新增 `nanobot/agent/child_executor.py`
- 新增 `nanobot/agent/child_worker.py`
- `nanobot/agent/subagent.py`
- Provider/runtime factory 相关文件（只做 worker rehydration 必需修改）
- Shell/exec session helper（只抽取 kill/reap 公共能力时修改）

断言真实卡死进程被 kill/reap、线程型不可杀调用不被错误标记终止、平台 capability 降级。回退通过配置禁用 process backend，回到阶段 1 fail-closed。

### 阶段 3：Audit Graph/API

影响：

- `nanobot/audit/context.py`
- `nanobot/audit/hook.py`
- `nanobot/audit/schema.py`
- `nanobot/audit/graph.py`
- `nanobot/audit/graph_types.py`
- `nanobot/audit/read_service.py`
- `nanobot/webui/audit_api.py`

断言 schema/version/ETag、双端 anchor、旧数据和 dangling 降级。回退为旧客户端普通边，不改历史数据。

### 阶段 4：WebUI 与真实验收

影响：

- `webui/src/lib/audit-types.ts`
- `webui/src/lib/audit-api.ts`
- `webui/src/components/traces/TraceGraph.tsx`
- `webui/src/components/traces/TraceWorkbench.tsx`
- `webui/src/components/traces/TraceNodeInspector.tsx`
- `webui/src/components/traces/TraceTimeline.tsx`
- `webui/src/hooks/useAuditTimeline.ts`
- WebUI unit tests 和真实 Gateway Playwright 脚本

回退为隐藏增强 inspector，保留普通 Graph/Timeline；合成 fixture 继续用于快速回归。

## 12. 测试矩阵

### Python 单元与集成

- cooperative Child 收到 cancel 并退出：确认 `cooperatively_exited`。
- Child 吞掉第一次 CancelledError：二次流程进入 kill 或 `termination_failed`。
- 阻塞线程：不得写 `force_killed`。
- 卡死进程及孙进程：进程组 kill、reap、无 orphan。
- 强杀失败：required status `lost`，guard fail-closed。
- required=false：主 Run 不等待，结果只通知一次。
- `await_subagents` 本次 timeout 不延长 durable deadline。
- 七种 restart/claim 场景全部覆盖。
- corrupted durable state：block/degraded，不清空证据。
- 所有 Runner final/error/continuation 出口经过 guard。
- Audit request、exit、termination failure 语义不混淆。
- `tool_recovery` 只由 explicit IDs 构建；unrelated、跨 Run/Trace、dangling 均不误连。

### WebUI

- unknown edge/schema 降级。
- edge inspector 双端信息和 evidence count。
- 两端独立定位、scroll、not found、cursor stale、limit。
- Payload 请求默认 0，显式操作后为 1。
- Graph/Events revision mismatch 显示 degraded。
- 真实 Gateway/dist Chromium 桌面和移动通过。

## 13. 验证命令

按改动范围运行，至少包括：

```bash
pytest tests/agent/tools/test_goal_orchestration.py \
  tests/agent/tools/test_subagent_tools.py \
  tests/agent/test_runner_injections.py \
  tests/agent/test_loop_runner_integration.py \
  tests/agent/test_subagent_lifecycle.py \
  tests/agent/test_task_cancel.py \
  tests/agent/test_loop_save_turn.py \
  tests/agent/test_runner_audit.py \
  tests/audit/test_graph_builder.py \
  tests/audit/test_webui_api.py \
  tests/audit/test_end_to_end.py -v

ruff check nanobot/agent/loop.py nanobot/agent/runner.py \
  nanobot/agent/subagent.py nanobot/agent/tools/await_subagents.py \
  nanobot/session/goal_orchestration.py nanobot/session/goal_state.py \
  nanobot/session/turn_continuation.py nanobot/audit nanobot/webui/audit_api.py \
  tests/agent tests/audit

cd webui
bun run test -- src/tests/trace-graph.test.tsx src/tests/audit-trace-ux.test.tsx
bun run build
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

不要运行 `ruff format`。

## 14. 提交、PR 与回退纪律

- 在当前任务分支继续工作，不重写已推送历史，不合并 `main`。
- 每个阶段形成独立中文提交并立即推送。
- 推荐提交边界：状态契约测试、deadline 恢复、killable executor、Audit/API、WebUI、真实验收。
- PR 正文持续更新“改动内容”“验证结果”“风险与注意事项”。
- killable backend 必须有配置级回退；回退后语义是 fail-closed，不是“已终止”。
- 未经用户明确确认，不得合并 `main`。

## 15. 实施前必须确认

开始阶段 2 前必须确认：

1. process executor 首批支持平台：建议 Linux/容器先完整支持，Windows 在 Job Object 或等价证明完成前保持 fail-closed。
2. Provider runtime 如何在 worker 中重建，尤其动态 provider、model override 和临时凭据；不能直接 pickle Provider。
3. 是否接受新增 `termination_state` durable 子字段、Graph/API 可选 evidence 字段和 Audit lifecycle Event。
4. 是否新增按 trace+event_id 的安全 Event 查询；MVP 可先不新增。

这些决定不阻塞阶段 0/1 的诚实状态和 deadline 修复，但阻塞“不可协作 I/O 已可强制终止”的完成声明。

## 16. 完成定义

只有全部满足才可关闭任务：

- 不可协作 Child 在受支持平台由独立进程监管，强杀后有退出/reap 证据；不支持的平台 fail-closed。
- request、cancel、timeout、force kill、termination failure 的状态和 Audit 证据可区分。
- required deadline 跨 continuation/checkpoint/restart 不重置。
- durable terminal/result/claim/continuation 全部幂等，MessageBus 重复或丢失不造成重复结果。
- `required=false` 行为保持兼容。
- `tool_recovery` 不污染 `caused_by_event_id`，不使用任何启发式推断。
- 真实 Gateway、真实 dist、真实 Audit API 的 Chromium 双端定位全部通过，Payload 未自动加载。
- 所有聚焦 pytest、ruff、WebUI test/build 和真实 Chromium 命令通过。

在阶段 2 和真实环境验收完成前，不得宣称“不可协作 I/O 已强制终止”或“生产 WebUI 双端定位已完成”。
