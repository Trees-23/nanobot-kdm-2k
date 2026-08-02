# Tool 恢复关系右侧公共轨道修复实施方案

更新时间：2026-08-03（Asia/Tokyo）

仓库：`/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k`

设计基线：`_other/Tool恢复关系右侧公共轨道Handoff.md`

当前任务分支：`codex/tool-error-recovery-plan`

当前 PR：[功能（工具错误）：统一可观测诊断与确定性恢复](https://github.com/Trees-23/nanobot-kdm-2k/pull/10)

文档状态：待实施；本文不代表代码和浏览器验收已经完成

## 1. 方案目标

将审计运行轨迹中的三类 Tool 关系边：

```text
tool_retry
tool_continuation
tool_recovery
```

从当前的 opposite-target 折线改为真正的右侧公共恢复轨道：

```text
right-source → 右侧公共恢复轨道 → right-target
```

修复完成后，关系边必须从 source 节点右侧离开，在当前可见图形的右边界外沿稳定轨道纵向移动，
再从 target 节点右侧进入。路径不能穿过中间节点，也不能因刷新、Graph edge 顺序或折叠状态变化而
随机漂移。

本方案只修改 WebUI 几何路由与相应测试，不修改 Audit Event、Graph 关系语义、恢复证据、Session
聚合或 Payload 安全边界。

## 2. 当前事实与根因

### 2.1 当前代码事实

`webui/src/components/traces/TraceGraph.tsx` 当前具备：

- `AuditEdge()`：所有边统一调用 `getSmoothStepPath(props)`；
- `edgeHandles()`：全部 `edge.type.startsWith("tool_")` 进入 Tool 关系 Handle 分支；
- `renderEdges`：将后端 Graph edge 转成 React Flow edge；
- `positions`：来自 Layout Worker 或 fallback layout；
- `NODE_WIDTH = 248`、`NODE_HEIGHT = 76`：语义节点的当前静态布局尺寸；
- 隐藏 attempt、折叠组和 active collapse group 已形成可见节点集合。

`webui/src/components/traces/nodes/TraceNode.tsx` 已经提供：

```text
right-source
right-target
```

因此不需要新增 Handle。

### 2.2 根因

当前 Tool 关系采用“source 同侧、target 对侧”：

```text
right-source → left-target
```

`getSmoothStepPath()` 只在端点附近做默认小偏移，不知道全图右边界、障碍物或其他 Tool 关系占用的
轨道。结果是路径从 source 右侧短暂外扩后横穿节点列。

问题不是 `list_dir` 特例，也不是遗漏某类 Tool。`tool_retry`、`tool_continuation`、
`tool_recovery` 当前都受同一几何缺陷影响。

### 2.3 当前测试盲区

- 组件测试只断言 Handle ID，并且仍期望 `right-source → left-target`；
- E2E 只断言 SVG path 非空且不同于 sequence；
- 没有断言 `railX` 位于节点外侧；
- 没有节点矩形相交检查；
- 没有多边分槽稳定性检查；
- 没有证明轨道在 `fitView` 后仍处于可见区域。

## 3. 不可变合同

### 3.1 Handle 合同

```text
tool_retry        right-source → right-target
tool_continuation right-source → right-target
tool_recovery     right-source → right-target
sequence          bottom-source → top-target
```

`spawn_branch`、`result_return`、`caused_by`、Provider retry 等其他关系保持现状。

### 3.2 路径合同

同 lane Tool 关系的基础路径点为：

```text
P0 = (source.right, source.centerY)
P1 = (railX,       source.centerY)
P2 = (railX,       target.centerY)
P3 = (target.right, target.centerY)
```

允许在 `P1`、`P2` 增加有界圆角，但必须保留以下不变量：

- P0 对应 `right-source`；
- P3 对应 `right-target`；
- 主纵向段固定在同一个 `railX`；
- 最后一段从右向左进入 target，箭头方向正确；
- 圆角不得侵入节点包围盒。

### 3.3 轨道合同

轨道使用 React Flow 图坐标，不使用 viewport 像素：

```text
visibleMaxRight = max(所有可见障碍物的 right)
baseRailX = visibleMaxRight + RAIL_GAP
railX = baseRailX + slot * SLOT_GAP
```

初始建议值：

```text
RAIL_GAP = 48
SLOT_GAP = 12
CORNER_RADIUS = 8
MIN_INTERVAL_GAP = 8
```

这些值必须作为命名常量，并由桌面、移动端真实截图校准。不得使用 `window.innerWidth`、CSS `right`、
当前 zoom 或固定屏幕像素计算轨道。

### 3.4 障碍物合同

参与右边界与相交检查的对象包括：

- 当前可见语义节点；
- 当前可见 collapse presentation node；
- Region 的可见右边界或等价的 28 图坐标外边距。

不把 MiniMap、Controls、Inspector、viewport 边缘当成图坐标障碍物。

source、target 自身只允许路径在对应右侧 Handle 接触边界。其他水平段和纵向段不得与任何可见节点
矩形相交。相交判断需要计入线宽、箭头和最小 clearance，不能只检查中心线。

### 3.5 语义与安全合同

- 前端只消费后端显式 `tool_*` 关系，不重新推断 retry/recovery；
- 不根据 Tool 名称建立白名单；
- 不修改 Event、Graph 或恢复状态；
- 不自动请求 Payload；
- 不把 `continued`、`unresolved` 改成 `recovered`；
- 不改写历史 Audit、真实运行记录或截图；
- 旧 Graph 中已有 `tool_*` 边可以使用新路由，缺少关系的旧记录不得由前端补边。

## 4. 实现设计

### 4.1 新增纯几何模块

建议新增：

```text
webui/src/components/traces/toolRelationRouting.ts
```

该模块不得依赖 React、DOM、React Flow instance 或全局 viewport。建议暴露以下结构：

```ts
export interface RoutePoint {
  x: number;
  y: number;
}

export interface RouteNodeBounds {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ToolRelationEdgeInput {
  id: string;
  type: "tool_retry" | "tool_continuation" | "tool_recovery";
  source: string;
  target: string;
}

export interface RouteBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ToolRelationRouteInput {
  edges: ToolRelationEdgeInput[];
  nodeBounds: RouteNodeBounds[];
  rightBoundary: number;
}

export interface ToolRelationRoute {
  edgeId: string;
  slot: number;
  railX: number;
  points: readonly RoutePoint[];
  path: string;
  bounds: RouteBounds;
}
```

具体名称可按现有 TypeScript 风格调整，但必须保留结构化 `slot`、`railX` 和 `points`，不能只返回一条
无法可靠断言的 `path` 字符串。

建议提供纯函数：

```ts
buildToolRelationRoutes(input): Map<string, ToolRelationRoute>
buildOrthogonalRoutePath(points, cornerRadius): string
segmentIntersectsBounds(segment, bounds, clearance): boolean
```

如果实现者能用更少的纯函数保持同样可测性，可以合并命名；不要引入通用图布局框架或新的运行时依赖。

### 4.2 可见边与端点准备

在 `TraceGraph` 中先复用当前隐藏规则构建 `visibleGraphEdges`：

```text
不含 hiddenAttemptIds 的端点
不含 hiddenCollapsedIds 的端点
source 和 target 都存在于当前可见 render node 集合
```

再从中筛选 `edge.type.startsWith("tool_")` 作为路由输入。dangling 或隐藏端点不生成 route，也不退回
到 `(0, 0)` 或普通直连。

端点用布局数据计算：

```text
sourceX = source.position.x + source.width
sourceY = source.position.y + source.height / 2
targetX = target.position.x + target.width
targetY = target.position.y + target.height / 2
```

若未来节点改为动态尺寸，应优先使用 React Flow measured dimensions；当前实现可使用已有显式 width/
height，但不得复制另一套与 `renderNodes` 不一致的尺寸常量。

### 4.3 确定性 slot 分配

为每条 Tool 关系建立纵向占用区间：

```text
startY = min(sourceY, targetY)
endY = max(sourceY, targetY)
```

按以下 tuple 稳定排序：

```text
startY, endY, edge.type, edge.id
```

采用 first-fit 区间着色：

1. 从 slot 0 开始检查；
2. 与该 slot 已有区间间距至少为 `MIN_INTERVAL_GAP` 时可以复用；
3. 否则尝试下一 slot；
4. 分配结果只由几何和稳定 ID 决定；
5. Graph edge 输入顺序变化不得改变结果。

三种关系共享同一 slot 池，避免不同类型在相同坐标完全重叠。

### 4.4 路径生成

基础实现优先使用自定义正交 SVG path。不要继续依赖默认 `getSmoothStepPath()` 来决定 Tool 轨道。

建议先实现无圆角的确定性路径并通过几何测试，再增加小圆角。圆角算法必须限制半径：

```text
effectiveRadius = min(
  CORNER_RADIUS,
  abs(P1.x - P0.x) / 2,
  abs(P2.y - P1.y) / 2,
  abs(P3.x - P2.x) / 2
)
```

sourceY 与 targetY 相同或垂直区间过短时，允许半径降为 0，但仍需保持外置 railX 和正确 target 方向。

### 4.5 `TraceGraph` 接入

修改：

```text
webui/src/components/traces/TraceGraph.tsx
```

建议接入顺序：

1. `edgeHandles()` 将全部 `tool_*` 映射为 right/right；
2. 从 `renderNodes` 或其上游布局数据构造可见 bounds；
3. 用 `useMemo` 一次性计算全部 Tool route；
4. `renderEdges` 将 route 放入 `edge.data.toolRoute`；
5. `AuditEdge()` 对存在 `toolRoute` 的边使用自定义 path；
6. 其他 edge 继续调用 `getSmoothStepPath(props)`；
7. `BaseEdge` 继续保持 marker、style 和至少 32px `interactionWidth`。

不要让每个 `AuditEdge()` 独立扫描节点或分配 slot。slot 必须在同一批 Tool 关系上统一计算。

新增 `useMemo` 的依赖至少覆盖：

- visible edges；
- visible/render node bounds；
- `positions`；
- active collapse groups；
- expansion/collapse 产生的可见集合。

pan/zoom 不改变 route。Graph revision、Worker positions、fallback positions、折叠/展开或节点尺寸改变时
必须重算。

### 4.6 fitView 与轨道可见性

公共轨道位于节点右边界之外，而当前初始布局和关系定位主要对节点执行 `fitView`。实现后必须确认：

- 初次进入轨迹时最外侧 slot 没有被 viewport 裁掉；
- 点击关系并调用 `selectEdge()` 后，source、target 和完整轨道同时可见；
- 桌面和移动端没有为了容纳轨道把节点缩小到不可读。

优先依靠现有合理 padding；如果真实测试证明轨道被裁切，再以 route `bounds` 调用 React Flow 的
`fitBounds` 或等价 API。不得创建会进入语义图、MiniMap、键盘导航或节点计数的伪节点。

### 4.7 跨 lane 处理

本轮最低交付必须覆盖真实同 lane 的 retry、continuation、recovery。另需建立一个跨 lane fixture。

三段路径的水平引出段若穿过右侧 lane 节点，不得静默回退为 Smooth Step。实现者应：

1. 让 `baseRailX` 位于所有相关障碍物外侧；
2. 对 P0-P1、P1-P2、P2-P3 执行矩形相交检查；
3. 若水平段仍冲突，使用确定性的上/下净空 waypoint 或专用跨 lane corridor；
4. 为最终选择增加纯几何和浏览器 fixture；
5. 若当前数据合同无法产生合法跨 lane Tool 关系，应以测试记录该不变量，而不是省略说明。

不得画一条已知穿过节点的 fallback 路径。

## 5. 分阶段实施

### 阶段 0：基线与失败测试

目标：确认代码事实未变化，并让新几何合同以正确原因失败。

操作：

1. 阅读 `AGENTS.md`、Handoff、本文和当前 PR；
2. 检查分支、工作区、远端和 `origin/main`；
3. 确认 `AuditEdge`、`edgeHandles`、`renderEdges`、六个 Handle 和测试符号仍存在；
4. 新增 `webui/src/tests/tool-relation-routing.test.ts`；
5. 更新 `trace-graph.test.tsx` 的 right/right 期望；
6. 增加单边、多边、输入乱序、障碍物、隐藏端点和 sequence 不回退测试；
7. 运行聚焦测试，保存预期失败原因。

退出条件：测试失败只因为公共路由尚未实现，而不是 fixture、类型或测试环境错误。

### 阶段 1：纯几何路由器

目标：在不接触 React/DOM 的情况下完成轨道计算。

操作：

1. 新增 `toolRelationRouting.ts`；
2. 实现 right boundary、端点、稳定排序和 first-fit slot；
3. 输出 points、railX、slot、path 和 bounds；
4. 实现线段与矩形相交检查；
5. 覆盖同向、反向、同 Y、短区间和多个重叠区间；
6. 证明输入 edge 顺序变化不影响结果。

退出条件：纯几何测试全部通过，不依赖 jsdom 尺寸偶然值。

### 阶段 2：`TraceGraph` 接入

目标：让真实 React Flow edge 使用公共轨道。

操作：

1. Tool Handle 改为 right/right；
2. 基于当前可见 nodes/positions 统一构建 routes；
3. `AuditEdge` 只对 Tool route 使用自定义 path；
4. 保留颜色、dash、marker、z-index、opacity 和 32px 点击区域；
5. 保留恢复链路聚焦、关系检查器、移动端关系按钮和双端 Event 定位；
6. 覆盖 Worker 成功、Worker error fallback、折叠和展开后的重算；
7. 检查 route 超出节点 bounds 后的 fitView 可见性。

退出条件：组件测试通过，三种 Tool 关系路径结构正确，普通边行为未变。

### 阶段 3：真实 Gateway 与浏览器验收

目标：在新产生的 Trace 上证明用户可从前端看到修复效果。

操作：

1. 使用现有真实运行生成器或等价受控场景创建新的 Session/Trace；
2. 场景包含 `tool_retry`、`tool_continuation`、`tool_recovery`；
3. 不复用“子智能体终止与恢复 V2”作为唯一证据；
4. 启动真实 Gateway 和 WebUI；
5. 在 Chromium `1440x900`、`390x844` 执行 E2E；
6. 读取节点和 SVG 的屏幕坐标，断言轨道位于节点右侧且不相交；
7. 检查最外侧 slot 未被裁掉；
8. 分别打开三类关系检查器并定位两端 Event；
9. 断言无 console/page error、无新增 Payload 自动请求；
10. 保存新 Trace ID、Session key、截图和坐标/像素证据。

退出条件：用户能在前端打开新的运行轨迹并看到 right/right 公共轨道，双视口自动化通过。

### 阶段 4：完整门禁与交付

目标：确认没有 WebUI 回归并完整维护当前 PR。

操作：

1. 运行完整 WebUI 测试；
2. 运行生产 build；
3. 执行 `git diff --check`；
4. 只暂存本任务明确路径；
5. 按独立工作单元创建中文提交并立即推送；
6. 更新 PR #10 的改动内容、验证结果、风险和新 Trace 验收入口；
7. PR 保持可审查状态；
8. 未经用户明确确认不得合并 `main`。

## 6. 测试矩阵

### 6.1 纯几何测试

至少覆盖：

- right/right 端点；
- railX 大于全部相关 right boundary 加 gap；
- 纵向主段固定 railX；
- source 在 target 上方、下方和同 Y；
- 单边 slot 0；
- 重叠区间分配相邻 slot；
- 非重叠区间复用 slot；
- edge 输入乱序结果不变；
- route points/path 重复构建稳定；
- 任一 segment 不与障碍节点相交；
- 短段圆角安全降级；
- dangling/隐藏端点不生成 route；
- 跨 lane fixture 有明确无相交结果；
- `tool_retry`、`tool_continuation`、`tool_recovery` 全覆盖。

### 6.2 组件测试

至少覆盖：

- `edgeHandles()` 对三类 Tool 关系返回 right/right；
- sequence 仍返回 bottom/top；
- Tool edge data 含结构化 route；
- Tool path 不调用普通中点路由结果；
- 关系样式和 32px interaction width 保留；
- 聚焦恢复链路仍统计三类关系；
- 折叠/展开后 route 重算；
- Worker fallback 后 route 正确；
- 点击关系仍打开检查器；
- 移动端关系入口仍存在。

### 6.3 真实浏览器测试

至少覆盖：

- 新 Session/Trace 可从 `/traces` 导航进入；
- 三类边均可见且为 right/right；
- 轨道屏幕 x 大于相关节点 `DOMRect.right` 加可见间距；
- 路径采样点不落入非端点节点矩形；
- 多条边的 slot 可区分且稳定；
- 初始 fit、关系 fit 和 viewport resize 后轨道可见；
- `1440x900` 与 `390x844` 均无重叠或裁切；
- 检查器显示正确关系类型、证据和双端 Event；
- Payload 请求数不增加；
- browser console/page errors 为零；
- 截图和 canvas/SVG 像素检查非空。

## 7. 验证命令

聚焦测试：

```bash
cd webui
bun run test -- src/tests/tool-relation-routing.test.ts src/tests/trace-graph.test.tsx \
  src/tests/audit-trace-ux.test.tsx
```

完整 WebUI：

```bash
cd webui
bun run test
bun run build
```

真实浏览器：

```bash
cd webui
bunx playwright test e2e/audit-tool-recovery-real.spec.ts --project=chromium
```

Git 检查：

```bash
git diff --check
git status --short
```

本轮预计不修改 Python。若实施者发现必须修改 Graph/API/Python，应先证明前端合同无法独立完成，并按
仓库规则补充最接近的 pytest、`ruff check` 和 PR 风险说明，不得暗自扩大范围。

## 8. 预计改动文件

必需：

```text
webui/src/components/traces/TraceGraph.tsx
webui/src/components/traces/toolRelationRouting.ts
webui/src/tests/tool-relation-routing.test.ts
webui/src/tests/trace-graph.test.tsx
webui/e2e/audit-tool-recovery-real.spec.ts
```

按真实验收需要可能修改：

```text
webui/e2e/generate-audit-tool-recovery-runtime.py
_other/评测/Tool错误与恢复/...
```

只有在 fitView 需要共享类型或 route bounds 时，才考虑增加一个局部类型文件。不要拆出无必要的通用
Graph abstraction，也不要修改无关节点、Inspector 或后端模块。

## 9. 提交建议

建议工作单元与中文提交：

1. `测试（审计图）：冻结Tool关系右侧轨道几何合同`
2. `修复（审计图）：实现Tool关系右侧公共轨道`
3. `测试（审计图）：完成公共轨道真实双视口验收`

如果测试和实现无法独立保持可验证状态，可以合并前两个提交，但不得提交明知失败且说明为完成的状态。
每次提交前检查差异，只暂存明确路径；提交后立即推送同一任务分支并更新同一 PR。

## 10. 风险与控制

| 风险 | 表现 | 控制方式 |
|---|---|---|
| 轨道被 fitView 裁掉 | 几何正确但用户看不到 | route bounds + 双视口可见性断言 |
| 多边完全重合 | 只能选中最上层关系 | 确定性区间分槽，共享 slot 池 |
| edge 顺序导致抖动 | 刷新后轨道左右跳 | 稳定 tuple 排序，乱序测试 |
| 跨 lane 横穿节点 | 水平引出段穿过右侧 lane | 全 segment 相交检查和专用 corridor |
| 折叠后旧 route 残留 | 轨道指向隐藏节点 | visible bounds/edges 作为 memo 输入 |
| target 箭头方向错误 | 箭头背离 target | right-target 合同和 SVG 方向测试 |
| 点击区域回退 | 桌面难以选择边 | 保持 `interactionWidth >= 32` |
| 移动端过度缩放 | 节点和文字不可读 | 双视口截图、fit 策略不引入伪节点 |
| 修改语义层 | 前端开始猜测恢复 | 仅消费显式 `tool_*`，禁止后端扩域 |
| 泄露 Payload | E2E 或 route data 带敏感内容 | route 只含坐标/ID，断言零新增请求 |

## 11. 回退方案

公共轨道路由是纯前端 additive 计算，不需要数据迁移。出现严重展示回归时：

1. 回退本次 WebUI 路由提交；
2. 保留已经存在的后端 `tool_*` 关系和诊断数据；
3. 不删除或改写 Audit JSONL；
4. 不回退 Tool 错误归一化与恢复证据提交；
5. 记录失败 viewport、Trace ID 和 route 输入，修复后重新验收。

不得通过切换到 `left-target` 并删除新测试来宣称问题解决。

## 12. 完成定义

- [ ] 三类 Tool 关系全部使用 `right-source → right-target`；
- [ ] 三类关系共享纯几何公共轨道路由器；
- [ ] `railX` 位于可见障碍物外侧并保留 gap；
- [ ] 多边 slot 确定、可区分、输入乱序稳定；
- [ ] 全部路径 segment 不穿过可见节点；
- [ ] dangling、隐藏、折叠和 Worker fallback 正确处理；
- [ ] sequence 和其他关系没有行为变化；
- [ ] 样式、箭头、点击区域、聚焦和检查器没有回退；
- [ ] 初始 fit 和关系 fit 均完整显示轨道；
- [ ] 新 Session/Trace 在前端展示修复效果；
- [ ] Chromium `1440x900` 和 `390x844` 坐标、像素、截图均通过；
- [ ] 无 console/page error，无新增 Payload 请求；
- [ ] 聚焦测试、完整 WebUI、build、E2E 实际通过；
- [ ] 中文提交已推送并更新 PR #10；
- [ ] 最终报告明确实际验证、剩余风险和回退状态；
- [ ] 未经用户确认没有合并 `main`。

## 13. 实施结论

本修复不需要围绕 `list_dir` 增加特例，也不需要改变恢复证据协议。最小且正确的实现是：冻结全部
`tool_*` 的 right/right Handle 合同，新增一个以当前可见节点包围盒为输入的纯几何路由器，对重叠
关系确定性分槽，并让 `AuditEdge` 仅对 Tool 关系使用该路径。

实现质量的判断依据不是“SVG 有线”或“看起来和 sequence 不同”，而是可证明的外置 `railX`、无节点
相交、稳定分槽、完整 fitView 可见性，以及新 Trace 在桌面和移动端的真实浏览器证据。
