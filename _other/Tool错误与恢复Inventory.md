# Tool 错误与恢复 Inventory

基线：`origin/main` (`c9925dcd`)。本清单覆盖 `nanobot/agent/tools/` 中可注册的内置 Tool、
动态 MCP Tool 和 entry-point 插件。错误可见性由 Runner 通用归一化保证；证据适配器只决定关系，
不能决定失败节点是否存在。

| Tool | 错误来源 | operation evidence | 无证据 fallback |
|---|---|---|---|
| `read_file` | validation、path policy、I/O、ToolResult | 规范化路径、精确重试、单层路径纠正 | `continued` / `unresolved` |
| `list_dir` | validation、path policy、I/O、ToolResult | 规范化目录、查询选项、精确重试、单层路径纠正 | `continued` / `unresolved` |
| `find_files` | validation、path policy、I/O、ToolResult | 规范化根目录和安全查询指纹、精确重试 | `continued` / `unresolved` |
| `grep` | validation、path policy、I/O、ToolResult | 规范化根目录和安全查询指纹、精确重试 | `continued` / `unresolved` |
| `write_file` | validation、path policy、I/O、ToolResult | 目标路径和 after hash，读取验证后恢复 | `continued` |
| `edit_file` | validation、path policy、冲突、ToolResult | 目标路径、before/after hash、替换数 | `continued` |
| `apply_patch` | validation、path policy、冲突、ToolResult | 受影响路径和 before/after hash | `continued` |
| `exec` | validation、policy、timeout、process、ToolResult | session/process identity、cwd、退出码 | `continued` / `unresolved` |
| `write_stdin` | validation、session 缺失、timeout、ToolResult | 同一 session/process identity 和动作 | `continued` |
| `list_exec_sessions` | runtime、ToolResult | session identity 的只读状态验证 | `unresolved` |
| `spawn` | validation、runtime、lifecycle、ToolResult | task ID、child Run ID、replacement claim | `continued` / `unresolved` |
| `await_subagents` | validation、runtime、lifecycle、ToolResult | task ID/group、child Run 和终态 receipt | `continued` / `unresolved` |
| `create_goal` | validation、session、version、ToolResult | goal ID/version 和持久化状态 | `unresolved` |
| `update_goal` | validation、session、version、ToolResult | goal ID/version 和状态转换 | `continued` / `unresolved` |
| `web_search` | validation、provider、HTTP、timeout、ToolResult | provider 和安全请求指纹的精确重试 | `continued` / `unresolved` |
| `web_fetch` | validation、SSRF policy、HTTP、timeout、ToolResult | 安全 URL 指纹和响应终态 | `continued` / `unresolved` |
| `message` | validation、routing、provider、ToolResult | channel/target 安全身份、幂等键、receipt | `unresolved`，禁止无幂等重发 |
| `cron` | validation、session、store、ToolResult | job ID/version，写后读取验证 | `continued` / `unresolved` |
| `generate_image` | validation、provider、timeout、ToolResult | provider request identity、可读产物引用 | `continued` / `unresolved` |
| `run_cli_app` | validation、policy、process、ToolResult | 应用声明的 operation/receipt | `unresolved` |
| `my` | validation、policy、runtime state、ToolResult | action/key 和版本化状态读取 | `continued` / `unresolved` |
| 动态 `mcp_*` | MCP schema、transport、provider、timeout、cancel | 插件显式 operation ID/receipt；否则仅精确重试 | `unresolved` |
| entry-point plugin | validation、legacy string、exception、ToolResult | 插件显式 evidence；否则仅进程内精确重试 | `unresolved` |

## 通用分类

- validation、ToolResult、exception、timeout、cancel、policy、provider 和 runtime 均进入 Runner
  的单一错误归一化边界。
- policy blocked 默认 `non_retryable`，不创建 recovery。
- retry 和 continuation 不代表恢复；只有适配器提供确定性 verification/receipt 才能写 recovery。
- 未列出专用错误码的分支使用稳定的通用错误码，不能产生空诊断。
- 动态 MCP、CLI 和第三方插件没有证据协议时保留失败节点并标为 `unresolved`。
