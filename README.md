# ⚡ 电网智能体（electric-app）

基于 **LangGraph + FastAPI + Vue3** 的电网智能体：对话式 Web 应用，覆盖 **规程知识问答、电网数据查询、负荷预测计算、故障诊断** 等能力，多轮对话自带上下文记忆，内置模拟数据开箱即用。

## 仓库结构

```
electric-app/
├── electric_langgraph/    # 后端：LangGraph 智能体 + FastAPI 服务（自带单页对话 UI）
├── electric-agent/        # 前端：Vue3 + Element Plus 增强版对话界面（可选）
├── LICENSE                # MIT
└── .gitignore
```

| 子项目 | 说明 |
|---|---|
| [electric_langgraph/](electric_langgraph/README.md) | 核心：知识问答 RAG、自然语言转 SQL 数据查询、负荷预测、LangGraph 意图路由、SSE 流式输出。README 内含快速开始与 Docker 一键部署 |
| [electric-agent/](electric-agent/README.md) | 可选前端：多会话管理、表格/图表富展示、故障诊断结果页等，开发代理转发到后端 |

## 快速开始（后端）

```bash
cd electric_langgraph
cp .env.example .env          # 填入 LLM_API_KEY
bash scripts/run_local.sh     # 生成模拟数据 → 建知识库 → 启动 Web 服务
```

浏览器打开 http://127.0.0.1:8000 即可对话。详细说明见 [electric_langgraph/README.md](electric_langgraph/README.md)。

启动前端（可选）：`cd electric-agent && npm install && npm run dev`，访问 http://localhost:3001。

## 功能一览

- 🧠 **知识问答**：RAG 检索规程库作答（安规 / 调度规程 / 变压器规程 / 术语 / 事故案例）
- 🗄️ **数据查询**：自然语言转 SQL，查询台账 / 负荷曲线 / 告警事件，时间序列自动画折线图
- 🔮 **负荷预测**：基于历史规律预测指定节点未来 N 小时负荷
- 🔬 **缺陷检测（可选）**：YOLOv8 输电线路缺陷识别（`electric_langgraph/tools/yolo_mcp`）
- ⚡ **故障诊断（可选）**：TDOA 行波测距 / 局放 PRPD 模式识别（`electric_langgraph/tools/electric_mcp`）
- 🤖 **多智能体编排**：LangGraph supervisor 意图路由 + PostgreSQL checkpointer 对话记忆

## 开源说明

- 知识库全文语料（`data/assets/files/`）为内部材料，**不随仓库分发**；可自行放入后运行 `data/seed_knowledge.py` 重建索引。
- 所有 API Key / 数据库密码只写在 `.env`（已被 `.gitignore` 排除），提交时请勿包含。

## License

[MIT](LICENSE)
