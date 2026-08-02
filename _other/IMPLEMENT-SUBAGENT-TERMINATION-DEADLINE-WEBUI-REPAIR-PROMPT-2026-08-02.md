# 执行“子 Agent 强制终止、Deadline 恢复与真实 WebUI 双端定位修复方案”的任务提示词

你正在 nanobot fork 仓库中实施一项高风险的运行时、durable state、Audit API 和 WebUI 修复。

仓库根目录：

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 唯一目标来源

先完整阅读并遵守根目录 `AGENTS.md`，再完整阅读：

```text
_other/SUBAGENT-TERMINATION-DEADLINE-WEBUI-REPAIR-PLAN-2026-08-02.md
docs/subagent-lifecycle-three-issues-research.md
```

实施方案是目标契约，当前源码是实现事实。若二者冲突，先记录源码证据和受影响阶段，不得静默发明接口或放宽安全边界。

## 开始前必须执行

```bash
git status --short --branch
git log -12 --oneline --decorate
git remote -v
```

确认：

- 当前分支不是 `main`/`master`；优先继续 `codex/subagent-tool-recovery`。
- 工作区没有受保护的用户改动；如有，不得 stash、reset、clean、覆盖或绕过。
- 基线是 `origin/main`，不得 fetch/merge/rebase `upstream`。
- 已推送历史不得重写。
- PR 目标是 `Trees-23/nanobot-kdm-2k:main`；无法使用 `gh` 时准确记录，不能伪造 PR 状态。

## 必须阅读的源码

```text
.agent/design.md
.agent/security.md
.agent/gotchas.md
nanobot/agent/loop.py
nanobot/agent/runner.py
nanobot/agent/subagent.py
nanobot/agent/tools/spawn.py
nanobot/agent/tools/await_subagents.py
nanobot/agent/tools/long_task.py
nanobot/agent/tools/shell.py
nanobot/agent/tools/exec_session.py
nanobot/session/goal_orchestration.py
nanobot/session/goal_state.py
nanobot/session/turn_continuation.py
nanobot/session/manager.py
nanobot/bus/queue.py
nanobot/audit/context.py
nanobot/audit/hook.py
nanobot/audit/schema.py
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
```

同时阅读所有相关 pytest、WebUI tests、Playwright 脚本和当前分支最近提交的 diff。

## 不可违反的语义

1. `task.cancel()`、timeout 或 cancellation request 不等于 Child 已终止。
2. 只有观察到 cooperative exit 或可杀 executor 的 kill/reap 证据，才能确认执行已停止。
3. 不可协作执行无法强杀时必须 fail-closed；required barrier 不满足，completion guard 不通过。
4. timeout 业务状态只在执行退出已确认后写 `timed_out`；否则写 `lost`/termination failure。
5. `required=false` 保持 `background_notify`，不能无意改成同步等待。
6. durable deadline 使用绝对 UTC；恢复后计算剩余时间，绝不重新获得完整 300 秒。
7. monotonic 只用于当前进程内计时，不持久化为跨重启真相。
8. Child result 先 durable finish/claim，再发 MessageBus；重复、晚到、重放只处理一次。
9. owner 使用创建 Child 的 `owner_run_id`；Continuation 不是 Child，不得用 `parent_run_id` 冒充。
10. `tool_recovery` 只由显式 `recovery_of_tool_call_ids` 构建。
11. 不得把 `tool_recovery` 写入 `caused_by_event_id`。
12. 不得按 basename、同名 Tool、时间相邻、资源字符串或前端文本推断恢复关系。
13. Payload 默认关闭；Graph、Events、edge click 和 Event locate 都不得自动加载 Payload。
14. 合成 fixture 通过不能写成真实 Gateway/WebUI 通过。

## 实施顺序

严格按阶段执行；数据语义先于 UI。

### 阶段 0：失败测试和契约

先增加确定性失败测试，覆盖：

- Child cooperative cancel 后退出；
- Child 吞掉/延迟 CancelledError；
- 不可协作线程在 Task cancel 后仍运行，系统不得声称终止；
- deadline 恢复不重置；
- duplicate/late result claim；
- required=false 不等待；
- Graph recovery unrelated/跨 Run/跨 Trace/dangling；
- WebUI Payload 请求默认 0。

不要使用真实模型失控或公网不稳定制造负向测试。

### 阶段 1：诚实取消状态与 durable deadline

实现最小可行修复：

- `termination_state` 与时间/evidence 字段；
- cancel -> grace -> cooperative exit/termination failure；
- 宽限失败映射 `lost`，不得写 `cancelled/timed_out`；
- completion guard 按 owner Run 和 durable remaining 工作；
- `await_subagents.timeout_seconds` 只能缩短本轮等待；
- checkpoint/continuation/restart 继承原 deadline；
- startup recovery 幂等扫描；
- durable result claim 和 continuation delivery phase。

这一阶段结束时可以声明“状态已诚实 fail-closed”，不能声明不可协作 I/O 已被强杀。

### 阶段 2：killable executor

在开始编码前先证明 Provider/runtime 可以安全重建；若不能确认，停止阶段 2 并报告阻塞，不要 pickle Provider 或把 secret 写入命令行/临时文件。

实现 process-per-child supervisor：

