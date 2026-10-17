"""配网 FLISR 工具：馈线故障 定位→隔离→恢复 辅助决策算法。

经典三阶段算法，纯硬编码确定性输出：
1. 定位：校验故障区段属于该馈线；
2. 隔离：拉开故障段上/下游两个边界开关；
3. 恢复：上游健康段由源端断路器合闸恢复；下游健康段合联络开关转供相邻馈线，
   并做容量校验（转供后相邻馈线总负荷 ≤ load_limit_kw），超限返回维持失电/转移负荷。
数据存 feeders / feeder_sections（幂等建表，表空播种）。
定位铁律（辅助不拍板）：输出建议操作步骤，所有开关操作需值班调度确认后执行。
"""
from __future__ import annotations

from langchain_core.tools import tool

from .db import connect
from .flisr_db import ensure_flisr_tables


@tool
async def list_feeders() -> dict:
    """查询配网馈线拓扑清单（只读，含各段负荷与边界开关）。

    返回每条馈线的馈线号、变电站、源端断路器、联络开关/相邻馈线，
    以及按拓扑顺序排列的馈线段（区段号/段名/负荷/上端开关/下端开关），
    供前端下拉选择与 FLISR 演示参考。

    Returns:
        dict: feeders[{feeder_id, feeder_name, substation, voltage_level,
                       source_breaker, tie_breaker, tie_to_feeder,
                       rated_capacity_kva, load_limit_kw, total_load_kw, sections[]}]
    """
    await ensure_flisr_tables()
    conn = await connect()
    try:
        rows = await conn.fetch("SELECT * FROM feeders ORDER BY feeder_id")
        result = []
        for f in rows:
            secs = await conn.fetch(
                "SELECT section_no, section_name, load_kw, up_switch, down_switch, is_lateral "
                "FROM feeder_sections WHERE feeder_id = $1 ORDER BY seq",
                f["feeder_id"],
            )
            total = sum(s["load_kw"] for s in secs)
            result.append({
                "feeder_id": f["feeder_id"],
                "feeder_name": f["feeder_name"],
                "substation": f["substation"],
                "voltage_level": f["voltage_level"],
                "source_breaker": f["source_breaker"],
                "tie_breaker": f["tie_breaker"],
                "tie_to_feeder": f["tie_to_feeder"],
                "rated_capacity_kva": f["rated_capacity_kva"],
                "load_limit_kw": f["load_limit_kw"],
                "total_load_kw": total,
                "sections": [dict(s) for s in secs],
            })
        return {"mode": "flisr", "feeders": result}
    finally:
        await conn.close()


