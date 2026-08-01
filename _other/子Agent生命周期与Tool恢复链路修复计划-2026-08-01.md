# nanobot 子 Agent 生命周期与 Tool 恢复链路修复计划

日期：2026-08-01

状态：待实施。本文只记录调研结论、目标契约和实施顺序，不修改产品代码、配置、Audit 原始数据或测试资产。

## 一、目标与范围

本计划处理两个相互独立但会在运行轨迹中交汇的问题：

1. 必需 Child 的生命周期收口：提示 LLM 正确使用 `await_subagents`，并以运行时硬门阻止主 Run 在 `required=true` 的 Child 尚未终止时发布最终答复。
2. Tool 纠错恢复可视化：把已有 `recovery_of_tool_call_ids` 转为 Graph 中明确的 `tool_recovery` 边，并支持前端聚焦、双端 Event 导航和三路径防误连验收。

必须保持以下边界：

- `required` 继续表达 Goal 完成义务，不把普通 `required=false` 后台任务静默改成必需任务。
- `caused_by_event_id` 只表达有明确触发证据的直接因果，不承载 Tool 恢复关系。
- Tool 恢复只消费运行时已经记录的 `recovery_of_tool_call_ids`，不按 basename、同名 Tool、时间相邻或前端字符串猜测。
- Checkpoint/Goal 接续继续保留；它与晚到 Child 结果触发的 Continuation 不是同一机制。
- 本轮不新增 `tool_correction_selected` 决策 Event。只有未来能够记录真实 LLM 决策证据时，才考虑把 Tool 纠错接入严格因果链。

## 二、已核验现状

### 2.1 Child、Goal 与 Run

`spawn` 在 `nanobot/agent/tools/spawn.py` 中被定义为后台任务。`SubagentManager.spawn()` 使用 `asyncio.create_task()` 启动 Child 后立即返回 `task_id` 和 `child_run_id`。

`required=true` 的当前语义已经清晰且有持久化保护：

- 只能在 Active Goal 中创建；
- 在 `GoalOrchestrationStore` 中登记状态、任务组、Child Run 和 spawn Tool Call；
- `required_gate()` 只接受最终解析为 `succeeded` 的义务；
- `update_goal(complete)` 会拒绝仍在运行、失败、取消、超时或丢失的必需任务；
- 失败义务可通过 `replaces_task_id` 显式替代。

`await_subagents` 是 Goal-scoped 的显式等待屏障。它等待所选 `task_ids` 或 `task_group` 中的全部任务进入终态，单次最多 300 秒；超时只返回 `waiting=true`，当前不会取消仍在运行的 Child。

当前还有一层软等待：Runner 在 Tool 执行或准备接受最终文本时调用 injection callback；当 pending queue 为空且同 Session 有 Child 运行时，最多等待第一条回流 300 秒。它不是 join-all：一个 Child 返回后即可继续，下一次 LLM 可能直接生成最终答复，其余 Child 晚到后仍可能创建可见的 Continuation Run。

### 2.2 提示与 Tool 描述缺口

当前信息分散且不足以约束 LLM：

- `spawn` 只说明后台执行和结果回报，没有明确 `required=true` 后最终答复前必须收口；
- `await_subagents` 只说明等待一次及超时行为，没有明确它是必需任务的交付关卡；
- `nanobot/templates/agent/goal_runtime.md` 只要求 Goal 真正完成后再调用 `update_goal(complete)`，没有说明必需 Child 的等待、失败替代和超时处理；
- Goal continuation 提示没有列出未完成 required obligations，也没有给出明确的 `await_subagents` 动作。

因此提示词只能提高正确率，不能提供硬保证。

### 2.3 Tool 恢复证据与 Graph 缺口

`RunnerAuditHook.after_execute_tool_terminal()` 已在同一 Run 内维护待恢复失败列表。成功 Tool Event 会根据规范化 `resource_key` 和有界 `correction_keys` 计算 `recovery_of_tool_call_ids`。该字段由运行时写入，不是 LLM 输出。

