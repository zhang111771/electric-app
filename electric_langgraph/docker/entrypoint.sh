#!/usr/bin/env bash
# 容器入口：等数据库 → 首次初始化业务数据/知识库 → 启动 Web 服务。
# 初始化只在"全新库"上跑一次（generate_data.py 无幂等，重复跑会重复插数据）。
set -e
cd "$(dirname "$0")/.."

echo "[1/3] 等待 PostgreSQL 就绪 ..."
python - <<'PY'
import asyncio, os, sys
import asyncpg
URI = os.environ["POSTGRES_URI"]

async def wait_db():
    for _ in range(60):
        try:
            conn = await asyncpg.connect(URI, timeout=2)
            await conn.close()
            return
        except Exception:
            await asyncio.sleep(2)
    print("PostgreSQL 未就绪，请检查 db 容器日志", file=sys.stderr)
    sys.exit(1)

asyncio.run(wait_db())
PY

echo "[2/3] 检查是否需要初始化数据 ..."
if python - <<'PY'
import asyncio, os, sys
import asyncpg
URI = os.environ["POSTGRES_URI"]

async def is_fresh():
    try:
        conn = await asyncpg.connect(URI)
        try:
            # 业务表已建且有数据 → 非首次，跳过初始化
            return await conn.fetchval("SELECT count(*) FROM substations") == 0
        finally:
            await conn.close()
    except Exception:
        return True  # 业务表尚未创建 → 全新库

sys.exit(0 if asyncio.run(is_fresh()) else 1)
PY
then
  echo "  首次启动：生成模拟业务数据 + 知识库索引 ..."
  python data/generate_data.py
  # 知识库入库失败不致命：检索层会自动回退 tfidf（见 graph/retriever.py）
  python data/seed_knowledge.py || echo "  ⚠️  知识库入库失败，将以 tfidf 兜底检索"
else
  echo "  已有数据，跳过初始化"
fi

echo "[3/3] 启动 Web 服务 → http://0.0.0.0:${PORT:-6006}"
exec python -m uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-6006}"
