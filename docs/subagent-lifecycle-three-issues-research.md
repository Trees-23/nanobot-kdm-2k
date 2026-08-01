# 子 Agent 生命周期、Deadline 恢复与 Audit WebUI 双端定位调研方案

日期：2026-08-02  
范围：只读调研；未修改产品代码、测试代码或配置。  
基线：`origin/main`；当前分析分支：`codex/subagent-tool-recovery`。

## 执行摘要

本报告针对三个尚未完全覆盖的问题，区分四种证据等级：

- **源码已证实**：直接由当前实现和字段/调用路径得到。
- **已有测试证明**：现有聚焦测试或浏览器测试已经断言，但不等于生产环境证明。
- **评测已复现**：本次调研中通过真实运行环境或可重复命令观察到。
- **尚未验证**：代码没有提供足够证据，必须在实施阶段补测。

本次按“只调研”约束没有启动真实 Gateway、没有执行破坏性故障注入，也没有新增运行时评测。因此下文没有把任何新结论标为“评测已复现”；此前已运行的 pytest、WebUI 单测、构建和合成 Chromium fixture 只归入“已有测试证明”。

当前真实完成度：

| 问题 | 完成度 | 结论 |
| --- | --- | --- |
| 不可协作 I/O 强制终止 | **部分完成，约 45%** | Shell 直接子进程有 kill/reap；Child/Provider/线程池没有统一可杀执行器。`task.cancel()` 不能推出 Child 已终止。 |
| Checkpoint/重启 deadline 恢复 | **部分完成，约 35%** | Checkpoint 和 Goal 编排状态可持久化，required 任务有 `deadline_at` 字段；等待实现仍用固定 `wait_for(..., 300)`，没有跨重启的剩余预算恢复状态机。 |
| 真实 WebUI 双端定位 | **合成链路完成，生产链路未证明，约 55%** | Graph/API/前端 anchor、定位、分页上限和 Payload 显式加载已经存在并有 fixture 测试；没有真实 Gateway dist/API/真实审计索引的 Chromium 验收。 |

最高风险是第一个问题：系统可能在取消后返回有界结果，同时真实 Child 仍在阻塞 I/O 或线程中运行。它会污染 required barrier、结果 claim、Audit terminal 状态和后续重启恢复。第二高风险是把相对的“本次等待 300 秒”误当作持久化 deadline，导致重启后重新获得完整等待预算。

## 当前实现证据

### 运行时和取消

- `SubagentManager.spawn()` 在 [nanobot/agent/subagent.py](../nanobot/agent/subagent.py) 中使用 `asyncio.create_task(self._run_subagent(...))`，内存索引为 `_running_tasks`、`_task_statuses`、`_session_tasks`。源码已证实没有 Child 专用进程、线程池或可杀执行器。
- `cancel_by_session()` 调用 `task.cancel()` 后 `await asyncio.gather(..., return_exceptions=True)`；这是协作取消请求，不是强制终止证明。
- `timeout_tasks()` 先 `task.cancel()`，再用 `asyncio.wait_for(gather, grace_seconds)` 返回布尔值。返回 `False` 只表示宽限期内未观察到 Task 退出，当前没有二级 kill 路径。
- `_run_subagent()` 在 `CancelledError` 中记录 `cancelled` 或 `timed_out`，随后重新抛出；`finally` 会持久化 required 终态。这里的持久化发生在 Task 的取消处理路径，不证明第三方同步调用已停止。
- Child 结果通过 `MessageBus.publish_inbound()` 发送 `InboundMessage(metadata.subagent_task_id)`；bus 是两个内存 `asyncio.Queue`，进程重启会丢失队列中的消息。
- AgentLoop 的 `/stop` 路径取消主 Run Task 和该 session 的 subagent，随后等待 Task；`CANCEL_REQUESTED` Audit 事件记录的是请求，不是已终止事实。

### AgentRunner、AgentLoop 和 completion guard

