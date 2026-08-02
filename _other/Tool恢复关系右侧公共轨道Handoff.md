# Tool 恢复关系右侧公共轨道 Handoff

更新时间：2026-08-03（Asia/Tokyo）

仓库：`/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k`

当前分支：`codex/tool-error-recovery-plan`

当前 PR：[功能（工具错误）：统一可观测诊断与确定性恢复](https://github.com/Trees-23/nanobot-kdm-2k/pull/10)（Open、非 Draft、目标 `main`）

文档状态：设计与实施交接；本文创建时未修改 WebUI 路由实现

## 1. 交接目标

下一位实施者需要把运行轨迹中所有 Tool 关系边从当前的“侧边起步、横穿节点列”改为真正的右侧公共
恢复轨道。不可变的视觉与几何契约是：

```text
right-source
  → 右侧公共恢复轨道
  → right-target
```

这里的“侧边”不是只把起点 Handle 放到右侧，也不是让默认折线路径在节点旁短暂偏移。完整关系路径
必须离开 source 右侧，进入节点包围盒之外的公共轨道，沿轨道纵向移动，再从右侧进入 target。

本次设计针对以下三类显式 Tool 关系：

```text
tool_retry
tool_continuation
tool_recovery
```

三类边共享路由器和轨道分配规则，但继续保留各自的颜色、虚线样式、文案、关系检查器内容和后端语义。

## 2. 不可变决策

### 2.1 路由合同

同一执行 lane 内的 Tool 关系必须满足：

```text
source.right handle
  → source 右侧水平引出段
  → railX 上的纵向公共轨道段
  → target 右侧水平进入段
  → target.right handle
```

即：

```text
right-source → 右侧公共恢复轨道 → right-target
```

不得继续使用：

```text
right-source → left-target
```

也不得：

- 用 source/target 的中点生成一条穿过节点列的普通 Smooth Step；
- 仅增大 `getSmoothStepPath()` 的固定 `offset` 来冒充公共轨道；
- 按 `list_dir`、`read_file` 或其他 Tool 名称编写单独路由分支；
- 在前端重新推断 retry、continuation、recovery 语义；
- 修改后端关系 ID 或恢复证据来迎合画线逻辑；
- 让 Tool 关系边与普通 `sequence` 边共用上下 Handle。

### 2.2 语义边界

- Graph/API 继续提供权威的关系类型和显式 source/target ID；前端只负责几何布局。
- `edge.type.startsWith("tool_")` 是当前通用覆盖入口，不建立 Tool inventory 白名单。
- `sequence` 继续使用 `bottom-source → top-target`。
- `spawn_branch`、`result_return` 和其他关系不应被本次修改顺手改线。
- 后端 Graph、Audit Event、恢复证据协议和安全诊断字段不是本次路由修复范围。

## 3. 当前现象

用户在“子智能体终止与恢复 V2”等轨迹中看到的恢复链条仍像中轴直连。以失败 `list_dir` Tool 节点
为例，节点详情已经能够显示“终态：失败”等信息，Graph 也已经返回 `tool_recovery`，但恢复边从
source 右侧出发后，很快折向 target 左侧，因此中间长横段仍穿过节点列。

一次真实页面检查中观察到的路径坐标为：

```text
source right x = 1210
默认右侧偏移 x = 1230
target left x = 958
target 左侧偏移 x = 938
```

默认 Smooth Step 只在起点右侧外扩约 20 个图坐标。在当时的页面缩放下，这约等于 8 个屏幕像素，
随后路径便横穿到左侧，所以肉眼看到的仍然是一条直连恢复链。

这也解释了为什么此前“已经改成侧边”但最终效果仍不符合预期：此前改动完成的是侧边 Handle，不是
独立公共轨道。

## 4. 已验证根因

### 4.1 不是 Tool 覆盖遗漏

当前 `webui/src/components/traces/TraceGraph.tsx::edgeHandles()` 使用：

```ts
edge.type.startsWith("tool_")
```

因此 `tool_retry`、`tool_continuation`、`tool_recovery` 都会进入同一 Handle 选择逻辑。失败案例中的
Graph 也确实返回了 `tool_recovery`。问题不是 `list_dir` 没被识别，也不是只覆盖了部分 Tool。

### 4.2 当前 Handle 合同本身会横穿

当前逻辑根据 source 的 `lane_side` 选择 source 侧边，再故意选择相反方向的 target：

```text
left source  → right target
right source → left target
```

对主 lane 中默认视为右侧的节点，实际结果就是：

```text
right-source → left-target
```

这与目标合同直接冲突。

### 4.3 默认路径算法没有公共轨道概念

`AuditEdge()` 当前对所有边统一调用：

```ts
getSmoothStepPath(props)
```

该调用只掌握单条边的端点和默认偏移，不知道：

- 当前可见节点的最大右边界；
- source 和 target 所在 lane 的包围盒；
- source/target 之间是否有其他节点；
- 其他 Tool 关系已经占用哪些轨道；
- 折叠、展开或 Worker 布局更新后的新位置。

因此它无法实现“公共恢复轨道”。即使把 target Handle 改为 `right-target`，若仍完全依赖默认路径算法，
也不能证明路径位于节点列外侧或多条边不会重叠。

## 5. 历史修改定位

### 5.1 `8bcc37d3`

提交：`修复（WebUI）：完成恢复边与双端事件定位`

该提交提取了 `edgeHandles()`，并首次给 `tool_recovery` 指定侧边 Handle。它解决的是恢复边与普通
sequence 边重叠、关系两端 Event 可定位等问题，但采用了“source 同侧、target 对侧”的合同，没有
实现公共轨道。

### 5.2 `751d5018`

提交：`功能（审计图）：展示通用错误与工具关系`

该提交把 Handle 逻辑从单独的 `tool_recovery` 扩展为全部 `tool_*`，同时补齐 retry、continuation、
recovery 的展示。它扩大了语义覆盖范围，但沿用了相同的 opposite-target 路由，所以三类 Tool 关系
现在都会遇到同一几何问题。

结论：历史改动没有回退，也不是后来遗漏了某个 Tool；它一直缺少第二层“基于全图几何的轨道路由”。

## 6. 当前关键代码与测试盲区

### 6.1 实现位置

```text
webui/src/components/traces/TraceGraph.tsx
  AuditEdge()                 自定义边渲染，目前统一调用 getSmoothStepPath
  edgeHandles()               Handle 选择，目前 Tool 关系使用 opposite target
  renderEdges                 把 Graph edge 转换成 React Flow edge
  NODE_WIDTH / NODE_HEIGHT    当前节点静态尺寸
  positions                   Layout Worker 或 fallback 返回的图坐标

webui/src/components/traces/nodes/TraceNode.tsx
  top-target / bottom-source
  left-source / right-source
  left-target / right-target

webui/src/workers/auditLayout.worker.ts
  计算语义节点位置，是轨道重算的上游输入
```

### 6.2 当前测试只证明“有一条线”

`webui/src/tests/trace-graph.test.tsx` 当前明确断言 Tool 恢复边为：

```text
right-source → left-target
```

这条断言应改为新的同侧合同，但只改这一条仍不够，因为 Handle 正确不代表实际路径正确。

`webui/e2e/audit-tool-recovery-real.spec.ts` 当前只断言：

- 恢复边 SVG `path.d` 非空；
- 恢复边路径与一条 sequence 边不同。

任意一条横穿节点列的折线也能通过这些断言。当前没有测试证明：

- 轨道位于节点包围盒外；
- 路径不与中间节点矩形相交；
- 多条 Tool 关系使用确定且稳定的分槽；
- 折叠、展开和重新布局后轨道仍正确；
- 三种 `tool_*` 全部遵循相同路由合同。

## 7. 建议的实现结构

不要把公共轨道逻辑继续塞进 `edgeHandles()`。建议拆成两个职责明确、可独立单测的纯函数。

### 7.1 Handle 选择

建议保留 `edgeHandles()`，只负责关系类型与 Handle ID 的映射：

```ts
tool_*: right-source → right-target
sequence: bottom-source → top-target
```

对本文目标范围内的 Tool 关系，不再根据 `lane_side` 选择 `left-source`，也不再选择
`oppositeTarget`。如果未来产品确实需要左侧公共轨道，应作为另一套明确合同设计，不能在本次实现中
自动左右漂移。

### 7.2 公共轨道路由器

新增一个纯几何模块，例如：

```text
webui/src/components/traces/toolRelationRouting.ts
```

建议输入：

```ts
interface ToolRelationRouteInput {
  edges: ToolRelationEdge[];
  visibleNodes: NodeBounds[];
  sourcePointByEdge: Map<string, Point>;
  targetPointByEdge: Map<string, Point>;
  railGap: number;
  slotGap: number;
  cornerRadius: number;
}
```

建议输出：

```ts
interface ToolRelationRoute {
  edgeId: string;
  railX: number;
  slot: number;
  points: Point[];
  path: string;
}
```

`renderEdges` 根据当前可见节点和 `positions` 计算全部 Tool 关系路由，并把 `railX`、`slot` 或完整
route 放入 edge data；`AuditEdge()` 对 `tool_*` 使用该自定义路径，对其他边继续使用现有
`getSmoothStepPath()`。

不建议让每个 `AuditEdge()` 单独查看自己的端点后临时计算路径，因为那样无法进行全局分槽，也无法
保证多边稳定。

## 8. 公共轨道几何算法

### 8.1 坐标空间

所有几何值必须使用 React Flow 的图坐标，不使用 viewport 屏幕像素。缩放和平移只改变投影，不应
改变轨道相对节点的位置。

不得把轨道写成 `window.innerWidth - 40`、固定 CSS `right`，或依据当前 zoom 反推。轨道属于图布局，
不是页面装饰。

### 8.2 基础轨道

对当前可见、参与布局的语义节点和折叠组计算右边界：

```text
nodeRight = node.x + node.width
visibleMaxRight = max(nodeRight)
baseRailX = visibleMaxRight + railGap
```

建议从 `railGap = 48` 图坐标起步，再用真实桌面和移动端截图校准。最终值应成为命名常量，并有测试
解释其用途。Region 背景是否计入边界必须统一：如果 Region 视觉边框会与轨道相碰，就应把 Region 的
右侧 padding 纳入 `visibleMaxRight`，但不要把无界 viewport 或 MiniMap 当作障碍物。

最低不变量是：

```text
railX > max(source.right, target.right, relevantObstacle.right) + clearance
```

### 8.3 确定性分槽

多条关系完全共用同一个 SVG 纵向段时，用户无法区分或点击。建议把“公共轨道”实现为一个窄的公共
轨道走廊，在需要时分配相邻 slot：

```text
slotRailX = baseRailX + slot * slotGap
```

推荐使用确定性区间着色：

1. 为每条边计算纵向区间 `[min(sourceY, targetY), max(sourceY, targetY)]`；
2. 按 `sourceY, targetY, edge.type, edge.id` 稳定排序；
3. 将边分配到第一个与已有区间不冲突的 slot；
4. 不重叠的纵向区间可以复用同一 slot；
5. 相交或距离小于最小分隔的区间使用下一 slot。

建议从 `slotGap = 12` 图坐标起步。不得使用数组当前遍历顺序或随机数分槽，否则刷新、折叠或 Graph
响应顺序变化时边会左右跳动。

### 8.4 路径构造

最小正交路径为：

```text
(sourceRightX, sourceY)
  → (railX, sourceY)
  → (railX, targetY)
  → (targetRightX, targetY)
```

可以在两个 90 度转角加入小圆角，但圆角不得改变 `railX` 纵向主体，也不得让箭头从错误方向进入
target。建议由纯函数生成 SVG path，并同时保留未经圆角处理的 route points 供测试断言。

不要仅靠解析最终 `path.d` 做所有测试；结构化 points/railX 更稳定、更容易定位失败原因。

### 8.5 障碍物检查

轨道纵向段和两个水平引出/进入段都应与可见节点矩形做相交检查，source 和 target 自身按预期接触
Handle 的边界除外。至少应检查 sourceY 到 targetY 之间的节点。

若将来出现跨 lane Tool 关系，简单的三段式路径可能从主 lane 横穿右侧 lane。不得静默退回当前的
中点直连。应采取以下顺序：

1. 先确认关系是否确实跨 lane，而不是错误的 lane membership；
2. 计算全部相关障碍物外侧的 `baseRailX`；
3. 检查 source/target 水平连接段；
4. 如仍相交，增加确定性的上/下净空 waypoint 或专用跨 lane corridor；
5. 为该 fixture 增加几何与浏览器测试后再交付。

本轮的最低交付范围是让真实同 lane retry、continuation、recovery 全部走右侧公共轨道；跨 lane
场景必须有显式测试、可靠路由或清晰的受控 fallback，不能悄悄画错。

## 9. 可见性与重算规则

公共轨道依赖当前渲染集合，以下状态变化后必须重新计算：

- Layout Worker 返回新 `positions`；
- Worker 失败并切换到 fallback positions；
- Graph 数据或 revision 更新；
- 模型尝试组折叠/展开；
- 节点因折叠组隐藏或重新出现；
- 可见语义节点、presentation group 或尺寸变化。

单纯 pan/zoom 不需要改变图坐标中的 route。viewport 尺寸变化如果触发节点布局或尺寸变化，则通过
布局依赖重算；如果只改变投影，也不应改变 route。

对 dangling 或隐藏端点：

- source 或 target 不在当前可见渲染节点集合时，不生成一条指向 `(0, 0)` 的错误轨道；
- 保持现有隐藏边过滤行为；
- 如产品需要展示 dangling 关系，应单独设计占位端点，不属于本次修复。

## 10. 交互与视觉约束

路由修改不得破坏已经实现的关系交互：

- 三类关系保留当前颜色和 dash；
- 箭头方向必须从失败/前序 Tool 指向 retry、continuation 或 recovery 目标；
- `interactionWidth` 至少保持 32px；
- 桌面端点击边仍打开“恢复关系检查器”；
- 移动端关系选择按钮继续可用，不要求用户精确点击细线；
- 聚焦“恢复链路”时仍覆盖三类 `tool_*`；
- 非选中关系的淡化、z-index 和 fitView 行为不回退；
- 长轨道不能覆盖节点标题、状态徽标、检查器或页面固定工具栏；
- 不为了显示轨道而把图整体缩小到难以阅读。

轨道是信息架构的一部分，不应增加没有语义的装饰、发光或动画。若多条边需要区分，优先使用稳定
slot、现有颜色/线型和关系选择器，而不是添加持续运动效果。

## 11. 测试与验收矩阵

### 11.1 纯几何单元测试

建议为路由模块新增独立测试，至少覆盖：

1. 单条同 lane 边使用 `right-source` 和 `right-target`；
2. `railX` 大于 source、target 和相关障碍节点的最大右边界加 gap；
3. 中间纵向段的两个端点具有相同 `railX`；
4. 路径不与 source/target 之间的节点矩形相交；
5. source 在 target 上方和下方时都保持正确箭头方向；
6. 同一输入重复构建产生相同 `slot`、`railX`、points 和 path；
7. Graph edge 顺序变化不改变既有关系的 slot；
8. 纵向区间不重叠时可以复用 slot；
9. 区间重叠时分配可预测的相邻 slot；
10. `tool_retry`、`tool_continuation`、`tool_recovery` 全部走公共路由器；
11. `sequence` 仍为上下 Handle 且不走公共轨道；
12. 隐藏或 dangling 端点不会产生错误路径；
13. 折叠前后仅按新的可见包围盒确定性重算；
14. 跨 lane fixture 不与任何可见节点矩形相交。

几何相交测试应考虑线宽/箭头和最小 clearance，不能只检查中心线是否刚好落在矩形边界外。

### 11.2 组件测试

更新 `webui/src/tests/trace-graph.test.tsx`：

- 把旧的 `right-source → left-target` 期望改为 `right-source → right-target`；
- 同时加入 retry、continuation、recovery 三种边；
- 验证 edge data 中存在结构化 route/railX；
- 验证普通 sequence 路由未改变；
- 验证折叠、展开或 positions 更新后 route 重算；
- 验证关系聚焦、点击和 inspector 行为不回退。

### 11.3 真实浏览器 E2E

增强 `webui/e2e/audit-tool-recovery-real.spec.ts`，不要只检查 `path.d` 非空。至少在真实 Gateway
fixture 上验证：

- 三种 Tool 关系都存在；
- 两端 DOM Handle ID 或 edge contract 都是 right/right；
- 从页面读取节点 bounding box 与 SVG route，证明轨道在相关节点右侧；
- 恢复边不与中间节点矩形相交；
- 三种关系可以分别打开正确的关系检查器；
- 页面没有 console error 或 page error；
- 没有因画线新增 Payload 请求；
- 桌面 `1440x900` 截图中可清楚看到公共轨道；
- 移动端 `390x844` 中关系选择按钮、检查器和轨道均可用；
- canvas/SVG 像素检查证明轨道非空且位于预期右侧区域。

截图只能作为辅助证据，不能代替坐标和相交断言。

### 11.4 推荐验证命令

```bash
cd webui
bun run test -- src/tests/trace-graph.test.tsx src/tests/audit-trace-ux.test.tsx
bun run build
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

修改实现后还应运行完整 WebUI 测试。只有实际执行并成功的命令才能在 PR 中写为通过。

## 12. 实施分段建议

### 工作单元 A：冻结几何合同

- 先新增路由纯函数测试；
- 将 Tool Handle 期望改为 right/right；
- 加入单边、多边、障碍物、稳定分槽和 sequence 不回退用例；
- 此阶段测试应以预期的路由缺失原因失败。

### 工作单元 B：实现公共轨道

- 新增纯几何路由模块；
- 从当前可见节点包围盒计算 `baseRailX`；
- 确定性分配 slot；
- 在 `renderEdges` 注入 route data；
- `AuditEdge` 仅对 `tool_*` 使用新路径；
- 保持其他 edge type 使用现有路径。

### 工作单元 C：状态变化和边界

- 覆盖 Worker/fallback positions；
- 覆盖折叠、展开、隐藏和 dangling；
- 验证跨 lane fixture 或实现明确受控处理；
- 检查交互宽度、z-index、关系聚焦和 fitView。

### 工作单元 D：真实 Gateway 验收

- 使用新的真实运行记录或确定性 fixture，不能修改历史权威记录；
- 在桌面和移动端执行 E2E；
- 保存截图、坐标/像素检查和浏览器错误结果；
- 更新同一 PR 的改动内容、验证结果和风险说明。

每个独立、可验证工作单元按仓库约定使用中文提交并立即推送当前分支。不得夹带无关工作区内容，
不得合并 `main`，直到用户针对 PR 明确确认。

## 13. 非目标与安全边界

本次不要借画线路由修改以下内容：

- 不修改恢复语义或把 continued/unresolved 伪装成 recovered；
- 不按时间相邻、同名 Tool 或一次后续成功猜测关系；
- 不改写历史 Audit JSONL、评测记录或截图；
- 不重新持久化 Tool 参数、结果、stdout/stderr、堆栈或 secret；
- 不自动加载 Payload；
- 不修改 `/api/audit/sessions` 的聚合和历史数据保留策略；
- 不把旧轨迹缺少诊断字段的问题误判为新实现回退；
- 不为了 E2E 通过而写死某个 trace ID、节点坐标或 viewport 像素。

用户此前提到“只看到一个 Session”和“V2 仍是旧轨迹”。这两项与本次边路由是不同层的问题：

- Session 列表由当前 Gateway 所读取的 Audit 数据目录、索引和 revision 决定；仅凭前端列表数量不能
  证明历史 JSONL 已删除；
- 旧轨迹是历史 Event/Graph 的只读展示，不会因新代码自动补造当时不存在的诊断或关系证据；
- 路由修复可以对旧 Graph 中已经存在的 `tool_*` 关系生效，但不能替旧记录创建关系；
- 若要验收新错误详情和新恢复证据，应新建会话产生新的真实 Trace；这不应与公共轨道路由实现耦合。

后续若继续调查 Session 数量，必须单独核对 Gateway 实际使用的 audit root、catalog/SQLite read model、
API 分页/过滤和原始 append-only JSONL，再判断是数据源切换、索引未重建、过滤条件还是数据确实不存在。
不要通过修改或复制历史证据让旧 Session 出现在页面中。

## 14. 完成定义

只有同时满足以下条件，才可以称为“右侧公共恢复轨道完成”：

- [ ] `tool_retry` 使用 `right-source → right-target`；
- [ ] `tool_continuation` 使用 `right-source → right-target`；
- [ ] `tool_recovery` 使用 `right-source → right-target`；
- [ ] 三类关系进入同一个基于全图可见几何的轨道路由器；
- [ ] `railX` 位于相关节点包围盒之外，并保留明确 gap；
- [ ] 多条重叠关系使用确定性 slot，刷新后不漂移；
- [ ] 路径不穿过中间可见节点；
- [ ] sequence 和其他关系路径没有行为回退；
- [ ] 折叠、展开、Worker 更新和 fallback 布局会正确重算；
- [ ] dangling/隐藏端点不会生成错误路径；
- [ ] 桌面端可点击边并打开正确关系检查器；
- [ ] 移动端关系选择与检查器仍可用；
- [ ] 交互宽度至少 32px；
- [ ] 单元、组件、build、Chromium E2E 实际通过；
- [ ] `1440x900` 与 `390x844` 的坐标、像素和截图证据均通过；
- [ ] 页面没有新增 console/page error，也没有新增 Payload 自动请求；
- [ ] PR 如实记录核心 WebUI 路由契约变更与验证结果。

“Handle 改成 right/right”“SVG path 非空”或“肉眼看起来不完全重合”中的任意一项都不足以单独
宣称完成。

## 15. 下一位 AI 的执行清单

1. 阅读根目录 `AGENTS.md`、本文和当前 PR，不拉取或合并 `upstream`；
2. 检查分支、工作区、`origin/main` 基线和用户受保护修改；
3. 重新打开 `TraceGraph.tsx`、`TraceNode.tsx`、Layout Worker 和两处现有测试，确认符号未变化；
4. 先把本文第 11 节的几何合同写成失败测试；
5. 用纯函数实现 route points、`railX`、确定性 slot 和障碍物检查；
6. 将全部 `tool_*` 改为 `right-source → right-target` 并接入自定义路径；
7. 保持 sequence、其他关系、检查器、聚焦、移动端入口和 Payload 安全边界不变；
8. 运行聚焦测试、完整 WebUI 测试、build 和真实 Chromium 双视口验收；
9. 检查差异，只暂存本任务明确路径，使用中文提交并推送同一分支；
10. 更新 PR #10 的“改动内容”“验证结果”“风险与注意事项”；
11. 最终报告分支、提交、推送、PR、实际验证和剩余风险；
12. 明确写出“等待用户确认后合并 main”，不得自行合并。

## 16. 交接结论

当前问题已经定位到前端几何路由层：语义关系存在、三类 Tool 关系覆盖存在、侧边 Handle 也存在，
但 opposite-target 与默认 `getSmoothStepPath()` 组合无法形成公共轨道。

下一步不需要继续围绕 `list_dir` 补特例，也不需要修改恢复证据协议。正确修复是把 Handle 合同改为
right/right，并新增基于当前可见节点包围盒、支持确定性分槽和障碍物验证的右侧公共轨道路由器。
