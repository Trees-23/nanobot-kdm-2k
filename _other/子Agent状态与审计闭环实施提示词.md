# 子 Agent 状态与审计闭环实施提示词 V2

你正在 nanobot 仓库中实施“统一子 Agent 任务状态机与主子 Agent 协同闭环”。目标不是重新实现
子 Agent，也不是重写主/子两套 AgentRunner，而是在当前进程执行、Goal orchestration 和 Audit Graph
能力之上补齐统一任务状态、审计证据、实时协议和 WebUI 闭环。

仓库根目录：

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 一、目标来源与工作方式

先完整阅读当前目录适用的全部 `AGENTS.md`、`.agent/design.md`、`.agent/security.md`、
`.agent/gotchas.md`，再完整阅读：

```text
_other/子Agent状态与审计闭环实施方案.md
```

方案定义目标架构，源码与测试定义当前事实。若二者冲突：

1. 记录代码、测试和提交证据；
2. 判断是方案过时、实现偏离还是兼容阶段差异；
3. 优先保护现有已验证语义；
4. 在 PR 中说明取舍，不得静默创造第三套状态模型。

不要一次实施全部阶段。每个工作单元必须可独立验证、提交、推送并进入同一个任务 PR。

## 二、开始前检查

执行并记录：

```bash
git status --short --branch
git branch --show-current
git log -15 --oneline --decorate
git remote -v
```

遵守：

- 不在 `main`、`master` 或默认分支开发；
- 以 `origin/main` 为当前 fork 基线，不擅自 fetch、merge 或 rebase `upstream`；
- 保护用户已有修改，不 stash、reset、clean、覆盖或夹带提交；
- 已推送历史不得重写；
- 每个可验证工作单元使用中文提交，推送并维护同一个中文 PR；
- 未经用户明确确认不得合并到 `main`；
- 不运行 `ruff format`。

## 三、必须先建立当前能力矩阵

完整阅读以下实现，不得依据旧方案假设能力尚未存在：

```text
nanobot/agent/subagent.py
nanobot/agent/child_executor.py
nanobot/agent/child_worker.py
nanobot/agent/runner.py
nanobot/agent/loop.py
nanobot/agent/tools/spawn.py
nanobot/agent/tools/await_subagents.py
nanobot/session/goal_orchestration.py
nanobot/session/goal_state.py
nanobot/session/turn_continuation.py
nanobot/session/webui_turns.py
nanobot/templates/agent/subagent_system.md
nanobot/templates/agent/subagent_announce.md
nanobot/templates/agent/goal_runtime.md
nanobot/audit/types.py
nanobot/audit/schema.py
nanobot/audit/hook.py
nanobot/audit/graph.py
nanobot/audit/graph_types.py
nanobot/audit/read_service.py
nanobot/audit/runtime.py
nanobot/webui/audit_api.py
nanobot/channels/websocket/runtime.py
webui/src/lib/types.ts
webui/src/lib/audit-types.ts
webui/src/lib/audit-api.ts
webui/src/hooks/useNanobotStream.ts
webui/src/components/traces/TraceGraph.tsx
webui/src/components/traces/TraceWorkbench.tsx
webui/src/components/traces/TraceNodeInspector.tsx
webui/src/components/traces/TraceTimeline.tsx
```

同时阅读最近相关提交和测试，至少包括：

```text
tests/agent/test_subagent_lifecycle.py
tests/agent/test_child_executor.py
tests/agent/tools/test_goal_orchestration.py
tests/agent/test_runner_audit.py
tests/integration/test_subagent_restart_real.py
tests/audit/test_graph_builder.py
tests/audit/test_webui_api.py
nanobot/channels/websocket/tests/
webui/src/tests/audit-trace-ux.test.tsx
```

先输出能力矩阵：

| 能力 | 已实现 | 部分实现 | 缺失 | 证据 |
|---|---|---|---|---|
| Process child/IPC | | | | |
| required durable state | | | | |
| background durable state | | | | |
| termination evidence | | | | |
| result claim/delivery | | | | |
| task lifecycle Audit | | | | |
| task projection/API | | | | |
| WebSocket task state | | | | |
| TaskSpec/TaskResult | | | | |
| count/token/cost/depth budget | | | | |

## 四、当前基线，不得重复建设

除非源码核对证明已经回退或损坏，否则把以下能力视为基线：

- Linux `ProcessChildExecutor`、进程组、版本化 IPC 和身份校验；
- Child Worker runtime/config/tools/audit 重建；
- cooperative cancellation、force kill、进程树回收和终止证据；
- `termination_failed` fail-closed 与 late result 拒绝；
- Goal orchestration schema V2 的 required task、deadline、executor、replacement；
- result claim、claim owner、delivery phase 和 startup recovery；
- `await_subagents` required barrier；
- Audit parent/child Run、`spawn_branch`、`result_return`；
- WebUI 现有 Run/Model/Tool、retry/continuation/recovery 展示。