- `AgentRunner` 对 Provider 请求使用 `asyncio.wait_for(coro, timeout=outer_timeout_s)`；Provider 也可能在 `asyncio.to_thread()` 中执行 SDK 调用。取消协程不能停止已经进入线程的同步函数。
- `AgentRunSpec.completion_guard` 在正常 final、tool error、Provider error、empty final 和 max-iteration 收口路径检查 required owner Run；拒绝时注入运行时指令并不保存候选 final。这是完成门禁，不是 Child kill 机制。
- 当前 guard 的 required 等待使用固定 300 秒边界（已有实现提交与测试证明），尚未从持久化 `deadline_at` 计算跨 continuation/checkpoint/restart 的剩余时间。
- `AgentLoop` 的 pending queue/`_drain_pending()` 等待的是运行中子任务结果或消息，不是 durable 任务组的完整恢复协议。

### Child 使用的 I/O 类别

- **可协作取消**：纯 asyncio Provider、`asyncio.sleep`、等待 `asyncio.Queue`、`asyncio.subprocess.Process.communicate()` 的协程等待、已正确处理 `CancelledError` 的工具。
- **取消不可靠或不可协作**：阻塞式文件/压缩/数据库 I/O；第三方同步 SDK；`asyncio.to_thread()` 中的函数；不响应取消的异步库；卡死子进程的祖先进程或脱离进程组的孙进程。
- Shell `ExecTool` 自己创建的命令使用 `asyncio.create_subprocess_*`，超时/取消调用 `_kill_process()`；交互 session 可选择 `_kill_process_tree()`，Unix 通过 `SIGKILL` 进程组并 reap，Windows 通过 `taskkill /T /F`。该能力只覆盖 Shell 创建的进程，不覆盖整个 Child。

### Durable state、Checkpoint 和 deadline

- Goal 状态存于 Session 的 metadata（`goal_state`，旧键 `thread_goal` 兼容）。`GoalOrchestrationStore` 通过 SessionManager 的保存路径读改写，并保留 required task、owner Run、状态、`deadline_at`、replacement 和 `result_claims`。
- Session 历史由 `history.jsonl` 原子写入（临时文件、fsync、rename、目录 fsync）；这保证文件完整性，不保证并发 read-modify-save 不丢更新。
- AgentLoop 的 `runtime_checkpoint` 位于 Session metadata，包含 assistant message、已完成 tool result、pending tool calls 和 Audit checkpoint 标识。恢复在下一次处理消息时执行，并能去重重叠尾部；它不是服务启动时自动重建全部 Child 执行器的机制。
- 当前 required 等待入口 `await_subagents` 将 phase 设为 `waiting_for_children`，调用 `SubagentManager.wait_for(..., timeout_seconds)`，默认/上限 300 秒；超时后仍是等待或失败语义，不会给 Child 创建独立的跨进程 watchdog。
- `deadline_at` 是绝对 wall-clock 字段（源码已证实的持久化字段），但等待函数当前没有用它计算剩余预算；单调时间只适用于本进程内的 elapsed，不可直接跨重启持久化。

### Audit Graph、Events API 和前端

- `ToolFinished` schema 明确允许 `recovery_of_tool_call_ids`；没有把 `tool_recovery` 写入 `caused_by_event_id`。
- Graph builder version 为 4。`tool_recovery` 只由显式 recovery ID 构边，并要求同 Trace、同 Run、失败 terminal 到成功 terminal；anchor 指向两端 `tool_finished` Event。源码已证实不按 basename、同名 Tool、时间邻近或前端字符串推断。
- Graph node 有 `raw_event_ids`、`raw_events`，edge 有 `source`、`target` 和 `anchor.source_event_id/target_event_id`。Graph API 支持 `level=trace_full`，并返回 ETag 与 index revision。
- Events API `GET /api/audit/traces/{trace_id}/events` 返回 event_id、event_type、occurred_at、trace/run/turn、tool_call_id、caused_by_event_id、status、payload_id、semantic_node_id 等；它不提供独立的按 event_id 深链 API。
- Payload API 是独立、认证、显式调用的 `/api/audit/payloads/{payload_id}`，支持 metadata-only、文件 containment、大小上限和脱敏；前端不会因选中恢复边自动加载 Payload。
- `TraceWorkbench` 的 `locateEvent()` 通过 Events cursor 最多 5 页、1000 Event、10 秒，去重并处理 `cursor_stale`；`TraceTimeline` 选中并滚动已加载 Event。关系检查器消费 edge anchor，分别提供失败端和恢复端按钮。
- 现有 `webui/e2e/audit-tool-recovery.spec.ts` 加载的是 `AuditToolRecoveryFixture` 合成页面；**已有测试证明** UI 行为，但不能作为真实 Gateway/API/dist 通过证据。

