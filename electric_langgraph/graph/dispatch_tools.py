"""调度事故研判工具：保护动作 → 故障类型/区段推理 → 处理建议。

规则引擎纯硬编码（保护动作组合 → 故障类型/影响区段/处置建议），确定性输出。
定位铁律（辅助不拍板）：输出推理链(reasoning) + 建议 + 疑问，最终事故定性以人工复核为准。
数据存 fault_events / protection_actions（幂等建表，表空播种）。
"""
from __future__ import annotations

from langchain_core.tools import tool

from .db import connect
from .dispatch_db import JUDGE_FLOW, ensure_dispatch_tables

# 事件子集列：列表/研判回填用（时间戳转北京时区文本，asyncpg 无需 datetime 对象）
_EVENT_COLS = (
    "id, event_no, substation, line_name, voltage_level, tripped_breaker, loss_mw, "
    "affected_area, judge_status, fault_type, "
    "to_char(ts AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS ts, "
    "to_char(updated_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS updated_at"
)


def _has(actions: list[dict], prot: str) -> bool:
    return any(a["prot_name"] == prot for a in actions)


def _has_act(actions: list[dict], prot: str, act: str) -> bool:
    return any(a["prot_name"] == prot and a["act"] == act for a in actions)


def _matched_prots(actions: list[dict], *prots: str) -> list[str]:
    """返回 actions 中命中的保护名称（用于推理链展示）。"""
    return [p for p in prots if any(a["prot_name"] == p for a in actions)]


def _infer_fault(actions: list[dict]) -> dict:
    """保护动作集合 → {fault_type_zh, scope, advice[]}。规则按优先级先命中先得。"""
    # 1. 断路器拒动/失灵（优先级最高，风险最大）
    if _has(actions, "失灵保护"):
        return {
            "fault_type_zh": "断路器拒动（失灵保护动作）",
            "scope": "故障断路器及其相邻断路器/母线，需扩大隔离范围",
            "advice": [
                "核对断路器本体状态与操作机构，确认是否存在拒动",
                "启动失灵保护扩大隔离范围至相邻断路器/母线",
                "故障未查清前严禁送电",
            ],
        }
    # 2. 变压器区内故障
    if _has(actions, "变压器差动保护"):
        return {
            "fault_type_zh": "变压器区内故障",
            "scope": "主变本体及差动保护区内（绕组/引线/套管/CT 回路）",
            "advice": [
                "立即检查变压器本体、瓦斯继电器、油色谱与外观",
                "确认故障未消除前严禁送电（差动保护动作属区内故障，不可强送）",
                "通知检修班组对差动保护区内一次设备全面检查",
            ],
        }
    # 3. 线路区内故障（纵联差动）
    if _has(actions, "线路纵联差动保护"):
        return {
            "fault_type_zh": "线路区内故障（全线）",
            "scope": "该线路全线（两侧开关之间）",
            "advice": [
                "对该线路全线巡线查找故障点",
                "确认故障隔离、线路具备送电条件后再恢复供电",
                "结合故障录波核对故障电流性质与保护配合",
            ],
        }
    # 4. 单相接地永久故障（零序 + 重合失败）
    if _has(actions, "零序保护") and _has_act(actions, "重合闸", "重合失败"):
        return {
            "fault_type_zh": "近区单相接地永久性故障",
            "scope": "本线路近区（保护Ⅰ段范围）接地故障",
            "advice": [
                "按规程严禁强送，需巡线查单相接地故障点",
                "核对零序电流与故障录波，确认故障性质与故障点距离",
                "处理接地故障时注意人身安全距离与接地点防护",
            ],
        }
    # 5. 单相接地故障（无重合失败信息）
    if _has(actions, "零序保护"):
        return {
            "fault_type_zh": "单相接地故障",
            "scope": "本线路接地故障（区段结合距离/录波确认）",
            "advice": [
                "结合重合闸与故障录波进一步确认故障性质",
                "若为瞬时性故障可恢复送电，否则需巡线处理",
            ],
        }
    # 6. 距离Ⅰ段 + 重合失败 → 近区永久
    if _has(actions, "距离保护Ⅰ段") and _has_act(actions, "重合闸", "重合失败"):
        return {
            "fault_type_zh": "本线路近区永久性故障",
            "scope": "本线路近区（距离Ⅰ段保护范围，首段）",
            "advice": [
                "按规程严禁强送，需对近区段设备/线路巡线检查",
                "核对故障录波故障距离与保护定值是否匹配",
            ],
        }
    # 7. 距离Ⅰ段
    if _has(actions, "距离保护Ⅰ段"):
        return {
            "fault_type_zh": "本线路近区故障",
            "scope": "本线路近区（距离Ⅰ段保护范围，首段）",
            "advice": [
                "对近区段巡线检查",
                "结合重合闸结果与故障录波确认故障性质",
            ],
        }
    # 8. 距离Ⅱ段
    if _has(actions, "距离保护Ⅱ段"):
        return {
            "fault_type_zh": "本线路中段故障",
            "scope": "本线路中段（距离Ⅱ段保护范围）",
            "advice": [
                "对线路中段巡线检查",
                "结合重合闸与故障录波确认故障性质",
            ],
        }
    # 9. 距离Ⅲ段
    if _has(actions, "距离保护Ⅲ段"):
        return {
            "fault_type_zh": "本线路远端或相邻线路故障",
            "scope": "本线路远端，或相邻线路（存在越级可能）",
            "advice": [
                "检查相邻线路保护配合与定值，判断是否存在越级跳闸",
                "对线路远端巡线检查",
            ],
        }
    # 10. 电流速断
    if _has(actions, "电流速断保护"):
        return {
            "fault_type_zh": "近区故障（电流速断动作）",
            "scope": "本线路近区",
            "advice": [
                "对近区设备/线路巡线检查",
                "核对保护定值与故障录波",
            ],
        }
    # 11. 重合成功 → 瞬时性故障（已恢复）
    if _has_act(actions, "重合闸", "重合成功"):
        return {
            "fault_type_zh": "瞬时性故障（重合闸成功，已恢复送电）",
            "scope": "本线路（故障点已消失）",
            "advice": [
                "线路已重合成功恢复供电",
                "持续监视负荷与保护报文，如有异常及时处理",
            ],
        }
    # 12. 重合失败（无其他主保护信息）
    if _has_act(actions, "重合闸", "重合失败"):
        return {
            "fault_type_zh": "永久性故障（重合闸失败）",
            "scope": "本线路（故障未消除）",
            "advice": [
                "按规程严禁强送，需巡线查找故障点",
                "待故障处理并验收后再送电",
            ],
        }
    # 13. 仅有后备保护（过流等）
    if _has(actions, "过流保护"):
        return {
            "fault_type_zh": "故障类型待确认（后备保护动作）",
            "scope": "本线路或本站相邻设备，需结合录波确认",
            "advice": [
                "结合故障录波、保护报文综合判断故障性质",
                "检查本站设备与相邻线路，判断是否越级",
            ],
        }
    # 14. 无法判定（护栏）
    return {
        "fault_type_zh": "无法判定，请人工确认",
        "scope": "信息不足，无法确定影响区段",
        "advice": [
            "补充保护动作报文、故障录波、开关变位等信息后再研判",
            "由值班调度会同继保专业人员人工分析",
        ],
    }


