# -*- coding: utf-8 -*-
"""知识库种子脚本：读取 data/assets/ 下的规程文本 → 切块 → 保存。

- pgvector 模式（推荐）：调用本地 Ollama bge-m3 编码，向量写入 PostgreSQL pgvector 表。
- tfidf 模式：只需生成 kb_chunks.json，检索索引在运行时由纯 Python 构建（零依赖）。
- api 模式：调用 OpenAI 兼容的 Embedding 接口，把向量保存到 kb_index.json（旧方案）。

运行：python data/seed_knowledge.py
"""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

MAX_CHUNK = 600  # 每块最大字符数（600 减少表格/段落被从中间切碎的情况）


def split_chunks(text: str, source: str) -> list[dict]:
    """按 ## 章节 + 段落切块；文件无 ## 标题时，用首个 **加粗** 标题作章节名（作业指导卡）。"""
    chunks = []
    heading, buf = "", []
    has_heading = False
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                chunks.extend(_para_chunks(heading, "\n".join(buf), source))
                buf = []
            heading = line[3:].strip()
            has_heading = True
        elif line.strip():
            buf.append(line.strip())
    if buf:
        chunks.extend(_para_chunks(heading, "\n".join(buf), source))

    # 无 ## 章节时，用首个加粗行作为章节名（作业指导卡的卡片标题，如"隔离开关首检式验收（到货验收）"）
    if not has_heading:
        title = ""
        for line in text.splitlines():
            m = re.match(r"\*\*(.+?)\*\*", line.strip())
            if m:
                title = m.group(1).strip()
                break
        if title:
            for c in chunks:
                c["section"] = title
    return chunks


def _para_chunks(heading: str, body: str, source: str) -> list[dict]:
    out = []
    cur = ""
    for para in body.split("\n"):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) < MAX_CHUNK:
            cur += "\n" + para
        else:
            if cur:
                out.append({"source": source, "section": heading, "text": cur})
            cur = para
    if cur:
        out.append({"source": source, "section": heading, "text": cur})
    return out


def build_chunks() -> list[dict]:
    """扫描 data/assets/ 下所有 .md（递归含子文件夹），source 用相对路径便于区分。"""
    chunks = []
    for path in sorted(config.KB_ASSETS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        chunks.extend(split_chunks(text, path.relative_to(config.KB_ASSETS_DIR).as_posix()))
    return chunks


def save_embeddings_pgvector(chunks: list[dict]) -> None:
    """pgvector 模式：编码向量并写入 PostgreSQL kb_chunks 表（先清空再写入）。

    向量以文本字面量 + ::vector 显式转换传给 asyncpg（不引入 vector 类型适配）。
    需已安装 pgvector 扩展；未安装时调用会报错并给出提示。
    """
    asyncio.run(_seed_pgvector(chunks))


async def _seed_pgvector(chunks: list[dict]) -> None:
    from graph.db import connect, ensure_pgvector
    from graph.embeddings import embed_texts, vector_to_literal

    await ensure_pgvector()
    texts = [c["text"] for c in chunks]
    vectors = []
    batch = 32
    for i in range(0, len(texts), batch):
        vectors.extend(embed_texts(texts[i:i + batch]))
        print(f"  embedded {min(i + batch, len(texts))}/{len(texts)}")

    conn = await connect()
    try:
        await conn.execute(f"TRUNCATE {config.KB_VECTOR_TABLE}")
        await conn.executemany(
            f"INSERT INTO {config.KB_VECTOR_TABLE} (source, section, text, embedding) "
            "VALUES ($1, $2, $3, $4::vector)",
            [(c["source"], c["section"], c["text"], vector_to_literal(v))
             for c, v in zip(chunks, vectors)])
    finally:
        await conn.close()
    print(f"[OK] pgvector 入库完成：{len(chunks)} 块 → {config.KB_VECTOR_TABLE}")


def save_embeddings_api(chunks: list[dict]) -> None:
    """调用 OpenAI 兼容 Embedding 接口并保存向量索引。"""
    if not config.EMBEDDING_API_KEY:
        print("[SKIP] api 模式但未配置 EMBEDDING_API_KEY，退回 tfidf 检索")
        return
    from langchain_openai import OpenAIEmbeddings

    emb = OpenAIEmbeddings(model=config.EMBEDDING_MODEL,
                           api_key=config.EMBEDDING_API_KEY,
                           base_url=config.EMBEDDING_BASE_URL)
    texts = [c["text"] for c in chunks]
    vectors = []
    batch = 32
    for i in range(0, len(texts), batch):
        vectors.extend(emb.embed_documents(texts[i:i + batch]))
        print(f"  embedded {min(i + batch, len(texts))}/{len(texts)}")
    index = {"mode": "api", "model": config.EMBEDDING_MODEL,
             "vectors": vectors, "chunks": chunks}
    config.KB_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] 向量索引已保存 → {config.KB_INDEX_PATH}")


def main():
    chunks = build_chunks()
    config.KB_CHUNKS_PATH.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] 知识库切块完成：{len(chunks)} 块 → {config.KB_CHUNKS_PATH}")

    if config.EMBEDDING_MODE == "pgvector":
        save_embeddings_pgvector(chunks)
    elif config.EMBEDDING_MODE == "api":
        save_embeddings_api(chunks)
    else:
        print("[INFO] tfidf 模式：无需向量索引，运行时自动构建")


if __name__ == "__main__":
    main()