## 问题一：不可协作 I/O

### 现状判定

当前系统只有三层取消语义：请求取消、asyncio Task 取消、Shell 子进程 kill。它们必须分开命名：

| 观察结果 | 允许的状态含义 |
| --- | --- |
| 收到 `/stop` 或 deadline | `cancel_requested` / `timeout_requested`，并写 Audit 请求事件 |
| Task 在协作点退出且已 gather | `cancelled` 或 `timed_out` |
| killable executor 返回退出码并完成资源回收 | `terminated`（建议作为内部证据，不直接替代业务 terminal） |
| 宽限后仍无法确认退出 | `termination_unknown` / `lost`，不能声称 Child 已终止 |

当前没有独立 Child executor；因此对于不可协作 I/O，源码只能证明“取消请求已发出”或“asyncio Task 已退出”，不能证明底层调用停止。`required=true` 应 fail-closed：在未确认退出时不得满足 barrier、不得让 completion guard 通过；`required=false` 保持 background_notify，结果晚到时允许兼容性通知，但必须受一次性 claim 和重复抑制约束。`await_subagents` 超时仍返回 waiting/未满足，不应将取消请求伪装成成功。

### 最小可行修复方案（MVP）

1. 建立显式 Child execution capability：默认把可能阻塞的工具标为 `cooperative` 或 `non_cooperative`。在无法证明可杀时，required Child 采用 fail-closed，不把 Task 取消当作终止。
2. `SubagentManager.timeout_tasks()` 实现两阶段状态机：`cancel_requested -> grace_waiting -> cancelled/timed_out`；宽限期到达仍未退出时写 `termination_unknown`/`lost`，保留 owner/deadline，不写 `cancelled`。
3. completion guard 只读取 durable terminal/termination evidence；`termination_unknown` 和 `lost` 永远不满足 required obligation。
4. 在 Task、Child、Run 和 Audit 元数据中记录 `cancel_requested_at`、`grace_deadline_at`、`termination_evidence`，并保证 late result 不能覆盖终态。

影响文件和关键点：

| 文件 | 关键函数/类 | 状态变化 | 测试断言 | 回退 |
| --- | --- | --- | --- | --- |
| `nanobot/agent/subagent.py` | `SubagentManager.timeout_tasks`, `cancel_by_session`, `_run_subagent` | 增加请求/宽限/确认/未知退出边界；保留 terminal cache | cancel 后必须 gather；宽限失败不得返回 Child 已终止；required barrier 仍 false | 保留原 asyncio cancel 作为 cooperative backend，禁用不可协作声明 |
| `nanobot/agent/runner.py` | `AgentRunSpec` guard 入口及所有 final 出口 | guard 拒绝 `lost/termination_unknown` | normal/tool-error/empty/max-iteration/finalization 都拒绝 | 关闭 killable backend，仅 fail-closed |
| `nanobot/agent/loop.py` | `/stop`、pending queue、runtime event | 请求事件与终止事件分离 | `/stop` 只产生 request，确认后才产生 terminal | 回退到仅请求语义，不承诺终止 |
| `nanobot/audit/schema.py`, `hook.py` | typed lifecycle/request events | 增加安全状态字段，不写 Payload/秘密 | 字段兼容旧 schema，旧客户端降级普通边 | 仅 source_metadata 记录，保留旧事件 |
| `tests/agent/test_subagent_lifecycle.py`, `test_task_cancel.py` | 新增阻塞假工具 | 覆盖 cooperative/non-cooperative | 断言“请求取消 != 已终止” | 保留现有 asyncio 测试 |

### 增强方案：killable executor

推荐把真正不可协作的 Tool execution 放进可杀的独立进程（每个 Child 一个受控 worker 或小型 worker pool），而不是线程池。设计要点：

