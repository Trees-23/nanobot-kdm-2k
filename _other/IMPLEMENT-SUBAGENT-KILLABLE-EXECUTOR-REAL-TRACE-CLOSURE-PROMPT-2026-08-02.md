# 执行“子 Agent 强杀、Deadline 重启与恢复链路真实验收收口方案”的任务提示词

你正在 nanobot fork 仓库中继续一项尚未完成的高风险运行时修复。你的任务不是重新调研，也不是只完成
MVP，而是把 killable Child executor、真实 deadline 重启、WebUI 恢复边和真实运行轨迹验收全部收口。

仓库根目录：

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

当前任务分支：

```text
codex/subagent-tool-recovery
```

## 1. 唯一目标来源

先完整阅读并遵守根目录 `AGENTS.md`，再完整阅读：

```text
_other/SUBAGENT-KILLABLE-EXECUTOR-REAL-TRACE-CLOSURE-PLAN-2026-08-02.md
_other/SUBAGENT-TERMINATION-DEADLINE-WEBUI-REPAIR-PLAN-2026-08-02.md
_other/IMPLEMENT-SUBAGENT-TERMINATION-DEADLINE-WEBUI-REPAIR-PROMPT-2026-08-02.md
docs/subagent-lifecycle-three-issues-research.md
.agent/design.md
.agent/security.md
.agent/gotchas.md
```

新收口方案优先于旧方案中把 killable executor 视为“增强项”的部分。旧方案的状态真实性、deadline、
Audit、安全和兼容契约继续有效。

## 2. 开始前检查

执行：

```bash
git status --short --branch
git log -12 --oneline --decorate
git remote -v
git diff --stat
```

必须确认：

- 继续使用 `codex/subagent-tool-recovery`，不得切到 `main`/`master`。
- 不创建 worktree，不 fetch/merge/rebase `upstream`。
- 用户已有修改全部受保护；不得 stash、reset、clean、覆盖或夹带提交。
- 当前已知 `webui/package-lock.json` 可能有未提交修改，先核对来源；只有在 WebUI 构建阶段确认属于本任务
  且差异正确时，才能作为独立提交暂存。
- 已推送历史不得重写，不得强推。

## 3. 当前已知基线

不要重复声称下面内容尚未实现，也不要破坏它们：

- absolute UTC `deadline_at`。
- `termination_state` 与 honest fail-closed。
- 宽限失败进入 `termination_failed/lost`。
- durable result claim 和 delivery phase。
- `tool_recovery.evidence_count` 和双端 anchor。
- Graph/Events revision mismatch 降级基础。
- 真实 Audit writer/indexer + Gateway Playwright 基础设施。

但必须承认并修复：

- 当前没有 Child 进程级 killable executor。
- 恢复边与 sequence 重叠。
- 首次 Event 定位会误报 `not_found`。
- 现有 E2E 没有断言目标 Event 行选中，属于假阳性。
- 当前 seed Trace 不是 Runtime 自然执行证据。
- Gateway/Chromium 仍有 WebSocket/NotSameOrigin 错误。

## 4. 必须阅读的实现

至少完整阅读：

```text
nanobot/agent/loop.py
nanobot/agent/runner.py
nanobot/agent/subagent.py
nanobot/agent/tools/spawn.py
nanobot/agent/tools/await_subagents.py
nanobot/agent/tools/long_task.py
nanobot/agent/tools/shell.py
nanobot/agent/tools/exec_session.py
nanobot/process_runtime.py
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
webui/src/components/traces/nodes/TraceNode.tsx
webui/src/components/traces/TraceWorkbench.tsx
webui/src/components/traces/TraceNodeInspector.tsx
webui/src/components/traces/TraceTimeline.tsx
webui/src/hooks/useAuditTimeline.ts
webui/src/workers/auditLayout.worker.ts
webui/e2e/audit-tool-recovery-real.spec.ts
webui/e2e/seed-audit-tool-recovery.py
```

同时阅读所有相关 pytest、WebUI tests、Playwright 配置、Dockerfile/compose、当前分支最近提交 diff。

## 5. 不可违反的语义

