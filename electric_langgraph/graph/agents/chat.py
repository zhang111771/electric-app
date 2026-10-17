"""通用对话节点：寒暄、闲聊等与业务数据无关的问题。"""
from langchain_core.messages import AIMessage, SystemMessage

from ..llm import get_llm

SYSTEM = """你是电网智能体助手，亲切友好。当前与电网业务数据无关的问题由你直接回答。
回答保持简洁、自然，不编造数据。"""


async def chat_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]
    parts = []
    async for chunk in llm.astream(msgs, config=config):
        if chunk.content:
            parts.append(chunk.content)
    return {"messages": [AIMessage(content="".join(parts))]}