- 父进程持久化 `executor_id`、PID/进程组标识、启动时间、owner Run、task_id、workspace capability 和 deadline；不落盘命令参数、凭据和完整 Payload。
- 首次取消向 worker 发送协作 cancel；宽限期后只对受控进程组执行 terminate/kill，并等待 reap。Unix 使用独立 process group/cgroup；Windows 使用 Job Object 或等价进程树控制。线程池只能减少事件循环阻塞，不能提供强杀保证。
- kill 后必须关闭 stdin/stdout/stderr、回收 pipe、reap 子进程、释放文件描述符，并记录无法回收的资源。子进程再派生的孙进程必须纳入组；否则进入 `termination_unknown`。
- 若 worker 丢失但 PID 可能复用，必须使用 process instance token/启动时间或 pidfd 类证据，不能只看 PID。
- 运行结果采用 `task_id` 原子 claim；worker late result 只能进入 discarded/late audit，不得重写 required terminal。

强制终止失败时：

1. 写 `termination_failed` 审计事件（不含命令、Payload、凭据）。
2. required Child 标记 `lost`，owner Run completion guard fail-closed，必要时 Goal block。
3. required=false 保持 `background_notify`，但 UI 和日志显示“无法确认退出”，不显示 succeeded。
4. 进程级 supervisor 在服务重启时扫描仍存活的受控 worker，无法确认归属时 kill 或隔离，并写 orphan 事件；不能静默复用。

备选方案是仅使用现有 Shell 的进程组 kill：实施较小，但只覆盖 Shell，无法覆盖同步 SDK/线程阻塞，不能作为完整承诺。线程池方案只适合把少量同步 I/O 移出 event loop，不满足强制终止目标。

### 风险

- 在 fork/spawn worker 中复制 Provider、凭据或大运行时对象可能泄露秘密或造成不可序列化失败。
- kill 可能发生在文件原子 rename、数据库事务或外部 API side effect 中，系统无法回滚外部副作用；必须把工具契约标为 idempotent/不可逆并在 Audit 中保留不确定性。
- Windows、容器无 cgroup、无 PID namespace 时，进程树控制能力不同；MVP 必须按平台 capability fail-closed。

## 问题二：Checkpoint/重启 deadline

### 当前缺口

当前 Goal/Child/Run/Checkpoint 均能落到 Session metadata 或 history，但执行器、MessageBus、pending queue 都是进程内状态。重启后：

- `running` durable task 若没有对应 `_running_tasks`，只能被恢复逻辑标为 `lost`；不能重新声称 running。
- Child 已完成而结果尚未 claim 时，结果不应依赖 MessageBus；必须从 durable task terminal/result reference 重建通知。
- 重复消息、晚到消息、恢复动作执行两次必须通过 `subagent_task_id` 原子 claim 抑制；已有 `claim_result()` 主要覆盖 required orchestration，`required=false` 仍依赖兼容层历史扫描，属于尚未验证风险。
- owner 必须是创建该 Child 的 `owner_run_id`，不能把同 Session 的后台任务混合到恢复 Run。Continuation 是新的处理 Run，不能借 `parent_run_id` 冒充 Child。

### 最小可行修复方案

1. 所有 required obligation 以绝对 UTC `deadline_at` 持久化，另存 `created_at`、`last_transition_at` 和 `grace_deadline_at`。恢复时计算 `remaining = deadline_at - now_utc`；小于等于零直接进入 cancel-and-gather/expired 流程，不能重新给 300 秒。
2. 使用 wall-clock 绝对 deadline 作为跨重启契约；本进程等待用 `time.monotonic()` 计算短期 sleep，并在每次 checkpoint/continuation 前重新从 UTC deadline 计算剩余值。不要持久化 monotonic 值作为跨重启真相。
3. 服务启动恢复扫描：加载 Goal orchestration；`running` 且没有活跃 executor 的 Child 迁移为 `lost`，`deadline_at` 已过的 Child 迁移为 `timed_out`（前提是已有终止确认；否则 `termination_unknown`/`lost`）。
4. 恢复动作幂等：在 Session lock 内执行状态迁移；terminal 只能从 `running` 进入一次，迟到结果只生成 suppressed/late audit。
5. Checkpoint restore 只恢复 assistant/tool context；required barrier 由 durable orchestration 重新计算，不因恢复 checkpoint 获得新 timeout。