当前 `AuditGraphBuilder` 已使用该字段计算失败节点的：

- `recovery_status="recovered"`；
- `recovered_by_event_id`；
- Run 级 `recovered_failure_count`。

但 `AuditEdgeType` 没有 `tool_recovery`，Graph 构建器没有从失败 Tool 节点连接到成功 Tool 节点。前端“恢复链路”目前只包含 `result_return` 和 `resumed_from`，所以 Tool 节点即使显示“已恢复”，点击恢复链路仍可能命中 0 条边。

Graph Edge 已具备 `anchor.source_event_id/target_event_id`，API 也会直接序列化 Graph；缺口主要是构边、前端类型、聚焦规则、边交互和双端 Event 导航。

## 三、目标语义

### 3.1 三种不同的约束

| 机制 | 负责什么 | 不负责什么 |
|---|---|---|
| `required=true` | Goal 是否允许完成 | 不自动等待 Tool 调用，不单独保证当前 Run 不回复 |
| `await_subagents` | 当前 Run 主动等待所选必需 Child | LLM 不调用时不会自动生效；超时后当前不会取消 Child |
| Run completion guard | 最终答复能否发布 | 不改变 Goal 的成功/失败判定，不替代 `await_subagents` 的中途同步用途 |

本轮最小兼容规则：

> `required=true` 的 Child 默认同时属于当前主 Run 的交付依赖。主 Run 发布最终答复前，它们必须全部进入终态；若超过明确 deadline，必须先取消并等待任务真正退出、持久化真实终态，再允许主 Run 继续诚实收口。

`required=false` 暂时保留既有后台语义，避免本轮破坏普通通知、资料收集等场景。长期建议将 Goal 义务和交付策略拆开：

```text
completion_policy = join_current_run
completion_policy = background_notify
completion_policy = background_silent
```

在该契约正式引入前，不让 `required` 同时扩展出更多隐含语义。

### 3.2 Tool 恢复与直接因果

```text
caused_by_event_id
= 当前 Event 由哪个明确 Event 直接触发

recovery_of_tool_call_ids
= 当前成功 Tool 确定性恢复了哪些失败 Tool

tool_recovery Graph edge
= 对 recovery_of_tool_call_ids 的可视化投影
```

目标 Graph：

```text
失败 read_file(runtime/config.json)
        |
        | tool_recovery
        v
成功 read_file(config.json)
```

中间的 `read_file(unrelated/config.json)` 即使 basename 和 Tool 名相同，也不得成为边的端点。

## 四、Child 生命周期后端设计

### 4.1 提示词与 Tool 描述

修改以下位置并保持规则一致：

- `nanobot/agent/tools/spawn.py::SpawnTool.description` 与 `required` schema 描述；
- `nanobot/agent/tools/await_subagents.py::AwaitSubagentsTool.description`；
- `nanobot/templates/agent/goal_runtime.md`；
- `AgentLoop` 生成的 Goal continuation message。

目标指令：

```text
创建 required=true 的 Child 后，在最终答复或 update_goal(complete) 前，
必须按完整 task_group 或 task_ids 调用 await_subagents。
超时或失败时不得声称完成；必须继续等待、显式替换失败任务、block Goal，
或按运行时 deadline 取消并报告真实终态。
```

动态 Goal 提示应只暴露有界状态，不注入 Child 原始任务正文。建议列出：

- 未完成 required task ID、label、group、status；
- 推荐的 `await_subagents(task_group=...)`；
- 失败任务允许的 `replaces_task_id`；
- 当前 deadline/剩余等待预算。

### 4.2 Runner 主完成硬门

不建议只在 `_state_respond` 拦截，因为到达该状态时 Runner 已完成、历史已保存，重新进入 Iteration 会变得脆弱。

