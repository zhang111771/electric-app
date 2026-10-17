# 电网智能体 · AutoDL 云端部署手册

将本项目部署到 [AutoDL](https://www.autodl.com)（GPU 云服务器），公网可访问 Web 界面。
假设服务器**可联网**（AutoDL 默认 NAT 上网）；完全断网场景见同目录 [离线部署.md](离线部署.md)。

## 0. 部署架构总览

| 组件 | 用途 | 必须? | 说明 |
|---|---|---|---|
| PostgreSQL | 业务数据（台账/负荷/告警）+ 对话记忆（checkpointer） | ✅ 必须 | 本项目核心存储，默认 `electric_power` 库，首次运行自动建库 |
| pgvector 扩展 | 知识库向量表 | 推荐 | 一条 apt 命令装上；tfidf 模式下不用也不影响对话 |
| DeepSeek API | LLM | ✅ 必须 | OpenAI 兼容接口，`.env` 里配 key 即可 |
| Embedding（可选） | 知识库向量编码 | 可选 | 见下「两种检索方案」 |
| YOLOv8 缺陷检测 | 线路破损/鸟巢/异物检测 | 可选 | 云端自动用 `best.pt`（PyTorch），见第 7 节注意事项 |
| electric_mcp 三算法 | TDOA 定位 / 故障类型 / 局放识别 | 可选 | 纯 CPU 推理，numpy/sklearn/torch/h5py |

### 两种知识库检索方案

| 方案 | 依赖 | 效果 | 建议 |
|---|---|---|---|
| **tfidf（推荐起步）** | 零依赖、零费用 | 关键词检索，够用 | `EMBEDDING_MODE=tfidf`，`data/kb_chunks.json` 已随仓库带上，开箱即用 |
| pgvector + Ollama bge-m3 | 本地 Ollama（~600MB 模型）+ pgvector | 语义检索，效果更好 | 部署稳定后再升级，步骤见附录 A |

> 无论哪种方案，PostgreSQL 都必须装（业务数据 + 对话记忆都用它）。升级 pgvector 只需在**已有数据库**上执行一次 `CREATE EXTENSION vector`（代码会自动建，见 [graph/db.py](../graph/db.py)）。

---

## 1. 创建实例

1. AutoDL 控制台 → 租用 GPU 实例
   - **GPU**：最低 T4 即可（YOLO 推理建议有 GPU）；常见选 4090 / A100。
   - **镜像**：选 **PyTorch 2.x + Python 3.10/3.11/3.12 + CUDA** 的官方镜像（自带 torch+CUDA+opencv+numpy，省去手动装 torch）。
   - **数据盘**：默认 50G 足够（代码 ~40MB + 模型 + PG 数据 < 3G）。
2. 开机后进「容器实例」→ 复制 **SSH 登录指令**（形如 `ssh -p 12345 root@connect.xxx.seetacloud.com`）和密码。

## 2. 连接服务器

```bash
ssh -p <端口> root@connect.xxxx.seetacloud.com
# 输入密码后，先确认 Python / torch 就绪：
python --version
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# 期望输出类似: 2.5.1+cu124   True  （False 也能跑，YOLO 走 CPU 稍慢）
```

> AutoDL 镜像默认 `python` 指向一个装好 torch 的 conda 环境。若想用独立环境，见第 6 节备注。

## 3. 传输代码

仓库未用 git，最直接是 `scp` 整个目录（本机 Windows 在 Git Bash / PowerShell 下执行）：

```bash
# 本机执行（注意 -P 大写，替换为你的端口/地址）
scp -P <端口> -r electric_langgraph root@connect.xxxx.seetacloud.com:/root/autodl-tmp/
```

服务器上做清理（删掉 Windows 本地环境残留与敏感文件）：

```bash
cd /root/autodl-tmp/electric_langgraph
rm -rf .env __pycache__ server/__pycache__ graph/__pycache__ data/__pycache__ \
       tools/electric_mcp/__pycache__ tools/yolo_mcp/__pycache__ \
       screenshot .tmp_pgv bundle server/uploads/* server/reports/*
# best.engine 是 TensorRT 引擎，绑定本机 4060Ti/TRT11.1，云端用不上，删掉省 18MB：
rm -f tools/yolo_mcp/models/best.engine
```

> 也可先把项目 `git init` 推到私有仓库（Gitee/GitHub），再在服务器 `git clone`，后续升级代码更方便。无论哪种方式，**不要把本机 `.env`（含真实 key）带上去**。

## 4. 安装 PostgreSQL + pgvector

用 PGDG 官方仓库装 **PostgreSQL 16 + pgvector**（Ubuntu 20.04/22.04 通用）：

```bash
apt-get update
apt-get install -y postgresql-common
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y   # 添加官方仓库
apt-get install -y postgresql-16 postgresql-16-pgvector

# 启动并设置 postgres 密码（替换成你自己的强密码）
service postgresql start
pg_ctlcluster 16 main start   # 若上面命令没启动成功则执行这条
su - postgres -c "psql -c \"ALTER USER postgres WITH PASSWORD '你的密码';\""
```

验证 TCP 连接（应用走 `localhost:5432`）：

```bash
psql "postgresql://postgres:你的密码@localhost:5432/postgres" -c "SELECT version();"
pg_lsclusters   # 确认 16/main 监听 5432
```

> 若 5432 被系统自带的 PG14 占用了（AutoDL 官方 PyTorch 镜像一般不会预装 PG）：`pg_ctlcluster 14 main stop` 停掉旧集群，或把 PG16 改到别的端口并同步改 `.env`。
> pgvector 装不上时，可源码编译：`apt-get install -y build-essential postgresql-server-dev-16`，再 `git clone --branch v0.8.0 https://github.com/pgvector/pgvector && cd pgvector && make && make install`。

## 5. 配置 .env

```bash
cp .env.example .env
vi .env   # 至少改三处：
```

```ini
# 1) LLM key（必填，默认 DeepSeek，可换 Qwen/智谱/硅基流动）
LLM_API_KEY=sk-你的key

# 2) 数据库密码改成你第 4 步设的
POSTGRES_URI=postgresql://postgres:你的密码@localhost:5432/electric_power

# 3) 起步用零依赖检索（不改也行，默认 pgvector 需要 Ollama，见附录 A）
EMBEDDING_MODE=tfidf
```

## 6. 安装 Python 依赖

```bash
# 配国内镜像，加速
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 核心依赖（requirements.txt 已含 langgraph/fastapi/asyncpg 等）
pip install -r requirements.txt

# 算法工具依赖（electric_mcp + yolo_mcp）
pip install "openai" "numpy" "joblib" "h5py" \
            "scikit-learn==1.7.2" \
            "ultralytics>=8.3.0" "opencv-python>=4.8.0"
```

要点：

- **scikit-learn 固定 1.7.2**：`classify_gbdt.joblib` / `prpd_gbdt.joblib` 用该版本训练，避免 joblib 反序列化报错。
- **torch 不用装**：PyTorch 镜像已带（含 CUDA）。验证：`python -c "import torch, cv2; print(torch.__version__, cv2.__version__)"`。
- 若你新建了独立 conda 环境（`conda create -n electric python=3.11 -y && conda activate electric`），需补装 torch：
  `pip install torch torchvision`（清华源会带 CUDA 依赖，约 2.5GB，一次性）。
- `mcp` / `tensorrt` 包**不需要装**：本项目在 Web 进程里直接调用算法模块（[graph/cable_tools.py](../graph/cable_tools.py)），不经过 MCP 服务端；`tensorrt` 缺失时 YOLO 自动回退 `best.pt`。

## 7. 初始化数据与知识库

```bash
python data/generate_data.py   # 生成台账/负荷/告警 → 自动创建 electric_power 库（需 PG 已就绪）
python data/seed_knowledge.py  # tfidf 模式：仅重建 data/kb_chunks.json（随仓库已有，可跳过）
```

```bash
# 跑一个冒烟测试：确认对话链路通
python - <<'EOF'
import asyncio
from server.main import stream_chat
async def t():
    n = 0
    async for ev in stream_chat("有哪些110kV主变？", "smoke"):
        print(ev)
        n += 1
        if n > 3: break
asyncio.run(t())
EOF
```

> YOLO 注意事项：`tools/yolo_mcp/models/` 下的 `best.engine`（TensorRT）绑定本机 GPU 架构，云端会自动回退到 `best.pt`（PyTorch），**无需任何配置**。检测结果的 `engine` 字段会显示 `torch`，属正常现象。首次调用才加载模型（约 1~2 秒）。

## 8. 启动服务

Linux 上**不需要** Windows 的 `--loop server.loop_policy:selector_loop_factory` 参数（[sitecustomize.py](../sitecustomize.py) 在 Linux 是空操作）。用 tmux 保活：

```bash
tmux new -s web
bash scripts/run_autodl.sh        # 或手动： python -m uvicorn server.main:app --host 0.0.0.0 --port 6006
# 退出 tmux：Ctrl+B 再按 D；回来： tmux attach -t web
```

> 必须 `--host 0.0.0.0`，否则公网访问不到。端口默认用 **6006**（AutoDL 自定义服务的常见端口），可自改。

## 9. 公网访问（二选一）

**方法 A：AutoDL「自定义服务」（推荐，有公网 URL）**
1. 控制台 → 实例 → 更多 → **自定义服务**；
2. 填写服务端口 `6006`，应用类型选 Web/HTTP（或 TCP）；
3. 开启后得到公网地址，形如 `http://xxxx.service-xxx.autodl.com`，浏览器直接打开即可。

**方法 B：SSH 隧道（本机看，不走公网）**
```bash
ssh -CNg -L 6006:127.0.0.1:6006 -p <端口> root@connect.xxx.seetacloud.com
# 然后本机浏览器打开 http://127.0.0.1:6006
```

## 10. 验证

| 检查项 | 命令 / 操作 | 预期 |
|---|---|---|
| 服务存活 | `curl -s http://127.0.0.1:6006/` | 返回 HTML 页面 |
| 对话接口 | `curl -N -X POST http://127.0.0.1:6006/chat -H 'Content-Type: application/json' -d '{"message":"变压器过负荷运行有什么规定？","session_id":"test1"}'` | 流式返回 `token` / `done` 事件 |
| 电缆诊断 | 网页「电缆诊断」页上传 `tools/electric_mcp/sample_data/` 下的示例 CSV / 图片 | 返回定位/类型/缺陷结果 |
| YOLO | 网页诊断上传 `tools/yolo_mcp/sample_data/test_insulator.jpg` | 返回检测框 + 标注图，`engine=torch` |
| 多轮记忆 | 重启 uvicorn 后在同一会话提问 | 仍记得上文（checkpointer 在 PG） |

## 11. 常见问题

- **`/chat` 报 `Connection error` / 知识库答非所问**
  大概率是 Embedding 服务连不上（Ollama 未装/未启动），不是 DeepSeek 的问题。
  起步直接用 `EMBEDDING_MODE=tfidf`，或按附录 A 启动 Ollama。检索层会自动回退 tfidf，见 [graph/retriever.py](../graph/retriever.py)。
- **`joblib` 加载模型报错 / 版本警告**
  `scikit-learn` 版本与训练时不一致，固定 `==1.7.2` 重装。
- **YOLO 报 `Model not found`**
  `tools/yolo_mcp/models/best.pt` 被删了（第 3 步清理时误删）。重新从本机传一份，或从 `best.engine` 导出。
- **`/chat` 超时（120s）**
  检查 `LLM_API_KEY` 是否有效、服务器能否访问 `https://api.deepseek.com`（`curl -sI https://api.deepseek.com`）。AutoDL 若走公网代理异常，尝试 `source /etc/network_turbo`。
- **公网打不开**
  确认 uvicorn 绑定 `0.0.0.0`、端口在「自定义服务」里已开；防火墙一般由 AutoDL 托管。
- **对话能答、但问“有没有某设备”这类查询为空**
  说明 `generate_data.py` 还没跑（业务表为空），重跑第 7 步。

---

## 附录 A：升级到语义检索（pgvector + Ollama bge-m3）

1. 安装并启动 Ollama（GitHub releases 下载 `install.sh`，或 `curl -fsSL https://ollama.com/install.sh | sh`）：
   ```bash
   service ollama start || (ollama serve &)   # 后台运行
   ollama pull bge-m3                          # 约 600MB
   curl http://localhost:11434/v1/embeddings -d '{"model":"bge-m3","input":"测试"}'  # 验证
   ```
2. 改 `.env`：`EMBEDDING_MODE=pgvector`（`EMBEDDING_BASE_URL` 默认已指向 `http://localhost:11434/v1`，无需改）。
3. 入库（先清空再写向量表，约 1~2 分钟）：
   ```bash
   python data/seed_knowledge.py
   # 验证
   psql "postgresql://postgres:你的密码@localhost:5432/electric_power" -c "SELECT count(*) FROM kb_chunks;"
   ```
4. 重启服务。此后提问“规程里怎么规定……”会用语义检索；Ollama 挂掉时自动回退 tfidf，不中断问答。

> AutoDL 上 Ollama 默认用 CPU 推理 embedding，bge-m3 体量小，速度足够；若想用 GPU 跑，设 `OLLAMA_GPU` 相关环境变量（可选，非必需）。