影响文件和关键点：

| 文件 | 关键函数/类 | 状态变化 | 测试断言 | 回退 |
| --- | --- | --- | --- | --- |
| `nanobot/session/goal_orchestration.py` | `register`, `finish`, `select`, `claim_result` | 绝对 deadline、单调终态迁移、owner 过滤 | 同一 task 两次 finish/claim 仅一次有效 | 仅保留旧字段，恢复时全部 fail-closed |
| `nanobot/session/goal_state.py` | schema parse/runtime summary | 版本化 orchestration phase | 损坏 blob 返回 active-but-blocked/明确错误，不清空证据 | 旧状态只读兼容 |
| `nanobot/agent/loop.py` | startup recovery、continuation、checkpoint | owner Run 与 continuation 分离；恢复不重置 deadline | 重启后剩余预算严格减少 | feature flag 关闭自动恢复，人工 block |
| `nanobot/agent/subagent.py` | result persistence/announce | durable terminal 先于 MessageBus announce | bus 丢失时可恢复；重复消息不重复 history/outbound | required 结果只保留 durable，禁止自动补回 |
| `nanobot/bus/queue.py` | 兼容层 | 明确 bus 非 durable | 重启模拟后不声称消息仍在队列 | 继续内存 bus，但启动扫描 durable state |

### 必测重启矩阵

| 场景 | 预期 |
| --- | --- |
| 等待期间正常重启 | 恢复读取原 `deadline_at`，只等待剩余时间；owner 不变 |
| 重启前 Child 已完成 | durable terminal 被发现；未 claim 的结果只 claim 一次并触发一次 continuation |
| 重启后 Child 才完成 | Child 使用原 owner/deadline；完成事件可被恢复 Run claim |
| 重启后 deadline 已过 | 不重新等待 300 秒；执行 cancel-and-gather，无法确认则 `lost`/block |
| durable state 损坏 | 不猜测成功、不清空 history；进入 degraded/block，并写恢复失败 Audit |
| MessageBus 重复结果 | task_id 原子 claim 只有首条进入 pending/history/outbound |
| 恢复动作执行两次 | 状态迁移和 continuation key 幂等，第二次为 no-op |

### 推荐时钟模型

使用 **绝对 UTC deadline 作为 durable 真相 + monotonic 作为单次进程等待计时器**。绝对时间可跨重启、容器和机器恢复；monotonic 避免本进程 NTP 回拨影响 sleep。应记录 `clock_observed_at` 和 skew/degraded 标记，若系统时钟异常则 fail-closed，不延长 deadline。

## 问题三：真实 WebUI 双端定位

### 当前能力与未证实项

源码已证实 Graph/API 契约能表达失败端和恢复端：edge 的 source/target 是 semantic node，anchor 是两个 `tool_finished` Event ID；Events API 能分页返回这些 ID；前端 `TraceWorkbench.locateEvent()` 能通过 cursor 定位并选中时间线 Event。

已有 WebUI 单测证明：`tool_recovery` 独立于 `causal` focus、显示 0 命中、显示节点/边计数、点击 edge 打开关系检查器。已有 Chromium 测试证明：1440x900 和 390x844 合成页面可点击两端定位，控制台/页面错误为 0（已有测试证明，非真实生产评测）。

尚未验证：真实 gateway 进程加载的 WebUI dist 是否与宿主机 `webui` 构建一致；真实 Audit index 的 revision/ETag 与 Events cursor 是否稳定；真实旧 schema、索引 lag、dangling anchor、Graph/Events 不一致时前端是否保留可解释降级。

### 最小可行修复方案

1. 后端继续只接受显式 `recovery_of_tool_call_ids`，验证同 Trace、同 Run、失败 terminal 到成功 terminal，非法关系不构边但返回 integrity warning。
2. Graph edge 增加稳定的 `evidence_count` 或显式 recovery ID 摘要（只计数，不泄露参数），anchor 两端必须是存在于同一 trace 的 `tool_finished` Event。
3. Events API 保留 `event_id`、`semantic_node_id`、`payload_id` 和 index revision；增加按 trace+event_id 的安全查找语义，或让前端在 cursor stale 后重新从第一页定位，不能构造 URL 深链。
4. 前端关系检查器显示 source/target node、状态、两端 Event ID、证据计数；分别调用现有 `locateEvent()`；Payload 继续默认关闭。
5. Graph/Events 不一致时：节点不存在显示 dangling；Event 延迟显示待重试；cursor stale 重新加载并提示；超过 5 页/1000 Event/10 秒明确 limit；旧 schema 未知 edge 降级为普通边。