建议给 `AgentRunSpec` 增加异步 `completion_guard`（或 `finalization_guard`）回调，由 `AgentLoop` 注入，Runner 不直接依赖 `SubagentManager` 或 Session 存储。

回调返回结构化结果：

```python
CompletionGuardResult(
    allow: bool,
    unresolved: list[RequiredChildStatus],
    injected_messages: list[dict],
    deadline_reached: bool,
)
```

Runner 在以下出口接受 `final_content` 前调用：

- 正常 LLM 最终文本；
- Tool error/fatal 后的收口；
- `max_iterations` 的 no-tools finalization；
- 空回复和长度恢复的最终 fallback；
- Goal internal continuation 准备切片前。

流式输出必须先解决一个不可回撤的问题：如果 required Child 尚未收口，不能先把模型最终文本的 token 发送给用户，再在 `StreamEnd` 处拒绝。首版建议对拥有未完成 required Child 的 Run 缓冲最终回答 token，只允许发送明确标记为 progress/resuming 的事件；completion guard 通过后才发布最终 stream segment。若产品不接受缓冲，则该类 Run 必须关闭最终回答 streaming，直到 guard 通过。

拒绝完成时：

1. 不保存当前候选最终文本为正式 Assistant 消息；
2. 将有界 guard 指令注入 messages；
3. 优先调用或等效执行 required task group 的 barrier；
4. Child 结果进入上下文后继续下一 Iteration；
5. 只有 required obligations 全部终止并经过 LLM 处理后，才再次尝试最终答复。

### 4.3 Deadline、取消与不可协作任务

硬门不能无限等待，也不能在超时后假装后台 required Child 已退出。计划采用单一、可观测的 Run join deadline，不能把现有多个 300 秒等待简单串联成无界总时长。deadline 必须使用单调时钟，并明确从“本 Run 首次创建 required Child”开始；同一主 Run 的后续 Iteration 继承剩余预算，Continuation/Checkpoint 是否继承必须在阶段 0 冻结，进程重启后不得用墙上时间猜测剩余预算。

deadline 到达时：

1. 获取当前 Run 所有未终止 required Child；
2. 调用 SubagentManager 的 cancel-and-gather；
3. 进入第二个短取消宽限期，等待每个 asyncio Task 真正退出；
4. 只有确认 Task 已退出，才能持久化 `cancelled`/`timed_out` 终态；不能把仍运行的 Task 伪装成终态；
5. 若 Child 不响应取消或卡在不可协作 I/O，当前 asyncio 任务模型无法同时保证“有界返回”和“任务必然终止”。阶段 0 必须在“引入可杀的独立 Child 进程/执行器”与“fail-closed 保持主 Run/Goal 阻塞并报告运行时故障”之间做选择；不得用 `lost` 掩盖仍存活的任务；
6. 只有所有 required Child 已确认终止，才允许注入终态摘要并让 LLM 选择替代任务、block Goal 或向用户报告未完成；
7. 禁止晚到结果再次触发用户可见 Continuation。

等待 Child 时不得持有 `GoalOrchestrationStore` 的 Session lock，避免 Child finally 写终态时死锁。状态读取、等待和持久化应分段执行。

### 4.4 所有权与去重

出口检查应按创建它的主 Run 归属 Child，而不是简单等待同 Session 的所有后台任务。建议为 `SubagentStatus` 和持久化 orchestration record 明确保存 `owner_run_id`；已有 `child_run_id`、`spawn_tool_call_id` 和 Audit parent run 不能替代运行时所有权查询接口。

Child 终态有两条信息路径：

- durable Goal orchestration status；
- MessageBus `subagent_result` 注入内容。

实现必须以 `subagent_task_id` 建立一次性 durable claim，而不只是依赖内存集合：

- Child 完成时先登记 `result_id/task_id/owner_run_id/claimed_at`（或等价的 Session orchestration record）；
- 当前 Run 注入前原子 claim，claim 成功才进入 pending queue；
- 已 claim 的结果重放、重复 MessageBus 消息和重启恢复消息只更新可观测计数，不再写入历史；
- claim 与“是否允许创建 Continuation”使用同一个 owner/session 锁边界；
- `await_subagents` 不能吞掉未 claim 的结果正文，completion guard 也不能绕过 claim。

