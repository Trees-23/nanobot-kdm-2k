# nanobot 常用操作

## 进入项目

```bash
cd /home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 启动与停止

日常使用前台启动，终端会持续显示日志：

```bash
docker compose up nanobot-gateway
```

保持这个终端窗口开启，然后访问 `http://localhost:8765`。按 `Ctrl+C` 会停止
nanobot。正常使用不需要加 `-d`。

常用管理命令：

```bash
# 查看运行状态
docker compose ps

# 停止
docker compose down
```

修改配置后，在运行日志的终端按 `Ctrl+C`，再重新执行前台启动命令。

更新代码、依赖或 Dockerfile 后，先完成任务提交，再使用项目脚本重建并替换唯一的长期 Gateway：

```bash
./scripts/rebuild_gateway_for_scenario.sh
```

脚本不会自动发送模型请求。将它输出的场景标识附在本次验收提示词中，在新会话页面发送；随后从运行轨迹
页面找到对应 trace，核对回答、工具行为、图和事件时间线。

长期 Gateway 固定使用 `8765` 与 `18790`，并一直挂载 `runtime/`，其工作区是
`runtime/workspace/`。如果这两个端口被其他容器占用，脚本会报告容器名称后退出；它不会自动换端口、
停止或删除其他容器。

## 配置文件

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k/runtime/config.json
```

Docker Compose 会把宿主机的 `./runtime` 挂载为容器内的
`/home/nanobot/.nanobot`。因此配置中的 workspace 应保持：

```json
"workspace": "~/.nanobot/workspace"
```

它在宿主机上实际对应 `runtime/workspace/`，不要改成宿主机绝对路径。

主要运行数据：

```text
runtime/workspace/memory/    # 长期记忆
runtime/workspace/sessions/  # 对话历史和上下文
runtime/workspace/skills/    # 工作区技能
runtime/workspace/cron/      # 当前定时任务
runtime/workspace/HEARTBEAT.md
runtime/webui/               # WebUI 历史
runtime/audit/v1/            # Agent 审计事件、完整 payload、catalog 和查询索引
runtime/media/               # 上传和生成的媒体文件
```

`runtime/` 包含 API Key、聊天记录、个人记忆和完整明文审计 payload，已加入
`.gitignore`，不要提交到 Git。审计记录可通过
`docker compose run --rm nanobot-cli audit ...` 查询。

## OpenAI API 配置

模型与 Provider 在 `runtime/config.json` 中配置，例如：

```json
{
  "agents": {
    "defaults": {
      "model": "gpt-5.5",
      "provider": "openai"
    }
  },
  "providers": {
    "openai": {
      "apiKey": "填写实际 API Key",
      "apiBase": "https://ai.klinkw.com",
      "apiType": "responses"
    }
  }
}
```

`apiType` 必须与中转服务实际提供的接口一致：

- `"responses"`：强制请求 `{apiBase}/responses`，适用于支持 OpenAI Responses API
  的 GPT-5.x 服务；失败时不会退回 Chat Completions。
- `"chat_completions"`：请求 `{apiBase}/chat/completions`。
- `"auto"`：对 OpenAI 官方地址可自动选择；对第三方地址通常会选择 Chat
  Completions，不适合用来确认第三方 Responses API。

Klink 明确提供的 `apiBase` 是 `https://ai.klinkw.com`，不要自行追加 `/v1`。
使用 `"responses"` 时，nanobot 会请求 `https://ai.klinkw.com/responses`。

修改配置后重新前台启动：

```bash
docker compose up nanobot-gateway
```

## 浏览器与健康检查

WebUI：

```text
http://localhost:8765
```

场景验收新会话：

```text
http://localhost:8765/#/new
```

运行轨迹：

```text
http://localhost:8765/#/traces
```

健康检查：

```bash
curl http://127.0.0.1:18790/health
```

## 同步官方代码

`origin` 是自己的仓库，`upstream` 是官方仓库。

```bash
git fetch upstream
git merge upstream/main
git push origin main
```

普通提交推送到自己的仓库：

```bash
git add .
git commit -m "描述本次修改"
git push
```