影响文件和关键点：

| 文件 | 关键函数/类 | 状态变化 | 测试断言 | 回退 |
| --- | --- | --- | --- | --- |
| `nanobot/audit/graph.py`, `graph_types.py` | `_add_tool_recovery_edges`, edge/node models | 显式证据、稳定 anchor、dangling 不崩溃 | unrelated basename 不产生边；跨 Run/Trace 不产生边 | 旧 Graph builder 返回普通边/隐藏未知边 |
| `nanobot/webui/audit_api.py`, `read_service.py` | `_graph`, `_events` | revision/coverage/lag 明示；事件字段一致 | Graph anchor 在 Events 页面可找到或产生明确降级 | 回退到只显示 node/raw_event_ids |
| `webui/src/lib/audit-types.ts`, `audit-api.ts` | response types/fetch | 未知 edge 类型兼容 | 旧 schema 不让 React 崩溃 | `unknown` edge 作为普通关系 |
| `TraceGraph.tsx`, `TraceWorkbench.tsx`, `TraceNodeInspector.tsx`, `TraceTimeline.tsx`, `useAuditTimeline.ts` | edge click、locate、payload | 双端选中、滚动、错误提示 | 两端定位、payload 不自动加载、上限提示 | 隐藏关系检查器但保留普通 node navigation |

### 真实 Chromium 验收方案

必须使用实际启动的 Gateway API 和实际打包 dist，不挂载 `AuditToolRecoveryFixture`。准备脱敏、合成但由真实 Audit emitter/indexer 写入的 trace，包含失败 Tool、显式 recovery ID、成功 Tool、至少 1001 Event 的分页变体。

验收顺序：

1. 启动 Gateway，确认其静态资源路径和版本标识与本次 `bun run build` 产物相同；记录 dist hash，禁止宿主机旧 dist 冒充。
2. 打开真实 Trace，检查 `trace_full` Graph、index revision、coverage/lag。
3. 选择“恢复链路”，断言 2 个节点/1 条边；unrelated basename fixture 必须 0 命中。
4. 点击恢复边，检查失败端/恢复端、状态、Event ID、显式 recovery 证据计数。
5. 分别点击两端 Event，验证 Timeline 选中并滚动；再从 Node Inspector 验证原始 Event 导航。
6. 确认 Payload 请求数在初始打开和 edge click 后仍为 0；用户显式点击后才请求，且得到脱敏/上限结果。
7. 注入 cursor revision 变化，断言 `cursor_stale` 提示和可重试；构造缺失 Event、dangling ID、Graph/Events 延迟，断言降级提示。
8. 用 5 页/1000 Event/10 秒上限测试 limit 提示；测试移动 390x844、桌面 1440x900，人工补测 125%/150% zoom、trackpad 和原生 scrollbar。
9. 记录 console/page error、网络请求、真实 dist hash、Graph/Events revision 和 Payload 请求日志，作为验收附件。

## 三个问题之间的依赖关系

```text
killable/termination evidence
        ↓
Child durable terminal + owner/deadline + result claim
        ↓
Checkpoint/restart recovery and completion guard
        ↓
Audit terminal events and stable tool_recovery anchors
        ↓
Graph/Events consistency and WebUI dual-end navigation
```

问题一是状态真相的前置条件；如果 Child 仍运行却被标记 cancelled，问题二会错误恢复，问题三会展示不存在的 terminal Event。问题二是 Graph/API 一致性的前置条件；重启后的重复/晚到结果必须先 claim，才能保证 recovery 关系和 Timeline 只展示一次。WebUI 不应承担任何关系推断或终止判定。

## 推荐总体架构

采用四层：