_GENERIC_QUESTIONS = [
    "是否已核对故障录波（故障电流/电压波形）？",
    "是否已检查保护动作报文与 SOE 时标？",
    "断路器跳闸时是否伴随操作机构异常或 SF6 气压低告警？",
]


@tool
async def analyze_fault_event(event_no: str) -> dict:
    """对一次跳闸故障事件做自动研判（保护动作 → 故障类型/区段 → 处理建议）。

    读取事件档案及其保护动作明细，按硬编码规则表推理故障类型、影响区段与处置建议，
    输出推理链(reasoning)供人工复核。本工具只出建议与疑问，不下最终事故定性结论。

    Args:
        event_no: 故障事件编号 A-YYYYMMDD-序号，如 A-20260909-001

    Returns:
        dict: mode, event_no, fault_type_zh, reasoning, scope, suggestions,
              questions, summary, judge_status
    """
    await ensure_dispatch_tables()
    conn = await connect()
    try:
        row = await conn.fetchrow(
            f"SELECT {_EVENT_COLS} FROM fault_events WHERE event_no = $1", event_no
        )
        if not row:
            return {"error": f"故障事件 {event_no} 不存在，可先调用 list_fault_events 查看事件清单"}
        acts = await conn.fetch(
            """SELECT device, prot_name, act, description
               FROM protection_actions WHERE event_no = $1 ORDER BY id""",
            event_no,
        )
        action_list = [dict(a) for a in acts]
        infer = _infer_fault(action_list)

        # 推理链：保护动作记录 + 命中的规则
        reasoning = [f"保护动作记录：{a['device']} {a['prot_name']}{a['act']}" for a in action_list]
        reasoning.append(
            f"规则匹配：{'、'.join(_matched_prots(action_list, *[a['prot_name'] for a in action_list]) or ['（无主保护）'])} "
            f"→ {infer['fault_type_zh']}"
        )

        suggestions = list(infer["advice"])
        suggestions.append("以上为辅助研判建议，具体处置请以值班调度指令为准。")

        await conn.execute(
            "UPDATE fault_events SET judge_status='已研判', fault_type=$2, updated_at=now() "
            "WHERE event_no=$1",
            event_no, infer["fault_type_zh"],
        )

        summary = (
            f"依据 {len(action_list)} 条保护动作信息，研判为「{infer['fault_type_zh']}」，"
            f"影响范围：{infer['scope']}。请结合故障录波人工复核后处置。"
        )
        return {
            "mode": "dispatch",
            "event_no": row["event_no"],
            "ts": row["ts"],
            "substation": row["substation"],
            "line_name": row["line_name"],
            "voltage_level": row["voltage_level"],
            "tripped_breaker": row["tripped_breaker"],
            "loss_mw": row["loss_mw"],
            "affected_area": row["affected_area"],
            "judge_status": "已研判",
            "fault_type_zh": infer["fault_type_zh"],
            "scope": infer["scope"],
            "reasoning": reasoning,
            "suggestions": suggestions,
            "questions": list(_GENERIC_QUESTIONS),
            "summary": summary,
        }
    finally:
        await conn.close()


