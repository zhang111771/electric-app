"""计算分析智能体：负荷预测等计算任务。"""
import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..llm import get_llm
from ..tools import load_forecast

SYSTEM = """你是电网运行计算分析专家，负责负荷预测等计算分析任务。

使用要求：
1. 先调用 load_forecast 工具获取预测结果，再基于结果回答；
2. 负荷节点名称可从工具返回的 available 提示中获得；
3. 用中文回答，说明预测峰值负荷、出现时间等关键信息。"""

FINALIZE = "请基于预测结果，用中文给出简洁清晰的最终回答：预测峰值负荷与时间、总体走势。"


async def calc_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM), *state["messages"][-6:]]
    msgs = await run_tool_loop(llm, msgs, [load_forecast], config)

    # 生成预测折线图
    chart = None
    for m in msgs:
        if isinstance(m, ToolMessage) and m.name == "load_forecast":
            try:
                d = json.loads(m.content)
                if "forecast" in d and d["forecast"]:
                    chart = {
                        "type": "line",
                        "title": f"{d['bus']} 未来 {d['hours']} 小时负荷预测",
                        "x": [p[0] for p in d["forecast"]],
                        "series": [{"name": "预测(MW)", "data": [p[1] for p in d["forecast"]]}],
                    }
            except Exception:
                continue
            break

    final = [*msgs, SystemMessage(FINALIZE)]
    parts = []
    async for chunk in llm.astream(final, config=config):
        if chunk.content:
            parts.append(chunk.content)
    return {"messages": [AIMessage(content="".join(parts))], "chart": chart}