1. `task.cancel()`、timeout、SIGTERM/SIGKILL request 都不等于执行已经退出。
2. 只有 cooperative exit，或 kill 后 root exit + reap + 后代清理证据，才能确认终止。
3. 无法确认退出时写 `termination_failed/lost`，required barrier 和 completion guard fail-closed。
4. timeout 只有退出已确认后才写 `timed_out`。
5. PID 不是跨重启唯一身份，禁止 PID reuse 误杀。
6. secret 不得进入 argv、日志、Audit、state、临时文件、截图或测试附件。
7. 线程池不是 killable executor。
8. deadline 使用绝对 UTC；重启后只计算剩余时间，绝不重置完整 timeout。
9. result durable finish/claim/delivery 先于 MessageBus，重复恢复只能投递一次。
10. `required=false` 保持 background notify。
11. `tool_recovery` 只来自显式 `recovery_of_tool_call_ids`。
12. 不得把 `tool_recovery` 写入 `caused_by_event_id`。
13. 不得按 basename、同名 Tool、时间相邻、资源字符串或前端文字推断恢复关系。
14. 恢复边必须侧边独立走线，不能覆盖 sequence。
15. Payload 默认关闭，自动请求数必须为 0。
16. fixture、mock、直接 seed Audit Event 不能冒充 Runtime 真实轨迹。
17. 测试脚本显示 passed 但未断言目标行为时，不得视为验收通过。

## 6. 执行方式

严格按阶段实施。每个阶段都执行：失败测试 -> 最小实现 -> 聚焦验证 -> 差异审查 -> 中文提交 -> 推送 ->
更新同一 PR。不要把所有工作堆在一个提交中。不要在测试失败时继续对外声明该阶段完成。

### 阶段 0：固化当前失败

先添加会失败的测试，证明：

- Child 吞掉取消后没有 kill backend。
- `tool_recovery` 和 `sequence` 同端点 path 重叠。
- Timeline 关闭且未选节点时，首次定位已有 Event 误报 `not_found`。
- 当前 Playwright 点击定位按钮但没有检查 Event 行选中。

测试必须命中真实缺陷，不能先改测试数据让它绕过问题。

### 阶段 1：修复 WebUI 恢复链路

实现：

- sequence 保持上下走线。
- tool recovery 使用侧边 handles 和外侧稳定 offset；同端点多边不重叠。
- 提供“恢复”标签或等价非颜色提示、足够 interaction width、tooltip 和键盘访问。
- 点击恢复边直接进入恢复链路聚焦并打开双端检查器。
- 因果链和恢复链路显示各自关系计数；零命中给出具体解释。
- `ensureEvent()` 在初始未加载时先加载第一页，再用响应 cursor 有界分页。
- 并发 load/ensure 不允许旧 state 闭包误判或旧 revision 覆盖新结果。
- 失败端和恢复端分别进入 Timeline 明确选中态。
- Node Inspector 原始 Event 可再次定位同一行。
- 上述操作 Payload 请求数为 0。

更新 E2E，必须断言：

- 初始不预选 node。
- 两条边的 SVG path/handles 不同。
- 失败 Event 行有选中态。
- 恢复 Event 行随后有选中态，失败行取消选中。
- 页面没有 `not_found`/revision/limit 错误。
- 原始 Event 导航真实改变 selection。
- 1440x900 和 390x844 均通过。

### 阶段 2：实现 Linux/容器 killable executor

先写一份简短 rehydration proof 到 PR 或代码旁设计记录，证明 provider/model/tool/runtime 如何在 worker 重建。
不得 pickle Provider，也不得把 secret 写入 argv/临时文件。

实现 process-per-child：

- 结构化版本化 execution spec/result/lifecycle IPC。
- executor ID + process instance identity + PID/PGID 诊断信息。
- worker 独立 session/process group。
- cooperative cancel 和第一段 grace。
- force kill request、SIGTERM、第二段 grace、SIGKILL process group。
- root wait/reap、pipe/fd/reader/watchdog 清理、后代 orphan 检查。
- late/stale executor result suppression。
- Linux/容器 capability；不支持平台 cooperative-only + fail-closed。
- 配置级 fallback，不用线程池冒充。