主 Run 创建的 Child 归属 `owner_run_id=主 Run`；同一 Goal 的 Goal continuation 只继承未完成 obligation，不自动继承旧 Run 的 pending queue；replacement Child 使用新 `child_run_id`，并以 `resolved_by_task_id` 连接原 obligation，同时明确新的 owner Run。Continuation Run 只消费已 claim 的 Child 结果，不反向改变原 Child 的 owner。恢复 ID 仍限定在 Audit trace/Tool Call ID 命名空间内，不跨 Trace 猜测。

所有权转移按下表实现并审计：

| 场景 | required obligation 所有权 | 是否允许用户可见最终答复 |
|---|---|---|
| 普通主 Run 创建 Child | `owner_run_id=当前主 Run` | Child 未终止时不允许 |
| Goal internal continuation 切片 | 在创建新 Run 时原子转移 `active_owner_run_id`，保留原 owner 证据和 deadline | 旧切片不得发布最终答复；新切片继续收口 |
| Checkpoint 恢复 | 从 durable Goal 恢复 obligation，并按阶段 0 规则恢复/重建 deadline | 未完成 required obligation 时不允许 |
| Child-result Continuation | required Child 正常路径禁止产生；仅允许已确认的后台策略 | 由 `background_notify/silent` 策略决定 |
| replacement Child | 归属创建 replacement 的当前 Run，同时保留 `resolved_by_task_id` | replacement 未终止时不允许 |

所有权转移和 result claim 必须在同一 Session orchestration 写边界内完成，避免旧 Run 清理 pending queue 与新 Run claim 同时发生。

### 4.5 Prompt 要求与 Runtime auto-barrier 的关系

“必须调用 `await_subagents`”是产品期望的 Agent 行为和可审计操作，不等于运行时安全保证。若 LLM 忘记调用，completion guard 可以执行等效的 runtime auto-barrier，但必须：

- 注入有界结构化指令，说明哪些 required task 尚未收口；
- 标记本次等待来源为 runtime guard，而不是伪造一次 LLM Tool Call；
- 继续要求 LLM 在结果注入后处理成功、失败、替代或 block；
- 不因为 auto-barrier 已等待就允许未处理的最终文本直接发布。

### 4.6 Loop 防御性出口

在 Runner 主 guard 之外，`AgentLoop._state_respond` 前增加只读 invariant 检查作为最后防线：若当前 Run 仍拥有运行中的 required Child，不得发布任何代表任务已完成的普通 Outbound。`/stop`、cancel、block、shutdown、provider error、stream abort 和命令类 Outbound 必须走各自明确的 bypass/终态契约，不能统称为“完成型”。

该防线只用于发现实现绕过，不应在保存后临时重启同一个 Runner。推荐记录结构化 runtime/audit error，并调度受控内部接续或执行 deadline 取消流程。正常路径必须在 Runner guard 内解决。

## 五、Tool Recovery 后端设计

### 5.1 Graph 契约

在 `nanobot/audit/graph_types.py` 中增加：

```python
AuditEdgeType = Literal[..., "tool_recovery"]
```

边定义：

```text
type/relation = tool_recovery
source = 失败 Tool semantic node
target = 成功 Tool semantic node
anchor.source_event_id = 失败 tool_finished.event_id
anchor.target_event_id = 成功 tool_finished.event_id
```

边 ID 必须确定性生成并去重，例如：

```text
tool_recovery:{source_node_id}:{target_node_id}
```

新增契约后提升 `GRAPH_BUILDER_VERSION`，使 Graph ETag 与缓存自然失效。

### 5.2 构边规则

在 `nanobot/audit/graph.py` 中新增共用 helper，让 run-level `_edges` 和 trace-full `_trace_full_edges` 使用同一规则：

