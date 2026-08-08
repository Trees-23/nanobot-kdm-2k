# 上下文分层与 Agent 状态栏实施计划

## 1. 文档定位

本文是 nanobot 上下文层改造的实施方案草案，目标是解决长轨迹 Agent 在上下文组织和运行时状态感知上的实际问题。

本方案先解决两个相互关联但不能混为一谈的问题：

1. 将静态提示、动态事实和运行时状态分层，改善 prompt KV Cache 的稳定性。
2. 建立由代码维护的 Agent 状态投影，减少盲目重试、任务遗漏和恢复时的上下文扫描。

状态栏不是 UI 装饰，也不是新的记忆系统。它是从已有结构化运行状态生成的、面向模型的短摘要。

## 2. 当前实现事实

### 2.1 System Prompt 的组成

`nanobot/agent/context.py` 中的 `ContextBuilder.build_system_prompt()` 当前会拼接：

- `AGENTS.md`、`SOUL.md`、`USER.md`；
- 工具使用契约；
- `memory/MEMORY.md`；
- `always` skill 的完整内容；
- 全量 skill 索引；
- `memory/history.jsonl` 中尚未被 Dream 消费的 Recent History，最多 8,000 tokens；
- archived session summary。

这些内容目前共享一个 system 消息。最近历史、记忆、摘要、skill 可用性和工作区文件变化，都可能改变该消息。

### 2.2 工具 schema 的现状

工具定义通过 provider 的 `tools` 参数发送，不属于 system prompt 文本。`ToolRegistry` 已经对 built-in 和 MCP 工具做稳定排序，并在注册表变化前复用 schema 列表。

### 2.3 Runtime Context 的现状

项目已有 `nanobot/runtime_context.py`：

- runtime context provider 在每个 user turn 开始前解析；
- 内容追加在当前 user 文本之后；
- 通过内部 marker 保证 WebUI 展示时可以移除；
- 当前已被 Goal 和 CLI App 能力使用。

这套机制应作为状态栏的主要承载方式，而不是重新发明一套消息协议。

### 2.4 重要边界

- 状态栏由代码维护，不能由 LLM 批量总结历史后再写回。
- 状态栏只提供决策辅助；硬限制必须在工具执行层或完成检查层强制执行。
- 不能因为某工具在整个 session 中调用过几次，就对所有后续任务做全局拦截。
- 失败观察必须绑定明确的作用域，例如当前 turn、当前 Goal 和同一规范化请求。
- 不要把行为规则、安全规则和 Skill 操作指令为了缓存而降级成普通 user 文本。
- 不要在没有指标和场景证据的情况下宣称 KV Cache 或准确率一定改善。

## 3. 要解决的用户场景

### 3.1 长任务恢复

问题：任务经过多轮工具调用、上下文压缩、进程重启或用户暂时离开后，模型需要重新扫描历史才能判断已经完成什么、还缺什么。

目标：用户说“继续”时，模型能直接看到可靠的任务快照。

示例：

```text
目标：完成 Agent 评测基线
已验证：benchmark-report.md 已生成
未完成：benchmark-matrix.json、校验、提交
子任务：2/3 已完成，1 个仍在运行
```

首版复用已有 `goal_state`、session metadata、子任务生命周期和完成 barrier，不新增自由文本 TODO 解析。

### 3.2 工具失败后的避免盲重试

问题：模型连续多次重复相同工具调用，尤其是路径错误、参数错误或外部查询失败时，重试没有带来新信息。

目标：把连续失败次数、错误类型、最近策略和建议的下一步以短状态呈现。

示例：

```text
操作：read_file(path="src/config.py")
连续失败：2 次
最后错误：FileNotFoundError
建议：先列目录或搜索候选路径，不要重复相同调用
```

首版先做提示和观测；明确的重复失败策略经验证后再增加执行层拒绝。

### 3.3 多子 Agent 完成状态

问题：父 Agent 可能只看到部分子 Agent 的文本回传，就误以为整体任务完成，或忘记等待仍在运行的必要任务。

目标：展示真实生命周期汇总和必要任务 barrier。

示例：

```text
必要子任务：3
已完成：2
运行中：1
失败：0
当前：不能宣布总体完成
```

父 Agent 是否允许结束仍由现有 goal orchestration/completion guard 决定，不能只信 prompt 中的状态文本。

## 4. 目标上下文布局

目标布局如下：

```text
system:
  稳定身份、行为规则、安全边界、工具契约、稳定 Skill 指令

tools:
  稳定排序的工具 schema

history:
  对话、assistant tool call、tool result

当前 user:
  用户原始消息
  + Runtime Context / Agent Status（短、结构化、代码生成）
```

状态栏使用现有 runtime context marker，并与用户可见历史分离。

如果需要在同一个用户请求内部更新状态，则在工具结果之后、下一次 LLM 请求之前追加 model-only runtime user 消息。该消息不是用户真实输入，WebUI 展示和持久化投影必须隐藏或剥离。

## 5. 状态模型建议

建议引入版本化、受限大小的状态投影模型。名称可以在实现阶段确定，例如 `AgentExecutionState` 和 `AgentStatusSnapshot`。

状态来源必须是结构化事实：

- `goal_state`：目标、目标状态、开始时间、UI 摘要；
- session metadata：持续目标和恢复信息；
- 工具执行事件：工具名、成功/失败、错误分类和当前 turn 内的执行次数；
- 子 Agent 生命周期：created/running/succeeded/failed/lost 等终态；
- 完成 barrier：仍未满足的必要任务；
- 当前 turn：迭代号、剩余工具预算、当前 turn 内的调用次数。