@tool
async def list_fault_events(
    status: str = "",
    substation: str = "",
    keyword: str = "",
    limit: int = 20,
) -> dict:
    """按条件查询故障事件台账（只读）。

    多条件为 AND 关系；keyword 模糊匹配线路名/变电站/影响区域/故障类型。

    Args:
        status: 状态筛选（待研判/已研判/已处置），空=全部
        substation: 变电站名模糊匹配
        keyword: 线路名/变电站/影响区域/故障类型关键词
        limit: 最多返回条数，默认 20

    Returns:
        dict: total, items[{event_no, substation, line_name, judge_status, fault_type, ...}]
    """
    await ensure_dispatch_tables()
    where, params = [], []
    if status:
        if status not in JUDGE_FLOW:
            return {"error": f"非法状态 {status}，可选: {list(JUDGE_FLOW)}"}
        where.append(f"judge_status = ${len(params) + 1}")
        params.append(status)
    if substation:
        where.append(f"substation ILIKE ${len(params) + 1}")
        params.append(f"%{substation}%")
    if keyword:
        where.append(
            f"(line_name ILIKE ${len(params) + 1} OR substation ILIKE ${len(params) + 1} "
            f"OR affected_area ILIKE ${len(params) + 1} OR fault_type ILIKE ${len(params) + 1})"
        )
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
    sql = f"SELECT {_EVENT_COLS} FROM fault_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY ts DESC LIMIT ${len(params) + 1}"
    params.append(limit)
    conn = await connect()
    try:
        rows = await conn.fetch(sql, *params)
        return {"total": len(rows), "items": [dict(r) for r in rows]}
    finally:
        await conn.close()


@tool
async def update_fault_event(event_no: str, action: str = "已处置", note: str = "") -> dict:
    """推进故障事件状态（状态机校验）。

    动作 → 目标状态：已处置 → 已处置。待研判事件必须先研判（analyze_fault_event）
    才能处置，非法跳转返回 error。

    Args:
        event_no: 故障事件编号 A-YYYYMMDD-序号
        action: 已处置
        note: 处置备注

    Returns:
        dict: event_no, old_status, new_status, message
    """
    if action not in ("已处置",):
        return {"error": f"非法动作 {action}，可选: 已处置"}
    await ensure_dispatch_tables()
    conn = await connect()
    try:
        row = await conn.fetchrow(
            "SELECT judge_status FROM fault_events WHERE event_no = $1", event_no
        )
        if not row:
            return {"error": f"故障事件 {event_no} 不存在"}
        old = row["judge_status"]
        target = action
        if target not in JUDGE_FLOW.get(old, []):
            return {"error": f"状态流转非法: {old} → {target}（当前 {old} 只能转 {JUDGE_FLOW.get(old)}），待研判事件需先研判再处置"}
        await conn.execute(
            "UPDATE fault_events SET judge_status=$2, handling_note=$3, updated_at=now() "
            "WHERE event_no=$1",
            event_no, target, note,
        )
        return {
            "event_no": event_no, "old_status": old, "new_status": target,
            "message": f"故障事件 {event_no} 状态已更新: {old} → {target}。",
        }
    finally:
        await conn.close()


ALL_DISPATCH_TOOLS = [
    analyze_fault_event, list_fault_events, update_fault_event,
]
