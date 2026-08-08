# 上下文分层与 Agent 状态栏方案审查提示词

你现在要审查 nanobot 项目的方案文档：

`_other/上下文分层与Agent状态栏实施计划.md`

本轮任务是“方案审查和补充”，不是直接实现代码。请先阅读仓库根目录及当前任务适用的 `AGENTS.md`，再阅读方案和相关代码。除非用户后续明确授权，本轮不要修改代码、不要修改方案文档、不要创建提交、不要推送、不要创建或合并 PR、不要改动 `runtime/workspace/`。

## 审查目标

请判断这份方案是否足以指导后续实现，重点检查：

1. 上下文分层是否真的改善 prompt KV Cache，而不是只把同样的 token 换一个消息角色；
2. 哪些内容必须继续留在 system，哪些内容适合放进 runtime context；
3. provider 差异是否覆盖，包括 Anthropic cache marker、OpenAI-compatible provider、OpenAI Codex 的缓存键和工具 schema 缓存；
4. 状态栏的数据来源是否来自代码维护的结构化事实，而不是让 LLM 批量总结历史；
5. turn、iteration、model call 的边界是否清晰；
6. “跨 turn 恢复”和“同一 turn 内工具完成后的实时状态”是否被正确区分；
7. 长任务恢复、工具失败避免盲重试、多子 Agent 完成状态三个场景是否都有明确的状态源、更新时机、作用域和失败处理；
8. 是否错误地把工具名全局计数并作为通用硬限制；
9. 状态栏错误、陈旧、重复、超长、泄露敏感信息时会发生什么；
10. 与现有 `goal_state`、`goal_orchestration`、session metadata、runtime context、AgentRunner hooks、audit 记录是否重复或冲突；
11. 旧 session、上下文压缩、进程重启、子 Agent 回传和 Gateway 真实运行是否有兼容方案；
12. 测试、指标和真实场景验收是否能证明实际效果，而不是只证明最终回答看起来正确。

## 需要重点阅读的代码

- `nanobot/agent/context.py`
- `nanobot/runtime_context.py`
- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/context_governance.py`
- `nanobot/session/goal_state.py`
- `nanobot/session/goal_orchestration.py`
- `nanobot/session/manager.py`
- `nanobot/agent/subagent.py`
- `nanobot/agent/hook.py`
- `nanobot/agent/tools/registry.py`
- `nanobot/providers/anthropic_provider.py`
- `nanobot/providers/openai_compat_provider.py`
- `nanobot/providers/openai_codex_provider.py`
- `tests/agent/test_context_prompt_cache.py`
- `tests/agent/test_runtime_context.py`
- `tests/agent/test_loop_runner_integration.py`
- `tests/agent/test_session_manager_history.py`

## 审查输出格式

请使用中文输出，按以下顺序：

### 一、结论

说明方案是否可以进入实现准备阶段：可以、需要补充后再进入、或存在阻塞问题。

### 二、必须修正的问题

按严重程度排序。每项包括：

- 问题描述；
- 代码证据，给出文件和行号；
- 为什么会导致行为错误、缓存失效、数据漂移或安全风险；
- 建议如何修改方案。

如果没有必须修正的问题，请明确写“未发现必须修正的问题”，但仍要列出残余风险。

### 三、建议补充的边角场景

至少检查以下情况：

- 相同工具名但不同参数/不同外部目标；
- 同一 Goal 与不同 Goal 之间的计数继承；
- 工具调用被取消、进程崩溃或 Gateway 重启；
- 状态更新和模型请求之间发生新用户消息或子 Agent 回传；
- 状态栏连续追加造成上下文膨胀；
- 旧 session 中已有 runtime context marker；
- 状态内容被 WebUI 展示或误写入用户可见历史；
- 状态栏包含工作区路径、外部 ID、错误详情或其他不应泄露的信息；
- provider 拒绝连续 user 消息、工具消息后追加 user 消息或多模态内容的情况；
- 状态栏和现有完成 barrier、审计状态之间不一致。

### 四、建议的最小实现切片

请给出一个比完整方案更小、可以先验证的实现切片，明确：

- 首批只覆盖哪些状态字段；
- 哪些状态只展示，哪些状态强制；
- 哪些文件需要修改；
- 最接近的测试命令；
- 一个真实 Gateway 场景的提示词和预期证据。

不要直接写代码。若认为方案需要拆成多个 PR，请说明每个 PR 的边界和依赖关系。

### 五、待向用户确认的问题

只列出确实会改变架构或行为的问题，例如：

- 计数按 turn、session 还是 Goal 作用域；
- 状态栏是否默认启用；
- 是否允许模型请求之间追加 model-only user 消息；
- 状态是否需要保留在模型历史中，还是仅保留结构化状态。

问题不要超过 8 个。能够从代码或现有配置确定的事项，不要反问用户。

## 审查原则

- 不要把状态栏当成安全边界；真正的拒绝必须在执行层完成。
- 不要把所有动态内容都机械移出 system；先区分静态指令、动态事实和不可信输入。
- 不要为了理论上的缓存收益删除原始上下文；先证明状态投影覆盖了所需维度。
- 不要新增一个与现有 Goal、子 Agent、审计状态并行且没有单一事实源的状态机。
- 不要使用 LLM 批量统计长历史来维护计数。
- 不要用一次成功调用代替失败恢复、取消或进程重启后的恢复验证。
- 方案审查结论必须基于当前代码，不要只复述计划文档。
- 本次只审查长任务恢复、工具失败避免盲重试和多子 Agent 状态；不要扩展到外部操作状态、Cron 或其他工具的幂等设计。
