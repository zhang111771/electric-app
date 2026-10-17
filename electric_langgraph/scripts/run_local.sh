#!/usr/bin/env bash
# 电网智能体一键启动：生成数据 → 建知识库 → 启动 Web 服务
# 用法： bash scripts/run_local.sh   （可设置 PYTHON 环境变量覆盖 Python 路径）
set -e
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

if [ ! -f ".env" ]; then
  echo "⚠️  未找到 .env 文件。请先执行: cp .env.example .env 并填入 LLM_API_KEY"
fi

echo "[1/3] 生成模拟数据..."
"$PYTHON" data/generate_data.py

echo "[2/3] 构建知识库索引..."
"$PYTHON" data/seed_knowledge.py

echo "[3/3] 启动 Web 服务 → http://127.0.0.1:8000"
# --loop server.loop_policy:selector_loop_factory：Windows 下强制 SelectorEventLoop
#（psycopg 异步访问 PostgreSQL 不支持 uvicorn 默认的 ProactorEventLoop）
"$PYTHON" -m uvicorn server.main:app --host 127.0.0.1 --port 8000 \
  --loop server.loop_policy:selector_loop_factory
#重启服务：1. netstat -ano | findstr :8000 2.taskkill /PID 13496 /F