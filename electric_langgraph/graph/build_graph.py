"""组装并编译 LangGraph 图。

拓扑：START → router（意图识别）→ 按意图分发到 knowledge/data/calc/chat → END。
每个专职智能体直接产出最终答复（含可选图表），checkpointer 用 PostgreSQL
持久化多轮对话记忆（跨进程、重启不丢）。

图与数据库连接都惰性初始化（首次调用 get_graph() 时建立），连接由模块级
引用 _CTX 持有，避免被 GC 关闭。
"""
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

import config

from .agents.cable_agent import cable_agent_node
from .agents.calc_agent import calc_agent_node
from .agents.chat import chat_node
from .agents.data_agent import data_agent_node
from .agents.defect_agent import defect_agent_node
from .agents.dispatch_agent import dispatch_agent_node
from .agents.flisr_agent import flisr_agent_node
from .agents.knowledge import knowledge_node
from .agents.line_loss_agent import line_loss_agent_node
from .agents.substation_agent import substation_agent_node
from .agents.ticket_agent import ticket_agent_node
from .router import router_node
from .state import AgentState

_INTENTS = {"knowledge", "data", "calc", "cable", "substation", "defect", "ticket", "dispatch", "flisr", "line_loss", "chat"}

_CTX = None  # 持有 async context manager，防止连接被 GC 关闭
_GRAPH = None


def _route(state: AgentState) -> str:
    intent = state.get("intent", "chat")
    return intent if intent in _INTENTS else "chat"


def _build(checkpointer):
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("data", data_agent_node)
    g.add_node("calc", calc_agent_node)
    g.add_node("cable", cable_agent_node)
    g.add_node("substation", substation_agent_node)
    g.add_node("defect", defect_agent_node)
    g.add_node("ticket", ticket_agent_node)
    g.add_node("dispatch", dispatch_agent_node)
    g.add_node("flisr", flisr_agent_node)
    g.add_node("line_loss", line_loss_agent_node)
    g.add_node("chat", chat_node)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", _route)
    g.add_edge("knowledge", END)
    g.add_edge("data", END)
    g.add_edge("calc", END)
    g.add_edge("cable", END)
    g.add_edge("substation", END)
    g.add_edge("defect", END)
    g.add_edge("ticket", END)
    g.add_edge("dispatch", END)
    g.add_edge("flisr", END)
    g.add_edge("line_loss", END)
    g.add_edge("chat", END)

    return g.compile(checkpointer=checkpointer)


async def get_graph():
    """惰性创建编译图：连接 PostgreSQL checkpointer 并建表（幂等），进程内复用。"""
    global _CTX, _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    _CTX = AsyncPostgresSaver.from_conn_string(config.POSTGRES_URI)
    saver = await _CTX.__aenter__()
    await saver.setup()  # 幂等：CREATE TABLE IF NOT EXISTS
    _GRAPH = _build(saver)
    return _GRAPH
