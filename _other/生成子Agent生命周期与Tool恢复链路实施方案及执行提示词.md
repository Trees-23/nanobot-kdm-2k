# 生成子 Agent 生命周期与 Tool 恢复链路实施方案及执行提示词

把下面整段内容作为一个完整任务交给负责调研、生成实施方案和生成执行提示词的 AI。

---

你正在 nanobot 仓库中为“子 Agent 生命周期收口”和“Tool 纠错恢复链路”准备下一阶段开发资料。

仓库根目录：

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 一、任务结果

你必须先阅读现有修复计划和真实代码，再生成两个独立 Markdown 文件：

1. 一份可以交给开发者评审和拆分任务的实施方案；
2. 一份可以直接交给另一个实施 AI 执行的提示词，该提示词必须引用实施方案，而不是重新发明一套目标。

不得停留在“建议进一步调研”。如果信息不足，必须通过读取仓库、Git 历史和测试补齐证据；确实无法决定的内容放入“实施前待确认项”，并说明不确认会阻塞哪个阶段。

## 二、输出位置与版本

不要把生成文件裸放在 `_other` 根目录。先检查以下目录和版本：

```text
_other/子Agent生命周期与Tool恢复链路/
```

只把严格匹配 `^V[1-9][0-9]*$` 的目录视为版本。选择“现有最大版本号加一”的目录；例如已有 `V1`、`V3` 时必须创建 `V4`，不得回填 `V2`。若没有版本目录，从 `V1` 开始。

```text
_other/子Agent生命周期与Tool恢复链路/V1/
_other/子Agent生命周期与Tool恢复链路/V2/
...
```

不得覆盖已有版本。候选版本目录即使只包含部分文件也视为已占用，必须继续递增。

在选定版本目录中只创建以下两个文件：

```text
实施方案.md
执行实施方案提示词.md
```

文件名和正文使用中文；代码标识符、路径、协议名和命令保留英文。不要使用用户已明确反感的“`[] a.`”逐行格式，使用正常的 Markdown 标题、段落、表格和项目符号。

## 三、上下文读取顺序

先完整阅读：

```text
AGENTS.md
.agent/design.md
.agent/security.md
.agent/gotchas.md
_other/子Agent生命周期与Tool恢复链路修复计划-2026-08-01.md
```

然后以当前代码为唯一事实来源，核验：

```text
nanobot/agent/tools/spawn.py
nanobot/agent/tools/await_subagents.py
nanobot/agent/subagent.py
nanobot/agent/tools/long_task.py
nanobot/session/goal_orchestration.py
nanobot/session/goal_state.py
nanobot/session/turn_continuation.py
nanobot/agent/runner.py
nanobot/agent/loop.py
nanobot/templates/agent/goal_runtime.md
nanobot/audit/context.py
nanobot/audit/hook.py
nanobot/audit/schema.py
nanobot/audit/graph.py
nanobot/audit/graph_types.py
nanobot/webui/audit_api.py
webui/src/lib/audit-types.ts
webui/src/components/traces/TraceGraph.tsx
webui/src/components/traces/TraceWorkbench.tsx
webui/src/components/traces/TraceNodeInspector.tsx
webui/src/components/traces/TraceTimeline.tsx
webui/src/hooks/useAuditTimeline.ts
webui/src/tests/trace-graph.test.tsx
webui/src/tests/audit-trace-ux.test.tsx
tests/agent/tools/test_goal_orchestration.py
tests/agent/tools/test_subagent_tools.py
tests/agent/test_runner_injections.py
tests/agent/test_loop_runner_integration.py
tests/agent/test_subagent_lifecycle.py
tests/agent/test_runner_audit.py
tests/audit/test_graph_builder.py
tests/audit/test_webui_api.py
tests/audit/test_end_to_end.py
```

同时执行只读检查：

```bash
git status --short --branch
git log -12 --oneline --decorate
git diff --stat
git remote -v
```

目标基线固定为当前 fork 的 `origin/main`，PR 目标为 `Trees-23/nanobot-kdm-2k:main`。使用只读的 `gh pr view`/`gh pr list` 或托管平台 API 核验当前分支的现有 PR；若工具缺失、未认证或网络不可用，将 PR 状态记录为“无法确认”，不得根据本地分支名猜测。

必须识别工作区已有修改、当前分支、目标基线和现有 PR；不得把用户已有修改当成待实现内容，也不得覆盖、还原、stash 或清理它们。

## 四、生成阶段的硬边界

本次生成阶段只允许：

