"""知识库检索器。

- pgvector（推荐）：向量存 PostgreSQL pgvector 表（HNSW 余弦索引），本地 Ollama bge-m3 编码。
- tfidf（兜底）：纯 Python 实现（中文单字 + 双字切分 + TF-IDF 余弦），零依赖零费用。
- api（旧方案）：OpenAI 兼容 Embedding 接口，向量存 kb_index.json、内存余弦。
"""
import json
import math
import re
from collections import Counter
from typing import Any

import config
from .db import connect
from .embeddings import embed_query, vector_to_literal

_CJK = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """中文文本切词：ASCII 单词 + 汉字单字 + 汉字相邻双字。"""
    text = text.lower()
    toks = re.findall(r"[a-z0-9]+", text)
    cjk = _CJK.findall(text)
    toks.extend(cjk)
    for i in range(len(cjk) - 1):
        toks.append(cjk[i] + cjk[i + 1])
    return toks


class TfidfRetriever:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.doc_tokens = [_tokenize(c["text"]) for c in chunks]
        df: Counter = Counter()
        for dt in self.doc_tokens:
            df.update(set(dt))
        self.N = len(chunks)
        self.idf = {t: math.log(self.N / (1 + cnt)) + 1 for t, cnt in df.items()}

    def _vec(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        return {t: (1 + math.log(c)) * self.idf.get(t, 0) for t, c in tf.items() if t in self.idf}

    @staticmethod
    def _cos(a: dict, b: dict) -> float:
        if not a or not b:
            return 0.0
        num = sum(a[k] * b[k] for k in set(a) & set(b))
        den = math.sqrt(sum(x * x for x in a.values())) * math.sqrt(sum(x * x for x in b.values()))
        return num / den if den else 0.0

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        qv = self._vec(_tokenize(query))
        scored = [(self._cos(qv, self._vec(dt)), i) for i, dt in enumerate(self.doc_tokens)]
        scored.sort(key=lambda x: -x[0])
        return [
            {"source": self.chunks[i]["source"], "section": self.chunks[i]["section"],
             "text": self.chunks[i]["text"], "score": round(s, 4)}
            for s, i in scored[:top_k] if s > 0
        ]

    async def asearch(self, query: str, top_k: int = 4) -> list[dict]:
        """异步包装（与 PgvectorRetriever 接口一致）。"""
        return self.search(query, top_k)


class ApiRetriever:
    """使用 OpenAI 兼容 Embedding 接口的向量检索（效果更好）。"""

    def __init__(self):
        idx = json.loads(config.KB_INDEX_PATH.read_text(encoding="utf-8"))
        self.chunks: list[dict] = idx["chunks"]
        self.vectors: list[list[float]] = idx["vectors"]

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        qv = embed_query(query)
        scored = sorted(
            ((sum(a * b for a, b in zip(qv, v)), i) for i, v in enumerate(self.vectors)),
            key=lambda x: -x[0],
        )
        return [
            {"source": self.chunks[i]["source"], "section": self.chunks[i]["section"],
             "text": self.chunks[i]["text"], "score": round(s, 4)}
            for s, i in scored[:top_k]
        ]

    async def asearch(self, query: str, top_k: int = 4) -> list[dict]:
        """异步包装（与 PgvectorRetriever 接口一致）。"""
        return self.search(query, top_k)


class PgvectorRetriever:
    """pgvector 向量检索：查询编码 → PostgreSQL HNSW 余弦相似度检索。

    向量以文本字面量 + ::vector 显式转换传给 asyncpg（不引入 vector 类型适配，
    少一个离线依赖）。首次 asearch 时校验扩展/表/数据就绪；查询向量编码失败
    （本地 Ollama 未启动等）时自动回退 tfidf，保证问答不因 embedding 挂掉。
    """

    def __init__(self):
        self._ready = False
        self._fallback: TfidfRetriever | None = None

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        conn = await connect()
        try:
            cnt = await conn.fetchval(
                f"SELECT count(*) FROM {config.KB_VECTOR_TABLE}")
            if not cnt:
                raise RuntimeError(
                    f"知识库向量表 {config.KB_VECTOR_TABLE} 为空，"
                    "请先运行 python data/seed_knowledge.py")
        finally:
            await conn.close()
        self._ready = True

    def _tfidf(self) -> TfidfRetriever:
        """懒加载 TF-IDF 兜底检索器（零依赖，本地 kb_chunks.json）。"""
        if self._fallback is None:
            chunks = json.loads(config.KB_CHUNKS_PATH.read_text(encoding="utf-8"))
            self._fallback = TfidfRetriever(chunks)
        return self._fallback

    async def asearch(self, query: str, top_k: int = 4) -> list[dict]:
        await self._ensure_ready()
        try:
            qv = vector_to_literal(embed_query(query))
        except Exception as e:
            # Ollama/Embedding 服务未启动等：回退 tfidf，不中断问答
            print(f"[retriever] 向量编码失败（{type(e).__name__}），回退 tfidf 检索")
            return self._tfidf().search(query, top_k)
        conn = await connect()
        try:
            rows = await conn.fetch(
                f"SELECT source, section, text, "
                f"1 - (embedding <=> $1::vector) AS score "
                f"FROM {config.KB_VECTOR_TABLE} "
                f"ORDER BY embedding <=> $1::vector LIMIT $2",
                qv, top_k)
        finally:
            await conn.close()
        return [{"source": r["source"], "section": r["section"], "text": r["text"],
                 "score": round(float(r["score"]), 4)} for r in rows]


# 文档级扩展缓存：source -> [chunk, ...]（文档内原顺序），模块级懒加载
_doc_cache: dict[str, list[dict]] | None = None


def _load_chunks_by_source() -> dict[str, list[dict]]:
    """按 source 分组的全部块（文档内保持原顺序）。

    与 seed 写入的 kb_chunks.json 同源，pgvector / tfidf / api 三种检索器共用，
    用于"先定位文档、再整篇展开"的两级检索。
    """
    global _doc_cache
    if _doc_cache is None:
        _doc_cache = {}
        for c in json.loads(config.KB_CHUNKS_PATH.read_text(encoding="utf-8")):
            _doc_cache.setdefault(c["source"], []).append(c)
    return _doc_cache


def expand_by_source(hits: list[dict], top_docs: int = 2, max_chars: int = 8000) -> list[dict]:
    """两级检索第二步：把命中块按 source 聚合，返回最相关源文件的整篇内容。

    解决"问某文档/规程内容时只捞到零散块、和原文件相差太大"的问题：
    - 源文件按块内最高相似度排序（命中块数做次级排序），取前 top_docs 个；
    - 这些源文件的全部块按文档原顺序返回（保持 准备→实施→结束 结构），
      每篇按 max_chars 限流，避免超大文件（规程类可达数百块）撑爆上下文；
    - 其余源文件命中的高分散块原样附加，保证具体事实类问题仍能答准。
    """
    if not hits:
        return hits
    by_src: dict[str, list[dict]] = {}
    for h in hits:
        by_src.setdefault(h["source"], []).append(h)
    ranked = sorted(
        by_src.items(),
        key=lambda kv: (max(c["score"] for c in kv[1]), len(kv[1])),
        reverse=True,
    )
    all_chunks = _load_chunks_by_source()
    per_doc_budget = max_chars // len(ranked[:top_docs]) if ranked else max_chars

    out, top_sources = [], set()
    for src, _ in ranked[:top_docs]:
        top_sources.add(src)
        used = 0
        for c in all_chunks.get(src, []):
            if used + len(c["text"]) > per_doc_budget:
                break
            out.append(c)
            used += len(c["text"])
    # 其余源文件的高分散块：补全具体问题答案，避免关键信息被整篇展开挤掉
    seen = {(c["source"], c["text"]) for c in out}
    out.extend(h for h in hits if h["source"] not in top_sources
               and (h["source"], h["text"]) not in seen)
    return out


_retriever: Any = None


async def get_retriever():
    """单例获取检索器（异步：pgvector 需探测数据库就绪）。

    pgvector 模式不可用（扩展未装/表为空/连接失败）时自动回退 tfidf，保证不报错。
    """
    global _retriever
    if _retriever is not None:
        return _retriever

    if config.EMBEDDING_MODE == "pgvector":
        try:
            r = PgvectorRetriever()
            await r._ensure_ready()
            _retriever = r
            print("[retriever] pgvector 检索已就绪")
            return r
        except Exception as e:
            print(f"[retriever] pgvector 不可用（{e}），回退 tfidf 检索")

    if config.EMBEDDING_MODE == "api" and config.KB_INDEX_PATH.exists():
        _retriever = ApiRetriever()
    else:
        chunks = json.loads(config.KB_CHUNKS_PATH.read_text(encoding="utf-8"))
        _retriever = TfidfRetriever(chunks)
    return _retriever
