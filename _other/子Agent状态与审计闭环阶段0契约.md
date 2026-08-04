# 子 Agent 状态与审计闭环：阶段 0 契约冻结

日期：2026-08-03

基线：`origin/main@4b94f587`，任务分支 `codex/subagent-state-plan-v2`

## 当前能力矩阵

| 能力 | 已实现 | 部分实现 | 缺失 | 证据 |
|---|---:|---:|---:|---|
| Process child/IPC | 是 |  |  | `ProcessChildExecutor` 使用独立进程组、V1 IPC、executor/process identity 校验；`child_worker.py` 重建 runtime、tools、workspace 与 Audit。 |
| required durable state | 是 |  |  | `GoalOrchestrationStore` schema V2 持久化 required task、deadline、executor、replacement、claim 与 delivery。 |
| background durable state |  |  | 是 | `SubagentManager._task_statuses`、`_terminal_statuses` 和 `_session_tasks` 均为进程内状态，普通任务重启后没有任务记录。 |
| termination evidence | 是 |  |  | `ChildExit.termination_confirmed`、进程身份校验、cooperative/force kill、`termination_failed -> lost` 及 late result 拒绝已有测试。 |
| result claim/delivery |  | 是 |  | required 路径已有 durable claim/delivery；background 仍主要依赖 MessageBus 注入，没有统一 exactly-once task record。 |
| task lifecycle Audit |  | 是 |  | 已有 parent/child Run、spawn metadata、input injection 与 delivery 证据；没有版本化 task lifecycle event/outbox。 |
| task projection/API |  |  | 是 | Audit API 只提供 trace/graph/event/payload；没有脱敏 task list/detail/snapshot/timeline DTO。 |
| WebSocket task state |  |  | 是 | 现有协议覆盖 turn、goal、message、tool progress；没有 `subagent_snapshot` 和带 revision 的 task 增量。 |
| TaskSpec/TaskResult |  | 是 |  | `spawn` 与 worker 仍以字符串 task、文本结果为主；已有结构化 spawn admission 返回值，但不是版本化输入输出契约。 |
| count/token/cost/depth budget |  | 是 |  | 已有 `max_concurrent_subagents`；缺少 owner/session/Goal 总量、depth、token、cost 和原子预算预留。 |

## 冻结决策

1. `SubagentTask` 使用 schema V2，revision 从 1 开始；所有持久化时间必须是带时区 UTC。
2. 业务状态、执行 phase、termination state、delivery phase 使用四个独立枚举；终态不可覆盖。
3. TaskStore 使用 workspace 下独立 `subagent_tasks/` 目录和按 task 原子 JSON 文件，写入采用
   `fsync + os.replace + directory fsync`。它不写入 Session history，也不保存完整 prompt、provider 或
   runtime object。
4. 每次有效变更与 lifecycle outbox 在同一个原子文件内提交；幂等键固定为
   `task_id:revision:event_type`。Audit 发布与补发留到阶段 2 接线。
5. 活动 Goal orchestration V2 暂不迁移、不双写。阶段 1 接入时使用 TaskStore 作为任务真相，Goal
   store 继续拥有 required obligation、group、replacement 和 barrier，并保留活动 Goal 兼容读取。
6. result ready、claim、delivery 分步提交；重复 claim/delivery 不增加 revision，失败交付未来可回到
   pending delivery，但不得重复产生消费效果。
7. 对缺少新字段的旧任务记录只做显式 `legacy_inferred` 降级，不伪造 executor、evidence、usage 或
   delivery 成功事实。
8. 公开接口只能使用版本化 `SubagentTaskDTO`，不得序列化 `SubagentStatus` 或完整任务存储记录；
   usage/budget 仅暴露固定数值字段，不透传内部 reservation、provider 或 executor 内容。
9. `TaskSpec`、`TaskResult`、预算 reservation、REST/WebSocket 和 Audit event materialization 分别在
   后续阶段 additive 接入；本阶段不改变 spawn、Goal、Graph 或 WebUI 运行行为。

## 状态转移表

```text
created -> queued | failed | cancelled
queued  -> running | failed | cancelled | timed_out | lost
running -> succeeded | failed | cancelled | timed_out | lost
terminal -> terminal 仅同状态幂等；其他 late result 保留原终态和 revision
```

交付状态：

```text
not_ready -> ready -> claimed_pending_delivery -> delivered
                                      |-> delivery_failed -> claimed_pending_delivery
```

本阶段骨架先实现 ready、claim 与 delivered 的原子边界；delivery failed/retry 在阶段 2 与真实
MessageBus 注入语义一起接入。

## 与当前实现的取舍

- 方案对“现有 required record”描述准确，但它不是通用任务模型，因此本阶段没有继续扩展 Goal
  metadata。
- 当前 Graph 的 `spawn_branch` 和 `result_return` 是真实 Run/Tool/Injection 证据，但仍从 Run 解释
  task；保留现有语义，后续增加 Task node 时不得把 Child Run 改名冒充 Task。
- 当前并发限制在工具与 manager 两侧检查，但不是跨 owner/session 的预算 reservation；能力矩阵标记
  为部分实现，不另造第三套 limiter。
