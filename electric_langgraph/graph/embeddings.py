"""Embedding 统一封装：知识库入库（seed）与查询（retriever）共用。

通过 OpenAI 兼容接口生成向量，默认指向本地 Ollama（bge-m3，离线可用），
也可指向任意 OpenAI 兼容 Embedding 服务（硅基流动 / DashScope 等）。

为什么不用 langchain_openai.OpenAIEmbeddings：新版默认用 tiktoken 把文本
切成 token 数组发给接口（Ollama 不认，报 invalid input type）；关闭 tiktoken
又强制依赖 transformers（离线部署不划算）。因此这里用 openai 客户端直接调
/v1/embeddings，参数最干净，零新增依赖。
"""
import config

_client = None


def get_client():
    """单例获取 openai 客户端（base_url 由 EMBEDDING_BASE_URL 控制）。"""
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=config.EMBEDDING_API_KEY,
                         base_url=config.EMBEDDING_BASE_URL)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量编码文本 → 向量列表（seed 入库用）。"""
    resp = get_client().embeddings.create(model=config.EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_query(text: str) -> list[float]:
    """编码单条查询（检索用）。"""
    resp = get_client().embeddings.create(model=config.EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def vector_to_literal(vec: list[float]) -> str:
    """把向量转成 pgvector 的文本字面量，供 ::vector 转换。

    用字符串 + 显式 cast，避免引入 asyncpg 的 vector 类型适配（少一个离线依赖）。
    """
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
