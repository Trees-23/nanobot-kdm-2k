# KdmCopilot

KdmCopilot 是一套面向个人工作流的 AI 助手。它运行在你自己的环境中，能够围绕日常沟通、信息整理、任务执行和长期工作持续提供协助。

## 能力

- 在浏览器或终端中进行连续对话
- 连接常用消息渠道，将助手带入已有沟通场景
- 使用文件、命令、网页、MCP、定时任务和图像生成等工具完成工作
- 保留会话历史、工作记忆和长期任务状态
- 支持多模型配置、备用模型和本地部署
- 提供 WebUI、OpenAI 兼容 API 与 Python 集成入口

## 本地启动

推荐使用 Docker Compose 运行。准备好 Docker 后，在仓库根目录执行：

```bash
docker compose up -d --build
```

启动完成后，打开 [http://localhost:8765](http://localhost:8765)。运行数据保存在 `runtime/`，首次使用前请按自己的模型服务和密钥配置该目录中的配置文件。

查看服务状态与日志：

```bash
docker compose ps
docker compose logs -f
```

停止服务：

```bash
docker compose stop
```

## 开发

运行环境要求 Python 3.11+。前端构建需要 Bun 或 npm。

```bash
python -m pip install -e ".[dev]"
cd webui && bun install && bun run build
```

常用检查：

```bash
ruff check .
pytest
cd webui && bun run test
```

## 项目结构

- 核心运行时：模型接入、工具、渠道与 API
- `webui/`：浏览器端工作台
- `docs/`：配置、部署和开发文档
- `runtime/`：本地运行数据与工作区

## 许可

本项目采用 [MIT License](./LICENSE)。
