# nanobot 子 Agent 强杀、Deadline 重启与恢复链路真实验收收口方案

日期：2026-08-02
实施分支：`codex/subagent-tool-recovery`
目标基线：当前分支 `72ae22d8` 及其后续提交
性质：对既有修复方案的强制收口，不替代历史调研和状态契约。

## 1. 执行摘要

当前分支已经完成诚实取消状态、绝对 UTC deadline、durable result claim/delivery phase、
`tool_recovery` evidence count 和真实 Gateway 测试基础设施，但尚不能验收为三个问题全部完成：

1. Child 仍运行在父进程的 `asyncio.Task` 中；取消宽限失败后只能写
   `termination_failed/lost`，没有进程级 kill/reap 能力。
2. deadline 恢复已有单元测试，但缺少真实 Gateway 进程崩溃、重启和容器重建验收。
3. `tool_recovery` 与 `sequence` 共用上下连接点并相互重叠；首次从恢复边定位 Event
   存在 Timeline 初始加载竞态；现有 Playwright 没有断言目标 Event 行被选中，因此出现假阳性。
4. 当前可见恢复 Trace 是经真实 Audit writer/indexer 写入的脱敏合成数据，只能证明读取链路，
   不能证明 Runtime 自然执行产生了修复状态。

本方案把原方案中的 killable executor 从“增强实现”提升为必做项，并规定：只有代码测试、
真实进程故障测试、真实 Gateway/API/dist Chromium 测试和 `localhost:8765` 可见运行轨迹全部通过，
任务才允许验收。

## 2. 完成声明的硬门禁

以下条件缺少任何一项，都不得使用“全部完成”“强制终止已实现”“重启恢复已完成”或
“生产 WebUI 双端定位已通过”：

- Linux/容器上的 required Child 由独立、可监管进程执行。
- 不可协作 Child 经进程组 kill 后有 root exit、reap 和后代清理证据。
- 强杀失败保持 `termination_failed/lost`，required completion guard 拒绝成功收口。
- 原始绝对 `deadline_at` 跨真实 Gateway 重启保持不变，恢复后不获得新预算。
- finish、claim、delivery 和 continuation 在真实重启与重复恢复中保持幂等。
- `tool_recovery` 使用显式 `recovery_of_tool_call_ids`，不污染 `caused_by_event_id`。
- 恢复边从节点侧面独立走线，不与 `sequence` 重叠，可见、可点击、可键盘访问。
- 失败端和恢复端 Event 在 Timeline 中分别进入可断言的选中状态。
- Graph、Events、Event Inspector 均不自动加载 Payload。
- 真实运行轨迹由实际 AgentLoop/Runner/Subagent/Executor 行为产生，不直接伪造 Audit Event。
- 完整 pytest、ruff、WebUI test/build、真实 Chromium 和部署后 smoke test 全部返回成功。

## 3. 当前事实与保留契约

### 3.1 已有实现，必须保留

- 业务状态：`running -> succeeded | failed | cancelled | timed_out | lost`。
- 终止状态：`none`、`cancel_requested`、`grace_waiting`、`cooperatively_exited`、
  `force_kill_requested`、`force_killed`、`termination_failed`。
- durable 权威时间为绝对 UTC `deadline_at`；monotonic 只用于单进程内等待。
- timeout 只有在退出已确认后才能写 `timed_out`，否则写 `lost`。
- required Child 由 owner Run barrier 和 completion guard 约束。
- `required=false` 保持 background notify，不被无意改成同步等待。
- result 顺序为 durable finish -> claim -> delivery -> MessageBus/continuation。
- `tool_recovery` 只由显式失败 Tool Call ID 构建，anchor 指向两端真实 Event ID。
- Payload 默认关闭，只有用户显式点击才加载。

### 3.2 当前已复现缺陷

- `SubagentManager` 的 cancellation backend 是 `asyncio`，evidence 明确为
  `force_kill_available=false`。
- `TraceGraph` 给 `tool_recovery` 使用默认 bottom/top handles，与相同端点的 `sequence` 重叠。
- `TraceWorkbench.locateEvent()` 先 `setTimelineOpen(true)`，随后立刻调用旧闭包中的
  `timeline.ensureEvent()`；初始 `events=[]/nextCursor=null` 时误判 `not_found`。
- 现有真实 Gateway Playwright 只检查 Timeline 可见，没有检查目标行选中或错误提示缺失。
- Gateway/Chromium 仍出现 `ERR_BLOCKED_BY_RESPONSE.NotSameOrigin` 和 WebSocket
  `assert not self.eof_sent`，必须归因或修复。

