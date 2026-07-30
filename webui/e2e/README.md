# 审计轨迹浏览器验收

`audit_trace_scroll.py` 使用原生 Python Playwright 驱动真实的 `TraceTimeline` 组件，
以 536 条 Event、首批 200 条和两次增量加载覆盖桌面与移动布局。

先启动独立 Vite 端口：

```bash
bun run dev -- --host 127.0.0.1 --port 5174
```

再运行三浏览器矩阵：

```bash
python e2e/audit_trace_scroll.py \
  --base-url http://127.0.0.1:5174/e2e/audit-trace.html
```

可用一个或多个 `--browser chromium|firefox|webkit` 缩小范围。测试会输出每个场景的
viewport 尺寸、`clientHeight`、`scrollHeight`、`scrollTop`、首末可见 Event、已加载数量
和 next cursor，并检查浏览器 console error。

以下输入依赖真实桌面环境，自动化不得替代人工验收：

[] a. 在 125% 和 150% browser zoom 下复核普通、最大化和拖高布局。

[] b. 使用真实 trackpad 惯性连续滚动，确认无卡死或方向反转。

[] c. 使用操作系统原生 scrollbar 拖动到 Event 200、201 和 536。

[] d. 使用用户真实 Trace 进行只读复核，确认分页后不重复、不丢失且不跳回顶部。
