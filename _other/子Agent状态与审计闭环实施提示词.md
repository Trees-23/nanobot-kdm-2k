# 子 Agent 状态与审计闭环实施提示词

你正在 nanobot 仓库中实施“子 Agent 状态、审计和 WebUI 运行轨迹闭环”任务。

仓库根目录：

/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k

## 一、目标来源

先完整阅读根目录 AGENTS.md、.agent/design.md、.agent/security.md、.agent/gotchas.md，再阅读：

_other/子Agent状态与审计闭环实施方案.md

方案是目标契约，源码是当前事实。冲突时记录证据和兼容影响，不得静默发明语义。

## 二、开始前

执行：

~~~bash
git status --short --branch
git log -12 --oneline --decorate
git remote -v
~~~

遵守：

- 不在 main、master 或默认分支开发；
- 保护用户已有修改，不得 stash、reset、clean、覆盖或夹带提交；
- 以 origin/main 为 fork 基线，不 fetch、merge 或 rebase upstream；
- 已推送历史不得重写；
- 每个可验证工作单元单独提交、推送并维护同一中文 PR；
- 未经用户确认不得合并到 main；
- 不运行 ruff format。

## 三、必须阅读的代码

~~~text
nanobot/agent/subagent.py
nanobot/agent/runner.py
nanobot/agent/loop.py
nanobot/agent/tools/spawn.py
nanobot/agent/tools/await_subagents.py
nanobot/session/goal_orchestration.py
nanobot/session/goal_state.py
nanobot/audit/schema.py
nanobot/audit/hook.py
nanobot/audit/graph.py
nanobot/audit/graph_types.py
nanobot/audit/read_service.py
nanobot/webui/audit_api.py
webui/src/lib/audit-types.ts
webui/src/lib/audit-api.ts
webui/src/components/traces/TraceGraph.tsx
webui/src/components/traces/TraceWorkbench.tsx
webui/src/components/traces/TraceNodeInspector.tsx
webui/src/components/traces/TraceTimeline.tsx
webui/src/hooks/useAuditTimeline.ts
~~~

同时阅读相关子 Agent、Audit、Goal、WebUI Trace 测试，先理解 SubagentStatus、parent/child Run、spawn_branch、result_return 和 Payload 脱敏边界。

## 四、不可违反的语义

1. SubagentManager 内存状态不是跨重启真相；Audit event 是生命周期事实，projection 只供查询。
2. 业务状态与执行 phase 分离；终态一旦写入，不得被 late result 覆盖。
3. task.cancel、timeout 或 cancellation request 不等于已终止；没有终止证据必须 fail-closed。
4. required=false 保持 background notify；required Goal 仍受 barrier 约束。
5. result 必须先 durable finish/claim，再 MessageBus publish；重复和晚到结果必须幂等。
6. spawn_branch 和 result_return 只能使用真实 ID，不得按时间、同名工具或文本猜测。
7. 默认 Audit/Graph 不返回完整 prompt、思维链、秘密、完整参数或完整外部内容。
8. 结构化 TaskSpec/TaskResult 必须兼容现有字符串 task 和纯文本 result。
9. 修改 runner.py 或 loop.py 必须运行聚焦集成测试；安全边界必须覆盖拒绝、脱敏和限长。
10. 不要为了可视化把业务逻辑搬到 WebUI；状态语义必须在 Python 后端和 Audit contract 中定义。

## 五、实施顺序

### 阶段 0：状态和事件契约

- 定义 SubagentTask、业务状态、phase、终态覆盖规则；
- 定义 lifecycle event 字段、schema version、脱敏和幂等 key；
- 为非法转移、late result、重复 claim、旧字段缺失增加失败测试；
- 契约未冻结前不要大规模改 UI。

### 阶段 1：后端运行状态

- spawn admission 创建任务记录；
- 在 initializing/model/tools/final/result delivery/terminal 阶段发出事件；
- 将现有 hook、checkpoint 和 SubagentStatus 接入统一状态机；
- 保留旧工具返回格式，新增字段只做 additive 兼容。

### 阶段 2：持久化、恢复和交付

- 写入 durable event，并重建 current-state projection；
- 实现 result finish/claim/delivery phase；
- 处理重启扫描、lost、cancelled、timed_out、late result 和重复 MessageBus；
- Goal orchestration 继续负责 required 归属和 barrier，不复制第二份 Goal 逻辑。

### 阶段 3：任务/结果协议和成本观测

- 增加 TaskSpec/TaskResult 适配；
- 兼容旧字符串输入输出；
- 记录 token、耗时、并发、总子任务数和预算剩余；
- 先实现观测和拒绝原因，再启用总量/费用门禁。

### 阶段 4：Audit Graph/API

- 为 child task node 补 task ID、phase、usage、delivery、error 和预算摘要；
- 使用真实 parent/child/run/tool/event ID 建立边；
- 新增实时状态查询或 WebSocket 事件；
- 保持 Payload 默认关闭、认证、限长和旧客户端未知字段兼容；
- 更新 Graph builder version、API 类型和契约测试。

### 阶段 5：WebUI 闭环

- TraceGraph 显示 task phase/status、耗时、iteration、当前工具和 tokens；
- Inspector 按 Task/Run/Model/Tool/Delivery/Checkpoint 分类；
- 支持 spawn -> child -> result return 双向定位；
- 实时状态与历史 Audit 使用相同枚举和文案；
- 增加 stale、lost、timeout、cancelled、failed、result pending；
- 用 Vitest 和真实 Playwright 验证桌面/移动端、刷新、索引延迟和历史回放。

## 六、实现约束

- 优先扩展现有 Audit schema、Graph builder、Audit API 和 Trace components；
- 只有消除真实重复或保护持久化边界时才新增 abstraction；
- 不把完整模型上下文复制到每个审计节点；
- 不把 Run 成功自动解释为所有子任务成功；
- 不把 SubagentStatus 直接序列化成公开 API，先定义脱敏 DTO；
- 所有用户可见摘要在后端生成并限长；
- provider/runtime 不能直接 pickle 到持久化或 worker；
- 若要强杀 executor，先证明 runtime 重建、secret 传递和进程树回收；否则先完成 cooperative fail-closed MVP。

## 七、最低验证

~~~bash
pytest tests/agent/test_subagent_lifecycle.py \
  tests/agent/tools/test_subagent_tools.py \
  tests/agent/tools/test_goal_orchestration.py \
  tests/agent/test_runner_audit.py \
  tests/audit/test_graph_builder.py \
  tests/audit/test_webui_api.py -q

ruff check nanobot/agent/subagent.py nanobot/agent/runner.py \
  nanobot/agent/loop.py nanobot/audit nanobot/webui tests/agent tests/audit

cd webui && bun run test
cd webui && bun run build
~~~

修改核心 Runner/Loop 必须覆盖 result injection、failure、cancellation、restart/recovery。Playwright 未运行时不得写成 WebUI 已验证。

## 八、交付格式

每个阶段报告：

- 修改文件和状态契约；
- 新增/更新测试；
- 执行的命令和真实结果；
- Audit/API/WebUI 兼容影响；
- 未完成项、风险和下一阶段阻塞；
- 中文提交标题、提交编号、推送结果和 PR 状态。

完成判定不是“前端能画出一个子 Agent 节点”，而是状态产生、事件持久化、查询投影、实时更新、历史回放、结果交付和异常恢复使用同一套可验证语义。
