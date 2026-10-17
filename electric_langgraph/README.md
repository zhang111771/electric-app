# ⚡ 电网智能体（electric_langgraph）

基于 **LangGraph** 的通用/综合型电网智能体：对话式 Web 应用，覆盖**规程知识问答、电网数据查询、负荷预测计算**三类核心能力，多轮对话自带上下文记忆，内置模拟数据开箱即用。

## 功能地图

| 模块 | 能力 | 说明 |
|---|---|---|
| 🧠 知识问答 | RAG 检索规程库作答 | 安规、调度规程、变压器规程、术语、事故案例 |
| 🗄️ 数据查询 | 自然语言转 SQL | 台账 / 负荷曲线 / 告警事件查询，时间序列自动画折线图 |
| 🔮 负荷预测 | 基于历史规律预测 | 指定节点未来 N 小时负荷，给出峰值与走势 |
| 🤖 意图路由 | LangGraph supervisor | 按意图分发到专职智能体，多轮上下文记忆 |
| 💬 对话界面 | 流式输出（SSE） | 单页 Web，零外部前端依赖，内嵌 SVG 图表 |

## 快速开始

> 前置依赖：本地已安装并启动 **PostgreSQL + pgvector 扩展**（本项目默认 `postgresql://postgres:postgres@localhost:5432/electric_power`，首次运行会自动创建 `electric_power` 库）；**Ollama 已拉取 bge-m3** embedding 模型（检索向量编码，完全离线）。

1. **配置 API Key 与数据库**（LLM Key 必填）
   ```bash
   cp .env.example .env
   # 编辑 .env：
   #   LLM_API_KEY   必填（默认 DeepSeek，可换 Qwen/智谱/硅基流动）
   #   POSTGRES_URI  本地 PostgreSQL 连接串（默认 postgres:postgres@localhost:5432/electric_power）
   #   EMBEDDING_*   检索向量模型（默认本地 Ollama bge-m3，离线可用）
   ```

2. **安装 pgvector + 导入知识库**
   ```bash
   # pgvector 未装时：用 Stack Builder 勾选 pgvector 组件，或离线包方式（见 docs/离线部署.md）
   ollama pull bge-m3              # 首次拉取本地 embedding 模型（约 600MB）
   python data/seed_knowledge.py   # 切块 + bge-m3 编码 → 写入 pgvector 表
   ```

3. **一键启动**
   ```bash
   bash scripts/run_local.sh
   ```
   浏览器打开 http://127.0.0.1:8000

4. **提问示例**
   - 知识问答：`变压器过负荷运行有什么规定？`
   - 数据查询：`有哪些110kV主变？` / `城东变电站1号主变最近一周负荷趋势`
   - 告警查询：`最近有什么严重告警？`
   - 负荷预测：`预测城东变电站1号主变明天负荷`
   - 多轮追问：`那35kV的呢？`

## Docker 一键部署（可选）

整套服务（PostgreSQL + 应用）用 Docker 一条命令跑起来，无需本机装 PostgreSQL/Ollama：

```bash
cp .env.example .env    # 填好 LLM_API_KEY（compose 会自动读取本仓库 .env）
docker compose up -d --build
# 打开 http://localhost:6006
```

- 默认知识库检索用 **tfidf**（零依赖开箱即用）；语义检索（pgvector + Ollama bge-m3）
  见 [docs/Docker部署.md](docs/Docker部署.md)。
- GPU 加速 YOLO：`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`。
- 数据在 named volume 里，`docker compose down -v` 才会清空。

## 项目结构

```
electric_langgraph/
├── config.py               # 全局配置（.env 加载、路径、POSTGRES_URI）
├── sitecustomize.py        # Windows 事件循环修复（psycopg 异步需要）
├── graph/                  # LangGraph 图
│   ├── state.py            #   共享状态（messages/intent/chart）
│   ├── db.py               #   PostgreSQL 连接层（自动建库/建 vector 扩展）
│   ├── embeddings.py       #   Embedding 统一封装（默认本地 Ollama bge-m3）
│   ├── router.py           #   意图路由节点
│   ├── retriever.py        #   知识库检索器（pgvector HNSW 默认 / tfidf 兜底 / api 旧方案）
│   ├── tools.py            #   工具：query_sql / load_forecast
│   ├── agent_loop.py       #   ReAct 工具调用循环
│   ├── chart.py            #   查询结果 → 折线图/柱状图配置
│   ├── agents/             #   三个专职智能体
│   └── build_graph.py      #   组装编译图（PostgreSQL checkpointer 记忆）
├── data/
│   ├── generate_data.py    # 生成模拟台账/负荷/告警 → PostgreSQL
│   ├── seed_knowledge.py   # 规程文本 → 切块 → bge-m3 编码 → pgvector 表
│   └── assets/*.md         # 知识库种子文本（可替换为真实规程）
├── server/
│   ├── main.py             # FastAPI + SSE /chat 接口 + 会话管理
│   └── static/index.html   # 对话页面（markdown 表格/代码块/图表富展示）
├── bundle/pgvector/        # pgvector 离线部署包（二进制 + 说明）
├── docs/离线部署.md         # 完全离线部署手册
└── scripts/
    ├── run_local.sh        # 一键启动
    └── install_pgvector.ps1 # pgvector 离线一键安装（幂等）
```

## 配置说明（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址 |
| `LLM_API_KEY` | 空 | **必填**，否则对话会报错 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `EMBEDDING_MODE` | `pgvector` | 检索模式：`pgvector`（推荐，本地 Ollama bge-m3）、`tfidf`（零依赖兜底）、`api`（旧方案） |
| `EMBEDDING_BASE_URL/KEY/MODEL` | `http://localhost:11434/v1` + `bge-m3` | 向量编码模型（默认本地 Ollama，完全离线；可换硅基流动等 OpenAI 兼容服务） |
| `KB_VECTOR_TABLE/DIM` | `kb_chunks` / `1024` | pgvector 知识库向量表（bge-m3 维度） |
| `POSTGRES_URI` | `postgresql://postgres:postgres@localhost:5432/electric_power` | 本地 PostgreSQL：模拟业务数据 + 多轮对话记忆（checkpointer）+ 知识库向量 |
| `LANGSMITH_API_KEY` | 空 | LangSmith 链路追踪 key（在 smith.langchain.com 获取） |
| `LANGSMITH_TRACING` | `true` | 开启追踪后，每次对话都会在 LangSmith 记录完整链路 |
| `LANGSMITH_PROJECT` | `electric_langgraph` | LangSmith 项目名，用于区分不同应用 |

> 用 `pgvector` 模式时，先运行 `python data/seed_knowledge.py` 把知识库向量写入 pgvector 表（需已装 pgvector 扩展、Ollama 已有 bge-m3）。pgvector 不可用时自动回退 tfidf，不会报错。

## 待扩展功能（规划）

故障诊断建议、事故报告自动生成、工作票草拟与审核、潮流/N-1 校验、多智能体深化、图像识别（巡检/红外）、对接真实 SCADA 系统等。

## 环境

- Python 3.11，conda 环境 `langgraph`
- 依赖见 `requirements.txt`（langgraph 1.x / langchain-openai / fastapi / asyncpg / langgraph-checkpoint-postgres）
- PostgreSQL 16（本地服务 `postgresql-x64-16`）+ **pgvector 扩展**，业务表、对话记忆、知识库向量都存里面
- Ollama（本地向量编码 bge-m3），完全离线；离线部署见 `docs/离线部署.md`


