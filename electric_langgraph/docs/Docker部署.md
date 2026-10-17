# 电网智能体 · Docker 一键部署

用 Docker 把 **PostgreSQL（含 pgvector）+ 应用服务** 打包成一条命令部署，
无需在宿主机手动装 PostgreSQL / Ollama / Python 环境。适合长期稳定跑（轻量云
2C4G 即可，纯 CPU）；GPU 加速 YOLO 见文末。

> 知识库检索默认用 **tfidf**（零依赖、零费用、开箱即用）；想要语义检索
> （pgvector + Ollama bge-m3）见[第 4 节](#4-升级为语义检索pgvector--ollama-bge-m3)。

## 0. 前置

- 已装 **Docker**（Windows 用 Docker Desktop；Linux 服务器用 Docker Engine + Compose 插件）
- 拉取 `pgvector/pgvector:pg16` 与 `ollama/ollama`（首次 `up` 自动拉取）
- 项目根目录的 `.env` 里已填好 `LLM_API_KEY`（compose 会自动读取本仓库 `.env`
  做 `${VAR}` 替换，key 只进容器运行环境、不进镜像）

## 1. 一条命令启动

```bash
cp .env.example .env   # 确认 LLM_API_KEY 已填
docker compose up -d --build
```

- 首次构建约 5~10 分钟（主要下载 CPU 版 torch + ultralytics，镜像约 2GB）；
  之后秒级启动。
- 启动后打开 **http://localhost:6006**（默认端口，可用 `WEB_PORT=8000` 改）。
- 首次启动会自动：建库 → 生成模拟业务数据（台账/负荷/告警）→ 建知识库索引；
  之后再重启不会重复初始化。

## 2. 验证

```bash
docker compose ps          # 两个容器都应为 healthy / running
curl -s http://localhost:6006/ | head -5                 # 返回 HTML 页面
curl -N -X POST http://localhost:6006/chat \
     -H 'Content-Type: application/json' \
     -d '{"message":"有哪些110kV主变？","session_id":"test1"}'   # 流式返回 token/done
```

浏览器里上传 `tools/yolo_mcp/sample_data/test_insulator.jpg` 做缺陷检测、上传
`tools/electric_mcp/sample_data/` 下 CSV 做电缆诊断，验证四个诊断工具。

## 3. 常用运维命令

| 操作 | 命令 |
|---|---|
| 查看日志 | `docker compose logs -f app`（`db` / `ollama` 同理） |
| 重启服务 | `docker compose restart app` |
| 升级代码后重新部署 | `docker compose up -d --build` |
| 完全停止（保留数据） | `docker compose down` |
| **清空全部数据**（含数据库） | `docker compose down -v` |
| 进容器 | `docker compose exec app bash` / `docker compose exec db psql -U postgres -d electric_power` |

数据持久化位置（named volume，`down -v` 才会删）：

- `pgdata` — PostgreSQL 数据（业务表 + 对话记忆 + 知识库向量表）
- `uploads` — 诊断标注图；`reports` — HTML 检测报告

## 4. 升级为语义检索（pgvector + Ollama bge-m3）

默认 tfidf 关键词检索够用；要语义检索，两步：

1. 编辑 [docker-compose.yml](../docker-compose.yml) 中 `app` 的环境变量：
   `EMBEDDING_MODE: pgvector`（`EMBEDDING_BASE_URL` 保持 `http://ollama:11434/v1`）。
2. 带 profile 启动，顺带拉起 Ollama：

```bash
docker compose --profile semantic up -d
```

- Ollama 首次启动会自动 `ollama pull bge-m3`（约 600MB）。
- 入库后浏览器「知识库管理」页就有数据了；Ollama 挂掉时检索自动回退 tfidf，问答不中断。
- 注意：「知识库管理」页（kb 列表）只展示 pgvector 表里的块，tfidf 模式下显示为空属正常。

## 5. GPU 加速 YOLO（可选）

镜像默认 CPU 推理（YOLO 单张图约 0.5~2s，诊断场景够用）。要 GPU：

```bash
# 前置：宿主机 NVIDIA 驱动 + nvidia-container-toolkit
#   Windows Docker Desktop：Settings → Resources → WSL integration → 启用 GPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

这会用 CUDA 版 torch（约 3GB）重建一个 `electric-langgraph:gpu` 镜像。
注意：TensorRT 引擎（best.engine）绑定本机 GPU 架构，容器里不装 tensorrt，
YOLO 走 `best.pt`（PyTorch）在 GPU 上推理，结果里 `engine=torch` 属正常。

## 6. 常见问题

- **`required variable LLM_API_KEY is missing a value`** → 在项目根 `.env` 里填好
  `LLM_API_KEY`（compose 启动时会自动读取）。
- **对话报「Connection error」** → 大概率 Embedding 服务问题，默认 tfidf 模式不依赖
  Ollama；若改过 pgvector 模式，确认 Ollama 容器在跑（`docker compose --profile semantic ps`）。
- **joblib 报错 / sklearn 版本警告** → 镜像固定 `scikit-learn==1.7.2` 与模型训练版本一致，
  一般不会出现；出现的话进容器 `pip install scikit-learn==1.7.2` 并重启。
- **公网访问** → 轻量云上把安全组放行 `WEB_PORT`（默认 6006），或再配一层 Nginx 反代 + 域名。
- **对话能答、但数据查询为空** → `generate_data.py` 初始化失败过；`docker compose exec app bash -c "python data/generate_data.py"` 重跑，或 `docker compose down -v` 后重新 up。
- **端口被占** → `WEB_PORT=7000 docker compose up -d` 改宿主机映射端口。

## 7. Docker Hub 被墙 / 镜像加速

国内网络 Docker Hub（registry-1.docker.io）经常拉不动，报 `failed to do request ... EOF`。
两种处理方式：

**服务器（Linux 标准 Docker）**：在 `/etc/docker/daemon.json` 加镜像源后重启 docker：

```json
{ "registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1ms.run"] }
```

**Windows Docker Desktop**：daemon.json 里的 `registry-mirrors` 可能不生效（containerd
image store 的已知行为差异）。替代办法——先用镜像地址显式拉取并打回原名，构建时就会
命中本地镜像、不再访问 Docker Hub：

```bash
docker pull docker.m.daocloud.io/library/python:3.11-slim && docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim
docker pull docker.m.daocloud.io/pgvector/pgvector:pg16    && docker tag docker.m.daocloud.io/pgvector/pgvector:pg16 pgvector/pgvector:pg16
docker pull docker.m.daocloud.io/ollama/ollama:latest      && docker tag docker.m.daocloud.io/ollama/ollama:latest ollama/ollama:latest
docker pull docker.m.daocloud.io/docker/dockerfile:1       && docker tag docker.m.daocloud.io/docker/dockerfile:1 docker/dockerfile:1
docker compose build
```

## 8. 与 AutoDL 方案对比

| 维度 | Docker（本手册） | AutoDL（见 [AutoDL部署.md](AutoDL部署.md)） |
|---|---|---|
| 成本 | 轻量云 2C4G ~¥100/年，7×24 常驻 | 按小时计费，适合演示/验收 |
| GPU | 可选（需 nvidia-container-toolkit） | 自带（PyTorch 镜像） |
| 部署复杂度 | 一条命令，环境全部进镜像 | 手动装 PG/Ollama/依赖 |
| 数据持久化 | named volume，`down -v` 才删 | 数据盘，实例关机保留 |