- 阅读代码、测试、文档和 Git 历史；
- 运行不改变仓库状态的检查；
- 按需创建一个版本目录及其中两个 Markdown 输出文件；
- 按 `AGENTS.md` 完成这两个文档的分支、提交、推送和同一 PR 维护。

本次生成阶段禁止：

- 修改产品代码、测试代码、配置、Audit JSONL、Payload、catalog 或 WebUI dist；
- 使用 worktree；
- 重启 gateway、WebUI 或容器；
- 虚构不存在的接口、事件类型、API 或工具参数；
- 用 basename、同名 Tool、时间相邻或前端字符串推断 Tool 恢复；
- 把 `tool_recovery` 写入 `caused_by_event_id`；
- 把真实用户目录、凭据、Token、完整 Tool 参数或完整 Payload 写入新文档。

Git 交付必须遵循 `AGENTS.md`：不在 `main` 开发，默认在当前仓库目录使用 `git switch`；只暂存上述两个明确文件，中文提交并推送当前任务分支，维护同一个 PR；未经用户明确确认不得合并 `main`。除版本目录和两个目标文件外，仓库内不得产生缓存、截图、临时报告或其他新文件；临时数据必须放在仓库外并在结束前清理。

## 五、必须保留的产品语义

实施方案必须以这些约束为基线，不得悄悄改义。

### 5.1 Child 生命周期

- `spawn` 是后台启动；`required=true` 目前表示 Goal 完成义务，不等于 Tool 调用内部同步等待；
- `await_subagents` 是当前 Goal 的显式 barrier，等待所选 `task_ids` 或 `task_group` 的全部 Child 进入终态，单次等待有上限；
- Goal 完成有 `required_gate`，失败、取消、超时、丢失或仍运行的 required obligation 不得被标记为完成；
- 本轮目标是为 `required=true` 增加主 Run completion guard，防止最终答复早于必需 Child 收口；`required=false` 的后台语义不能被无意破坏；
- 如果取消不响应，不能同时假装“有界返回”和“Child 已终止”。方案必须明确二次取消宽限、可杀执行器或 fail-closed 的取舍；
- MessageBus 结果和 durable Goal 状态必须有原子 claim/去重方案，不能重复写历史或重复触发 Continuation；
- Goal internal continuation、Checkpoint 恢复、Child-result Continuation 和 replacement Child 必须分别说明 owner Run、deadline 和用户可见性；
- 流式最终回答不能在 completion guard 之前发出不可撤回的“完成”内容，必须设计缓冲或明确关闭最终回答 streaming 的策略。

### 5.2 Tool 恢复与因果

- `recovery_of_tool_call_ids` 由运行时根据规范资源身份和已记录失败计算，不是 LLM 输出字段；
- Graph 新增的 `tool_recovery` 边必须由该显式字段驱动；
- 边的 source 是失败 Tool semantic node，target 是成功恢复 Tool semantic node，anchor 指向两端 `tool_finished` Event；
- 三路径样本中，`runtime/config.json` 失败、`unrelated/config.json` 成功、逻辑纠错路径成功时，只允许确定性关联的两端生成边；
- `caused_by_event_id` 继续表达明确直接因果；本任务不凭事后资源匹配伪造 LLM 决策 Event；
- 恢复链路必须支持 Graph 聚焦、命中数、空状态、边点击和失败/恢复两端原始 Event 导航；
- Graph、Events API 和前端不得泄露完整 Payload、resource fingerprint、敏感绝对路径或凭据；
- 旧 Event 缺字段、旧 Graph schema、dangling recovery ID、跨 Trace/不合法跨 Run 引用必须有兼容和降级策略。

## 六、`实施方案.md` 必须包含

实施方案必须引用到函数、类、字段或测试名称，不得只罗列文件名。至少包含以下章节：

1. **执行摘要**：目标、非目标、最高风险和建议交付顺序。
2. **现状证据**：区分“源码已证实”“已有测试证明”“用户/评测已复现”“尚待浏览器复现”。
3. **运行时语义**：Run、Iteration、MessageBus、AgentLoop、AgentRunner、Child Run、Goal、Continuation 的状态转移图。
4. **Child 生命周期方案**：提示词职责、`await_subagents` 触发条件、completion guard 入口、owner Run、deadline、取消和不可协作任务处理。
5. **MessageBus 与结果一致性**：pending queue、durable orchestration、result claim、去重、重启、晚到消息和 Continuation 抑制。
6. **Audit wire contract**：`tool_recovery` 的类型、边、anchor、schema version、Graph builder version、旧数据兼容。
7. **后端改动清单**：逐项列文件/符号、输入输出、锁边界、错误和回退。
8. **前端改动清单**：TypeScript contract、边样式、恢复聚焦、关系检查器、Event 导航、Payload 不自动加载和分页上限提示。
9. **安全与隐私**：脱敏、限长、合成 fixture、Graph/Events/Payload 边界和日志禁泄漏规则。
10. **分阶段实施**：每阶段必须有目标、前置依赖、文件/符号、具体改动、测试命令、验收断言、风险和回退点。
11. **测试矩阵**：至少覆盖 required Child 乱序完成、失败/替代、超时、取消不响应、重启、重复回流、streaming、max iterations、三路径 recovery、dangling ID、collapse、前端双端导航和真实 Chromium。
12. **实施前待确认项**：deadline 起点/继承、超时状态、不可协作任务策略、`required=false` 交付策略、auto-barrier 是否写 runtime decision、旧客户端 schema 兼容和 feature flag。
13. **完成定义**：什么证据齐全才可以宣称完成，未完成项如何报告。