## 4. 总体架构

```text
AgentLoop / SubagentManager
        |
        | ChildExecutionSpec（最小、结构化、无 secret 落盘）
        v
ChildExecutorSupervisor
        |
        +-- process-per-child worker（独立 session / process group）
        |       |
        |       +-- AgentRunner / Child AgentLoop
        |       +-- 结构化 result / lifecycle IPC
        |
        +-- cancel -> grace -> TERM -> grace -> KILL -> wait/reap
        |
        +-- durable termination evidence / result claim
```

状态真相保持在现有 Session/Goal orchestration 与 Audit durable log 中；executor handle 是运行时监管
能力，不是第二套业务真相。PID 仅作诊断，不能作为跨重启唯一身份，必须组合不可复用的
`executor_id`、process instance token、启动时间和父监管实例身份，避免 PID reuse 误杀。

## 5. 工作包 A：进程级 Killable Child Executor

### 5.1 最小可行实现

首批完整支持 Linux/容器，采用 process-per-child。Windows 若没有 Job Object 或等价进程树退出证据，
保持 cooperative-only + fail-closed，不得写 `force_killed`。

建议新增：

- `nanobot/agent/child_executor.py`：协议、supervisor、handle、exit/evidence 类型。
- `nanobot/agent/child_worker.py`：worker 入口、runtime 重建、结构化 IPC。
- 聚焦测试文件：executor 生命周期、进程组、fd/reap、late result、平台 capability。

建议关键接口：

```python
class ChildExecutor(Protocol):
    async def start(self, spec: ChildExecutionSpec) -> ChildHandle: ...
    async def request_cancel(self, handle: ChildHandle) -> None: ...
    async def terminate(self, handle: ChildHandle) -> ChildExit | None: ...
    async def force_kill(self, handle: ChildHandle) -> ChildExit | None: ...
    async def wait(self, handle: ChildHandle, timeout: float | None) -> ChildExit | None: ...
    async def close(self) -> None: ...
```

具体命名可按仓库模式调整，但状态和证据语义不得缩水。

### 5.2 Runtime 重建与 IPC

- 不得 pickle Provider、AgentLoop、Tool Registry、锁、socket 或 coroutine。
- worker 从结构化 spec 和受控 config snapshot 重建 provider/model/tool/runtime。
- 动态 provider/model override 必须有显式序列化契约；无法重建时返回 capability error。
- secret 只能通过匿名 pipe、继承 fd 或等价受控 IPC 传递；不得进入 argv、环境 dump、Audit、
  durable state、临时文件、异常文本或截图。
- IPC envelope 具有 schema version、executor ID、task ID、sequence/idempotency key 和大小上限。
- 父进程只接受当前 executor identity 的消息；旧 worker 晚到结果必须 suppressed。

### 5.3 终止状态机

```text
running
  -> cancel_requested
  -> grace_waiting
  -> cooperatively_exited

grace_waiting
  -> force_kill_requested
  -> SIGTERM + term grace
  -> SIGKILL(process group)
  -> wait/reap/descendant check
  -> force_killed

任何无法确认退出的分支
  -> termination_failed
  -> lost
```

- 第一次取消请求只进入 cooperative grace。
- deadline 到达后使用原剩余预算，不开启新的完整 timeout。
- 二次 `/stop` 或 watchdog escalation 可立即进入 force kill，但必须幂等。
- `force_kill_requested` 不是退出证据；只有观测到退出、完成 reap 和后代检查后才能写
  `force_killed`。
- 强杀不能撤销已经发生的外部副作用，结果和 Audit 必须保留该不确定性。

### 5.4 资源清理

- POSIX worker 使用独立 session/process group；强杀针对已验证 PGID。
- root process 必须 `wait()`，防止 zombie。
- 关闭 stdin/stdout/stderr、IPC pipe、reader task、watchdog 和 executor registry。
- 对孙进程进行有界 orphan 检查；不能证明后代清理时不得写完整成功证据。
- kill 目标必须匹配 executor identity，禁止仅凭持久化 PID 操作。

### 5.5 影响边界

- `SubagentManager`：任务/owner/result orchestration，调用 executor，不内联平台 kill 细节。
- `AgentRunner`：仅增加 worker 可重建的运行 spec 和必要 completion 交点。
- `AgentLoop`：startup/shutdown recovery、completion guard 和 continuation 交付；改动保持最小。
- `spawn.py` / `await_subagents.py`：公开参数和等待语义，不承担进程管理。
- `goal_orchestration.py`：durable executor identity、termination evidence、deadline 和 claim。