不得只复用 Shell Tool 的 `_kill_process_tree()` 然后宣称整个 Child 可杀；必须证明 Child worker 本身和其后代
都处于 supervisor 的身份与进程树控制之下。

### 阶段 3：接入 Subagent、Runner、AgentLoop

接入：

- spawn owner、required/optional 和 executor lifecycle。
- deadline/watchdog、第一次取消、二次取消、shutdown。
- `await_subagents` 的原 deadline remaining。
- completion guard 的所有 final/error/stream/continuation 出口。
- durable termination evidence、finish、claim、delivery。
- late result、重复 result 和旧 executor result 抑制。

修改 `loop.py` 或 `runner.py` 后必须运行聚焦集成测试。guard 异常必须 fail-closed，不得保存或发送候选 final。

### 阶段 4：真实重启与 Audit/API

建立真实进程 scenario suite，必须真正停止/重启 Gateway 或 worker，覆盖：

1. required Child 等待中重启。
2. 重启前 Child 已完成、未 claim。
3. 重启后 Child 才完成；不能重接时诚实转 `lost`。
4. 重启时 deadline 已过。
5. durable state 损坏。
6. MessageBus 重复结果。
7. recovery 执行两次。
8. claimed pending delivery 时崩溃。
9. PID reuse/identity mismatch 不误杀。

补齐终止 lifecycle Audit，但只记录脱敏 evidence。Graph/API 保持旧 schema 兼容。`tool_recovery` 的 source、target、
anchor 必须来自显式真实 ID；跨 Run/Trace/dangling/malformed 不构边。

### 阶段 5：真实 Runtime 轨迹与 8765 验收

最终轨迹必须经过真实：

```text
Gateway -> AgentLoop -> Runner -> Tool/Subagent/Executor -> Audit emitter/indexer -> API -> dist -> Chromium
```

允许使用确定性本地 provider 和故障注入工具，禁止依赖公网模型；但禁止直接 seed 目标 Audit lifecycle Event
作为最终通过证据。

在 Gateway 实际 audit root 中产生并保留：

- `tool-recovery-navigation`
- `child-cooperative-cancel`
- `child-force-killed`
- `child-termination-failed`
- `required-child-deadline-restart`

检查 audit root 必须使用运行配置解析结果，不能猜测 `runtime/audit` 或 `runtime/audit/v1`。

构建镜像并重建 `nanobot-gateway` 后，在 `http://localhost:8765/` 使用真实 Chromium 验收。不得公开
bootstrap secret/token。记录 commit、镜像 ID、dist hash、bundle、Graph/Events revision 和 trace ID。

同时修复：

- `ERR_BLOCKED_BY_RESPONSE.NotSameOrigin`
- Gateway WebSocket `assert not self.eof_sent`
- 宿主 dist、镜像 dist、实际 8765 bundle 不一致

console error、page error 和失败资源请求必须为 0。

## 7. 测试要求

先运行最接近的聚焦测试，再运行全量门禁。新增测试文件应加入相应命令。

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

最终必须运行：

```bash
pytest -q
ruff check nanobot/ tests/
cd webui && bun run test && bun run build
cd webui && bunx playwright test --project=chromium
```

此外必须运行真实重启 suite、真实 executor 进程树 suite、容器重建和部署后 8765 Chromium suite。

规则：

- 所有命令退出码为 0 才算通过。
- 本任务新增 Linux/容器关键测试不得 skip、xfail 或条件绕过。
- 不得延长为无界 timeout 掩盖死锁。
- 不得预选节点掩盖 Timeline 首次加载问题。
- 不得只断言按钮可点击；必须断言最终状态和选中行。
- 不得只运行 fixture/mock Graph。
- 不运行 `ruff format`。
- 遇到仓库既有无关失败，先复现并记录基线；未经授权不得改无关代码，但最终验收仍标记未通过。

## 8. 真实验收断言

### 8.1 Child 强杀

- cooperative 场景没有 SIGKILL。
- 不可协作 worker 在宽限后被 TERM/KILL，root exit、reap 和后代退出均有证据。
- 强杀失败时状态为 `lost/termination_failed`，不出现虚假 `force_killed`。
- required 强杀失败阻止主 Run 成功 final。
- optional 强杀失败不阻塞 final，但 anomaly 和通知只出现一次。

