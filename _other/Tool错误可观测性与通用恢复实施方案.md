# Tool 错误可观测性与通用恢复实施方案

状态：实施前评审稿

基线：`origin/main`，主分支里程碑 `c9925dcd`；本方案依据 2026-08-02 对真实 Trace
`019fc14e-2517-79f9-be18-566cac4a41c4`、当前 Runner、ToolResult、Audit Event、Graph 和
WebUI 的只读调研编写。

## 1. 执行摘要

### 1.1 问题

当前 Tool 失败的实际错误文本已经返回给 Runner 和会话活动流，但 Audit 只有少量工具分支会写
`error_type`、`error_code` 和 `error_summary`。真实 Trace 中失败的 `list_dir` 在会话记录里明确为
`Directory not found`，对应 `tool_finished` 却只有 `status=error`，其余诊断字段全部为 `null`。

恢复关系也只覆盖 `read_file` 的有限路径纠正规则。其他 Tool 即使通过重试、换路径、继续进程、
重新连接或状态校验完成了业务恢复，Audit 仍只能显示“失败后继续”，无法形成有证据的恢复关系。

### 1.2 目标

1. 所有新产生的 Tool 异常终态都有可供前端显示的安全错误信息，不再依赖单个工具手工填写字段。
2. 参数校验、ToolResult、Python 异常、超时、取消、策略阻断、MCP/插件错误走同一归一化契约。
3. `metadata_only` 模式也保留有界、脱敏的错误诊断；完整 Payload 仍然只能显式加载。
4. 所有 Tool 失败都有明确的恢复状态，即使结论只能是 `continued` 或 `unresolved`。
5. 重试、继续、确定性恢复分开建模；只有存在可验证证据时才显示 `recovered`。
6. 建立覆盖内置 Tool、MCP/插件、子 Agent、进程会话和安全边界的自动化测试矩阵。

### 1.3 非目标

- 不把“后面出现一个成功 Tool”一律判定为原失败已恢复。
- 不按相邻时间、同名 Tool、basename 或前端字符串猜测恢复关系。
- 不在 Graph 自动暴露完整 Tool 参数、完整 stdout/stderr、异常堆栈、文件内容或凭据。
- 不把策略阻断伪装成可重试错误。
- 不重写已有 Audit JSONL、Payload、catalog、运行记录或历史 Trace。
- 不要求第三方 Tool 在首个版本立即实现确定性恢复；没有证据时必须诚实降级。

### 1.4 核心结论

“全部失败都显示错误原因”可以在统一 Runner 边界完整实现。“全部失败都有恢复结论”也可以实现，
但结论必须包含 `unresolved`；无法可靠证明的场景不能强行标记 `recovered`。

## 2. 已确认的现状

### 2.1 错误链路

`AgentRunner._run_tool_inner()` 已经汇聚以下失败入口：

1. 重复外部查询限制；
2. Tool 不存在、参数不是对象、schema 校验失败；
3. Tool 执行抛出异常；
4. Tool 返回 `ToolResult.error()`；
5. workspace、SSRF 等安全边界；
6. fail-on-tool-error 致命出口；
7. 取消及外围 timeout。

当前 `ToolAuditOutcome` 只有 `error_kind/error_type/error_code`，没有通用 `error_message`。
仓库内约有 191 处 `ToolResult.error()`，只有 `read_file` 和 `web_search` 的少数分支显式填写了
结构化类型/错误码。

### 2.2 Audit 与前端

- `tool_finished` schema 已有可选 `error_type/error_code/error_summary`，但允许异常终态全部为空。
- `safe_error_summary()` 在类型和错误码均为空时直接返回 `None`。
- `safe_tool_input()` 只为 `read_file` 和 `web_search` 提供有限策略。
- `metadata_only` 会删除 Tool output Payload，因此不能依靠 Payload 保证错误可见。
- `TraceNodeInspector` 只有在 `error_summary` 非空时才显示“根因”。
- 旧 Trace 缺字段属于合法历史数据，升级不能使旧数据无法读取。

### 2.3 恢复链路

