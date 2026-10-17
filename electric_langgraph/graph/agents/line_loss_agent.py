"""台区线损智能体：线损率计算 + 合理区间判定 + 异常疑点定位。

规则+统计启发式（均值/趋势/突增/持续超标/负线损）确定性输出。
客观计算可明确判级；"窃电/漏电"一律用疑似/疑点措辞并给建议（辅助不拍板）。
用户未给出台区/参数时下发对应子模式表单：
  - mode=calc：手输 供电量/售电量/容量 算线损率
  - mode=analyze：选台区做近 N 天线损分析
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..agent_loop import run_tool_loop
from ..line_loss_tools import (
    ALL_LINE_LOSS_TOOLS, analyze_area_loss, calc_line_loss, query_area_loss,
)
from ..llm import get_llm

LINE_LOSS_TOOLS = [
    calc_line_loss, analyze_area_loss, query_area_loss,
]

SYSTEM = """你是台区线损分析助手，负责台区线损率计算与异常疑点定位。你可以调用 3 个工具：

1. calc_line_loss —— 计算线损率并判定：损失电量 = 供电量 - 售电量，线损率 = 损失/供电 × 100%，
   按容量分档上限判定 正常/偏高/超标；极端值（售>供、>60%）给护栏结论。
2. analyze_area_loss —— 分析台区近 N 天线损：均值、趋势、突增日、持续超标（连续≥3 天超上限）、
   负线损（计量异常），输出疑点 suspicions + 建议，并回写台区状态。
3. query_area_loss —— 查询台区清单（不带 area_no）或某台区日电量明细（带 area_no）。

使用要求：
1. 用户给出台区号（如 T-005）并要求"分析/排查/核查线损" → 直接 analyze_area_loss。
2. 用户给出 供电量/售电量/容量 要求"算线损率" → 直接 calc_line_loss。
3. 用户笼统要求"算线损率"但没给参数 → 下发 mode=calc 表单。
4. 用户笼统要求"分析台区线损/线损异常"但没给台区号 → 下发 mode=analyze 表单。
5. 用户要求"查看台区/台区清单/有哪些台区" → query_area_loss 查询清单。
6. 措辞铁律：线损率/上限判定是客观计算可直接给结论；涉"窃电/漏电"一律用
   "疑似/疑点"并给建议，绝不武断定性为窃电。最后提醒疑点需现场核实。
7. 工具返回的 judgment/avg_rate/trend/anomaly_days/suspicions 必须原样传达，不要编造；
   用中文简洁给出工程结论。"""

FINALIZE = (
    "请基于上述线损分析结果，用中文给出简洁结论：线损率/均值与判定、趋势、异常日清单、"
    "疑点（用疑似措辞）+ 建议。最后提醒疑点为辅助分析，需现场核实后确定处理措施。"
)

# 分析意图 / 计算意图 / 仅查台区清单 / 是否已带台区号
_ANALYZE = re.compile(
    r'(分析|排查|核查|检视|评估|看看|关注).{0,10}(台区|线损)|(台区|线损).{0,10}(分析|异常|排查|核查|检视|突增|疑点)'
)
_CALC = re.compile(r'算(一?下)?.*(线损|损耗)|(线损|损耗).*(是|多少|怎么算)|计算.*(线损|损耗)')
_QUERY_ONLY = re.compile(r'哪些台区|台区清单|台区列表|查看台区|台区有哪些|有多少台区')
_AREA_NO = re.compile(r'T-\d{3}')


def _form_mode(user_text: str) -> str | None:
    """笼统意图 → 表单子模式；带了台区号/仅查清单/无相关意图 → None。"""
    if _QUERY_ONLY.search(user_text):
        return None
    if _AREA_NO.search(user_text):
        return None
    if _ANALYZE.search(user_text):
        return "analyze"
    if _CALC.search(user_text):
        return "calc"
    return None


async def line_loss_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    # 获取用户最新消息
    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, LINE_LOSS_TOOLS, config, max_iters=4)

    # 笼统意图 → 下发表单（确定性，不依赖 LLM 是否主动询问）
    mode = _form_mode(user_msg)
    form = None
    if mode:
        form = {"type": "line_loss", "mode": mode}

    if form:
        last_ai = None
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        content = last_ai.content if last_ai else "请补充台区/电量参数以便进行线损分析。"
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
