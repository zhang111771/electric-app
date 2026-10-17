"""数据查询智能体：自然语言 → SQL → 查询 → 解释，自动生成折线图。"""
import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from ..agent_loop import run_tool_loop
from ..chart import rows_to_chart
from ..llm import get_llm
from ..tools import get_data_dict, get_schema, query_sql

SYSTEM = """你是电网数据查询专家，负责在电网模拟数据库中查询台账、负荷、告警等数据。

数据库结构：
{schema}

数据字典（关键字段的取值，SQL 条件中请直接使用这些取值，不要猜测）：
{dict}

示例查询（可直接参考修改）：
- 查某类设备：SELECT asset_name, substation, voltage_level, status FROM assets WHERE asset_type='主变压器' AND voltage_level LIKE '110%'
- 查某节点最近负荷（约7天）：SELECT ts, load_mw FROM load_data WHERE bus_name='城东变电站1号主变' ORDER BY ts DESC LIMIT 168
- 按天聚合负荷趋势：SELECT substr(ts,1,10) AS day, ROUND(AVG(load_mw),2) AS avg_load FROM load_data WHERE bus_name='城东变电站1号主变' GROUP BY day ORDER BY day
- 查告警：SELECT ts, substation, device, alarm_type, level, message FROM alarms WHERE level='严重' ORDER BY ts DESC LIMIT 20

使用要求：
1. 直接调用 query_sql 工具查询，不要输出思考过程、不要先"探查表结构"；
2. 基于真实查询结果回答，涉及时间序列时描述趋势，涉及设备时说明电压等级、状态等关键字段；
3. 用中文回答，标注关键数字，不要编造数据；查询失败时根据错误修正 SQL 重试；
4. 返回多行明细（≥2 行）时用 markdown 表格展示，便于阅读。"""

FINALIZE = "请基于上述查询结果，用中文给出简洁清晰的最终回答，突出关键数字和结论。"


async def data_agent_node(state: dict, config):
    llm = get_llm()
    msgs = [SystemMessage(SYSTEM.format(schema=await get_schema(), dict=await get_data_dict())),
            *state["messages"][-6:]]
    msgs = await run_tool_loop(llm, msgs, [query_sql], config, max_iters=4)

    # 若工具结果含时间序列，自动生成折线图（取最后一次可绘图的查询结果，即最终数据）
    chart = None
    for m in msgs:
        if isinstance(m, ToolMessage) and m.name == "query_sql":
            try:
                rows = json.loads(m.content)
                if isinstance(rows, list) and rows and "error" not in rows[0]:
                    c = rows_to_chart("查询结果", rows)
                    if c:
                        chart = c
            except Exception:
                continue

    final = [*msgs, SystemMessage(FINALIZE)]
    parts = []
    async for chunk in llm.astream(final, config=config):
        if chunk.content:
            parts.append(chunk.content)
    return {"messages": [AIMessage(content="".join(parts))], "chart": chart}