### 5.6 测试断言

- cooperative Child：不发送 SIGKILL，终态为 `cancelled/timed_out + cooperatively_exited`。
- 吞掉 `CancelledError` 的 worker：宽限后真实进程退出，终态为 `force_killed` 对应的业务终态。
- 阻塞文件 I/O、阻塞网络替身、卡死同步 SDK 替身：父进程 event loop 不被阻塞，worker 可被杀。
- worker 派生孙进程：整个进程组退出，无 zombie/orphan。
- kill 权限错误、PID identity 不匹配、reap 超时：`termination_failed/lost`。
- required 强杀失败：barrier 不满足，主 Run 不发送成功 final。
- optional 强杀失败：不阻塞 owner final，但保留 anomaly/lost 和一次通知。
- late result：不能覆盖 terminal，不能重复 history/outbound/continuation。

### 5.7 回退

executor backend 必须具有显式配置级 capability fallback。回退到 cooperative-only 后，状态语义仍为
fail-closed，不能退回“task.cancel 即已终止”。不得用线程池作为回退的 killable executor。

## 6. 工作包 B：Deadline、Checkpoint 与真实重启

### 6.1 必须保持的算法

- durable 真相：带时区的绝对 UTC `deadline_at`。
- 每次等待：`remaining = max(0, deadline_at - now_utc)`，再映射为本进程 monotonic deadline。
- `await_subagents.timeout_seconds` 只能缩短本次等待，不能延长 durable deadline。
- checkpoint/continuation/startup recovery 继承原 task ID、owner Run 和 deadline。
- executor identity 无法证明仍存活时写 `termination_failed/lost`，不猜测继续运行。
- deadline 已过时立即进入终止状态机，绝不重新等待默认 300 秒。

### 6.2 Result claim 与恢复

- finish 先于通知。
- `unclaimed -> claimed_pending_delivery -> delivered` 状态迁移原子、幂等。
- claim owner 使用创建 Child 的 owner Run；Continuation 不冒充 Child owner。
- 恢复动作可重复执行；重复 MessageBus、worker result 和 continuation 均由 task ID/idempotency key 抑制。
- durable blob 损坏时不清空、不猜测成功，记录安全 hash/错误码并 fail-closed。

### 6.3 真实进程验收场景

测试必须真正停止并重启 Gateway/worker 进程，不能只清空内存对象：

1. required Child 等待期间正常 Gateway 重启。
2. 重启前 Child 已完成，result 尚未 claim。
3. 重启后 Child 才完成，且 executor identity 可证明并支持重接；若架构不支持重接，必须明确
   转为 `lost`，不能假装恢复。
4. 重启时原 deadline 已过期。
5. durable state 损坏。
6. MessageBus 中存在重复结果。
7. startup recovery 执行两次。
8. claim 已写、delivery 未完成时崩溃并再次恢复。
9. 容器重建后旧 PID 被复用，不能误杀无关进程。

每个场景断言原 `deadline_at`、remaining、owner、terminal、termination state、claim/delivery、
消息次数和 Audit lifecycle Event。

## 7. 工作包 C：Audit Graph/API 契约

### 7.1 Tool recovery

- 关系只来自成功 `tool_finished.recovery_of_tool_call_ids`。
- 失败端必须是同 Trace、同 Run 的失败 terminal Tool Call；恢复端必须是成功 terminal Tool Call。
- source/target 是稳定 Graph node ID；anchor 是两端真实 `tool_finished.event_id`。
- 不使用 basename、同名 Tool、时间相邻、资源字符串或前端文本推断。
- 绝不把 `tool_recovery` 写入 `caused_by_event_id`。
- dangling、跨 Trace、跨 Run、重复 ID、旧 schema 和 malformed evidence 不构造错误边，不导致 API 崩溃。

### 7.2 终止与恢复审计

- 区分 cancellation requested、grace、TERM/KILL requested、exit observed、reap completed、
  termination failed、startup recovered/lost、late result suppressed。
- evidence 仅包含 executor/backend/capability/exit observation/安全错误码和时间，不含 task 正文、
  Tool 参数、secret、绝对用户路径或 Payload。
