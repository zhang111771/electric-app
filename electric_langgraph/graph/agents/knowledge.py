"""知识问答智能体：向量 RAG 为主 + agentic grep 兜底。

流程：
1. 先按块向量检索并做文档级展开（retriever.expand_by_source），得到基础上下文；
2. 用 stream_tool_loop 跑工具循环：若向量上下文不足、或问题明确指向某份规程/作业卡
   的章节细节，模型自主调用 grep_knowledge / get_document 在原文中精确检索、
   必要时拉取整篇文档；整条链路只走一次 astream，流出的即前端看到的最终回答，
   不会重复。

注：不用 run_tool_loop + FINALIZE 二次生成的模式——run_tool_loop 的 ainvoke
内部走流式聚合，token 会经 LangGraph contextvar 回调泄漏到前端，再加 FINALIZE
astream 就会把同一段话重复推给前端（data/calc 智能体也有此隐患）。
"""
from langchain_core.messages import AIMessage, SystemMessage

from ..agent_loop import stream_tool_loop
from ..llm import get_llm
from ..retriever import expand_by_source, get_retriever
from .knowledge_tools import get_document, grep_knowledge

SYSTEM = """你是电网运行知识专家，负责依据资料库中的规程、规范、术语和案例回答问题。

【向量检索到的资料】（已做文档级展开，可能包含相关文件的整篇内容）：
{context}

【可用工具】
- grep_knowledge：在原文文件中按正则精确搜索某章节/阶段/术语（返回行号与上下文）。
- get_document：按文件名/标题拉取某篇文档的完整原文。

【工作方式】
1. 优先依据向量检索资料直接作答；若已能完整回答，不要调用工具。
2. 仅当出现以下情况时，使用工具补充：
   a) 向量资料不足、含糊或明显缺少用户要的内容；
   b) 问题明确指向某份规程/作业指导卡的"具体阶段/章节"（如"XX卡检修结束阶段"），
      用 grep_knowledge 精确定位；需要整篇内容时用 get_document 拉取全文。
3. 尽量覆盖用户要的各个阶段/环节，不要遗漏；不要编造，检索不到就如实说明。
4. 不要输出思考过程/过渡语（如"让我搜索一下"），需要工具就安静地直接调用；最终给出完整回答。"""

_KNOWLEDGE_TOOLS = [grep_knowledge, get_document]


async def knowledge_node(state: dict, config):
    llm = get_llm()
    query = state["messages"][-1].content if state["messages"] else ""
    retriever = await get_retriever()
    hits = await retriever.asearch(query, top_k=8)  # 第一级：块级粗召回
    chunks = expand_by_source(hits, top_docs=2, max_chars=8000)  # 第二级：按源文件整篇展开
    context = "\n\n".join(
        f"[{c['source']}｜{c['section'] or '未分节'}] {c['text']}" for c in chunks)
    sys_msg = SystemMessage(SYSTEM.format(context=context or "（本次未检索到相关资料）"))
    msgs = [sys_msg, *state["messages"][-6:]]

    # agentic 工具循环（单条流式链路）：流出的即前端看到的最终回答，不会重复
    parts = []
    meta: dict = {}
    async for token in stream_tool_loop(llm, msgs, _KNOWLEDGE_TOOLS, config=config,
                                        max_iters=4, meta=meta):
        if token:
            parts.append(token)
    final = AIMessage(content="".join(parts))
    if meta.get("run_id"):
        # 复用流式 run id：LangGraph 会把节点返回的 AIMessage 在 messages 流里整体
        # 再重发一遍，同 id 可被其消息流去重，避免前端看到两份相同的回答。
        final.id = meta["run_id"]
    return {"messages": [final]}
