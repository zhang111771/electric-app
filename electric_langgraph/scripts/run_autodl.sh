#!/usr/bin/env bash
# AutoDL 一键启动：生成模拟数据 → 构建知识库索引 → 启动 Web 服务 (0.0.0.0:6006)
# 用法： bash scripts/run_autodl.sh   （可设置 PYTHON 环境变量覆盖 Python 路径）
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

echo "[3/3] 启动 Web 服务 → http://0.0.0.0:6006"
# Linux 无需 Windows 的 selector loop 参数（sitecustomize.py 在 Linux 为空操作）；
# 绑定 0.0.0.0 供 AutoDL「自定义服务」公网访问。
"$PYTHON" -m uvicorn server.main:app --host 0.0.0.0 --port 6006