- Runtime 只为具有 `resource_key` 的失败保留待恢复记录。
- 当前资源身份和 correction key 只覆盖 `read_file`。
- Graph 只投影后续成功事件显式声明的 `recovery_of_tool_call_ids`。
- 该保守策略是正确的；问题是 Runtime 的证据生产范围太窄，而不是 Graph 应该放宽猜测。

## 3. 通用错误契约

### 3.1 统一模型

新增内部不可变结构 `NormalizedToolFailure`，建议放在 Runner/Hook 均可依赖、但不依赖 Audit 的模块中：

```python
@dataclass(frozen=True, slots=True)
class NormalizedToolFailure:
    message: str
    summary: str
    error_type: str
    error_code: str
    source: Literal[
        "validation", "tool_result", "exception", "timeout",
        "cancelled", "policy", "provider", "runtime"
    ]
    retryability: Literal["retryable", "non_retryable", "unknown"]
```

字段规则：

| 字段 | 新异常事件要求 | 规则 |
|---|---|---|
| `error_message` | 必填 | 实际失败文本的脱敏、有界版本，最多 1024 字符 |
| `error_summary` | 必填 | 单行摘要，最多 160 字符 |
| `error_type` | 必填 | 显式类型优先；无类型时使用稳定默认值 `ToolError` |
| `error_code` | 必填 | 显式错误码优先；无错误码时按来源使用稳定通用码 |
| `error_source` | 必填 | 表明错误来自校验、返回值、异常、策略等哪个边界 |
| `retryability` | 必填 | 不从错误文案盲猜；未知时明确记录 `unknown` |

默认错误码至少包括：

```text
tool_not_found
invalid_tool_arguments
tool_error
tool_exception
tool_timeout
tool_cancelled
policy_blocked
provider_error
runtime_error
```

工具仍可提供更精确的稳定码，例如 `file_not_found`、`directory_not_found`、
`exec_session_not_found`。没有精确码不再导致诊断空白。

### 3.2 单一归一化入口

实现一个纯函数 `normalize_tool_failure(...)`，由 Runner 在确定最终 Tool 状态后调用一次。输入包含：

- Tool 名称；
- 终态 status；
- `ToolResult` 或普通结果；
- 捕获的异常；
- prepare/validation 错误；
- policy 分类；
- timeout/provider 元数据。

禁止在每个工具里复制脱敏、截断和 fallback 逻辑。工具只负责在能够稳定分类时补充精确元数据。

必须保留 `ToolResult.with_content()` 的结构化字段，增加 retry hint 时不得把 ToolResult 降为普通字符串。
`ToolRegistry.execute()`、动态插件和 legacy wrapper 也必须保留已有的错误类型/错误码。

### 3.3 文本安全

错误不应因安全顾虑完全消失，也不能把未经处理的原文直接放入 Graph：

1. 先从面向模型的 retry hint 中分离实际错误正文；
2. 使用 `AuditRedactor` 处理结构化 secret key、Bearer、常见 API key 和用户配置的附加规则；
3. 统一换行、控制字符和最大长度；
4. 摘要只包含必要原因，不包含完整参数、文件内容和堆栈；
5. 脱敏失败时写稳定占位 `Diagnostic summary unavailable`，但仍填写类型、错误码和来源；
6. 安全策略错误允许显示被阻断的原因，不显示可被利用的内部解析细节。

### 3.4 Event 与 Payload

对 `tool_finished` 增加 additive 字段：

```text
error_message
error_source
retryability
```

保留现有字段。为兼容旧 Event，Pydantic wire schema 仍允许缺失；但 Runtime 对新产生的
`error/timeout/cancelled/blocked` 终态执行不变量：归一化字段必须齐全。Graph 遇到旧数据时显示
“历史版本未记录错误详情”，而不是伪造原因。

`full` 模式的 Tool output Payload 可以继续保存经过现有红线处理的完整 result；
`metadata_only` 不保存 Payload，但 Event 中上述有界诊断必须存在。Graph API 不返回 Payload content。

## 4. 通用恢复模型

