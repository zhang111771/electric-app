"""知识库 agentic grep 工具：在规程/作业指导卡等原文 markdown 中精确检索。

向量检索给出大致方向后，若模型判断资料不足、或问题指向某份文档的具体章节，
可调用 grep_knowledge 按正则搜索原文行（带行号与上下文），或 get_document
按文件名/标题拉取整篇文档。匹配前统一归一化（去竖线/加粗/空白折叠），
保证表格化的作业卡也能被可靠命中。
"""
import re
from pathlib import Path

from langchain_core.tools import tool

import config

_BOLD_TITLE = re.compile(r"\*\*(.+?)\*\*")

_docs: list[dict] | None = None


def _normalize(text: str) -> str:
    """归一化：小写、去掉表格竖线/加粗/标题等符号、空白折叠；保留换行。"""
    text = text.lower()
    text = re.sub(r"[|*#`>]", "", text)
    text = re.sub(r"[ \t　]+", " ", text)
    return text.strip()


def _doc_key(text: str) -> str:
    """标题/文件名匹配用键：去空白与常见分隔符，便于对齐 '01 电力电缆…' 这类写法。"""
    return re.sub(r"[\s（）()_\-./。]+", "", (text or "").lower())


def _stem_keys(source: str) -> list[str]:
    """文件名派生匹配键：原 stem、去数字前缀的 stem（'01 电力电缆…'→'电力电缆…'）。"""
    stem = Path(source).stem
    keys = [_doc_key(stem)]
    stripped = re.sub(r"^\d+\s*", "", stem)
    if _doc_key(stripped) not in keys:
        keys.append(_doc_key(stripped))
    return keys


def _load_docs() -> list[dict]:
    """懒加载全部知识库原文文件（~785 个 md，~11MB），模块级缓存。"""
    global _docs
    if _docs is None:
        _docs = []
        for path in sorted(config.KB_ASSETS_DIR.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            title = ""
            for ln in lines:
                m = _BOLD_TITLE.search(ln)
                if m:
                    title = m.group(1).strip()
                    break
            _docs.append({
                "source": path.relative_to(config.KB_ASSETS_DIR).as_posix(),
                "title": title,
                "text": text,
                "norm_lines": [_normalize(ln) for ln in lines],
            })
    return _docs


def _candidates(docs: list[dict], head: str) -> str:
    lines = [head]
    for d in docs[:15]:
        lines.append(f"- {d['source']}" + (f" ｜ {d['title']}" if d['title'] else ""))
    return "\n".join(lines)


def _format_doc(doc: dict, max_chars: int) -> str:
    text = doc["text"]
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...（已截断，全文共 {len(doc['text'])} 字符）"
    return f"### {doc['source']}\n{text}"


@tool
async def grep_knowledge(pattern: str, doc_filter: str = "", context_lines: int = 2,
                         limit: int = 30) -> str:
    """在知识库原文（规程/作业指导卡等 markdown 文件）中按正则搜索，返回命中行及上下文。

    用于向量检索资料不足时，精确查找某一章节/阶段/术语在原文中的位置。
    - pattern：正则表达式，例如 '检修(准备|实施|结束)阶段'、'低压脉冲法'、'安全距离'。
    - doc_filter：可选，只搜索文件名或文档标题包含该子串的文件，例如 '电力电缆主绝缘故障'。
    - context_lines：命中行前后附带的上下文行数（表格跨行时连成整段）。
    - limit：最多返回的命中行总数。
    """
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则表达式语法错误: {e}。请改用合法正则，例如 '检修(准备|实施|结束)阶段'。"

    docs = _load_docs()
    f = _doc_key(doc_filter)
    blocks, total = [], 0
    for doc in docs:
        if f and f not in _doc_key(doc["source"]) and f not in _doc_key(doc["title"]):
            continue
        hits = [i for i, ln in enumerate(doc["norm_lines"]) if rx.search(ln)]
        if not hits:
            continue
        lines = [f"### {doc['source']}"]
        for i in hits:
            if total >= limit:
                break
            lo = max(0, i - context_lines)
            hi = min(len(doc["norm_lines"]), i + context_lines + 1)
            for j in range(lo, hi):
                if total >= limit:
                    break
                marker = ">>" if j == i else "  "
                lines.append(f"{marker}L{j + 1}: {doc['norm_lines'][j][:200]}")
                total += 1
        blocks.append("\n".join(lines))
    if not blocks:
        scope = f"（限定文件含 '{doc_filter}'）" if doc_filter else ""
        return (f"未找到匹配 '{pattern}' 的内容{scope}。"
                "可尝试：换更短的关键词、放宽正则，或先用 grep_knowledge 配合 doc_filter 定位文件。")
    return "\n\n".join(blocks)


@tool
async def get_document(title: str, max_chars: int = 8000) -> str:
    """按文件名或文档标题匹配知识库中的一篇文档，返回其完整原文。

    用于需要整篇内容时（如'给我完整的XX作业指导卡'）。优先精确匹配，其次子串匹配；
    多个文件相近时返回候选清单，可再用 grep_knowledge 的 doc_filter 精确限定。
    - title：文件名或文档标题，例如 '01 电力电缆主绝缘故障测试标准化作业指导卡'。
    - max_chars：返回原文的最大字符数（默认 8000）。
    """
    docs = _load_docs()
    t = _doc_key(title)

    # 1) 精确匹配（文件名原样 / 去数字前缀 / 文档标题）
    exact = [d for d in docs if any(k == t for k in _stem_keys(d["source"]) + [_doc_key(d["title"])] if k)]
    if len(exact) == 1:
        return _format_doc(exact[0], max_chars)
    if len(exact) > 1:
        return _candidates(exact, f"有 {len(exact)} 个文件精确匹配 '{title}'，请用更精确的文件名重试：")

    # 2) 子串匹配，按共享长度排序
    scored = []
    for d in docs:
        keys = _stem_keys(d["source"]) + [_doc_key(d["title"])]
        best = max((min(len(t), len(k)) for k in keys if k and t and (t in k or k in t)), default=0)
        if best:
            scored.append((best, d))
    if not scored:
        return (f"未找到标题/文件名包含 '{title}' 的文档（知识库共 {len(docs)} 个文件）。"
                "请尝试更精确的名称，或先用 grep_knowledge 搜索关键词。")
    scored.sort(key=lambda x: -x[0])
    if scored[0][0] > scored[1][0]:
        return _format_doc(scored[0][1], max_chars)
    top_score = scored[0][0]
    return _candidates([d for s, d in scored if s == top_score],
                       f"有多个文件接近 '{title}'（匹配度 {top_score}），请用更精确的文件名重试：")
