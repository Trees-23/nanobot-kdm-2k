# 执行“Tool 恢复关系右侧公共轨道修复实施方案”的任务提示词

你正在 nanobot fork 仓库中修复审计运行轨迹的 Tool 关系边路由。开始前先完整阅读并遵守：

```text
AGENTS.md
.agent/design.md
.agent/security.md
.agent/gotchas.md
_other/Tool恢复关系右侧公共轨道Handoff.md
_other/Tool恢复关系右侧公共轨道修复实施方案.md
```

Handoff 是设计决策和根因来源，修复实施方案是本任务的执行与验收合同，当前代码是实现事实来源。
如果文档中的符号或行号已经变化，应先更新事实判断，不得暗自绕过合同。

## 最终目标

把以下三类 Tool 关系：

```text
tool_retry
tool_continuation
tool_recovery
```

统一改为：

```text
right-source → 右侧公共恢复轨道 → right-target
```

公共轨道必须位于当前可见节点包围盒之外，多条重叠关系必须确定性分槽，路径不得横穿中间节点。
修复完成后，需要新建一个真实 Session/Trace，并让用户可以在 WebUI 运行轨迹中看到新效果。

## 开始前

1. 读取当前目录适用的全部 `AGENTS.md`；
2. 检查当前分支、工作区、远端、`origin/main` 和现有 PR；
3. 当前任务应继续维护 `codex/tool-error-recovery-plan` 和 PR #10；若现场已经变化，按仓库规则安全处理；
4. 不在 `main`/`master` 开发，不创建 worktree，不 fetch 或合并 `upstream`；
5. 用户已有修改全部受保护，不 stash、reset、clean、覆盖、暂存或提交无关改动；
6. 确认 `AuditEdge()`、`edgeHandles()`、`renderEdges`、六个 Trace Handle、Layout Worker 和现有测试仍存在；
7. 先检查 Handoff 中的根因是否仍成立，再开始修改。

## 硬性合同

### Handle

```text
tool_retry        right-source → right-target
tool_continuation right-source → right-target
tool_recovery     right-source → right-target
sequence          bottom-source → top-target
```

不得继续使用 `right-source → left-target`，不得根据 Tool 名称写 `list_dir`、`read_file` 等个别分支。

### 几何

```text
P0 = source 右侧 Handle
P1 = railX, source.centerY
P2 = railX, target.centerY
P3 = target 右侧 Handle
```

必须满足：

- `railX > max(相关可见障碍物.right) + gap`；
- 所有几何值使用 React Flow 图坐标，不使用 viewport 像素；
- 多条纵向区间重叠的关系使用确定性相邻 slot；
- Graph edge 输入顺序变化不改变 slot；
- 路径所有 segment 不与非端点可见节点矩形相交；
- source、target 上下反转或同 Y 时仍正确；
- dangling/隐藏端点不生成错误轨道；
- 折叠、展开、Worker positions 和 fallback layout 更新后重算；
- 初始 fitView 和关系定位后最外侧轨道仍完整可见。

### 语义和安全

- 只消费后端显式 `tool_*` 关系，不在前端猜测恢复；
- 不修改 Event、Graph、恢复状态、Session 聚合或 Payload 协议；
- 不把 continued/unresolved 伪装成 recovered；
- 不自动加载 Payload；
- route data 只能包含坐标、稳定关系 ID 和非敏感布局元数据；
- 不改写历史 Audit、旧运行记录或截图；
- 不把旧“子智能体终止与恢复 V2”轨迹作为唯一新实现证据。

## 严格实施顺序

### 阶段 0：先写失败测试

1. 新增 `webui/src/tests/tool-relation-routing.test.ts`；
2. 更新 `webui/src/tests/trace-graph.test.tsx`；
3. 冻结 right/right、外置 railX、稳定 slot、无矩形相交、隐藏端点和 sequence 不回退合同；
4. 覆盖 `tool_retry`、`tool_continuation`、`tool_recovery`；
5. 运行测试，确认只以“公共路由尚未实现”的预期原因失败；
6. 不要把 fixture 或 TypeScript 错误当作红灯阶段完成。

### 阶段 1：实现纯几何路由器

建议新增：

```text
webui/src/components/traces/toolRelationRouting.ts
```

要求：

1. 输入为可见 edges、node bounds 和 right boundary；
2. 输出至少包含 `edgeId`、`slot`、`railX`、`points`、`path` 和 route bounds；
3. 按 `startY, endY, edge.type, edge.id` 稳定排序；
4. 使用 first-fit 区间分槽，非重叠区间可复用 slot；
5. 提供 segment/rectangle 相交检查；
6. 不依赖 React、DOM、viewport 或随机数；
7. 不增加新的图布局运行时依赖。

### 阶段 2：接入 `TraceGraph`

