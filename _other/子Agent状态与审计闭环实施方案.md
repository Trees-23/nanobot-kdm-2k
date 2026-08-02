# nanobot 子 Agent 状态与审计闭环实施方案

日期：2026-08-02
范围：子 Agent 运行状态、持久化审计、实时状态接口、WebUI 运行轨迹
目标：让“创建任务 -> 执行 -> 结果生成 -> 结果回传 -> 主 Agent 消费”全过程可观察、可审计、可恢复。

## 1. 现状与目标

当前已有：

- SubagentStatus：内存中的 phase、iteration、tool events、usage、错误和终态；
- Audit：parent/child Run、model call、tool call、checkpoint 和 result return；
- WebUI：spawn_branch、result_return、Run/Tool/Model 节点。

缺口是三者没有统一的子 Agent 任务模型：普通子 Agent 状态重启后不能完整恢复，Graph 仍需从 Run/Tool 事件推断关系，前端无法稳定显示任务目标、阶段、结果交付和预算，输入输出也没有结构化协议。

目标：

1. 建立版本化 SubagentTask 状态模型和明确状态机；
2. 让运行时、持久化审计和 WebUI 使用同一套状态语义；
3. 记录 parent Run、child Run、spawn tool call、result delivery 的可验证关系；
4. 支持实时推送、历史回放、幂等交付和恢复；
5. 为 token、费用、时长和子 Agent 总量预算预留字段；
6. 引入结构化 TaskSpec / TaskResult，兼容现有字符串 task。

非目标：

- 第一阶段不强行改成独立进程；
- 不把完整 prompt、思维链、秘密或未脱敏参数写入 Audit；
- 不改写已有 Audit JSONL 和 Session 历史；
- 不让前端依赖 SubagentManager 内存字典；
- 没有可靠终止证据时不声称 Child 已强制终止。

## 2. 状态模型

业务状态：

~~~text
created -> queued -> running -> terminal
terminal = succeeded | failed | cancelled | timed_out | lost
~~~

执行阶段：

~~~text
initializing
running_model
awaiting_tools
tools_completed
final_response
result_delivering
~~~

阶段用于展示，不替代业务终态。终态一旦写入，不得被 late result 覆盖。

推荐记录：

~~~json
{
  "schema_version": 1,
  "task_id": "sub-123",
  "parent_run_id": "run-parent",
  "owner_session_key": "websocket:chat-1",
  "child_run_id": "run-child",
  "label": "研究 PostgreSQL",
  "status": "running",
  "phase": "awaiting_tools",
  "required": false,
  "task_group": "default",
  "current_iteration": 3,
  "current_tool": "web_search",
  "model": "provider/model",
  "usage": {"prompt_tokens": 12000, "completion_tokens": 3000},
  "budget": {"max_tokens": null, "max_cost_usd": null},
  "started_at": "2026-08-02T00:00:00Z",
  "finished_at": null,
  "result": {"status": "pending", "delivery": "pending"},
  "error": null
}
~~~

时间字段用带时区的 UTC；monotonic clock 只用于当前进程等待，不作为跨重启真相。

## 3. 真相、投影和边界

采用“事件是真相，投影供查询”：

~~~text
运行时状态变化 -> lifecycle audit event -> durable event log
                  -> current-state projection/index
                  -> WebSocket live update / Audit Graph API
~~~

- SubagentManager：调度和内存句柄；
- Session/Goal orchestration：Goal required 任务和 barrier；
- Audit event log：跨进程可追溯的生命周期和执行证据；
- projection/index：查询优化，不是第二套业务真相；
- result/artifact store：结构化结果和产物引用；
- MessageBus：通知，不是唯一持久化手段。

## 4. 事件和关系契约

优先复用现有 Run、Model、Tool、Input injection、Checkpoint 事件，并补充：

~~~text
subagent_created
subagent_phase_changed
subagent_budget_updated
subagent_result_ready
subagent_result_delivery_started
subagent_result_delivered
subagent_terminal
subagent_recovered
subagent_lost
~~~

事件至少携带 task/trace/turn/run/parent/child IDs、前后状态、phase、时间、iteration、安全摘要和幂等标识。

spawn_branch 必须绑定真实 spawn tool call；result_return 必须绑定真实 child terminal 和 input injection。禁止按同名工具、时间邻近或文本猜测恢复关系。

## 5. Parent-to-child 协议

