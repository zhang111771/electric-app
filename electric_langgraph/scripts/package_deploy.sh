#!/usr/bin/env bash
# ============================================================
# package_deploy.sh —— 打包迁移服务器所需的最小文件集
#
# 产物:  <项目根>/deploy_package.tar.gz
# 用法:  bash scripts/package_deploy.sh     （Windows 用 Git Bash 运行）
#
# 服务端部署:
#   tar -xzf deploy_package.tar.gz
#   cp .env.example .env        # 填 LLM_API_KEY 等（见 .env.example 注释）
#   docker compose up -d --build
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="deploy_package.tar.gz"

# 显式白名单：只打包 Dockerfile COPY 了的东西 + compose/.env 模板。
# 其余（bundle/ docs/ screenshot/ .claude/ .tmp_pgv/ 等）一律不进包。
INCLUDE=(
  Dockerfile
  docker-compose.yml
  docker-compose.gpu.yml
  .dockerignore
  .env.example
  requirements.txt
  config.py
  sitecustomize.py
  graph
  server
  data
  tools
  docker
)

files=()
for p in "${INCLUDE[@]}"; do
  [ -e "$p" ] && files+=("$p")
done

if [ "${#files[@]}" -eq 0 ]; then
  echo "错误：没有找到可打包的文件，请在项目根目录运行本脚本" >&2
  exit 1
fi

echo "打包 ${#files[@]} 个路径 → $OUT ..."
tar -czf "$OUT" \
  --exclude='*/__pycache__*' \
  --exclude='*.pyc' \
  --exclude='server/uploads/*' \
  --exclude='server/reports/*' \
  --exclude='data/eval/reports/*' \
  --exclude='data/assets/files/*' \
  --exclude='*/best.engine' \
  --exclude='*/annotated/*' \
  --exclude='*.mcp.json' \
  --exclude='.env' \
  "${files[@]}"

echo "完成：$(pwd)/$OUT（$(du -h "$OUT" | cut -f1)）"
echo
echo "服务端部署："
echo "  tar -xzf $OUT"
echo "  cp .env.example .env        # 在服务器上填写 LLM_API_KEY（.env 不打包，密钥不离开本机）"
echo "  docker compose up -d --build"
echo "  浏览器打开 http://<服务器IP>:6006"