1. 遍历成功的 `tool_finished` Event；
2. 读取其 `recovery_of_tool_call_ids`；
3. 通过显式、全局唯一的 Audit Tool Call ID 查找同 Trace 内的失败 terminal Event 和 semantic owner；
4. 仅当 source 确为 abnormal Tool terminal、target 确为成功 Tool terminal且两端均可定位时建边；
5. 不重新运行路径匹配，不从摘要或 Payload 推断；
6. dangling、malformed 或指向成功调用的 recovery ID 不建可见边，可记录 `resolution="unresolved"` 供诊断；
7. 将 `tool_recovery` 加入 collapse endpoint 保护，避免恢复两端被折叠隐藏。若 source/target 在当前 graph level 被折叠，必须保留关系 anchor 和可展开入口；不能静默丢边。

当前 Hook 只会在同一个 Runner/Run 内生成该字段。因此首版 Graph 只接受同一 Trace 内可解析的显式 ID；跨 Run 仅在未来 Event 契约明确允许时展示，当前不跨 Run 自动补边。一个成功 Event 恢复多个失败 ID 时生成多条边；同一失败被多个成功 Event 引用时每条显式引用都保留，edge ID 不得覆盖证据。

### 5.3 API 与安全

现有 Graph API 会序列化 `AuditGraph`，不需要新增 endpoint。响应只增加边类型和 anchor，不携带 Tool arguments、result、`resource_key` 或 `correction_keys`。

验收必须继续保证：

- Graph、events 和日志不暴露绝对敏感路径、凭据或完整 Payload；
- anchor 只包含 Event ID；
- Payload 仍由独立鉴权接口显式加载；
- Graph API 的 schema version/Builder version 与 `tool_recovery` 同步升级；当前 WebUI 与后端必须同提交升级。旧客户端不承诺理解新 edge type，至少应把未知边降级为不可聚焦的普通边，而不是拒绝整份 Graph；如无法做到，则 API 必须返回明确的 schema version 不兼容错误。

## 六、Tool Recovery 前端设计

### 6.1 类型、样式与图例

修改：

- `webui/src/lib/audit-types.ts::TraceEdgeType`；
- `webui/src/components/traces/TraceGraph.tsx` 的 edge style、focus map、z-index 和图例；
- 必要时同步 layout worker 对结构边的判断，但 `tool_recovery` 不应改变 lane/父子布局。

“恢复链路”聚焦包含：

```text
result_return   Child 结果回流
resumed_from    Checkpoint/Run 执行恢复
tool_recovery   Tool 纠错恢复
```

图例不能继续把“结果回流/恢复”混成一个模糊标签，应拆分显示。`tool_recovery` 使用与 `result_return` 可区分的线型和语义色，并保持选中、暗色模式和色觉可辨识度。

### 6.2 聚焦行为

选中失败或成功 Tool 节点后点击“恢复链路”：

- 高亮失败节点、恢复节点及 `tool_recovery` 边；
- 显示命中节点数和边数；
- 没有确定性恢复边时显示“当前节点没有可确认的恢复关系”，不把 `continued` 当作恢复；
- 多个失败被一次成功恢复时显示所有显式边；
- `unrelated/config.json` 节点保持暗化且不计入命中数。

“因果链”仍只包含 `caused_by/retry/retry_of` 等严格因果或重试关系。`tool_recovery` 不进入因果链。

### 6.3 Edge 点击与 Event 导航

当前 ReactFlow 只有节点点击，没有边点击。建议新增 `onSelectEdge`，并采用轻量关系检查器，而不是把边伪装成节点：

```text
Tool 纠错恢复
失败端：Tool、状态、Event ID、定位失败 Event
恢复端：Tool、状态、Event ID、定位恢复 Event
证据：recovery_of_tool_call_ids
```

点击“定位失败 Event”或“定位恢复 Event”复用 `TraceWorkbench.locateEvent()`：