兼容字符串 task，同时增加结构化输入：

~~~json
{
  "objective": "调研 PostgreSQL",
  "context": "主任务背景和必要资料",
  "constraints": ["只使用公开资料"],
  "deliverables": ["结论", "来源", "风险"],
  "acceptance_criteria": ["至少三个独立来源"],
  "dependencies": []
}
~~~

结构化结果：

~~~json
{
  "status": "succeeded",
  "summary": "...",
  "evidence": [],
  "artifacts": [],
  "files_changed": [],
  "tests": [],
  "risks": [],
  "error": null
}
~~~

旧纯文本结果在适配层转换为 summary，不破坏旧主 Agent。

## 6. 成本、取消和恢复

为任务和 session/goal 预留：

- max_children、max_concurrent_children；
- max_total_tokens、max_cost_usd；
- max_wall_time_seconds、max_child_depth。

先实现观测，再启用拒绝门禁。预算不足时返回结构化错误，让主 Agent 缩小任务或直接完成。

结果顺序固定为：

~~~text
durable finish -> durable claim -> delivery event -> MessageBus publish
~~~

重复消息、恢复扫描和 late result 使用同一 claim key 幂等处理。task.cancel、timeout、cancellation request 都不等于已终止；不可靠终止必须 fail-closed。required 与 background 的等待语义不能混淆。

## 7. WebUI 闭环

### TraceGraph

子 Agent 节点展示：

- label、objective 摘要、status、phase；
- elapsed、iteration、当前工具；
- model、prompt/completion tokens、预算；
- error、terminal reason、result delivery；
- parent Run、child Run、task ID。

边保持：

~~~text
spawn_branch  主 Agent 创建子 Agent
parent_run    父子 Run 层级
result_return 子 Agent 结果回传
retry         重试或替换
recovery      checkpoint 恢复
~~~

### Inspector

按节点类型展示：

- Task：任务定义、约束、验收条件、状态；
- Run：stop reason、iteration、失败/恢复统计；
- Model：provider、model、tokens、attempt；
- Tool：工具名、安全输入摘要、错误、timeout；
- Delivery：结果生成、claim、投递；
- Checkpoint：阶段、版本、恢复和清理。

完整 prompt、思维链和敏感 payload 只在用户显式操作且通过认证的接口中读取。

### 实时与历史

- WebSocket 推送 subagent_status_changed；
- Audit API 查询历史 Trace 和状态投影；
- 前端使用 task_id + revision 合并事件，处理乱序和重复；
- stale、lost、timeout、cancelled、failed 都必须有明确文案。

## 8. 分阶段实施

### 阶段 0：契约冻结

确认状态枚举、终态覆盖、脱敏字段、版本和兼容策略；补 schema fixture 与失败测试。

### 阶段 1：后端状态投影

正式化 SubagentTask；接入现有 hook/checkpoint；记录 created、phase、result ready、delivery、terminal；保留旧 SubagentStatus 兼容。

### 阶段 2：持久化与恢复

事件写入、投影重建、result claim 幂等、startup scan、lost、late result、取消和恢复。

### 阶段 3：协议和成本观测

接入 TaskSpec/TaskResult 适配层；记录 token、耗时、并发、总量和预算；先观测后拒绝。

### 阶段 4：API 与 TraceGraph

扩充 Graph node summary 和 edge anchor；增加实时状态查询/推送；保持 Payload 默认关闭和旧客户端兼容。

### 阶段 5：WebUI 和真实浏览器验收

实现 Task 节点、状态 badge、详情、预算、delivery、双向定位；用 Vitest 和 Playwright 验证桌面、移动端、刷新、索引延迟、失败和恢复。

## 9. 验收标准

后端：

- 每次状态转移有唯一事件；
- 终态不可被 late result 覆盖；
- 重启、重复消息、重复 claim 不重复交付；
- required/background 语义不混淆；
- 默认 Audit/Graph 不泄露 prompt、思维链和秘密。

前端：

- 可从 spawn 定位 child；
- 可从 child terminal 定位 result return 和主 Agent continuation；
- 可看到 phase、耗时、iteration、tool、usage、错误、delivery；
- 实时和历史使用同一套状态文案；
- 旧 Trace 和未知字段不导致页面崩溃。

首个工作单元建议只实现“状态枚举 + SubagentTask + lifecycle event + projection + 聚焦测试”，契约稳定后再改 Graph/API/WebUI。