1. **Execution layer**：cooperative asyncio backend；不可协作工具进入 killable process executor；明确 capability 和资源边界。
2. **Durable orchestration layer**：Goal task record 以 `task_id` 为主键，保存 owner Run、absolute deadline、attempt/replacement、termination evidence、terminal result reference 和 claim。
3. **Audit/read layer**：只记录请求、状态迁移、终止证据、恢复 evidence；Graph 的 `tool_recovery` 只消费显式 recovery IDs；Events API 返回稳定 ID/revision。
4. **Presentation layer**：前端只消费 Graph/API 声明的关系；双端 locate 使用 event_id；Payload 显式、认证、有界加载。

## 后端修改建议

最小实施顺序：

1. 先定义状态枚举和 migration：`cancel_requested`、`grace_waiting`、`cancelled`、`timed_out`、`termination_unknown`、`lost`，并写 owner/deadline/claim 的幂等规则。
2. 实现 cooperative fail-closed 路径和启动恢复；此阶段不宣称不可协作已被强杀。
3. 增加 killable process backend 与平台 capability；将 Shell 现有 kill/reap 逻辑复用为底层组件，但不扩大其保证范围。
4. 将 AgentLoop/Runner 的 guard 改为读取 durable termination evidence 和剩余 deadline；所有 final 出口维持 fail-closed。
5. 将 MessageBus 视为通知通道而非 durable source；先 durable finish/claim，再 publish。

## Audit Graph/API 修改建议

先后顺序：

1. 固化 Graph schema、builder version、ETag/index revision 语义；补充 recovery evidence count 和降级码。
2. 在后端验证同 Trace/Run、terminal status、双端 Event anchor；非法关系不崩溃、不猜测。
3. 确保 Events API 在同 revision 下能返回两端 anchor；必要时提供安全的 trace+event 定位接口，但不提供未授权 Payload 深链。
4. 完成真实 index lag/cursor stale/dangling 的 API 测试后，才接 WebUI。

## WebUI 修改建议

1. 先把 API response 解析为显式 unknown-safe union，未知 `tool_recovery` 按普通边降级。
2. 再实现 edge inspector 的证据计数、两端状态和两个 Event ID；不要把边伪装成 node。
3. 复用 `locateEvent()` 的分页上限、去重、cursor stale 和 timeout；将 Graph/Events revision 不一致显示为 degraded。
4. 最后用真实 Gateway dist/API 的 Chromium 脚本替代合成 fixture 验收；fixture 继续作为快速回归，不得升级为生产证据。

## 测试矩阵

| 层 | 必测内容 | 证据等级目标 |
| --- | --- | --- |
| Execution | cooperative cancel、阻塞线程、卡死进程、二次取消、kill/reap、孤儿进程、fd/pipe 清理 | 评测已复现 + pytest |
| Orchestration | owner 过滤、deadline 剩余、重启、late/duplicate claim、replacement chain、损坏 state | pytest/集成 |
| Runner/Loop | 所有 final 出口 guard；required 未终止 fail-closed；required=false 不变同步 | pytest/集成 |
| Audit | request vs terminal 事件、recovery explicit ID、跨 Trace/Run 拒绝、dangling、旧 schema | pytest/API |
| WebUI unit | focus、edge inspector、双端 locate、Payload 默认关闭、limit/stale/error | `bun run test` |
| WebUI real | 真实 Gateway/dist/API、桌面/移动、真实 index lag、console/page error 0 | Chromium 评测 |

## 安全、隐私和故障降级

- Worker 不继承未声明凭据；Audit 只写稳定 ID、状态、类型、摘要和 evidence count，不写完整命令、参数、Payload、resource fingerprint 或绝对用户路径。
- process kill 不能撤销外部副作用；对不可逆 Tool 必须在工具契约中标明 idempotency/side-effect 风险。
- Workspace containment、SSRF 校验和现有 Shell sandbox 保持不变；killable executor 不能借机放宽路径或网络边界。
- durable state 损坏、索引 stale、worker 归属不明、Graph/Events 不一致均采用 fail-closed/degraded，不猜测成功，不静默清理证据。
- UI 错误消息只显示可操作的状态和 retry 建议，不泄露 Payload 或内部路径。

## 实施阶段与回退点