### 8.2 Deadline 重启

- 重启前后 `deadline_at` 字符串保持原值。
- remaining 只减少，不恢复默认预算。
- deadline 已过时不重新等待。
- owner Run、claim owner 和 continuation 身份正确。
- 重复恢复和重复消息只交付一次。

### 8.3 WebUI 双端定位

- tool recovery 与 sequence 路径不同，恢复边位于节点侧面。
- 点击边显示两端真实 Event ID 和 evidence count。
- 失败端 Timeline 行选中。
- 恢复端 Timeline 行选中，失败端取消选中。
- Node Inspector 原始 Event 导航可重复定位。
- 因果链零命中不影响恢复链路命中，且文案解释清楚。
- 显式点击 Payload 前网络请求数为 0。
- stale/not found/dangling/revision/limit 有准确提示，不崩溃、不猜测关系。

## 9. 提交、推送与 PR

- 每个可验证阶段建立中文提交并立即推送当前分支。
- 提交标题使用中文类型，例如 `测试（子 Agent）：固化强杀与恢复链路缺口`。
- 只暂存明确文件；禁止 `git add .`、`git add -A`。
- 不提交 secret、runtime Audit 数据、截图、test-results 或临时文件。
- 同一任务维护同一个 PR，中文更新“改动内容”“验证结果”“风险与注意事项”。
- 无 `gh` 或无权限时完成本地提交和推送，准确报告 PR 阻塞，不得伪造链接。
- 未经用户明确确认，绝不合并 `main`。

建议提交边界：

1. 测试：固化强杀、侧边恢复边和首次定位缺陷。
2. 修复（WebUI）：完成恢复边和双端定位。
3. 功能（子 Agent）：增加 Linux killable executor。
4. 修复（运行时）：接入 required、deadline、claim 和 guard。
5. 测试（恢复）：增加真实进程重启场景。
6. 修复（Audit）：补齐终止证据和兼容投影。
7. 修复（Gateway）：清理 WebSocket 与资源错误。
8. 测试（验收）：增加真实 Runtime 轨迹和 8765 Chromium 门禁。

## 10. 工作持续性

不要在以下节点提前停止：

- 只完成测试；
- 只完成 cooperative fail-closed；
- 只发送 SIGKILL 但没有 wait/reap 证据；
- 只通过 fixture/mock；
- 只通过临时 Gateway，未验证 8765；
- 只生成 seed Trace，未生成 Runtime Trace；
- 部分测试通过；
- 代码完成但容器仍运行旧 dist。

只有安全或架构事实导致无法继续时才报告 blocker。报告必须包含已尝试方案、源码证据、阻塞的具体阶段和
仍可执行的工作；不能因为工作量大或测试耗时就停在阶段 1。

## 11. 最终交付格式

最终报告必须用中文，逐项给出：

1. 分支名、所有新增提交编号与中文标题。
2. 每次推送结果、PR 链接与状态；无法确认则说明工具/权限原因。
3. killable executor backend、支持平台、identity、kill/reap/orphan 证据。
4. cooperative、force killed、termination failed 三类状态的真实测试结果。
5. 九个真实 deadline/restart 场景结果。
6. Graph/API source、target、anchor、revision 和 Payload 默认关闭证据。
7. WebUI 侧边恢复边、双端 Timeline 选中和原始 Event 导航结果。
8. pytest、ruff、WebUI test/build、全部 Chromium、真实重启和 8765 smoke 的完整命令与结果。
9. 五条真实 Runtime Trace 的 URL/trace ID、commit、镜像和 dist hash；不得包含认证 secret。
10. console/page/network error 统计。
11. 剩余风险、平台降级和最近回退提交。
12. 是否合并 `main`；未获确认必须写“等待用户确认后合并 main”。

最终判定只能是：

- **通过**：收口方案完成定义全部满足；或
- **未通过**：列出失败门禁和证据。

不得使用“基本通过”“核心通过”“测试看起来通过”代替明确判定。
