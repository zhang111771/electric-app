"""变电设备诊断智能体：油色谱三比值法 + SF6 气体检查 + 红外测温相对温差法。

纯规则判据（DL/T 722 / DL/T 596 / DL/T 664），零训练数据，确定性输出。
用户消息未带数值时下发对应子表单（mode: dga / sf6 / ir），由前端填写后直连
/api/diagnose/substation/* 端点计算。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..llm import get_llm
from ..substation_tools import (
    ALL_SUBSTATION_TOOLS, ir_temperature_anomaly, oil_dga_three_ratio,
    sf6_gas_check,
)

SUBSTATION_TOOLS = [
    oil_dga_three_ratio, sf6_gas_check, ir_temperature_anomaly,
]

SYSTEM = """你是变电设备诊断专家，负责变压器等一次设备的例行诊断。你可以调用 3 个工具，
全部基于行业标准的确定性判据（非机器学习模型）：

1. oil_dga_three_ratio —— 变压器油色谱三比值法诊断 (DL/T 722-2014)
   输入油中溶解气体浓度 h2/ch4/c2h6/c2h4/c2h2 (μL/L)，输出三位比值编码 +
   故障类型判断 (低温过热/中温过热/高温过热/局部放电/电弧放电/低能放电等) +
   严重度 (正常/注意/严重/危急) + 处置建议。
   注意：仅在任一特征气体超过注意值时才适用，浓度正常时工具会提示"暂不适用"。

2. sf6_gas_check —— SF6 气体压力与微水检查 (DL/T 596)
   输入实测压力 measured_pressure (MPa) 与微水 moisture_ppmv (μL/L)，输出
   压力状态/微水状态/总体状态 (正常/注意/报警/危急) + 建议。
   压力四档：≥额定正常，低于额定待补气，低于报警值报警，低于闭锁值危急。
   微水按气室类型分档：灭弧室 150/250，其他气室 250/500 (μL/L)。

3. ir_temperature_anomaly —— 红外测温相对温差法 (DL/T 664-2016)
   输入热点温度 hot_temp、正常相对应点温度 ref_temp、环境温度 ambient_temp (℃)
   与设备类别 device_category，输出相对温差 δ% + 判级 (正常/一般/严重/危急)。
   温升 <15K 时相对温差法不适用，工具会提示改用表面温度法。

使用要求：
1. 用户提到"油色谱 / 三比值 / 溶解气体 / DGA / 乙炔"→ oil_dga_three_ratio
2. 用户提到"SF6 / 六氟化硫 / 压力 / 微水 / 露点 / 补气"→ sf6_gas_check
3. 用户提到"红外 / 热像 / 测温 / 温差 / 发热 / 热点"→ ir_temperature_anomaly
4. 用户没有提供具体数值时，不要调用工具、不要编造数字，直接告诉用户需要填哪些参数，
   由前端表单处理。
5. 工具返回的 fault_type_zh / severity / judgment / overall / advice 必须原样传达，
   不要编造数字；用中文解释判据含义，给出工程结论。"""

FINALIZE = (
    "请基于上述诊断结果，用中文给出简洁的工程结论："
    "故障类型/状态 + 严重度 + 处置建议。"
)

# 用户消息关键词 → 表单子模式（dga 放最前，避免误配）
_FORM_KEYWORDS = [
    (re.compile(r'油色谱|三比值|DGA|乙炔|溶解气体|C2H2|油化|气体分析'), 'dga'),
    (re.compile(r'SF6|六氟化硫|气压|压力|微水|露点|水分|密度继电器|补气'), 'sf6'),
    (re.compile(r'红外|热像|测温|温差|发热|过热|热点'), 'ir'),
]


def _detect_form_mode(user_text: str) -> str | None:
    """根据用户消息关键词判断应请求哪种子表单，返回 None 表示无法判断。"""
    for pattern, mode in _FORM_KEYWORDS:
        if pattern.search(user_text):
            return mode
    return None


async def substation_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    # 获取用户最新消息
    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, SUBSTATION_TOOLS, config, max_iters=4)

    # 若没有工具调用，说明缺少数值，根据关键词下发对应子表单
    has_tool_calls = any(isinstance(m, ToolMessage) for m in msgs)
    form = None
    if not has_tool_calls:
        mode = _detect_form_mode(user_msg)
        if mode:
            form = {"type": "substation", "mode": mode}

    if form:
        # 检查 LLM 是否已输出了请求数据的文字，如果有就用它
        last_ai = None
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        content = last_ai.content if last_ai else "请提供诊断所需的数据。"
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