禁止把不确定的方案写成已批准契约。对于无法在当前仓库确定的内容，必须标记“待确认”，并写出默认建议和反对代价。

## 七、`执行实施方案提示词.md` 必须包含

第二个文件必须是一份可以直接复制给实施 AI 的完整任务提示词，不是实施方案摘要。它必须：

1. 引用同版本目录中的 `实施方案.md`，要求实施 AI 先读计划再读实际代码。
2. 要求实施 AI 先检查 `AGENTS.md`、分支、工作区、远端和现有 PR；默认使用 `git switch`，不使用 worktree，不在 `main` 开发。
3. 明确“只实现已确认契约；待确认项没有决定前不得暗自发明不可逆 API”。
4. 要求先建立/校验合成 fixture，再改运行时、Audit Graph、API 和 WebUI；不得改写历史权威 Audit 数据。
5. 明确提示词/Tool 描述与运行时 guard 的职责边界：提示提高正确率，硬门负责不可绕过的安全约束。
6. 要求 completion guard 覆盖正常 final、Tool error、max iterations、Goal continuation、streaming、stop、shutdown 和异常路径。
7. 要求 `required=false` 回归测试，避免无意把后台任务全部变成同步任务。
8. 要求 `tool_recovery` 只由显式 `recovery_of_tool_call_ids` 构边，三路径样本必须证明 unrelated basename 不误连，`caused_by_event_id` 不被污染。
9. 要求前端提供恢复链命中数、空状态、明确图例、关系检查器和失败/恢复双端 Event 定位；Payload 只能显式加载。
10. 要求运行与改动范围匹配的 pytest、ruff、`bun run test`、必要的 `bun run build` 和真实 Chromium Playwright；测试未通过不得宣称完成。
11. 要求提交标题、PR 标题和正文使用中文；每个可验证工作单元提交并推送当前任务分支；未经用户明确确认不得合并 `main`。
12. 要求最终报告包含：分支、提交、推送、PR、测试、未完成项、风险和未合并 main 的事实。

执行提示词中必须写明异常处理：如果遇到受保护的用户改动、缺少决定、测试环境门禁、浏览器缺失、远端认证或权限问题，先保留已完成的本地验证和文档，准确报告阻塞，不得删除、回滚或伪造通过。

## 八、生成前的质量检查

生成两个文件后，重新阅读它们并检查：

- 实施方案与执行提示词的目标、文件路径、字段名、测试命令一致；
- 执行提示词没有引入实施方案中不存在的接口或未批准契约；
- 阶段顺序满足“数据语义先于 UI”；
- `tool_recovery`、`caused_by_event_id`、`required`、`await_subagents`、completion guard 的职责没有混淆；
- 所有 fixture 路径为合成/脱敏路径；
- 没有凭据、真实绝对用户路径、完整 Payload 或无界原始异常；
- Markdown 代码围栏闭合，路径和命令合理；
- 版本目录没有覆盖旧文件，输出目录中只有要求的两个新文件；
- 生成阶段没有修改产品代码、测试、配置或 Audit 数据；Git 变更只包含两个文档及其正常提交/推送/PR 维护。

完成后只报告：

- 两个输出文件的绝对路径；
- 选定的版本目录；
- 实施方案阶段数；
- 最高风险和待确认决策数量；
- 分支、中文提交、推送结果、PR 链接和未合并 main 的事实；
- 未执行产品代码修改、测试代码修改、Audit 数据修改和服务重启的事实。

若任务因受保护改动、权限、认证、网络或缺少必需文件无法完成，允许不生成或只生成尚可安全完成的部分，但必须额外报告：准确阻塞原因、已生成文件、未生成文件、本地提交状态和恢复所需条件。不得为了满足固定报告格式而隐瞒失败或伪造产物。
