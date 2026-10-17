"""调度事故研判智能体：保护动作 → 故障类型/区段推理 → 处理建议。

规则引擎（dispatch_tools.py）确定性输出，定位铁律：辅助不拍板，
只出推理链 + 建议 + 疑问，最终事故定性以人工复核为准。
用户未提供事件号时下发表单（mode: judge），由前端选择事件后直连
/api/dispatch/judge 端点研判。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..dispatch_tools import (
    ALL_DISPATCH_TOOLS, analyze_fault_event, list_fault_events, update_fault_event,
)
from ..llm import get_llm

DISPATCH_TOOLS = [
    analyze_fault_event, list_fault_events, update_fault_event,
]

SYSTEM = """你是电力调度事故研判助手，负责故障跳闸事件的自动研判（辅助决策）。你可以调用 3 个工具：

1. list_fault_events —— 查询故障事件台账（可按状态/变电站/关键词筛选），返回事件清单（含 event_no）。
2. analyze_fault_event —— 对一次跳闸事件做自动研判：读取其保护动作明细，按硬编码规则表推理
   故障类型（变压器区内/线路区内/单相接地/近区永久故障/瞬时性故障等）+ 影响区段 + 处置建议，
   输出推理链(reasoning) + 建议(suggestions) + 待确认问题(questions)。
3. update_fault_event —— 推进事件状态（已处置），待研判事件必须先研判才能处置。

使用要求：
1. 用户要求"研判事故 / 跳闸 / 故障事件"时，若消息里带有事件号（如 A-20260909-001）→ 直接
   analyze_fault_event 研判该事件。
2. 用户要求研判"最新 / 最近"的事件（未给事件号）→ 先 list_fault_events 找到 ts 最近的事件，
   再 analyze_fault_event 研判它，不要只询问用户。
3. 用户要求"查询/查看故障事件、跳闸记录" → list_fault_events。
4. 用户要求"处置/归档某事件" → update_fault_event（action=已处置）。
5. 定位铁律：只输出推理链 + 建议 + 疑问，绝不直接给出"事故定性结论"，措辞用
   "研判为/建议/请人工确认"，最后提醒"具体处置请以值班调度指令为准"。
6. 工具返回的 fault_type_zh / scope / reasoning / suggestions / questions 必须原样传达，
   不要编造；用中文简洁给出工程结论。"""

FINALIZE = (
    "请基于上述研判结果，用中文给出简洁的工程结论：故障类型 + 影响范围 + "
    "处置建议（分条）+ 待确认问题。最后提醒具体处置请以值班调度指令为准。"
)

# 研判意图 / 是否含事件号 / 是否要求"最新"（表单只在笼统研判意图 + 无事件号 + 非最新时下发）
_JUDGE_INTENT = re.compile(
    r'(研判|分析|判定|看看).{0,12}(事故|跳闸|故障|事件)|(事故|跳闸|故障).{0,12}(研判|分析|判定)'
)
_EVENT_NO = re.compile(r'[A-Za-z]-\d{8}-\d{3,}')
_LATEST = re.compile(r'最新|最近|刚刚|刚发生|最近一次|最新一起')


def _should_issue_form(user_text: str) -> bool:
    """笼统要求研判且未给事件号、也不是要"最新/最近"事件时，下发表单让用户选事件。"""
    if not _JUDGE_INTENT.search(user_text):
        return False
    if _EVENT_NO.search(user_text):
        return False
    if _LATEST.search(user_text):
        return False
    return True


async def dispatch_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    # 获取用户最新消息
    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, DISPATCH_TOOLS, config, max_iters=4)

    # 笼统要求研判且缺事件号 → 下发表单（确定性，不依赖 LLM 是否主动询问）
    form = None
    if _should_issue_form(user_msg):
        form = {"type": "dispatch", "mode": "judge"}

    if form:
        last_ai = None
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        content = last_ai.content if last_ai else "请提供故障事件编号以便研判。"
        ai_msg = AIMessage(content=content, additional_kwargs={"chart": None, "form": form})
        return {"messages": [ai_msg], "form": form}

    final = [*msgs, SystemMessage(FINALIZE)]
    parts = []
    async for chunk in llm.astream(final, config=config):
        if chunk.content:
            parts.append(chunk.content)
    full_content = "".join(parts)
    ai_msg = AIMessage(content=full_content, additional_kwargs={"chart": None})
    return {"messages": [ai_msg]}