### 4.1 分开表达三种关系

建议在 Event/Graph 中明确区分：

| 关系 | 含义 | 是否代表已恢复 |
|---|---|---|
| `tool_retry` | 后续调用明确重试前一失败操作 | 否 |
| `tool_continuation` | 后续调用继续同一进程、会话、任务或分页游标 | 否 |
| `tool_recovery` | 后续成功并有证据证明前一失败影响已解决 | 是 |

不要复用 Provider 的 `retry_scheduled/retry_of` 事件冒充 Tool 重试。新增 Tool 关系必须包含两端
`tool_finished` Event anchor，且不能进入严格 causal focus。

### 4.2 每个失败都必须有恢复状态

沿用并收紧现有状态：

```text
pending       Run 尚未结束，仍可能出现证据
recovered     已有确定性恢复证据
continued     Run 已继续，但没有证明失败影响已解决
unrecovered   失败导致 Run 失败或明确终止
unresolved    数据不足、第三方未知语义或历史字段缺失
```

所有异常 Tool 节点必须显示其中一个状态。`continued` 和 `unresolved` 是完整覆盖的一部分，不是失败。

### 4.3 运行时证据协议

扩展当前 `SafeToolInput` 为通用但保守的 `ToolOperationEvidence`：

```python
@dataclass(frozen=True, slots=True)
class ToolOperationEvidence:
    operation_kind: str
    safe_input_summary: str | None
    resource_keys: tuple[str, ...]
    continuation_key: str | None
    retry_key: str | None
    verification_kind: str | None
```

工具或适配器可声明证据；默认适配器只提供进程内精确重试比较，不持久化原始参数。第三方工具未提供
证据时仍记录错误和 `unresolved/continued`，不能因为缺适配器而丢失失败节点。

恢复声明至少记录：

```text
retry_of_tool_call_ids
continuation_of_tool_call_ids
recovery_of_tool_call_ids
recovery_evidence_kind
```

Graph 只投影显式 ID，不重新计算 Tool 语义。

### 4.4 工具族覆盖策略

| 工具族 | 身份/证据 | 允许的结论 |
|---|---|---|
| `read_file/list_dir/find_files/grep` | 规范化 workspace 路径、查询范围、只读参数 | 精确重试成功；有界路径纠正成功 |
| `write_file/edit_file/apply_patch` | 目标路径、before/after hash、预期替换数 | 仅状态校验成功后 recovered；否则 continued |
| `exec` | exec session/process identity、cwd、退出码 | 启动/终止结果；不能仅凭另一命令成功恢复 |
| `write_stdin/list_exec_sessions` | session identity、轮询/EOF/terminate 动作 | continuation；确认同一进程成功终态后可恢复 timeout/pending |
| `web_search/web_fetch` | provider、规范化请求身份、HTTP/timeout 类别 | retry；同请求成功可标 operational recovery，不代表外部内容正确 |
| `message` | channel/target 的安全身份、provider receipt、去重键 | receipt 确认后恢复；无幂等键时禁止自动重发判定 |
| `spawn/await_subagents` | task ID、child Run ID、replacement/claim | continuation/replacement/recovered，服从子 Agent 生命周期契约 |
| Goal 工具 | goal ID/version/action | 版本化状态转移；拒绝结果不能由任意后续成功覆盖 |
| cron | job ID、计划版本、持久化读取验证 | 创建/更新后读取验证才 recovered |
| MCP/CLI/插件 | 插件显式 operation/receipt，或进程内精确 retry | 无协议时 unresolved；不得按字符串推断 |
| image generation | provider request identity、产物引用 | 同请求重试；产物可读验证后 operational recovery |
| 安全策略阻断 | policy name/version/boundary | non-retryable、unrecovered/continued，不创建 recovery |

### 4.5 跨 Run 与重启

首版默认只允许同 Trace、同 Run 的普通 Tool 恢复。以下情况使用已有专属关系而不是强塞进 Tool recovery：