- Graph/Events 返回各自 revision；不一致时前端降级，不猜测映射。
- 如新增按 trace+event ID 查询，只返回认证、有界、脱敏 metadata；Payload 仍走独立显式 API。

## 8. 工作包 D：WebUI 恢复边与双端定位

### 8.1 独立侧边走线

- `sequence` 保持 bottom-source -> top-target。
- `tool_recovery` 使用 source/target 的侧边 handles，优先同一空闲侧。
- custom edge 路由在节点列外侧设置稳定 offset；同端点多关系边不能重叠。
- 边显示“恢复”短标签或等价清晰标识，颜色不能是唯一信息载体。
- 保留足够大的透明 interaction width、键盘焦点和 tooltip。
- 点击恢复边直接设置 selected edge，并进入恢复链路聚焦；不要求用户先理解“因果链”。

影响文件：

- `webui/src/components/traces/TraceGraph.tsx`
- `webui/src/components/traces/nodes/TraceNode.tsx`
- `webui/src/workers/auditLayout.worker.ts`
- 相应 Graph 单测与 Playwright 截图/像素断言

### 8.2 因果链与恢复链路语义

- 因果链只包含 `caused_by/retry/retry_of`。
- 恢复链路包含 `tool_recovery`，并与 Child `result_return/resumed_from` 的展示文案区分清楚。
- 聚焦按钮显示关系数量；零条时禁用或解释具体缺少的关系类型。
- `tool_recovery` 不得为了“因果链命中”而改写后端因果字段。

### 8.3 修复首次定位竞态

`ensureEvent(eventId)` 必须实现：

1. 已加载事件命中则直接返回。
2. 尚未加载第一页时，先请求第一页并以响应结果更新本地 collected/revision/cursor。
3. 然后按 cursor 继续有界分页，不能使用 state 更新前的旧闭包数据判断 `not_found`。
4. 同一 trace 的并发 load/ensure 要去重或串行，避免旧请求覆盖新 revision。
5. 上限保持 5 页、1000 Event、10 秒；到达上限返回 `limit`，不是 `not_found`。

### 8.4 双端导航与 Payload

- edge inspector 显示失败/恢复 node、status、完整 Event ID、evidence count。
- 两端按钮独立调用同一定位逻辑；一端失败不阻塞另一端。
- 定位成功必须让对应 `[data-event-id]` 行进入明确选中态并滚动可见。
- Node Inspector 的原始 Event 导航必须回到同一 Timeline 行。
- Graph、edge inspector、Timeline 定位和原始 Event 导航的 Payload 请求数都必须为 0。
- 只有显式点击“查看 Payload”后才请求认证接口，并执行大小/记录数上限与脱敏。

## 9. 工作包 E：Gateway、dist 与运行环境

- 修复或明确归因 Chromium `ERR_BLOCKED_BY_RESPONSE.NotSameOrigin`。
- 修复 Gateway WebSocket 握手 `assert not self.eof_sent`，真实浏览器 console/page error 必须为 0。
- 同步并提交 `webui/package-lock.json`，保证 Dockerfile 的 `npm ci` 可重复。
- 构建后记录源码 commit、镜像 ID、dist `index.html` hash 和主 bundle 名。
- 重建 `nanobot-gateway`，验证容器内 dist 与宿主构建产物 hash 一致。
- 不运行 `npm audit fix --force`；依赖漏洞单独评估和提交。
- 不输出或写入报告任何 bootstrap secret、token、Provider key、WebSocket ticket。

## 10. 测试矩阵

| 层级 | 核心场景 | 必须断言 |
|---|---|---|
| Python 单元 | 状态迁移、deadline、claim | 终态不可覆盖、原 deadline、幂等 |
| Executor 集成 | cooperative、TERM/KILL、孙进程、kill 失败 | exit/reap/orphan/evidence |
| Agent 集成 | required/optional、await、completion guard | final 是否允许、消息次数 |
| 重启集成 | 九个真实进程场景 | owner/deadline/claim/Audit |
| Audit 单元 | recovery 合法/非法关系 | source/target/anchor/revision |
| API 集成 | Graph/Events/Event/Payload | 认证、有界、默认无 Payload |
| WebUI 单元 | edge routing、focus、ensureEvent | 不重叠、真实选中、错误降级 |
| 临时 Gateway Chromium | 桌面/移动完整工作流 | 无 fixture、无 console error |
| 部署后 Chromium | `localhost:8765` 实际 dist | hash 一致、可见真实轨迹 |