1. 把全部 `tool_*` Handle 改成 right/right；
2. 复用当前隐藏/折叠规则构建 visible edges 和 bounds；
3. 在同一个 `useMemo` 中统一计算全部 Tool routes；
4. 将结构化 route 放入 edge data；
5. `AuditEdge()` 对 Tool route 使用自定义正交 path；
6. 其他关系继续使用现有 `getSmoothStepPath()`；
7. 保留颜色、dash、marker、opacity、z-index 和 `interactionWidth >= 32`；
8. 保留恢复链路聚焦、桌面边点击、移动端关系按钮、关系检查器和双端 Event 定位；
9. 检查 route bounds 是否需要纳入 `fitBounds`，不得用伪节点污染图语义。

### 阶段 3：边界和真实浏览器

1. 覆盖 Worker 成功与 error fallback；
2. 覆盖折叠、展开、dangling、隐藏和跨 lane fixture；
3. 跨 lane 路径若相交，使用确定性 corridor，不得退回已知错误直连；
4. 使用真实运行生成器或等价受控场景创建新的 Session/Trace；
5. 新 Trace 必须包含 retry、continuation、recovery 三类关系；
6. 在 WebUI `/traces` 中确认新 Session/Trace 可导航并展示新轨道；
7. 在 Chromium `1440x900` 和 `390x844` 检查坐标、像素、截图、裁切和交互；
8. 分别打开三种关系检查器并定位 source/target Event；
9. 断言无 console/page error、无新增 Payload 请求。

### 阶段 4：完整门禁与交付

运行：

```bash
cd webui
bun run test -- src/tests/tool-relation-routing.test.ts src/tests/trace-graph.test.tsx \
  src/tests/audit-trace-ux.test.tsx
bun run test
bun run build
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

然后运行：

```bash
git diff --check
git status --short
```

只有实际执行并成功的命令才能写成通过。本轮预计不修改 Python；如果确实必须修改 Graph/API/Python，
先证明原因，再补充匹配的 pytest 和 `ruff check`，并在 PR 中明确扩大的协议风险。

## 预期改动范围

```text
webui/src/components/traces/TraceGraph.tsx
webui/src/components/traces/toolRelationRouting.ts
webui/src/tests/tool-relation-routing.test.ts
webui/src/tests/trace-graph.test.tsx
webui/e2e/audit-tool-recovery-real.spec.ts
```

真实场景需要时可修改：

```text
webui/e2e/generate-audit-tool-recovery-runtime.py
_other/评测/Tool错误与恢复/...
```

不要顺手重构无关 Trace 节点、Inspector、后端 Graph 或 Audit 模块。

## 禁止项

- 不只把 target Handle 改成 right 就宣称完成；
- 不只增大 `getSmoothStepPath()` offset；
- 不用固定 viewport 像素或 `window.innerWidth` 计算 rail；
- 不让每个 Edge 独立、随机分配轨道；
- 不以 `path.d` 非空作为唯一 E2E 证据；
- 不画会穿过节点的 fallback；
- 不删除现有 32px 点击区域或移动端关系入口；
- 不自动请求 Payload；
- 不伪造或修改历史 Trace；
- 不运行 `ruff format`；
- 不执行破坏性 Git 命令；
- 不强推，不重写已推送历史；
- 未经用户明确确认不合并 `main`。

## 提交与 PR

按可验证工作单元创建中文提交，建议：

```text
测试（审计图）：冻结Tool关系右侧轨道几何合同
修复（审计图）：实现Tool关系右侧公共轨道
测试（审计图）：完成公共轨道真实双视口验收
```

每次提交前检查差异，只暂存本任务明确路径；提交后立即推送当前任务分支。持续维护 PR #10，更新：

```text
改动内容
验证结果
风险与注意事项
新 Session/Trace 验收入口
```

任务完成后保持 PR 可审查，等待用户针对整个 PR 确认后再合并。

## 完成定义

必须全部满足：

1. 三类 Tool 关系均为 `right-source → right-target`；
2. 公共 rail 位于可见障碍物外侧；
3. 多边分槽稳定、可预测且不完全重叠；
4. 路径不穿过中间节点；
5. 折叠、Worker fallback、dangling 和跨 lane 有明确处理；
6. sequence 和其他关系没有回退；
7. 箭头、样式、点击、聚焦、检查器和移动端入口正常；
8. fitView 后轨道完整可见且节点文字可读；
9. 新 Session/Trace 可在前端看到修复效果；
10. 双视口坐标、像素、截图、console 和 Payload 检查通过；
11. 聚焦测试、完整 WebUI、build、Chromium E2E 实际通过；
12. 中文提交已推送，PR #10 已更新；
13. 未经用户确认没有合并 `main`。

## 最终报告

最终报告必须包含：

1. 分支名；
2. 提交编号和中文标题；
3. 推送结果；
4. PR 链接、状态和目标分支；
5. 实际执行的聚焦测试、完整 WebUI、build 和 Chromium 结果；
6. 新 Session key、Trace ID 和前端访问方式；
7. right/right、railX、slot、无相交和 fitView 的验收结论；
8. Payload 请求数与 console/page error 结果；
9. 未完成项、已知风险和回退状态；
10. 明确写出“等待用户确认后合并 main”。
