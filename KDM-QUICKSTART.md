# nanobot 常用操作

## 进入项目

```bash
cd /home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 启动与停止

前台启动（终端持续显示日志，同时可以在浏览器中对话）：

```bash
docker compose up nanobot-gateway
```

保持这个终端窗口开启，然后访问 `http://localhost:8765`。按 `Ctrl+C` 会停止 nanobot。

后台运行方式：

```bash
# 后台启动
docker compose up -d nanobot-gateway

# 查看运行状态
docker compose ps

# 实时查看日志（Ctrl+C 退出日志，不停止服务）
docker compose logs -f --tail=100 nanobot-gateway

# 重启
docker compose restart nanobot-gateway

# 停止
docker compose down
```

更新代码或 Dockerfile 后重新构建：

```bash
docker compose build
docker compose up -d --force-recreate nanobot-gateway
```

## 配置文件

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k/runtime/config.json
```

主要运行数据：

```text
runtime/workspace/memory/    # 长期记忆
runtime/workspace/sessions/  # 对话历史和上下文
runtime/workspace/HEARTBEAT.md
runtime/cron/                # 定时任务
runtime/webui/               # WebUI 历史
runtime/audit/v1/            # Agent 审计事件、完整 payload、catalog 和查询索引
```

`runtime/` 包含 API Key、聊天记录、个人记忆和完整明文审计 payload，已加入
`.gitignore`，不要提交到 Git。审计记录可通过
`docker compose run --rm nanobot-cli audit ...` 查询。

修改配置后重启：

```bash
docker compose restart nanobot-gateway
```

## 浏览器与健康检查

WebUI：

```text
http://localhost:8765
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
