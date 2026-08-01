# 执行“子 Agent 生命周期与 Tool 恢复链路实施方案”的任务提示词

你正在 nanobot fork 仓库中实施一项受审查的运行时、Audit Graph 和 WebUI 改动。先阅读并遵守仓库根目录 `AGENTS.md`，再阅读：

```text
_other/子Agent生命周期与Tool恢复链路实施方案.md
```

该实施方案是唯一的目标来源；随后必须阅读方案列出的真实代码和测试，以当前代码为事实来源。不要重新发明目标、字段或 API。

## 任务范围

只实现实施方案中已经确认的契约：

- `required=true` Child 的主 Run completion guard、owner/deadline/cancel-and-gather、durable result claim 和重复回流抑制；
- `await_subagents`、Goal continuation、Checkpoint、replacement Child 的清晰生命周期边界；
- 显式 `recovery_of_tool_call_ids` 驱动的 `tool_recovery` Audit Graph 边、anchor、schema/builder version 和旧数据降级；
- WebUI 恢复聚焦、命中数、空状态、图例、关系检查器、失败/恢复两端 Event 定位和有界分页提示。

非目标包括：按 basename/同名 Tool/时间/前端字符串推断恢复；修改 `caused_by_event_id`；跨 Trace 或非法跨 Run 自动补边；改写历史 Audit JSONL/Payload/catalog；泄露完整 Tool 参数、Payload、凭据、真实用户路径；把 `required=false` 全部改成同步任务。

## 开始前检查

1. 检查所有适用 `AGENTS.md`、当前分支、工作区状态、远端、`origin/main` 基线和当前分支现有 PR。PR 目标是 `Trees-23/nanobot-kdm-2k:main`；工具缺失、未认证或网络不可用时准确记录“无法确认”。
2. 不在 `main`/`master` 开发；默认使用 `git switch` 创建或切换 ASCII 任务分支，不使用 worktree。若受保护用户改动阻塞，停止覆盖并报告。
3. 阅读实施方案第 12 节的待确认项。没有决定前不得暗自发明不可逆 API、状态枚举、公开 schema、deadline 继承或 feature flag 默认值；先报告哪个阶段被阻塞。
4. 只建立合成、脱敏 fixture，例如 `evals/audit-trace-recovery/runtime/config.json`、`evals/audit-trace-recovery/unrelated/config.json`。不要改写历史权威 Audit 数据或评测记录。

## 实施规则

按实施方案阶段 0 -> 5 执行。数据语义必须先于 UI。

### 阶段 0：fixture 和契约

- 先建立/校验合成 Child lifecycle 和三路径 recovery fixture。
- fixture 必须能证明：`runtime/config.json` 失败、`unrelated/config.json` 成功不恢复、规范纠错成功只恢复首个失败调用。
- 先写确定性断言，再改 runtime/Graph/API/WebUI；不得用真实模型失控制造负向测试。

### 阶段 1：提示和显式 barrier

- 更新 `SpawnTool.description`、`AwaitSubagentsTool.description`、`goal_runtime.md` 和 continuation prompt。
- 提示职责是提高模型正确率；runtime guard 才是不可绕过的硬门。
- `await_subagents` 仍是 Goal-scoped、一次有界等待；timeout 不能被写成终态，也不能自动改变 `required=false`。

### 阶段 2：completion guard 和生命周期

实现 guard 时必须覆盖：

- 正常 final；
- Tool error/fatal 收口；
- max iterations/no-tools finalization；
- empty/length fallback；
- Goal internal continuation；
- streaming、stop、shutdown、provider error、stream abort 和异常出口。

在 guard 通过前不得发送不可撤回的“完成”内容。选择并实现已经确认的 streaming 策略：缓冲最终 token，或关闭该 Run 的 final streaming。拒绝 final 时不能把候选 assistant 文本保存成正式历史。

required Child 必须按创建它的 owner Run 检查，不得把同 Session 所有后台任务混在一起。等待期间不要持有 `GoalOrchestrationStore` session lock。deadline 后先 cancel-and-gather，再在短宽限后确认 asyncio Task 真正退出，最后才持久化 `cancelled`/`timed_out`。不可协作 I/O 必须遵守已确认的“可杀执行器”或“fail-closed”策略，不能同时声称有界返回且 Child 已终止，也不能用 `lost` 掩盖仍运行任务。

MessageBus 结果和 durable Goal 状态必须通过 `subagent_task_id` 做一次性原子 claim：claim 成功才进入 pending/history；重复消息、重启重放和晚到消息不得重复写历史、重复 Outbound 或重复创建 Continuation。Goal internal continuation、Checkpoint 恢复、Child-result continuation、replacement Child 要分别记录 owner Run、deadline 和用户可见性。`required=false` 必须有回归测试，证明没有无意变成同步任务。

### 阶段 3：Audit Graph/API

