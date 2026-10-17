"""全局配置：加载 .env，提供路径与模型参数。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Windows 下 psycopg 异步（PostgreSQL checkpointer）不支持 ProactorEventLoop，
# 需在事件循环创建前切到 SelectorEventLoop（与 sitecustomize.py 双保险；
# uvicorn 默认 asyncio 循环也会自己切）。
if os.name == "nt":
    try:
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

# 数据文件路径
DATA_DIR = ROOT_DIR / "data"
KB_ASSETS_DIR = DATA_DIR / "assets"
KB_CHUNKS_PATH = DATA_DIR / "kb_chunks.json"
KB_INDEX_PATH = DATA_DIR / "kb_index.json"

# PostgreSQL（模拟台账/负荷/告警 + 对话记忆 checkpointer）
# 格式：postgresql://用户:密码@主机:端口/数据库
# 密码若含特殊字符（@ : / % 等）需做 URL 编码，例如 密码 a@b 写成 a%40b
POSTGRES_URI = os.getenv(
    "POSTGRES_URI", "postgresql://postgres:postgres@localhost:5432/electric_power"
)

# LLM（OpenAI 兼容）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# Embedding 检索模式：
#   tfidf    纯 Python 词频检索，零依赖零费用（兜底）
#   pgvector 推荐：向量存 PostgreSQL pgvector 表、HNSW 检索（默认本地 Ollama bge-m3）
#   api      向量存 kb_index.json 文件、内存余弦（旧方案，保留兼容）
EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "pgvector")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "ollama")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")

# pgvector 知识库向量表（与业务数据/对话记忆同库；bge-m3 向量维度 1024）
KB_VECTOR_TABLE = os.getenv("KB_VECTOR_TABLE", "kb_chunks")
KB_VECTOR_DIM = int(os.getenv("KB_VECTOR_DIM", "1024"))
