"""意图路由节点：判断用户意图，分发到专职智能体。"""
from typing import Literal

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from .llm import get_llm

ROUTER_PROMPT = """你是电网智能体的意图路由器。根据用户最近的问题和对话历史，判断用户意图属于哪一类：
- knowledge：询问电网规程、规范、安全规定、术语、设备原理、案例等知识性问题（如"变压器过负荷有什么规定"、"什么是N-1"）。
- data：需要查询台账、负荷、电量、告警等数据（如"有哪些110kV主变"、"最近一周负荷趋势"、"有什么告警"）。
- calc：需要做预测、计算、分析（如"预测明天负荷"）。
- cable：高压电缆故障诊断相关（如"定位故障点"、"识别故障类型"、"分析局放缺陷"、"检测线路缺陷"、"TDOA 测距"、"PRPD"、"电树/水树"）。
- substation：变电设备诊断相关（如"变压器油色谱三比值诊断"、"SF6 压力/微水检查"、"红外测温相对温差判断发热"）。
- defect：缺陷闭环管理相关（如"登记一个缺陷"、"查询严重缺陷"、"派单"、"消缺验收"、"缺陷进展"）。
- ticket：两票（工作票/操作票）智能审核相关（如"审核这张工作票"、"检查操作票"、"看看票面有没有问题"）。注意：AI 只标差异出建议、不下合格性结论。
- dispatch：调度事故研判相关（如"研判这次跳闸事故"、"这个故障事件是什么类型"、"查看保护动作记录"、"调度事件处置"）。注意：指调度运行侧的跳闸/故障事件研判，与电缆故障诊断(cable)不同；AI 只出推理和建议，不下最终定性。
- flisr：配网馈线故障定位→隔离→恢复辅助决策相关（如"帮我做配网故障隔离恢复"、"馈线 L1-S2 故障怎么处理"、"查看馈线拓扑"、"转供"）。注意：所有开关操作只是建议，需值班调度确认后执行。
- line_loss：台区线损分析相关（如"算一下台区线损"、"分析台区线损异常"、"台区 T-005 线损排查"、"查看台区清单"）。注意：窃电/漏电只用疑似/疑点措辞，不武断定性。
- chat：寒暄、闲聊、与电网业务无关的通用对话。

只能输出一个意图。"""


class Intent(BaseModel):
    intent: Literal["knowledge", "data", "calc", "cable", "substation", "defect", "ticket", "dispatch", "flisr", "line_loss", "chat"] = Field(description="识别出的意图")
    reason: str = Field(default="", description="判断理由")


async def router_node(state: dict, config):
    """路由节点：结构化输出识别意图。失败时兜底为 chat。"""
    llm = get_llm(temperature=0)
    # 用 function_calling 方式做结构化输出：DeepSeek 等国产 API 不支持 json_schema 型 response_format
    structured = llm.with_structured_output(Intent, method="function_calling")
    msgs = [SystemMessage(ROUTER_PROMPT), *state["messages"][-6:]]
    try:
        res = await structured.ainvoke(msgs, config=config)
        intent = res.intent
    except Exception:
        intent = "chat"
    return {"intent": intent}