`tool_recovery` 只能由运行时已经记录的显式 `recovery_of_tool_call_ids` 构边。source 必须是失败 Tool semantic node，target 必须是成功恢复 Tool semantic node，anchor 必须分别指向两端 `tool_finished` Event。三路径必须证明 unrelated basename 不误连。

不得把 `tool_recovery` 写进 `caused_by_event_id`，不得新增虚假的 LLM 决策 Event，不跨 Trace/非法跨 Run 猜测。处理多对多、dangling、malformed、旧 Event 缺字段、旧 Graph schema 和 collapse endpoint；不合法关系无可见边但 Graph 不崩溃且有明确降级。同步提升 Graph builder version/ETag 语义，确保后端和前端契约同提交。

Graph、Events API、日志和关系检查器不得自动携带完整 Payload、resource fingerprint、完整 Tool 参数/result、凭据或敏感绝对路径。Payload 仍只能由用户显式认证加载。

### 阶段 4：WebUI

- 增加 TypeScript `TraceEdgeType` 和 `tool_recovery` 样式/图例；与 `result_return`、`resumed_from` 可区分。
- 恢复聚焦必须显示命中节点数/边数，命中 0 时显示明确空状态；`causal` 不包含 `tool_recovery`。
- 实现 edge click 和轻量关系检查器，不把边伪装成节点。检查器必须显示失败端、恢复端、状态、Event ID 和显式 recovery 证据计数。
- 失败端和恢复端都调用既有 `locateEvent()`；保留最多 5 页、1000 Event、10 秒上限、去重、cursor stale/not found/limit 错误提示。定位成功后选中并滚动时间线；Payload 不自动加载。
- 处理多条边、折叠节点、筛选、dangling ID 和一端定位失败；不要由前端路径字符串重算关系。

### 阶段 5：联合验证和交付

只运行与改动匹配的检查；未通过不得宣称完成。提交标题、正文、PR 标题和正文全部使用中文。每个可验证工作单元只暂存本次明确路径，创建中文提交并推送当前任务分支，持续维护同一 PR；未经用户明确确认不得合并 `main`。

## 必须执行的验证

按方案阶段运行聚焦 pytest 和 `ruff check`，至少覆盖：

```bash
pytest tests/agent/tools/test_goal_orchestration.py tests/agent/tools/test_subagent_tools.py tests/agent/test_runner_injections.py tests/agent/test_loop_runner_integration.py tests/agent/test_subagent_lifecycle.py tests/agent/test_runner_audit.py tests/audit/test_graph_builder.py tests/audit/test_webui_api.py tests/audit/test_end_to_end.py -v
```

Python 改动运行对应路径的 `ruff check`，不要运行 `ruff format`。

WebUI 改动运行：

```bash
cd webui && bun run test -- src/tests/trace-graph.test.tsx src/tests/audit-trace-ux.test.tsx
cd webui && bun run build
```

同时建立可重复的真实 Chromium Playwright 入口，例如 `webui/e2e/audit-tool-recovery.spec.ts` 和项目配置，然后运行：

```bash
cd webui && bunx playwright test e2e/audit-tool-recovery.spec.ts --project=chromium
```

真实浏览器必须覆盖 1440x900 和 390x844、恢复链命中 2 节点/1 边、空状态、unrelated 未命中、失败/恢复双端 Event 定位、5 页/1000 Event/10 秒提示、Payload 未自动加载、collapse/filter、console/page error=0。125%/150% zoom、trackpad 和原生 scrollbar 作为人工补充，不得用 CSS zoom 冒充。

## 异常处理和安全边界

- 受保护用户改动：保留并报告，绝不覆盖、还原、stash、clean 或绕过。
- 缺少决定：暂停受影响阶段，报告阻塞决定和默认建议，不擅自发明公开契约。
- 测试环境门禁、浏览器缺失：保留已完成本地验证和文档，准确记录未执行命令。
- 远端认证、权限或网络失败：保留本地提交，报告 push/PR 无法完成的准确原因，不伪造链接或通过状态。
- 测试失败：不得写“完成”“修复”或“通过”；报告失败命令、首个错误、影响阶段和回退点。
- 任何临时数据放在仓库外并在结束前清理；不得生成截图、缓存、Audit 数据或无关文件到仓库。

## 最终报告

最终报告必须使用中文并包含：

1. 分支名。
2. 每个可验证工作单元的提交编号和中文标题。
3. 推送结果。
4. 同一 PR 的链接、状态和目标分支；无法确认时写明原因。
5. 实际运行的 pytest、ruff、`bun run test`、`bun run build`、Chromium 命令及结果。
6. 未完成项、待确认项、风险和回退状态。
7. 明确说明是否已合并 `main`；未经用户确认必须写“等待用户确认后合并 main”。

不得把未执行的产品代码、测试、Audit 数据修改或服务重启写成已执行；不得以固定格式掩盖阻塞。