| 阶段 | 目标 | 回退点 |
| --- | --- | --- |
| 0 | 状态/字段/事件契约、迁移和测试夹具 | 只读旧字段，所有未知状态 block |
| 1 | cooperative cancel + durable deadline/claim + startup recovery | 关闭自动恢复，保留 fail-closed |
| 2 | killable process executor、进程树/资源回收 | 按 capability 禁用 backend，不能降级为“已终止” |
| 3 | Runner/Loop guard 与 Audit terminal 事件 | 保留旧 guard，但禁止 required 在 unknown/lost 时完成 |
| 4 | Graph/API revision、anchor、dangling 降级 | 旧客户端将未知边当普通边 |
| 5 | WebUI 真实数据源 Chromium 验收 | 保留合成 fixture，仅作为回归，不宣称生产通过 |

## 实施前待确认项

以下决定会阻塞对应阶段，不能由实现者自行猜测：

1. 是否把不可协作 Tool 统一放入独立进程，还是首期全部 fail-closed（阻塞阶段 1/2）。建议先 fail-closed，再按 capability 增量启用。
2. 支持的平台是否必须包括 Windows 原生 Job Object、Linux cgroup 和无特权容器（阻塞 killable executor 发布）。
3. `termination_unknown` 与 `lost` 的公开状态是否合并；建议内部区分、对用户显示“无法确认退出”（阻塞 API/schema migration）。
4. deadline 的权威时钟源和允许的 clock skew 阈值（阻塞恢复状态迁移）。
5. required=false 的 durable result 是否也统一 claim；建议统一 claim，兼容层只读历史扫描（阻塞 MessageBus 完整去重）。
6. 是否新增按 `trace_id + event_id` 的安全定位 API；若不新增，前端必须接受从 cursor 扫描的上限和延迟（阻塞真实 WebUI 验收）。
7. 真实 Gateway dist 的发布/校验方式（构建 hash、打包路径、容器镜像版本）（阻塞生产 Chromium 验收）。

## 完成定义

只有同时满足以下条件才可宣称三个问题完成：

- 对 cooperative Child，取消后有 gather 退出证据；对 non-cooperative Child，要么 killable executor 明确 kill/reap，要么 required fail-closed 且显示未知退出，绝不把取消请求写成已终止。
- 重启恢复依据绝对 `deadline_at` 计算剩余预算；重复/晚到/丢失结果由 durable claim 抑制；owner Run、Child、Continuation 不混淆；上述七种重启测试全部通过。
- 真实 Gateway 使用与代码一致的 WebUI dist；真实 Graph/Events API 在双端 anchor、cursor stale、lag、dangling、limit、Payload 默认关闭场景下通过 Chromium 桌面/移动验收。
- Audit 中 `tool_recovery` 仍只来自显式 `recovery_of_tool_call_ids`，不写 `caused_by_event_id`，不按 basename/同名 Tool/时间/前端字符串推断。

在这些条件达到前，当前只能宣称：Shell 子进程具备有限 kill/reap、required completion guard 和 durable claim 已部分实现、合成 WebUI fixture 已通过；不能宣称真正不可协作 I/O 已被强制终止、Checkpoint 重启 deadline 已正确恢复、或真实生产 WebUI 已完成双端定位。

## 优先级实施任务清单

1. 确认 killable executor、状态枚举、平台范围和时钟策略。
2. 固化 durable task schema、绝对 deadline、owner/replacement/claim 迁移与损坏状态策略。
3. 实现 cooperative cancel + fail-closed，并补“请求取消不等于终止”的回归测试。
4. 实现进程级 worker、进程树 kill/reap、孤儿扫描和资源清理；按平台 capability 发布。
5. 接入 AgentLoop/AgentRunner completion guard 和 startup recovery，覆盖所有 final/error/continuation 出口。
6. 把 durable finish/claim 置于 MessageBus announce 之前，完成重复/晚到/重启集成测试。
7. 固化 Audit Graph/API evidence、anchor、revision、dangling 和旧 schema 降级。
8. 完成 WebUI 双端 inspector/locate 的真实 API 测试。
9. 启动真实 Gateway + 实际 dist，执行 Chromium 桌面/移动验收并记录证据。
10. 更新 PR 风险说明；在用户明确确认前保持功能分支，等待用户确认后合并 `main`。