- 子 Agent replacement：task/child Run 关系；
- Checkpoint 恢复：checkpoint/restored relation；
- Gateway 重启后的 Run：`resumed_from_run_id`；
- durable Goal 接管：goal version/owner relation。

确需跨 Run 的同一 Tool operation 时，必须由 durable、非敏感的 operation ID 显式声明，并在独立协议
版本中实施。首版不根据哈希碰撞、时间或相同错误文本跨 Run 连线。

## 5. Graph、API 与 WebUI

### 5.1 Graph

- 提升 `GRAPH_BUILDER_VERSION`。
- Tool 节点摘要增加 `error_message/error_source/retryability/recovery_evidence_kind`。
- 增加 `tool_retry` 和 `tool_continuation` 边；保留 `tool_recovery`。
- 边只消费 Event 中显式 ID，验证同 Trace、合法 Run 边界、source 异常、target 终态。
- malformed/dangling/旧字段不得令 Graph 构建失败，应降为节点 `unresolved` 或 integrity warning。
- 折叠、过滤和 focus 不得吞掉异常节点或把恢复边变成因果边。

### 5.2 API

- Graph API 返回安全诊断和关系元数据，不返回完整 arguments/result。
- Events API 保持原始 Event 导航能力。
- Payload API 继续独立认证、显式点击、`no-store`，并保留 metadata-only 不可用状态。
- ETag 必须随 builder version 变化；旧前端遇到未知 additive 字段不崩溃。

### 5.3 WebUI

失败 Tool 节点详情首屏必须显示：

1. 实际安全错误信息；
2. 错误类型、错误码、来源和可重试性；
3. 对 Run 的影响；
4. 恢复状态；
5. 有证据时显示恢复方式和关系入口。

旧节点没有详情时显示“历史版本未记录错误详情”。禁止只显示红色“失败”而不给原因。

关系聚焦分别展示 Tool 重试、继续和恢复。边检查器显示两端 Tool、状态、Event ID、证据类型，并可定位
两端 Event。Payload 不自动加载。桌面和移动端必须保证内容不重叠、长错误文本可换行。

## 6. 实施阶段

### 阶段 0：契约与失败矩阵

- 冻结字段、长度、枚举和兼容规则。
- 建立合成 Tool，覆盖 validation、ToolResult、exception、timeout、cancel、policy 和 legacy/plugin。
- 先写失败测试，证明当前 `list_dir` 等路径诊断为空。
- 建立 Tool inventory，要求每个注册工具明确选择证据适配器或 `unresolved` fallback。

### 阶段 1：统一错误归一化

- 实现 `NormalizedToolFailure` 和纯函数。
- 将 Runner 所有失败出口接入同一函数。
- 保留 ToolResult 元数据和 retry hint 分离。
- 为常见内置错误补精确码，但通用 fallback 必须先成立。

### 阶段 2：Audit 契约与兼容

- 扩展 Hook、schema、Graph summary 和 TypeScript types。
- metadata-only 验证错误信息存在、Payload 不存在。
- 验证 secret、超长文本、控制字符、堆栈和脱敏器失败。
- 旧 fixture 缺字段仍能读取和建图。

### 阶段 3：恢复证据协议

- 引入 Tool operation evidence 适配器接口和默认 fallback。
- 先覆盖 filesystem、exec session、subagent/goal 三类关键路径。
- 再覆盖 web、message、cron、MCP/CLI/plugin、image generation。
- 写入 retry/continuation/recovery 显式 ID 和 evidence kind。
- 任何不能证明的场景固定降级，不允许启发式补边。

### 阶段 4：Graph/API/WebUI

- 增加新边、状态、Inspector、关系检查器和兼容提示。
- 保留 Payload 安全边界和 Event 有界定位。
- 覆盖 collapse、filter、多边、dangling、移动端和长文本。

### 阶段 5：综合验收

- 新建 `_other/评测/Tool错误与恢复/说明.md` 和版本化 V1 资产；先读
  `_other/评测/评测资产组织规范.md`。
