"""LangGraph 共享状态定义。"""
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    # 多轮对话消息（add_messages 自动累加）
    messages: Annotated[list[BaseMessage], add_messages]
    # 意图路由结果：knowledge / data / calc / cable / chat
    intent: str
    # 可选图表数据（前端渲染折线图）
    chart: dict
    # 可选表单请求（cable_agent 检测到诊断意图后设置，前端渲染表单）
    form: dict