不得重新做一套 Goal barrier、第二个 child supervisor 或基于时间猜测的 Graph 关系。

## 五、必须保护的系统语义

1. `SubagentTaskStore` 是统一任务当前状态和 revision 的业务真相。
2. `GoalOrchestrationStore` 只拥有 required obligation、group、replacement 和 barrier。
3. Audit 是 append-only 生命周期与执行证据；projection/index 可重建，不是第二套可写业务状态。
4. `SubagentManager` 只管理当前运行句柄和短期镜像；其内存字典不是跨重启真相。
5. required 与 background 使用同一种任务模型，只在等待、完成门禁和通知策略上不同。
6. 业务 status、执行 phase、termination state、delivery phase 必须分离。
7. cancel request、timeout signal、`asyncio.Task.cancel()` 都不等于执行器已经停止。
8. 没有可靠终止证据时必须 `lost/termination_failed`，不得报告 cancelled 或 force killed。
9. 终态不能被 late result 覆盖；重复 terminal、claim、delivery 和恢复扫描必须幂等。
10. `succeeded` 不代表 `delivered`，Child Run 成功也不代表 Goal obligation 已满足。
11. spawn/result/replacement/recovery 关系只能使用真实 ID 和事件证据。
12. 默认接口不得返回完整 prompt、思维链、secret、完整参数或完整外部内容。
13. 不把 `SubagentStatus` 原样序列化成 API，必须使用版本化脱敏 DTO。
14. 不自动复制主 Agent 全部 system prompt/history 到子 Agent；通过公共 policy 和 TaskSpec 对齐。
15. provider/runtime 不得 pickle 或直接持久化；workspace 和安全 scope 必须显式重建。

## 六、架构决策要求

在写功能代码前，先以测试和短设计记录冻结以下决策：

- `SubagentTask` schema version、revision 和 UTC 时间字段；
- 合法状态转移表和终态覆盖规则；
- TaskStore 的持久化位置、原子写入和 retention；
- 活动 Goal orchestration V2 的兼容与迁移策略；
- task transition 与 Audit event 之间的 outbox/补发机制；
- lifecycle event schema 和 `task_id + revision + event_type` 幂等键；
- result ready/claim/delivery 的原子边界；
- TaskSpec/TaskResult 旧输入输出适配；
- task/owner/session/Goal 的预算预留和释放；
- REST/WebSocket DTO、snapshot、revision gap 恢复；
- 旧 Audit/旧 Trace 的 `legacy_inferred` 降级语义。

如果无法保证任务状态与 Audit 可靠双写，使用 durable outbox。不得通过“先分别写两个地方，再尽量补救”
制造不可对账状态。

## 七、实施顺序

### 阶段 0：契约与失败测试

- 建立当前能力矩阵；
- 定义四维状态、转移表、revision、DTO、event schema 和幂等键；
- 为非法转移、重复转移、late result、重复 claim、旧字段缺失增加失败测试；
- 契约未冻结前不大规模修改 Graph 或 WebUI。

### 阶段 1：统一 durable TaskStore

- required/background 均在 spawn admission 创建任务记录；
- `SubagentStatus` 改为 TaskStore 的运行时镜像；
- 接入现有 executor identity、checkpoint、usage 和 termination evidence；
- background 重启后也能恢复为诚实的 terminal/lost；
- 与 Goal orchestration V2 兼容，不复制 required barrier 逻辑。

### 阶段 2：状态转移、outbox 与结果交付

- 所有状态变化走统一 transition service；
- 每次成功转移递增 revision 并生成 lifecycle outbox；
- 发布版本化 Audit event，支持 degraded 后补发；
- 固化 result ready -> claim -> delivery -> injection 顺序；
- 验证重复 MessageBus、重启和 late result 不重复消费。

### 阶段 3：TaskSpec、TaskResult 与预算

- 增加结构输入输出并兼容字符串 task、纯文本 result；
- 记录 task/owner/session/Goal 的 count、concurrency、depth、token、cost、wall time；
- spawn admission 原子预留额度；
- 返回结构化 rejection reason；
- 先观测 token/cost，再开启默认门禁；并发和深度始终有硬限制。

### 阶段 4：查询 API 与实时协议

- 提供 task list/detail/snapshot/timeline 脱敏 DTO；
- 增加 `subagent_snapshot` 和 `subagent_status_changed`；
- 用 `task_id + revision` 去重，revision gap 时重新水合；
- 保持认证、Payload 默认关闭、限长和旧客户端未知字段兼容；
- WebSocket wire 细节放在 channel/WebUI coordinator，不塞入 AgentLoop。