- 必要时自动加载后续 Event 页面，但继续遵守既有最多 5 页、1000 Event 或 10 秒上限；
- cursor stale、超过上限或 Event 不存在时显示明确提示；
- 定位成功后选中时间线行并滚动到可见位置；
- 不自动打开 Payload，Payload 仍由用户显式操作。

关系检查器是本轮强制需求，不采用只定位 target 的临时实现。它必须在多条恢复边、折叠节点、筛选后的 Graph 和定位失败时仍显示两端状态、命中计数和明确错误。selected edge 是否写入 URL 可作为后续增强；本轮只要 Event 深链与浏览器返回行为不被破坏，可以使用 Workbench 本地 state。

## 七、分阶段实施计划

### 阶段 0：冻结契约与夹具

确认以下决策并建立固定测试数据：

- 本轮硬门仅覆盖 `required=true`；
- Run join deadline 及 deadline 后 `cancelled/timed_out` 映射；
- 普通 `required=false` 晚到结果继续通知还是默认静默；
- `tool_recovery` 是恢复链而非因果链；
- 三路径 fixture 的合成规范路径、期望 resource link 和 Event ID。

fixture 路径必须是合成、脱敏路径（例如 `evals/audit-trace-recovery/...`），不得使用真实 home、令牌或用户目录；测试断言 Graph label、safe summary、日志和 Payload 均不泄露其真实宿主路径。

退出条件：测试 fixture 可以稳定产生失败路径、无关成功路径和纠错成功路径，且现有权威 Audit 资产不被改写。

### 阶段 1：提示与等待行为

修改 Tool 描述、Goal runtime 提示和动态 continuation 指令；补 `await_subagents` 多任务乱序、超时、失败、取消和替代链测试。

建议验证：

```bash
pytest tests/agent/tools/test_goal_orchestration.py tests/agent/tools/test_subagent_tools.py -v
ruff check nanobot/agent/tools/spawn.py nanobot/agent/tools/await_subagents.py nanobot/session/goal_orchestration.py tests/agent/tools/test_goal_orchestration.py tests/agent/tools/test_subagent_tools.py
```

### 阶段 2：required Child 的 Run completion guard

增加 owner run、completion guard、deadline cancel-and-gather、消息去重和 Loop 防御性 invariant。覆盖正常 final、Tool error、max iterations、Goal continuation、streaming 和 stop/shutdown。

核心集成样本：主 Run 创建一快一慢两个 required Child；快 Child 返回后不得发布最终 Outbound；慢 Child 终止且两个结果均被 LLM 处理后只发布一次，不产生 Child-result Continuation Run。

建议验证：

```bash
pytest tests/agent/test_runner_injections.py tests/agent/test_loop_runner_integration.py tests/agent/test_subagent_lifecycle.py tests/agent/tools/test_goal_orchestration.py -v
ruff check nanobot/agent/runner.py nanobot/agent/loop.py nanobot/agent/subagent.py nanobot/session/goal_orchestration.py tests/agent/test_runner_injections.py tests/agent/test_loop_runner_integration.py tests/agent/test_subagent_lifecycle.py tests/agent/tools/test_goal_orchestration.py
```

### 阶段 3：后端 `tool_recovery` 边

扩展 Graph schema、共用构边 helper、折叠保护和 builder version。现有失败摘要逻辑继续使用同一显式字段，不复制一套资源匹配算法。

建议验证：

```bash
pytest tests/audit/test_graph_builder.py tests/audit/test_webui_api.py tests/audit/test_end_to_end.py -v
ruff check nanobot/audit/graph.py nanobot/audit/graph_types.py tests/audit/test_graph_builder.py tests/audit/test_webui_api.py tests/audit/test_end_to_end.py
```

### 阶段 4：前端恢复链路与 Event 导航

扩展 TypeScript edge contract、恢复聚焦、图例、edge click、关系检查器和双端 Event 定位。不得用前端路径字符串重算恢复关系。

