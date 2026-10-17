# electric-agent · 电网智能体前端

[Vue 3](https://vuejs.org/) + [Element Plus](https://element-plus.org/) + Vite 实现的对话式 Web 前端，配套后端 [electric_langgraph](../electric_langgraph/README.md)。

能力：多会话管理（侧边栏切换）、SSE 流式输出、Markdown 富展示、结果表格与图表（负荷曲线 / 台账查询）、故障诊断结果页（缺陷检测 / 局放 / 行波测距 / 线路损耗）。

## 快速开始

前置：后端已在本机 8000 端口启动（`electric_langgraph`，见其后端 README）。

```bash
npm install
npm run dev
```

访问 http://localhost:3001 。开发代理会把 `/chat` 与 `/api` 转发到 `http://localhost:8000`（见 [vite.config.ts](vite.config.ts)）。

生产构建：

```bash
npm run build        # 产物在 dist/，可部署到任意静态服务器，反代 /chat、/api 到后端
```

## 技术栈

- Vue 3 + TypeScript + Vite 6
- Element Plus（UI 组件）
- SSE（EventSource）流式接收对话 token
