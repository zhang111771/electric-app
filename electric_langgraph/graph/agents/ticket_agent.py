"""两票智能审核智能体（工作票 / 操作票）——只标差异、出建议、提疑问。

**铁律（写入 SYSTEM + 输出强制免责）：**
- 绝不给出"合格/不合格/通过/不通过/符合要求"结论，只输出差异项 + 建议 + 疑问；
- 措辞用"疑似缺项 / 顺序存疑 / 请人工确认 / 请人工核对"；
- 回答固定以 DISCLAIMER（人工复核免责）结尾。

流程：先用 check_ticket_rules 做确定性逐项核对（返回结构化 items），
必要时用 grep_knowledge / get_document 回填条款出处。整条链路走单次
stream_tool_loop（复用流式 run id 去重，避免重复推送）。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..agent_loop import stream_tool_loop
from ..llm import get_llm
from ..ticket_tools import (
    DISCLAIMER, FORBIDDEN_WORDS, check_ticket_rules, sanitize_forbidden,
)
from .knowledge_tools import get_document, grep_knowledge

TICKET_TOOLS = [check_ticket_rules, grep_knowledge, get_document]

SYSTEM = """你是电网"两票"（工作票/操作票）智能审核助手，负责对票面做合规性辅助检查。

【可用工具】
1. check_ticket_rules(ticket_text, ticket_type='work'|'operation') ——
   确定性逐项核对，返回结构化差异 items（level: missing 疑似缺项 / warning 顺序存疑 /
   question 需人工确认）+ summary + disclaimer。**这是核心工具，必须优先调用。**
2. grep_knowledge —— 在《安全工作规程》等原文中按正则检索条款出处。
3. get_document —— 按文件名拉取规程整篇原文（如 '01 安全工作规程'）。

【铁律（必须严格遵守）】
1. 你只做辅助检查，只输出三类内容：差异项 + 建议 + 疑问。
2. 绝对禁止输出"合格/不合格/通过/不通过/符合要求"等结论性判断词；一律用
   "疑似缺项 / 顺序存疑 / 请人工确认 / 请人工核对票面"。
3. 你不为票面作最终结论，也不代替任何人签字。
4. 回答结尾必须附上免责声明：{disclaimer}

【工作方式】
1. 用户给出票面全文时：先调用 check_ticket_rules 核对，再按 items 逐条转述
   （level 用【疑似缺项】【顺序存疑】【需人工确认】标注），每条给出建议；
   若某条差异涉及规程条款而 items 未给 clause，可用 grep_knowledge 补查出处。
2. 用户只提到要审票、但未贴出票面全文时：不要调用工具、不要编造票面内容，
   直接告诉用户需要粘贴票面（或下发表单）。
3. 不输出思考过程/过渡语（如"让我检查一下"），需要工具就安静地直接调用。"""

_ASK_FORM_MSG = (
    "请粘贴要审核的票面全文（工作票或操作票），我会做合规性辅助检查——"
    "只标差异、出建议、提疑问，结论以人工复核签字为准。"
)

# 用户消息中的审票/贴票意图
_ASK_REVIEW = re.compile(r"审核|检查|审一下|看看.*票|审查|核对.*票")
# 判定"贴了票面内容"：命中票面结构性关键词（避免把"操作票"三个字误判为已贴票）
_HAS_TICKET_TEXT = re.compile(
    r"停电|验电|接地|监护人|操作人|工作负责人|工作许可人|工作班成员|安全措施|"
    r"编号|签发|许可|终结|步骤|隔离开关|断路器|倒闸|第一种工作票|第二种工作票")


async def ticket_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM.format(disclaimer=DISCLAIMER)), *state["messages"][-6:]]

    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    # 用户只是要审票、未贴全文 → 下发表单
    if _ASK_REVIEW.search(user_msg) and not _HAS_TICKET_TEXT.search(user_msg):
        ai_msg = AIMessage(
            content=_ASK_FORM_MSG,
            additional_kwargs={"chart": None, "form": {"type": "ticket", "mode": "review"}},
        )
        return {"messages": [ai_msg], "form": {"type": "ticket", "mode": "review"}}

    # 贴了票面 → 单条流式链路：工具核对 + 叙述 + 免责
    parts = []
    meta: dict = {}
    async for token in stream_tool_loop(llm, msgs, TICKET_TOOLS, config=config,
                                        max_iters=4, meta=meta):
        if token:
            parts.append(token)
    content = "".join(parts)
    content = sanitize_forbidden(content)
    if DISCLAIMER not in content:
        content = content.rstrip() + ("\n\n" if content else "") + DISCLAIMER
    final = AIMessage(content=content)
    if meta.get("run_id"):
        final.id = meta["run_id"]
    return {"messages": [final]}