建议验证：

```bash
cd webui && bun run test -- src/tests/trace-graph.test.tsx src/tests/audit-trace-ux.test.tsx
cd webui && bun run build
```

使用真实 Chromium 验收桌面与移动视口，至少覆盖：恢复链命中数、无恢复空状态、两端 Event 导航、分页上限/cursor stale 提示、Payload 不自动加载、缩放后无控件重叠。

仓库当前没有正式 Playwright npm script。阶段 4 必须同时落地可重复入口（建议 `webui/e2e/audit-tool-recovery.spec.ts`、项目内 Playwright 配置和固定浏览器依赖），再使用类似以下命令执行，而不是只做临时人工点击：

```bash
cd webui && bunx playwright test e2e/audit-tool-recovery.spec.ts --project=chromium
```

自动断言至少包括 1440x900 与 390x844 两个 viewport、console/page error 为 0、一条 `tool_recovery`、恢复聚焦为 2 个节点/1 条边、双端 Event 均定位成功、unrelated 节点未命中，并保存结果 JSON 与安全截图。125%/150% 浏览器缩放和真实 trackpad/scrollbar 仍作为明确的人工补充项，不用 CSS zoom 冒充。

### 阶段 5：联合回归与评测资产修订

修订审计运行轨迹评测，使正常必需 Child 场景要求主 Run 收齐 Child 后才最终答复，不再把 `required=false + sleep + 主 Run 立即释放` 当作默认推荐行为。另保留一个显式 background policy 的兼容样本，用于验证允许的后台行为。

联合验收同一 Trace 中：

- required Child 全部在主 Run 收口前终止；
- 不产生意外的 Child-result Continuation 或第二条最终消息；
- Checkpoint/Goal continuation 仍正常；
- 三次 `read_file` 只生成一条正确 `tool_recovery` 边；
- “恢复链路”命中失败和纠错成功节点；
- “因果链”不伪造 Tool 恢复边；
- 两端原始 Event 均可定位。

## 八、测试矩阵

| 场景 | 预期 |
|---|---|
| 两个 required Child 乱序成功 | guard 等待全部，主 Run 只发布一次最终答复 |
| 一个 required Child 失败 | Goal 不可 complete；LLM 必须 replace、block 或诚实报告 |
| required Child 超过 deadline | 进入二次取消宽限；只有真正退出才持久化终态；不可协作任务按阶段 0 的 fail-closed/进程隔离策略处理，无晚到二次回复 |
| `await_subagents` 300 秒超时但 Run deadline 未到 | 返回 `waiting=true`，继续受 completion guard 约束 |
| `required=false` 后台 Child | 不受本轮 required guard；行为按阶段 0 决策回归 |
| `/stop` 与 guard 并发 | cancel-and-gather 无死锁、无遗留任务 |
| Child 吞掉取消或卡住 I/O | 不伪造终态；按阶段 0 的隔离/fail-closed 契约执行，主 Run 不误报完成 |
| 进程重启时 required 状态仍为 running | 按持久化所有权和 runtime 恢复规则标记/接管，不能自动 complete |
| result、deadline 和 `/stop` 同时到达 | durable claim 只成功一次，历史和 Outbound 不重复 |
| replacement Child 由 Continuation 创建 | 新 owner 明确，原 obligation 证据链保留，预算规则确定 |
| `runtime/config.json` 失败后 `unrelated/config.json` 成功 | 无恢复边，失败状态为 continued/unknown |
| 随后逻辑纠错 `config.json` 成功 | 仅失败节点到纠错成功节点生成 `tool_recovery` |
| 同 basename、同 Tool、不同规范资源 | 禁止误连 |
| 一个成功 Event 显式恢复多个失败 | 每个失败均有独立稳定边和正确 anchor |
| 同一失败被多个成功 Event 显式引用 | 不覆盖证据，全部合法显式边可见 |
| recovery ID 指向跨 Trace/首版跨 Run | 不自动补边，按 unresolved/兼容规则处理 |
| dangling recovery ID | 不建可见边，Graph 不崩溃，可诊断 unresolved |
| 恢复边端点处于 collapse group | 端点受保护或提供可展开入口，聚焦不丢边 |
| 旧 Event 无 recovery 字段 | Graph 向后兼容，恢复聚焦显示明确空状态 |
| edge 双端定位 | 分别定位失败和恢复 Event，不自动加载 Payload |