- 使用真实 Gateway、metadata-only 和 full 两种模式运行固定场景。
- 至少覆盖一个没有专用错误码的 Tool，证明通用 fallback 生效。
- 至少覆盖 retry、continuation、recovered、continued、unrecovered、unresolved。
- 桌面 1440x900 和移动 390x844 检查 Graph、节点详情、关系边与 Event 定位。

## 7. 测试要求

### 7.1 Python

至少新增或扩展：

```text
tests/agent/test_runner_errors.py
tests/agent/test_runner_audit.py
tests/tools/test_tool_registry.py
tests/audit/test_diagnostics.py
tests/audit/test_schema.py
tests/audit/test_graph_builder.py
tests/audit/test_webui_api.py
```

关键断言：

- 每一种 Runner 失败入口都产生非空安全错误信息；
- 未提供结构化元数据的 Tool 仍产生稳定 fallback；
- ToolResult 的精确字段不会被 wrapper/retry hint 丢失；
- metadata-only 没有 Payload，但 Graph 有错误原因；
- secret 和超长错误不泄露；
- 旧 Event 缺字段可读；
- 非相关成功 Tool 不产生恢复边；
- 同一失败可区分 retry、continuation、recovery；
- 所有失败都有恢复状态。

Python 改动执行聚焦 pytest、相关路径 `ruff check`，不要运行 `ruff format`。修改 Runner 时必须运行聚焦
集成测试；修改安全边界时必须覆盖拒绝行为。

### 7.2 WebUI

至少扩展：

```text
webui/src/tests/audit-trace-ux.test.tsx
webui/src/tests/trace-graph.test.tsx
webui/e2e/audit-tool-recovery-real.spec.ts
```

断言失败节点始终显示错误或明确历史缺失提示；关系类型可区分；长文本、窄屏、定位失败、旧 Graph、
metadata-only 和显式 Payload 行为正确。运行聚焦 `bun run test`、`bun run build` 和 Chromium。

### 7.3 回归

阶段性聚焦测试通过后运行完整 Python 与 WebUI 测试。完整测试存在与本任务无关的已知失败时，必须给出
失败清单和归因，不能以聚焦测试代替披露。

## 8. 兼容、迁移与发布

- 所有 wire 字段 additive；旧字段不删除、不改义。
- 新 Runtime 保证新异常 Event 完整，Reader/Graph 继续兼容旧 Event。
- 不批量回填历史错误；历史节点明确标记数据缺失。
- Graph builder version/ETag 同步升级。
- 如需 feature flag，只允许控制新关系展示，不允许关闭通用错误记录。
- 回退 UI 或新边时，错误 Event 仍应保留，避免再次失去可观测性。

## 9. 交付拆分

建议按以下可独立验证的提交推进：

1. `测试（工具错误）：建立通用失败归一化矩阵`
2. `功能（工具错误）：统一记录安全错误诊断`
3. `测试（工具恢复）：建立全工具证据分类矩阵`
4. `功能（工具恢复）：增加重试继续与恢复证据协议`
5. `功能（审计图）：展示通用错误与工具关系`
6. `功能（WebUI）：完善错误详情与恢复关系检查器`
7. `评测（工具恢复）：增加综合试卷并归档正式验收`

每个工作单元提交前运行对应验证，只暂存明确路径，提交后推送同一任务分支并维护同一 PR。未经用户明确
确认不得合并 `main`。

## 10. 完成定义

同时满足以下条件才可宣称完成：

1. 新产生的每个异常 Tool Event 都有非空、安全、可显示的错误诊断。
2. `list_dir` 原复现场景在 metadata-only Graph 中可直接看到目录不存在原因。
3. 所有异常 Tool 节点都有恢复状态，不再出现无解释空白。
4. retry、continuation、recovery 在 Event、Graph 和 UI 中语义可区分。
5. 没有确定性证据的场景不会显示 recovered。
6. 内置 Tool inventory 全部有适配策略或显式 fallback；MCP/插件无协议时安全降级。
7. 旧 Trace、full/metadata-only、桌面/移动端均通过兼容验收。
8. Python、ruff、WebUI test/build、Chromium 和新综合评测均有真实结果。
