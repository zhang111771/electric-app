"""缺陷闭环管理智能体：登记 → 定级 → 派单 → 处理 → 消缺 → 验收。

工具全部为确定性业务操作（PostgreSQL 持久化），状态机校验非法流转。
用户消息缺登记字段时下发 form={"type":"defect","mode":"register"}。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..defect_tools import (
    ALL_DEFECT_TOOLS, get_defect, query_defects, register_defect,
    update_defect_status,
)
from ..llm import get_llm

DEFECT_TOOLS = [
    register_defect, query_defects, update_defect_status, get_defect,
]

SYSTEM = """你是变电设备缺陷闭环管理助手。缺陷闭环五步：登记 → 定级 → 派单 → 消缺 → 验收，
缺陷可随时关闭作废。你可以调用 4 个工具（数据落库到 PostgreSQL）：

1. register_defect —— 登记缺陷。必填：substation 变电站、device 设备、defect_type 类别、
   description 现象描述；可选 severity(一般/严重/危急，默认一般)、source、discoverer、plan_time。
2. query_defects —— 按条件查询缺陷台账（status/severity/substation/keyword）。
3. update_defect_status —— 推进状态。动作 action ∈ 定级/派单/开始处理/消缺/验收/关闭；
   定级必须带 severity，派单必须带 assignee（责任人）并自动生成工单；
   状态机禁止跳级（如未定级不能派单），非法流转工具会返回 error。
4. get_defect —— 查询单条缺陷详情及关联工单。

定级规则：危急=设备损坏/停电/人身安全风险（立即处理）；严重=需尽快安排处理；
一般=纳入正常检修计划。

使用要求：
1. 用户报缺陷但缺字段时，不要编造，直接询问缺的字段，或下发登记表单。
2. 工具返回的 defect_no / 状态流转结果必须原样传达；状态、severity、defect_no 不能凭空编造。
3. 查询结果用简洁清单列出（编号/设备/严重度/状态），危急缺陷要突出提醒。"""

FINALIZE = "请根据工具返回结果，用中文简洁总结该缺陷当前状态与下一步应做的动作。"

# 登记意图关键词（命中且无工具调用时下发登记表单）
_REGISTER_PATTERN = re.compile(r'登记|上报|发现.*缺陷|新.*缺陷|报.*缺陷|录入缺陷')

_FORM_MSG = (
    "请填写缺陷登记信息，我来帮你建单并进入闭环管理。"
)


def _is_register_intent(user_text: str) -> bool:
    return bool(_REGISTER_PATTERN.search(user_text))


async def defect_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, DEFECT_TOOLS, config, max_iters=5)

    has_tool_calls = any(isinstance(m, ToolMessage) for m in msgs)
    form = None
    if not has_tool_calls and _is_register_intent(user_msg):
        form = {"type": "defect", "mode": "register"}

    if form:
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        content = last_ai.content if last_ai and last_ai.content else _FORM_MSG
        ai_msg = AIMessage(content=content, additional_kwargs={"chart": None, "form": form})
        return {"messages": [ai_msg], "form": form}

    final = [*msgs, SystemMessage(FINALIZE)]
    parts = []
    async for chunk in llm.astream(final, config=config):
        if chunk.content:
            parts.append(chunk.content)
    ai_msg = AIMessage(content="".join(parts), additional_kwargs={"chart": None})
    return {"messages": [ai_msg]}