@tool
async def run_flisr(
    feeder_id: str,
    fault_section: str,
    fault_type: str = "永久",
) -> dict:
    """馈线故障定位→隔离→恢复（FLISR）辅助决策。

    对一次馈线故障自动生成建议操作序列：定位故障段 → 拉开边界开关隔离 →
    上游段源端恢复 + 下游段联络转供（校验相邻馈线负荷不超限）。
    所有操作仅为辅助建议，需值班调度确认后执行。

    Args:
        feeder_id: 馈线号，如 L1（可用 list_feeders 查询）
        fault_section: 故障区段号，如 L1-S2
        fault_type: 故障性质（瞬时/永久），仅用于展示，默认永久

    Returns:
        dict: mode, feeder_id, feeder_name, fault_section, fault_type,
              steps[{seq, phase, action, switch, why}],
              restored_kw, unserved_kw, load_check, summary, note
    """
    await ensure_flisr_tables()
    conn = await connect()
    try:
        f = await conn.fetchrow(
            "SELECT * FROM feeders WHERE feeder_id = $1", feeder_id
        )
        if not f:
            return {"error": f"馈线 {feeder_id} 不存在，可先调用 list_feeders 查看馈线清单"}
        sections = await conn.fetch(
            "SELECT * FROM feeder_sections WHERE feeder_id = $1 ORDER BY seq", feeder_id
        )
        sec_names = [s["section_no"] for s in sections]
        if fault_section not in sec_names:
            return {"error": f"故障区段 {fault_section} 不在馈线 {feeder_id} 上，可选区段: {', '.join(sec_names)}"}

        i = sec_names.index(fault_section)
        sec = sections[i]
        upstream = sections[:i]
        downstream = sections[i + 1:]

        steps: list[dict] = []
        seq_no = 0

        def add(phase: str, action: str, switch: str, why: str) -> None:
            nonlocal seq_no
            seq_no += 1
            steps.append({"seq": seq_no, "phase": phase, "action": action,
                          "switch": switch or "-", "why": why})

        # 1. 定位
        add("定位", "判定", "-",
            f"{fault_section} 为故障区段（该段失电 + 保护动作/重合失败信号），故障性质：{fault_type}")

        # 2. 隔离：拉开故障段两个边界开关（联络开关平时断开则确认保持断开）
        add("隔离", "拉开", sec["up_switch"], "隔离故障段上游侧")
        if sec["down_switch"] == f["tie_breaker"]:
            add("隔离", "确认断开", sec["down_switch"], "联络开关正常运行在断开位置，保持断开隔离故障段下游侧")
        else:
            add("隔离", "拉开", sec["down_switch"], "隔离故障段下游侧")

        # 3. 恢复
        restored_kw = 0.0
        unserved_kw = 0.0
        load_check = None
        if upstream:
            add("恢复", "合闸", f["source_breaker"],
                f"恢复上游健康段 {', '.join(s['section_no'] for s in upstream)} 供电")
            restored_kw += sum(s["load_kw"] for s in upstream)
        if downstream:
            trans_kw = sum(s["load_kw"] for s in downstream)
            downstream_txt = ', '.join(s["section_no"] for s in downstream)
            if f["tie_to_feeder"]:
                nb = await conn.fetchrow(
                    "SELECT * FROM feeders WHERE feeder_id = $1", f["tie_to_feeder"]
                )
                nb_secs = await conn.fetch(
                    "SELECT load_kw FROM feeder_sections WHERE feeder_id = $1", f["tie_to_feeder"]
                )
                nb_load = sum(r["load_kw"] for r in nb_secs) if nb_secs else 0.0
                limit = nb["load_limit_kw"] if nb else 0.0
                new_total = nb_load + trans_kw
                rate_pct = round(new_total / limit * 100, 1) if limit else 0.0
                load_check = {
                    "transferred_kw": trans_kw,
                    "neighbor_feeder": f["tie_to_feeder"],
                    "neighbor_current_kw": nb_load,
                    "neighbor_limit_kw": limit,
                    "after_total_kw": new_total,
                    "rate_pct": rate_pct,
                    "ok": new_total <= limit,
                }
                if new_total <= limit:
                    add("恢复", "合闸", f["tie_breaker"],
                        f"由相邻馈线 {f['tie_to_feeder']} 转供下游健康段 {downstream_txt}"
                        f"（转供后 {f['tie_to_feeder']} 总负荷 {new_total:g}kW ≤ 限额 {limit:g}kW，负荷率 {rate_pct}%）")
                    restored_kw += trans_kw
                else:
                    add("恢复", "暂不合闸", f["tie_breaker"],
                        f"转供下游 {downstream_txt} 后 {f['tie_to_feeder']} 总负荷 {new_total:g}kW "
                        f"超过限额 {limit:g}kW（负荷率 {rate_pct}%），需先对 {f['tie_to_feeder']} "
                        f"转移负荷/限电后再合联络开关；或维持失电安排抢修")
                    unserved_kw += trans_kw
            else:
                add("恢复", "无联络", "-",
                    f"馈线 {feeder_id} 无联络开关，无法转供，下游健康段 {downstream_txt} 维持失电，安排抢修")
                unserved_kw += trans_kw

        summary = (
            f"馈线 {feeder_id}「{f['feeder_name']}」{fault_section} 故障隔离完成："
            f"可恢复 {restored_kw:g}kW，未恢复 {unserved_kw:g}kW。"
        )
        return {
            "mode": "flisr",
            "feeder_id": feeder_id,
            "feeder_name": f["feeder_name"],
            "substation": f["substation"],
            "fault_section": fault_section,
            "fault_type": fault_type,
            "steps": steps,
            "restored_kw": restored_kw,
            "unserved_kw": unserved_kw,
            "load_check": load_check,
            "summary": summary,
            "note": "所有开关操作仅为辅助建议，需值班调度确认后执行。",
        }
    finally:
        await conn.close()


ALL_FLISR_TOOLS = [
    list_feeders, run_flisr,
]