- 结构化 IPC；
- 独立进程/进程组身份；
- cooperative cancel；
- 短宽限；
- force kill；
- exit observation、reap、pipe/fd 清理；
- orphan 扫描；
- 平台 capability 降级；
- late result suppression。

线程池不算 killable executor。Shell 已有 kill helper 只能在证据等价时复用，不能把 Shell 的保证扩大到整个 Child。

### 阶段 3：Runner/AgentLoop 联合状态机

确保 completion guard 覆盖：正常 final、tool error、Provider error、empty final、max iterations、no-tools finalization、Goal continuation、stream abort、stop、shutdown 和异常出口。

guard 拒绝时：

- 不保存候选 assistant final；
- 不发送 final stream；
- 只注入 bounded runtime instruction；
- guard 异常 fail-closed。

### 阶段 4：Audit Graph/API

- 请求事件和终止证据分离。
- 恢复扫描和 duplicate/late suppression 有可审计、脱敏的 evidence。
- `tool_recovery` 保持显式 ID、同 Trace、同 Run、失败 terminal -> 成功 terminal。
- anchor 必须是两端真实 `tool_finished` Event。
- malformed/dangling/旧 schema 不崩溃，非法关系不构边。
- 同步提升 Graph builder version 和 ETag 语义。
- Graph/Events 都提供 revision；不一致时可降级。

如果新增 trace+event_id 查询接口，它必须认证、有界、仅返回 metadata，不返回 Payload。

### 阶段 5：WebUI

- edge inspector 显示双端 node/status/Event ID/evidence count。
- 两端分别复用 `locateEvent()`。
- 最多 5 页、1000 Event、10 秒；event_id 去重。
- cursor stale、not found、limit、revision mismatch、dangling 都有明确提示。
- 一端失败不阻塞另一端。
- 未知 `tool_recovery` 对旧客户端降级普通边。
- Payload 只在用户显式点击后加载。

### 阶段 6：真实 Gateway Chromium 验收

不得只运行 `AuditToolRecoveryFixture`。建立一个真实 Gateway/API/dist 的验收入口：

- 合成数据必须经真实 Audit emitter/indexer 落盘和读取；
- Gateway 必须提供本次构建的实际 dist；
- 记录 dist hash、Graph revision、Events revision 和网络请求；
- 覆盖 1440x900 与 390x844；
- 覆盖恢复 focus、edge click、失败端、恢复端、Timeline 双端、Node Inspector 原始 Event；
- 在显式点击前 Payload 请求数为 0；
- 覆盖 cursor stale、not found、5 页/1000 Event/10 秒、dangling、collapse/filter；
- console error/page error 为 0。

## 需要先确认、不得擅自决定的事项

以下事项只阻塞对应阶段，不阻塞前面的 MVP：

- killable executor 首批完整支持的平台；默认建议 Linux/容器，Windows 未有 Job Object 等价证据时 fail-closed。
- Provider/runtime worker rehydration 契约；禁止直接 pickle Provider。
- 是否接受新增 durable `termination_state`、可选 Graph evidence 字段和 Audit lifecycle Event。
- 是否增加按 trace+event_id 的安全 Events API；MVP 可以继续 cursor 扫描。

如果用户尚未确认，先完成所有不受阻塞的阶段，再准确报告阻塞点。不得用临时实现固定不可逆公开 schema。

## 测试与验证

至少执行：

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

同时增加真正不可协作 I/O 的隔离进程测试、重启恢复七场景测试和真实 API 浏览器测试。不要运行 `ruff format`。

测试失败时不得使用“完成”“修复”“已终止”“生产验收通过”等表述。报告首个错误、影响阶段和可回退点。

## 安全要求

- 继续使用 workspace path resolver、SSRF guard 和现有 sandbox 边界。
- worker 不在 argv、日志、Audit、state 或临时文件中写 secret。
- 不记录完整 Tool 参数、result、Payload、resource fingerprint 或用户绝对路径。
- kill 不能回滚外部 side effect；终止结果中保留不确定性。
- PID 不能作为唯一持久身份，避免 PID reuse 误杀。
- 临时 Audit 数据、浏览器截图和缓存放仓库外并在结束前清理。

## 提交与 PR

- 每个可验证阶段使用中文提交并立即推送当前任务分支。
- 只暂存本阶段明确路径；禁止 `git add .` 和 `git add -A`。
- 不重写已推送历史，不使用 worktree，不同步 upstream。
- 同一任务维护同一个 PR，中文标题和正文，持续更新改动、验证、风险和未完成项。
- 未经用户针对该 PR/分支明确确认，不得合并 `main`。

## 最终交付报告

必须用中文报告：

1. 分支名。
2. 每个提交编号和中文标题。
3. 推送结果。
4. PR 链接、状态和目标分支；无法确认时说明原因。
5. 实际运行的 pytest、ruff、WebUI test/build、真实 Chromium 命令和结果。
6. 三个问题各自真实完成度。
7. 哪些状态只有 cancel request，哪些有 cooperative exit/force kill 证据。
8. deadline 恢复、result claim 和 restart 场景的实际测试结果。
9. 真实 Gateway dist/API 证据；fixture 结果单独列出。
10. 未完成项、待确认项、风险和回退状态。
11. 明确说明是否已合并 `main`；未经确认必须写“等待用户确认后合并 main”。

在没有 kill/reap 证据、七种重启测试和真实 Gateway Chromium 证据前，绝不能宣称三个问题全部完成。