状态不应保存：

- 完整工具输出；
- 未截断的异常堆栈；
- 未验证的模型自述；
- 凭据、API Key 或隐私数据。

建议状态栏采用稳定且有上限的格式：

```text
[Agent Status]
Scope: goal:<goal-id> / session:<session-key>
Goal: <short objective>
Progress: <completed>/<total>
Tool activity: <tool> <result-state>
Failures: <operation> failed <count> times; last=<error-code>
Subagents: <done>/<total>, running=<count>, blocked=<count>
Next: <one short machine-derived hint>
[/Agent Status]
```

不适用的字段省略，整个状态必须有 token 上限。

## 6. 分阶段实施

### 阶段 0：基线和契约

目标：在改行为前确认问题规模和 provider 差异。

任务：

- 记录每轮 system prompt hash、动态区 token 数、输入 token、`cached_tokens`；
- 区分 system 静态区、session 动态区、turn 动态区；
- 定义状态作用域：turn、session、Goal 和同一规范化请求；
- 定义状态版本、最大长度、敏感字段过滤规则；
- 明确哪些状态只提示，哪些状态由代码强制。

验收：相同 workspace/channel 的重复请求能够看出 system 内容是否稳定，且不泄露凭据。

### 阶段 1：上下文分层和跨 turn 状态

目标：先改善缓存边界，并覆盖长任务恢复和子 Agent 汇总。

任务：

- 将 system prompt 拆成稳定快照和动态事实；
- 优先把 Recent History 从 system 移到当前 user 尾部；
- 对 Memory 和 archived summary 先做 token/变化频率评估，再决定迁移或快照缓存；
- 对 bootstrap 文件、always skills 和 skill 索引建立内容版本/变更失效策略；
- 注册一个统一的状态 provider，复用现有 `RuntimeContextProvider`；
- 从 Goal、session 和子任务真实记录生成恢复快照；
- 保留用户可见历史的剥离能力，避免内部状态污染 WebUI transcript。

验收场景：

- 中断后用户发送“继续”，模型能识别未完成项；
- 进程重启后仍能恢复同一 Goal；
- 子任务未全部完成时，模型不能只根据部分文本宣布完成；
- 不同历史/摘要变化不再导致完整 system prompt 每轮重写，或有明确指标证明代价可接受。

### 阶段 2：同一 turn 内的实时状态

目标：工具结果返回后，下一次模型调用立即看到最新计数和失败状态。

任务：

- 在 AgentRunner 的工具完成边界更新结构化状态；
- 仅在状态发生有意义变化时，追加 model-only runtime status 消息；
- 保持合法的 role alternation，并确保状态消息不会被当作真实用户历史展示；
- 为重复失败、当前 turn 工具活动和目标 barrier 提供稳定字段；
- 增加 runner 集成测试，确认工具结果、状态消息和下一次模型请求的顺序。

验收场景：

- 第一次工具失败后，第二次模型请求看到失败次数和错误类型；
- 模型换策略，而不是重复完全相同的调用；
- 多次状态更新不会无限扩大上下文；
- 状态消息不进入用户可见 transcript。

### 阶段 3：效果评估与后续决策

目标：依据真实指标决定是否继续扩展前三个场景的状态字段。

任务：

- 汇总 KV Cache、输入 token、任务恢复、失败重试和子 Agent 场景指标；
- 确认状态栏是否造成陈旧信息、上下文膨胀或 provider 兼容问题；
- 确认是否需要补充更多可靠的任务进度或失败恢复状态。

## 7. 测试和真实验收

代码实现时至少需要：

- `ContextBuilder` 的静态/动态 prompt 稳定性测试；
- runtime context 的持久化、剥离和长度上限测试；
- 工具失败计数和同一操作指纹测试；
- Goal 恢复、进程重启、上下文压缩后的恢复测试；
- 多子 Agent 创建、运行、终态、结果投递和父子关联测试；
- 受影响 Python 路径的 `pytest` 和 `ruff check`；
- 涉及 `loop.py`/`runner.py` 时的聚焦集成测试；
- 按仓库约定完成一次真实 Gateway 场景验收，核对实际模型请求、工具行为、状态投影和审计轨迹。

真实验收不能只检查最终回答，必须能证明：状态由代码生成、状态更新发生在正确的模型请求之前、拒绝和恢复属于同一场景。

## 8. 风险与待确认问题

- 把动态事实从 system 移到 user 尾部可能改变模型对它们的优先级，必须区分“事实”与“指令”。
- system prompt 拆分和 provider cache marker 的行为并不完全统一，需要按 Anthropic、OpenAI-compatible、OpenAI Codex 等 provider 分别验证。
- 旧 session 中已经持久化的 runtime context 需要兼容读取和剥离。
- 状态算错会被模型高度信任，因此状态生成函数应有独立单元测试和审计可追溯性。
- 状态栏 token 预算过大时会反过来增加输入成本，应优先显示异常、当前活动和未完成项。
- 子 Agent 和 Goal 已有多个权威状态来源，实施时应先确定单一事实源，避免维护第二套互相漂移的状态机。

## 9. 明确不在本次范围

- 不实现通用的模型自我意识或自由文本 TODO 解析；
- 不把所有历史删掉，只保留状态栏；
- 不对所有工具设置统一调用次数上限；
- 不为了缓存移除安全规则、行为规则或 Skill 操作指令；
- 不在没有指标的情况下承诺准确率、延迟或成本必然改善。
