# 执行“Tool 错误可观测性与通用恢复实施方案”的任务提示词

你正在 nanobot fork 仓库中实施一项跨 Runner、Tool、Audit、Graph 和 WebUI 的通用能力。先阅读并遵守
仓库根目录 `AGENTS.md`，再完整阅读：

```text
_other/Tool错误可观测性与通用恢复实施方案.md
```

实施方案是本任务的目标和语义来源；当前代码是实现事实来源。不要把任务缩减为 `list_dir` 或
`read_file` 的个别补丁。

## 最终目标

完成以下结果：

1. 所有新产生的 Tool 异常终态都有安全、非空、可供前端展示的错误信息。
2. validation、ToolResult、exception、timeout、cancel、policy、provider、runtime 错误走统一归一化。
3. metadata-only 不记录 Payload，但仍能在失败 Tool 节点看到实际安全错误原因。
4. 所有异常 Tool 都有明确恢复状态。
5. Tool retry、continuation、recovery 分开记录、建边和展示。
6. 只有确定性证据允许标记 recovered；证据不足必须是 continued 或 unresolved。
7. 内置 Tool、MCP/插件、子 Agent、Goal、exec session 和安全阻断都有测试覆盖或明确 fallback。

## 开始前

1. 读取所有适用的 `AGENTS.md`，检查分支、工作区、远端和 `origin/main`。
2. 不在 `main`/`master` 开发；在当前仓库目录创建 ASCII 任务分支，不创建 worktree。
3. 用户改动全部受保护。不得 stash、reset、clean、覆盖或提交无关改动。
4. 阅读方案列出的代码和测试，确认符号仍存在；如基线已变化，先更新方案中的事实判断，不暗自绕过。
5. 检查当前任务是否已有 PR；同一任务只维护一个 PR。

## 硬性语义

### 通用错误

- 在 Runner 的统一边界实现 `NormalizedToolFailure` 或等价不可变结构和纯归一化函数。
- 不要求 191 处 ToolResult.error 全部手写相同 fallback；通用 fallback 必须集中实现。
- 工具提供的精确 error type/code 必须保留，缺失时自动补稳定默认值。
- 新异常终态必须包含 `error_message`、`error_summary`、`error_type`、`error_code`、
  `error_source`、`retryability` 或经评审确认的等价字段。
- 增加 retry hint、经过 registry/wrapper、发生 fail-on-tool-error 时不得丢失结构化字段。
- 错误正文必须先脱敏、标准化和截断，再写入 Audit Event。Graph 不得包含完整参数、结果或堆栈。
- metadata-only 不能以 Payload 被关闭为理由丢失错误原因。
- 旧 Event 缺字段继续可读，UI 显示“历史版本未记录错误详情”。

### 通用恢复

- 区分 `tool_retry`、`tool_continuation`、`tool_recovery`。
- 所有异常 Tool 节点必须是 pending、recovered、continued、unrecovered、unresolved 之一。
- Graph 只消费 Runtime Event 中的显式关系 ID，不在后端 Graph 或前端重算 Tool 语义。
- 不按时间相邻、同名 Tool、basename、错误文本或一次后续成功猜测 recovered。
- 写操作必须有状态/副作用验证；message 等可能重复执行的 Tool 必须考虑幂等和 receipt。
- exec/write_stdin 使用同一 session/process identity 表达 continuation；不能把轮询动作简单写成恢复。
- spawn/await_subagents 使用 task ID、child Run、replacement、claim 证据，并遵守已有生命周期契约。
- policy blocked 默认 non-retryable，不创建 recovery。
- MCP/第三方插件没有证据协议时使用 unresolved fallback，不能丢节点或伪造关系。

## 实施顺序

严格按以下顺序推进；数据契约先于 UI：

### 阶段 0：测试和契约

1. 建立失败入口参数化矩阵：validation、ToolResult、exception、timeout、cancel、policy、legacy/plugin。
2. 建立 Tool inventory，列出每个注册 Tool 的错误来源、operation evidence 策略和 fallback。
3. 增加真实回归用例，复现 `list_dir` 错误文本存在于会话、Audit 字段为空的问题。
4. 增加 secret、超长文本、控制字符和旧 Event fixture。
5. 先让测试以预期原因失败，再改实现。

### 阶段 1：统一错误归一化

1. 实现统一模型和纯函数。
2. 接入 Runner 所有 Tool 失败出口，不漏 prepare error 和异常出口。
3. 保留 ToolResult 元数据；修复 registry、legacy wrapper 或 hint 拼接中的字段丢失。
4. 为常见内置工具补精确错误码，但不能依赖逐工具补码保证前端有内容。

### 阶段 2：Audit 和兼容

