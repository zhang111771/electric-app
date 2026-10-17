"""PostgreSQL 访问层：自动建库 + 连接。

业务数据（台账/负荷/告警）与对话记忆 checkpointer 都走本地 PostgreSQL，
连接串统一由 config.POSTGRES_URI 提供（默认 electric_power 库）。
"""
from urllib.parse import urlsplit

import asyncpg

import config

_checked = False  # 进程内只检查一次目标库是否存在


def _split_uri(uri: str) -> tuple[str, str]:
    """把 POSTGRES_URI 拆成 (基础库连接串, 目标库名)。

    例如 postgresql://postgres:postgres@localhost:5432/electric_power
    → (postgresql://postgres:postgres@localhost:5432/postgres, electric_power)
    """
    parts = urlsplit(uri)
    db = (parts.path or "/").lstrip("/") or "postgres"
    base = f"{parts.scheme}://{parts.netloc}/postgres"
    return base, db


async def ensure_database() -> None:
    """目标数据库不存在则创建（连接 postgres 维护库执行 CREATE DATABASE）。"""
    global _checked
    if _checked:
        return
    base, db = _split_uri(config.POSTGRES_URI)
    conn = await asyncpg.connect(base)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", db)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db}"')
            print(f"[db] 已创建数据库 {db}")
    finally:
        await conn.close()
    _checked = True


async def connect() -> asyncpg.Connection:
    """返回目标库连接；首次调用前确保数据库已创建。"""
    await ensure_database()
    return await asyncpg.connect(config.POSTGRES_URI)


async def ensure_pgvector() -> None:
    """确保 vector 扩展 + 知识库向量表 + HNSW 余弦索引存在（幂等）。

    仅在明确启用 pgvector 检索时调用（seed 入库 / 首次检索前），
    避免扩展未安装时阻塞业务数据与对话记忆的连接。

    注意：CREATE EXTENSION / TABLE / INDEX 的 IF NOT EXISTS 不是原子操作，
    并发请求同时首建时会撞唯一索引（pg_extension_name_index 等），
    故逐个捕获 UniqueViolationError 忽略——由先完成的那个请求负责建好。
    """
    conn = await connect()
    try:
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except asyncpg.UniqueViolationError:
            pass  # 并发下另一个请求已抢先创建扩展
        try:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {config.KB_VECTOR_TABLE} (
                    id SERIAL PRIMARY KEY,
                    source TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    embedding vector({config.KB_VECTOR_DIM})
                )""")
        except asyncpg.UniqueViolationError:
            pass  # 并发下另一个请求已抢先建表
        try:
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS kb_chunks_hnsw_idx
                ON {config.KB_VECTOR_TABLE} USING hnsw (embedding vector_cosine_ops)""")
        except asyncpg.UniqueViolationError:
            pass  # 并发下另一个请求已抢先建索引
    finally:
        await conn.close()
