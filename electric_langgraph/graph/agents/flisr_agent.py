"""配网 FLISR 智能体：馈线故障 定位→隔离→恢复 辅助决策。

经典三阶段算法（flisr_tools.py）确定性输出建议操作序列 + 转供容量校验。
定位铁律：辅助不拍板，所有开关操作需值班调度确认后执行。
用户未提供馈线/故障区段时下发表单（mode: run），由前端选择馈线与区段后
直连 /api/flisr/run 端点执行。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..agent_loop import run_tool_loop
from ..flisr_tools import ALL_FLISR_TOOLS, list_feeders, run_flisr
from ..llm import get_llm

FLISR_TOOLS = [
    list_feeders, run_flisr,
]

SYSTEM = """你是配网运行辅助决策助手，负责馈线故障的定位→隔离→恢复（FLISR）辅助决策。你可以调用 2 个工具：

1. list_feeders —— 查询配网馈线拓扑清单：每条馈线的源端断路器、联络开关/相邻馈线、
   各馈线段（区段号/负荷/边界开关），供确定故障区段与转供对象。
2. run_flisr —— 对一次馈线故障执行 FLISR 算法：定位故障段 → 拉开边界开关隔离 →
   上游源端恢复 + 下游联络转供（校验相邻馈线负荷不超限），返回建议操作步骤
   steps[{seq, action, switch, why}]、恢复/未恢复负荷、转供负荷校验 load_check。

使用要求：
1. 用户要求"FLISR / 配网故障隔离恢复 / 馈线故障处理"时，若消息里带馈线号与区段号
   （如 "L1-S2 故障"）→ 直接 run_flisr。
2. 用户要求"查看馈线 / 馈线拓扑 / 有哪些馈线" → list_feeders。
3. 用户笼统要求"配网故障隔离恢复"但未给馈线/区段 → 下发表单由前端选择，不要追问。
4. 定位铁律：只输出建议操作步骤（定位/隔离/恢复分阶段列出，每步含开关与依据），
   明确"所有开关操作需值班调度确认后执行"，绝不直接替调度拍板。
5. 工具返回的 steps / restored_kw / unserved_kw / load_check 必须原样传达，不要编造；
   用中文简洁给出工程结论。"""

FINALIZE = (
    "请基于上述 FLISR 结果，用中文给出简洁的操作序列：定位→隔离→恢复三个阶段分条列出，"
    "每条含开关操作与依据；再给出恢复/未恢复负荷与转供校验结果。"
    "最后提醒所有开关操作需值班调度确认后执行。"
)

# FLISR 运行意图 / 是否已带馈线-区段标识（如 L1-S2）/ 是否仅查询拓扑
_RUN_INTENT = re.compile(
    r'FLISR|隔离恢复|馈线故障|故障隔离|故障恢复|转供|配网.*(隔离|恢复|转供)|(隔离|恢复|转供).*馈线'
)
_SPEC = re.compile(r'L\s*\d\s*[-_]\s*[A-Z]*\s*\d+|L\d')
_QUERY_ONLY = re.compile(r'拓扑|有哪些馈线|查看馈线|馈线清单|馈线列表|几条馈线')


def _should_issue_form(user_text: str) -> bool:
    """笼统要求做 FLISR 且未带馈线/区段、也不是仅查拓扑时，下发表单。"""
    if not _RUN_INTENT.search(user_text):
        return False
    if _QUERY_ONLY.search(user_text):
        return False
    if _SPEC.search(user_text):
        return False
    return True


async def flisr_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    # 获取用户最新消息
    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, FLISR_TOOLS, config, max_iters=4)

    # 笼统要求做 FLISR 且缺馈线/区段 → 下发表单（确定性，不依赖 LLM 是否主动询问）
    form = None
    if _should_issue_form(user_msg):
        form = {"type": "flisr", "mode": "run"}

    if form:
        last_ai = None
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        content = last_ai.content if last_ai else "请选择馈线、故障区段与故障性质以执行 FLISR。"
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