1. 扩展 ToolAuditOutcome、tool_finished schema、Hook、Graph summary 和 TypeScript contract。
2. 新异常 Event 执行完整诊断不变量；wire schema 保持旧 Event 兼容。
3. metadata-only 断言 Event 有安全错误、Payload 不存在；full 模式保持显式 Payload 边界。
4. 提升 Graph builder version，验证 ETag 失效和旧客户端兼容。

### 阶段 3：恢复证据

1. 实现 Tool operation evidence 适配器协议和默认 unresolved fallback。
2. 先覆盖 filesystem、exec/write_stdin、spawn/await_subagents/Goal。
3. 再覆盖 web、message、cron、MCP/CLI/plugin、image generation。
4. Runtime 写入 retry/continuation/recovery 显式 ID 和 evidence kind。
5. 覆盖不相关成功、多失败一恢复、一失败多次重试、写操作部分副作用、跨 Run 拒绝和 dangling ID。

### 阶段 4：Graph/API/WebUI

1. Graph 增加 Tool retry/continuation/recovery 边和节点恢复状态。
2. API 只返回安全诊断与关系元数据；Payload 继续独立认证、显式加载、no-store。
3. Inspector 首屏显示错误信息、类型、错误码、来源、可重试性、影响和恢复状态。
4. 关系聚焦与检查器区分三种关系，可定位两端 Event，不自动加载 Payload。
5. 处理旧数据、长文本、折叠、过滤、分页、dangling、桌面和移动端布局。

### 阶段 5：综合评测

先阅读：

```text
_other/评测/评测资产组织规范.md
```

建立 `_other/评测/Tool错误与恢复/说明.md` 和 V1 资产，不把试卷裸放在评测根目录。试卷必须覆盖：

- 没有专用错误码的通用 fallback；
- 精确结构化错误；
- exception、timeout、cancel、policy；
- retry、continuation、recovered、continued、unrecovered、unresolved；
- metadata-only 和 full；
- 不相关成功不误连；
- WebUI 桌面和移动端错误详情、关系边、双端 Event 定位；
- Payload 不自动请求和 secret 不泄露。

使用真实 Gateway 和真实浏览器执行一次综合考试并归档运行记录。不要修改历史权威运行记录。

## 验证要求

Python 至少运行与改动对应的聚焦测试：

```bash
pytest tests/agent/test_runner_errors.py tests/agent/test_runner_audit.py \
  tests/tools/test_tool_registry.py tests/audit/test_diagnostics.py \
  tests/audit/test_schema.py tests/audit/test_graph_builder.py \
  tests/audit/test_webui_api.py -v
```

修改 `runner.py` 必须补充或运行聚焦集成测试。Python 改动对涉及路径运行 `ruff check`，不要运行
`ruff format`。完成聚焦验证后运行完整 pytest，并如实披露任何失败。

WebUI 至少运行：

```bash
cd webui
bun run test -- src/tests/audit-trace-ux.test.tsx src/tests/trace-graph.test.tsx
bun run build
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

真实浏览器至少覆盖 1440x900 和 390x844，检查长错误换行、节点详情、三种关系、双端定位、旧数据、
metadata-only、Payload 请求数和 console/page errors。

最终阶段运行完整 WebUI 测试。不得把未执行的命令写成通过。

## 安全与禁止项

- 不在 Event/Graph 自动持久化完整 Tool 参数、文件内容、stdout/stderr、堆栈或 secret。
- 不通过放宽 redaction、改成 full mode 或自动请求 Payload 来解决错误不可见。
- 不把 retry hint 当作实际错误正文展示。
- 不修改 `caused_by_event_id` 伪造恢复因果。
- 不改写历史 Audit、运行记录或截图制造通过证据。
- 不以单个 `read_file/list_dir` 用例替代全工具 inventory 和通用 fallback。
- 不因“全覆盖”要求而把证据不足的场景错误标记为 recovered。
- 不运行破坏性 Git 命令，不强推，不提交无关生成物和凭据。

## 提交与 PR

按方案建议的工作单元创建中文提交。每个单元先检查差异、运行匹配验证，只暂存明确路径；提交后立即
推送当前任务分支。第一个可审查提交后创建草稿 PR，后续更新同一个 PR。PR 标题和正文使用中文，正文
持续更新“改动内容”“验证结果”“风险与注意事项”。

所有工作完成并验证后将 PR 标记为可审查，但未经用户针对该 PR 明确确认，不得合并 `main`。

## 最终报告

最终报告必须包含：

1. 分支名；
2. 提交编号和中文标题；
3. 推送结果；
4. PR 链接、状态和目标分支；
5. 实际执行的 Python、ruff、WebUI、build、Chromium 和综合评测结果；
6. Tool inventory 的覆盖结论和明确 fallback；
7. 未完成项、已知风险和回退状态；
8. 明确写出“等待用户确认后合并 main”。