## 九、风险与回退

最高风险是生命周期硬门引入死锁或无限等待。缓解方式：单一 deadline、等待期间不持久化锁、cancel 后必须 gather、Runner 主 guard 与 Loop 防线分层、required=false 保持兼容。

第二风险是 streaming 已输出部分内容后 guard 拒绝 final。拥有未完成 required Child 时必须缓冲最终回答 token 或关闭最终回答 streaming；只在 guard 通过后发布。不得先向用户展示“完成”再撤回。

第三风险是 Child durable 状态和 MessageBus 结果重复。所有注入以 durable claim 原子去重，屏障状态不能替代结果正文。

第四风险是恢复误连。Graph 只消费显式 ID；任何证据不完整都降级为 unresolved/unknown。可以独立关闭 `tool_recovery` 边显示而保留原始 Event 和节点恢复摘要，不修改权威 Audit 数据。

回退顺序：

1. 前端关闭 edge 交互但保留 additive 类型兼容；
2. 后端停止生成 `tool_recovery` 可见边，保留现有摘要；
3. 通过预先定义的 feature flag 将 completion guard 切换为告警模式，但 Goal `required_gate` 不得回退；
4. Tool 描述和 Goal 提示可独立保留，不依赖新 Graph。

## 十、实施前待确认项

1. required Child 的 Run join deadline 默认值、配置范围、单调时钟起点，以及跨 Iteration/Continuation/Checkpoint/重启是否继承。
2. deadline 后统一记为 `timed_out`，还是主动取消记为 `cancelled` 并另存 timeout reason。
3. Child 不响应取消时采用可杀独立执行器，还是 fail-closed 阻塞主 Run/Goal；不得同时承诺有界返回和必然终止。
4. `required=false` 晚到结果保持 `background_notify`，还是默认 `background_silent`。
5. 本轮是否立即引入 `completion_policy`，或先采用 required => join_current_run 的最小兼容映射；已有运行中 Goal 如何迁移。
6. auto-barrier 是否产生独立 runtime decision Event，还是只保留结构化 guard metadata；不得伪造 LLM Tool Call。
7. Graph API 对旧客户端的 schema version 兼容策略。
8. edge 关系检查器是否需要 selected edge URL 深链；双端 Event 导航本轮必须实现。

除上述决策外，`tool_recovery` 不进入 `caused_by_event_id`、不按 basename/同名 Tool 推断、恢复链必须可定位两端 Event，均视为已确认约束。

## 十一、完成定义

只有同时满足以下条件才可宣称修复完成：

- 提示、Tool 描述和动态 Goal 指令对 required Child 的收口规则一致；
- LLM 尝试提前 final 时被运行时硬门拒绝；
- required Child 在成功、失败、取消和 deadline 场景中均无遗留 asyncio Task；
- 正常必需 Child 场景只产生一次用户可见最终答复；
- `tool_recovery` 在 run-level 和 trace-full Graph 均存在且锚点正确；
- recovery ID、owner Run、重复消息和跨 Trace/Run 边界均有明确的持久化/去重测试；
- 三路径样本只连接失败资源与确定性纠错成功资源；
- “恢复链路”能聚焦并导航失败、恢复两个原始 Event；
- “因果链”不包含伪造的 Tool 恢复因果；
- Python 聚焦测试、ruff、WebUI 测试、build 和真实 Chromium 验收全部通过；
- 未泄露 Payload、绝对敏感路径、凭据或完整 Tool 参数/结果。