新增测试不得通过降低断言、预选节点、延长无界 timeout、mock 掉目标边界或直接 seed 目标 Audit Event
来规避真实失败。

## 11. 真实运行轨迹验收

### 11.1 轨迹生成原则

- 使用确定性本地 provider/tool/scenario driver，避免公网模型不稳定，但必须经过真实
  Gateway -> AgentLoop -> Runner -> Tool/Subagent/Executor -> Audit emitter/indexer。
- 最终验收禁止直接调用 Audit emitter 手工拼装目标 lifecycle Event；直接 seed 只可用于 Graph
  解析单测，不能作为 Runtime 修复证据。
- 场景数据必须脱敏，无真实凭据和用户文件内容。
- 轨迹写入 Gateway 实际配置解析出的 audit root，不能猜测 `runtime/audit` 与 `runtime/audit/v1`。
- 每条轨迹记录 trace ID、run ID、commit、镜像、dist hash、Graph/Events revision 和场景断言摘要。

### 11.2 8765 必须可见的轨迹

至少保留以下五条可区分 Trace：

1. `tool-recovery-navigation`：真实 Tool 先失败、后以显式 recovery ID 成功。
2. `child-cooperative-cancel`：取消后在 grace 内自然退出。
3. `child-force-killed`：不可协作 worker 经 TERM/KILL、wait/reap 后确认退出。
4. `child-termination-failed`：故障注入导致退出无法确认，required task 为 `lost`，guard 拒绝成功。
5. `required-child-deadline-restart`：等待中重启，保留原 deadline 并完成或过期。

轨迹必须显示状态请求与退出证据的区别。`force_kill_requested` 不能单独作为“已强杀”证据。

### 11.3 Chromium 操作断言

在 1440x900 和 390x844 上至少执行：

1. 打开真实 Trace。
2. 选择失败 Tool 节点，确认“因果链”和“恢复链路”计数语义。
3. 确认恢复边位于节点侧面、与 sequence path 不相同且可点击。
4. 点击恢复边，检查两端 node/status/Event ID/evidence count。
5. 定位失败端，断言对应 Timeline 行选中并可见。
6. 定位恢复端，断言选中态从失败端切换到恢复端。
7. 从两端 Node Inspector 原始 Event 再次定位 Timeline。
8. 确认上述步骤 Payload 请求数始终为 0。
9. 显式打开脱敏 Payload，验证认证、上限和关闭行为。
10. 验证 not found、dangling、cursor stale、revision mismatch、5 页/1000 Event/10 秒提示。
11. 打开四条 Child/deadline Trace，核对状态、owner、deadline、kill/reap 和 guard 结果。
12. 断言 console error、page error、失败资源请求均为 0。

视觉断言必须检查实际 edge path/handle/offset 或稳定截图区域，不能只检查 DOM 中存在一条 edge。

## 12. 完整验证命令

实施者应先运行聚焦测试，再运行最终全量门禁。具体新增文件名可按实现调整。