### 阶段 5：Audit Graph Task 语义

- 增加 Task node/region 和 lifecycle summary；
- 连接真实 spawn、child Run、replacement、recovery、delivery、continuation；
- Child Run 不得继续冒充 Task；
- 更新 builder version、Python/TypeScript 类型和契约测试；
- 旧 Trace 只能标记 `legacy_inferred`，不能伪造 recorded evidence。

### 阶段 6：WebUI 闭环

- 展示 Task/Run/Model/Tool/Delivery 分层；
- 支持并行泳道和主 Agent 消费结果后串行二次委派；
- 展示 status、phase、termination、delivery、usage、budget 和错误；
- 实时快照/增量与历史 Audit 共用状态枚举和文案；
- 支持 spawn -> Task -> child -> result -> continuation 双向定位；
- 验证刷新、重连、索引延迟、旧数据和未知字段。

## 八、成本与任务分片门禁

实现中必须防止“一个任务被无界拆成许多子 Agent”：

- owner Run 子任务总量限制；
- session/Goal 累计子任务限制；
- 最大并发；
- 最大 child depth，默认不允许子 Agent 任意递归 spawn；
- token、cost 和 wall-time 预算；
- 相同 TaskSpec 或幂等键的重复任务拒绝；
- admission 必须先预留预算，启动失败或安全终态后按规则释放；
- 拒绝后让主 Agent等待、缩小任务或自己完成，不能静默重试制造更多任务。

不要仅依赖模型提示词控制成本，硬上限必须在工具/服务端 admission 边界执行。

## 九、测试矩阵

每个阶段运行最接近的测试；完成闭环前至少验证：

```bash
pytest \
  tests/agent/test_subagent_lifecycle.py \
  tests/agent/test_child_executor.py \
  tests/agent/tools/test_goal_orchestration.py \
  tests/agent/test_runner_audit.py \
  tests/integration/test_subagent_restart_real.py \
  tests/audit/test_graph_builder.py \
  tests/audit/test_webui_api.py -q

ruff check \
  nanobot/agent/subagent.py \
  nanobot/agent/child_executor.py \
  nanobot/agent/child_worker.py \
  nanobot/agent/runner.py \
  nanobot/agent/loop.py \
  nanobot/session \
  nanobot/audit \
  nanobot/webui \
  tests/agent tests/audit tests/integration

cd webui && bun run test
cd webui && bun run build
```

还必须增加或运行场景测试：

- 一个主 Agent 并发创建多个子 Agent；
- 主 Agent 消费结果后再串行创建另一个子 Agent；
- required barrier 成功、失败、替换；
- background 不阻塞但可持久化；
- cooperative cancel、force kill、timeout、termination failed、lost；
- 重启前后 claim/delivery exactly-once effect；
- late result 拒绝；
- child count/concurrency/depth/token/cost 拒绝；
- Audit degraded/outbox 补发；
- WebSocket 快照、乱序、重复、revision gap、重连；
- 旧 Goal、旧 Trace、旧客户端和旧字符串协议。

修改 `runner.py` 或 `loop.py` 必须运行聚焦集成测试。修改安全边界必须覆盖拒绝、脱敏、限长、
workspace scope 和 secret 不继承。Playwright 未运行时不得声称真实 WebUI 已验证。

## 十、前端真实验收

WebUI 阶段启动真实 Gateway 和前端，使用 Playwright 验证桌面及移动端：

- 并行任务不重叠，泳道和连线稳定；
- 串行二次委派顺序正确；
- 状态变化不会导致节点尺寸和布局跳动；
- 刷新后快照与刷新前一致；
- result pending、delivery failed、lost、timeout、cancelled 文案准确；
- Task、Child Run 和主 Agent continuation 可双向定位；
- 旧 Trace 和未知字段不崩溃。

保存必要的确定性 fixture、截图和运行命令，但不得提交密钥、大体积临时 runtime 或无关构建产物。

## 十一、每个工作单元的交付格式

每次提交前报告并核对：

- 本单元解决的一个明确不变量；
- 修改文件和状态/协议变化；
- 新增或更新的测试；
- 执行命令和真实结果；
- 对 Goal、Audit、WebSocket、WebUI 和旧数据的兼容影响；
- 风险、未完成项和下一阶段依赖；
- 中文提交标题、提交编号、推送结果、PR 链接和状态。

完成判定不是“前端画出了子 Agent 节点”，而是同一个任务从 admission、执行、终止、结果领取、
主 Agent 消费到历史回放都能用真实 ID、单调 revision 和持久化证据闭环解释。
