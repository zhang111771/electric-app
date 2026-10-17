"""高压电缆故障诊断智能体：TDOA 定位 + 故障类型 + 局放缺陷 + 线路缺陷检测。

对接 tools/electric_mcp 与 tools/yolo_mcp 的 4 个算法能力，面向《高压电缆故障诊断系统》
(4.8km 单回 220kV, 呼和浩特丁香路管廊) 的故障分析场景。
"""
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..cable_tools import (
    fault_location_tdoa, fault_type_classify, line_defect_detect,
    prpd_defect_classify,
)
from ..llm import get_llm

CABLE_TOOLS = [
    fault_location_tdoa, fault_type_classify, prpd_defect_classify,
    line_defect_detect,
]

SYSTEM = """你是高压电缆故障诊断专家，服务于 4.8km 单回 220kV 电缆线路
（呼和浩特丁香路管廊）。你可以调用 4 个诊断工具：

1. fault_location_tdoa —— 双端电流行波 TDOA 故障定位
   输入: 源端/负荷端三相电流 CSV (列序 A B C)，输出故障位置(米)。
   精度 MAE=10.1m, 验收线 29m, 电缆全长 4800m。

2. fault_type_classify —— 4 类短路故障类型识别 (AG/AB/ABG/ABC)
   输入: 9 通道电压波形 CSV 或特征 JSON, 输出故障类型+概率+规则交叉验证。

3. prpd_defect_classify —— 4 类局放缺陷识别 (TREE/WATR/JOIN/AGNG)
   输入: PRPD φ-q 矩阵 CSV 或 17 维描述子 CSV, 输出缺陷类型+严重度+概率。
   严重度阈值: <20pC 正常, 20~50pC 预警, >50pC 报警。

4. line_defect_detect —— YOLOv8 线路缺陷检测 (broken 破损 / bird nest 鸟巢 / object 异物)
   输入: 线路现场图片路径 (jpg/png/bmp/webp), 输出检测框+置信度+推理引擎。

路径必须以 tools/electric_mcp/ 或 tools/yolo_mcp/ 为基准解析, 也可以传绝对路径。

使用要求：
1. 用户提到"定位 / 测距 / TDOA"→ 调用 fault_location_tdoa
2. 用户提到"故障类型 / AG/AB/ABG/ABC / 短路类型" → 调用 fault_type_classify
3. 用户提到"局放 / 电树 / 水树 / PRPD / 缺陷类型" → 调用 prpd_defect_classify
4. 用户提到"线路 / 绝缘子 / 破损 / 鸟巢 / 异物 / 缺陷检测" → 调用 line_defect_detect
5. 用户问题模糊时, 主动询问是要做定位、类型识别、局放诊断还是缺陷检测;
6. 如果用户没有提供文件路径，不要调用工具，不要编造 sample_data 路径，
   直接告诉用户需要上传哪些文件，由前端表单处理;
7. 工具返回的 within_acceptance / severity / confidence / count 必须原样传达给用户,
   不要编造数字; 用中文解释算法含义, 给出工程结论。"""

FINALIZE = (
    "请基于上述诊断结果, 用中文给出简洁的工程结论: "
    "故障位置 / 类型 / 缺陷 + 置信度 + 处置建议。"
)

# 用户消息关键词 → 表单类型映射
_FORM_KEYWORDS = [
    # detect 必须先于 prpd（prpd 正则含 绝缘|缺陷，否则"线路缺陷/绝缘子缺陷"会被误判为 prpd）
    (re.compile(r'绝缘子|线路缺陷|导线缺陷|破损|鸟巢|异物|缺陷检测|检测.*缺陷'), 'detect'),
    (re.compile(r'定位|测距|TDOA|故障点|故障位置'), 'tdoa'),
    (re.compile(r'故障类型|短路类型|AG|AB|ABG|ABC'), 'classify'),
    (re.compile(r'局放|电树|水树|PRPD|缺陷|绝缘'), 'prpd'),
]


def _detect_form_type(user_text: str) -> str | None:
    """根据用户消息关键词判断应该请求哪种表单，返回 None 表示无法判断。"""
    for pattern, form_type in _FORM_KEYWORDS:
        if pattern.search(user_text):
            return form_type
    return None


async def cable_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]

    # 获取用户最新消息
    user_msg = ''
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            user_msg = m.content if isinstance(m.content, str) else ''
            break

    msgs = await run_tool_loop(llm, msgs, CABLE_TOOLS, config, max_iters=4)

    # 检查是否有工具调用（如果有，说明 LLM 已经获取了数据，不需要表单）
    has_tool_calls = any(isinstance(m, ToolMessage) for m in msgs)

    # 若没有工具调用，根据用户消息关键词判断需要哪种表单
    form = None
    if not has_tool_calls:
        form_type = _detect_form_type(user_msg)
        if form_type:
            form = {"type": form_type}

    # 若工具返回了 TDOA 定位结果，生成一个位置示意图（折线 / 标尺）
    chart = None
    for m in msgs:
        if isinstance(m, ToolMessage) and m.name == "fault_location_tdoa":
            try:
                d = json.loads(m.content)
                if "position_m" in d and "error" not in d:
                    pos = float(d["position_m"])
                    cable_len = float(d.get("cable_len_m", 4800))
                    chart = {
                        "type": "cable_fault",
                        "title": "TDOA 故障定位",
                        "position_m": pos,
                        "cable_len_m": cable_len,
                        "within_acceptance": d.get("within_acceptance"),
                    }
            except Exception:
                continue
            break

    # 如果请求了表单，跳过 FINALIZE（避免 LLM 重复说"请上传文件"）
    if form:
        # 检查 LLM 是否已经输出了请求文件的文字，如果有就用它
        last_ai = None
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        content = last_ai.content if last_ai else "请上传所需的文件。"
        # 把 chart/form 存到 additional_kwargs，checkpoint 会自动持久化
        ai_msg = AIMessage(content=content, additional_kwargs={"chart": chart, "form": form})
        return {"messages": [ai_msg], "chart": chart, "form": form}

    final = [*msgs, SystemMessage(FINALIZE)]
    parts = []
    async for chunk in llm.astream(final, config=config):
        if chunk.content:
            parts.append(chunk.content)
    full_content = "".join(parts)
    # 把 chart 存到 additional_kwargs，checkpoint 会自动持久化
    ai_msg = AIMessage(content=full_content, additional_kwargs={"chart": chart})
    return {"messages": [ai_msg], "chart": chart}