```bash
pytest tests/agent/tools/test_goal_orchestration.py \
  tests/agent/test_subagent_lifecycle.py \
  tests/agent/test_loop_runner_integration.py \
  tests/agent/test_task_cancel.py \
  tests/audit/test_graph_builder.py \
  tests/audit/test_webui_api.py -v

ruff check nanobot/agent/loop.py nanobot/agent/runner.py \
  nanobot/agent/subagent.py nanobot/agent/child_executor.py \
  nanobot/agent/child_worker.py nanobot/agent/tools/await_subagents.py \
  nanobot/session/goal_orchestration.py nanobot/audit nanobot/webui/audit_api.py \
  tests/agent tests/audit

cd webui
bun run test
bun run build
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

最终门禁：

```bash
pytest -q
ruff check nanobot/ tests/
cd webui && bun run test && bun run build
cd webui && bunx playwright test --project=chromium
```

还必须执行真实重启 scenario suite、重建 8765 容器和部署后 Chromium suite。命令应在实现时固化为
仓库脚本或 Playwright project，不得只留在个人 shell history。已有平台相关 skip 可以如实报告；本任务新增的
Linux/容器核心验收不得 skip、xfail 或条件绕过。

## 13. 实施阶段、提交与回退点

### 阶段 0：补失败测试与基线证据

- 固化当前四个缺陷：无 kill backend、edge 重叠、首次 Event 定位失败、E2E 假阳性。
- 提交边界：测试，不改变产品行为。
- 回退：删除新增测试提交，不触及 durable schema。

### 阶段 1：WebUI 可见性和真实定位

- 修复侧边走线、focus 文案、首次加载竞态和严格 Playwright 断言。
- 先完成该阶段，避免继续用假阳性评价后端。
- 回退：保留 API schema，回退前端路由与 hook。

### 阶段 2：Executor 协议和 Linux worker

- 完成 rehydration proof、IPC、process group、wait/reap 和资源清理。
- 提交边界：协议/worker 与聚焦 executor 测试。
- 回退：配置切回 cooperative-only，保持 fail-closed 状态。

### 阶段 3：Subagent/Runner/Loop 集成

- 接入 cancel escalation、required/optional、await、completion guard、shutdown 和 late result。
- 修改 `loop.py`/`runner.py` 必须运行聚焦集成测试。
- 回退：禁用 process backend；不得回退 durable 状态真实性。

### 阶段 4：真实 Deadline 重启与 Audit

- 完成九个进程级恢复场景、lifecycle Audit、Graph/API 投影和降级。
- 回退：Audit 新字段保持可选；reader 继续兼容旧 schema。

### 阶段 5：Gateway/dist/8765 最终验收

- 修复 WebSocket/NotSameOrigin、构建镜像、生成真实轨迹、执行桌面/移动 Chromium。
- 完成全量测试和交付证据。
- 回退：回到上一镜像；durable reader 必须仍能读取已写新字段。

每个阶段形成独立中文提交并立即推送当前分支，持续维护同一 PR。只暂存明确路径，不得夹带用户已有
`webui/package-lock.json` 修改，除非该文件在对应构建阶段经核对后作为独立提交处理。

## 14. 安全、隐私与故障降级

- 保持 workspace resolver、SSRF guard、shell sandbox 和路径约束。
- worker 不扩大 Tool 权限，不因进程隔离绕过现有安全配置。
- IPC、日志、Audit、截图、trace fixture 和 PR 不得包含 secret、完整 prompt、思维链或原始 Payload。
- 进程身份不匹配时拒绝 kill，进入 `termination_failed/lost`。
- Audit/index/durable state 损坏时保留原数据，禁止自动清空或猜测修复。
- Graph/Events 不一致时明确提示刷新，禁止按时间、名称或字符串补关系。
- Payload 默认关闭；自动加载数量严格为 0。
- 强杀后的外部副作用不可逆，状态必须显示结果不确定性。

## 15. 实施前决策

本收口方案采用以下默认决策，除非用户明确修改：

1. Linux/容器是首批完整 killable backend；其他平台 fail-closed。
2. process-per-child，不使用共享进程池或线程池冒充可杀执行器。
3. 不 pickle Provider；先完成 runtime rehydration proof，再接入生产路径。
4. executor backend 有配置级回退，默认切换策略必须通过兼容测试后决定。
5. 不新增虚构“恢复节点”；恢复是两端真实 Tool Event 的侧边关系。
6. 不把 `tool_recovery` 并入 `caused_by_event_id`。
7. 最终 Runtime 轨迹不接受直接 seed Audit Event 作为通过证据。
8. 所有本任务新增 Linux/容器关键测试必须实际执行，不允许 skip/xfail。

若 runtime rehydration、进程树控制或 secret 传递无法满足安全要求，实施者必须停止对应生产接入、保留
fail-closed，并准确报告 blocker；不得以不安全临时方案换取测试通过。

## 16. 完成定义

只有以下全部成立，验收结论才是“通过”：

- 工作包 A-E 全部完成且代码审查没有 P0/P1 未解决问题。
- 不可协作 Child 在 Linux/容器中有真实 kill/reap/orphan-free 证据。
- deadline 和 result claim 通过九个真实进程重启场景。
- 恢复边侧边可见，双端 Event 在 Timeline 中真实选中。
- 因果链零命中和恢复链路命中的语义清楚，未污染后端因果字段。
- 五条真实 Runtime Trace 在 `http://localhost:8765/` 可见并可重复验收。
- Payload 在显式操作前请求数为 0。
- pytest、ruff、WebUI test/build、全部 Chromium、真实重启 suite 和部署 smoke 全部成功。
- Gateway console/page error 为 0，实际 dist 与目标 commit/镜像一致。
- 分支提交均已推送，PR 中文说明包含风险、验证和未完成项。
- 用户尚未确认合并时明确写“等待用户确认后合并 main”。

任何一项失败时，最终报告必须写“未通过”，列出首个失败证据、受影响工作包和最近可回退提交。
