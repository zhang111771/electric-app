"""台区线损工具：线损率计算 + 合理区间判定 + 异常疑点定位。

客观计算（线损率 vs 容量分档上限）可明确判级；"窃电/漏电"一律用
疑似/疑点措辞并给建议，不做武断定性（辅助不拍板）。
数据存 transformer_areas / area_meter_reading（幂等建表，表空播种）。
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from .db import connect
from .line_loss_db import ensure_line_loss_tables

# 合理线损率上限（按台区容量分档，%）：≤50kVA 8%、≤200 6%、≤400 5%、>400 4%
_UPLIMIT_BY_CAPACITY = [
    (50, 8.0),
    (200, 6.0),
    (400, 5.0),
    (float("inf"), 4.0),
]


def _uplimit(capacity_kva: float) -> float:
    """台区容量 → 合理线损率上限（%）。"""
    for cap, limit in _UPLIMIT_BY_CAPACITY:
        if capacity_kva <= cap:
            return limit
    return 4.0


@tool
async def calc_line_loss(
    supply_kwh: float,
    sales_kwh: float,
    capacity_kva: float = 315,
) -> dict:
    """计算台区线损率并判定是否合理（客观计算，可明确判级）。

    损失电量 = 供电量 - 售电量；线损率 = 损失电量 / 供电量 × 100%。
    按容量分档上限判定 正常/偏高/超标；极端值（售>供、>60%）给护栏结论，
    "窃电/漏电"用疑似措辞，不做武断定性。

    Args:
        supply_kwh: 台区供电量（kWh）
        sales_kwh: 台区售电量（kWh）
        capacity_kva: 台区容量（kVA），用于查合理线损率上限

    Returns:
        dict: mode, sub_mode, supply_kwh, sales_kwh, capacity_kva, uplimit_pct,
              loss_kwh, loss_rate_pct, judgment, advice, note
    """
    if supply_kwh <= 0:
        return {"error": "供电量必须大于 0"}
    loss_kwh = supply_kwh - sales_kwh
    rate_pct = loss_kwh / supply_kwh * 100
    uplimit = _uplimit(capacity_kva)

    if rate_pct < 0:
        judgment = "计量异常"
        advice = [
            "售电量大于供电量，存在计量异常（表计故障/追补电量/数据录入错误），请核查台区总表与户表电量",
            "核对抄表周期与倍率，确认是否误装或错抄",
        ]
    elif rate_pct > 60:
        judgment = "疑似窃电/漏电或计量错误"
        advice = [
            "线损率异常偏高（>60%），疑似窃电或总表计量错误",
            "建议现场核查台区下大用户用电情况与表计封印",
            "核对供电量数据来源（总表采集是否异常）",
        ]
    elif rate_pct <= uplimit:
        judgment = "正常"
        advice = ["线损率在合理范围内，继续常规监测即可"]
    elif rate_pct <= uplimit * 1.5:
        judgment = "偏高"
        advice = [
            "线损率超出合理上限，建议核查三相不平衡、无功补偿与低压线路老化",
            "加强台区负荷监测，关注是否存在窃电疑点",
        ]
    else:
        judgment = "超标"
        advice = [
            "线损率显著超标，建议安排现场排查（线路老化/窃电/计量异常）",
            "按疑似窃电/漏电方向核查台区下用户用电情况",
        ]

    note = f"合理线损率上限（{capacity_kva:g}kVA 台区）：{uplimit:g}%。窃电/漏电仅为疑似判断，需现场核实。"
    return {
        "mode": "line_loss",
        "sub_mode": "calc",
        "supply_kwh": supply_kwh,
        "sales_kwh": sales_kwh,
        "capacity_kva": capacity_kva,
        "uplimit_pct": uplimit,
        "loss_kwh": round(loss_kwh, 1),
        "loss_rate_pct": round(rate_pct, 2),
        "judgment": judgment,
        "advice": advice,
        "note": note,
    }


@tool
async def analyze_area_loss(area_no: str, days: int = 30) -> dict:
    """分析台区近 N 天线损：均值/趋势/突增/持续超标/负线损，定位异常疑点。

    客观统计（均值、趋势、超上限天数）明确给出；"窃电/漏电"用疑似/疑点措辞
    并给建议，不武断定性。样本不足 5 天给护栏提示。

    Args:
        area_no: 台区编号 T-序号（可用 query_area_loss 不带 area_no 查全部台区）
        days: 分析窗口（天），默认 30，范围 7~90

    Returns:
        dict: mode, sub_mode, area_no, area_name, capacity_kva, uplimit_pct,
              days, n_readings, avg_rate, trend, anomaly_days[], suspicions[],
              suggestions[], overall, status
    """
    days = max(7, min(int(days), 90))
    await ensure_line_loss_tables()
    conn = await connect()
    try:
        area = await conn.fetchrow(
            "SELECT * FROM transformer_areas WHERE area_no = $1", area_no
        )
        if not area:
            return {"error": f"台区 {area_no} 不存在，可调用 query_area_loss 查看台区清单"}
        rows = await conn.fetch(
            "SELECT stat_date, supply_kwh, sales_kwh, loss_kwh "
            "FROM area_meter_reading WHERE area_no = $1 ORDER BY stat_date DESC LIMIT $2",
            area_no, days,
        )
        rows = list(reversed(rows))  # 时间正序，便于趋势/持续超标判断
        if len(rows) < 5:
            return {"error": f"台区 {area_no} 近 {len(rows)} 天样本不足（需 ≥5 天），暂无法分析"}

        uplimit = _uplimit(area["capacity_kva"])
        rates = []
        for r in rows:
            if r["supply_kwh"] > 0:
                rates.append(r["loss_kwh"] / r["supply_kwh"] * 100)
        avg_rate = sum(rates) / len(rates) if rates else 0.0

        # 趋势：近 7 天均值 vs 更早窗口均值
        recent = rates[-7:]
        prior = rates[:-7] if len(rates) > 14 else rates[: max(len(rates) // 2, 1)]
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        prior_avg = sum(prior) / len(prior) if prior else 0.0
        diff = recent_avg - prior_avg
        trend = "上升" if diff > 1 else ("下降" if diff < -1 else "平稳")

        # 异常日：突增（单日 > max(2×均值, 1.5×上限)）或 负线损（售>供）
        spike_threshold = max(2 * avg_rate, 1.5 * uplimit)
        anomaly_days = []
        for r, rate in zip(rows, rates):
            reasons = []
            if rate > spike_threshold:
                reasons.append("突增")
            if rate < 0:
                reasons.append("负线损")
            if reasons:
                anomaly_days.append({
                    "stat_date": r["stat_date"].strftime("%Y-%m-%d"),
                    "rate": round(rate, 2),
                    "supply_kwh": r["supply_kwh"],
                    "sales_kwh": r["sales_kwh"],
                    "reason": "、".join(reasons),
                })

        # 持续超标：连续 ≥3 天线损率 > 上限的区间天数
        over_streak = 0
        max_streak = 0
        for rate in rates:
            over_streak = over_streak + 1 if rate > uplimit else 0
            max_streak = max(max_streak, over_streak)
        sustained = max_streak >= 3

        # 疑似窃电：突增 + 该日售电量明显低于非异常段（销量下降）
        normal_sales = [r["sales_kwh"] for r, rate in zip(rows, rates) if rate <= uplimit]
        base_sales = sum(normal_sales) / len(normal_sales) if normal_sales else 0.0
        spike_sales_drop = [
            a for a in anomaly_days
            if "突增" in a["reason"] and base_sales and a["sales_kwh"] < base_sales * 0.9
        ]

        suspicions = []
        if spike_sales_drop:
            suspicions.append({
                "type": "疑似窃电",
                "evidence": f"共 {len(spike_sales_drop)} 天线损突增且售电量较日常均值下降超 10%"
                            f"（如 {spike_sales_drop[0]['stat_date']} 线损率 {spike_sales_drop[0]['rate']}%）",
            })
        if sustained and any(r > uplimit * 1.5 for r in rates):
            suspicions.append({
                "type": "疑似漏电或线损治理不到位",
                "evidence": f"近 {days} 天平均线损率 {avg_rate:.2f}%（上限 {uplimit:g}%），"
                            f"连续 ≥{max_streak} 天超上限，可能为低压线路老化/三相不平衡/漏电",
            })
        if any(r < 0 for r in rates):
            suspicions.append({
                "type": "计量异常",
                "evidence": "存在售电量大于供电量的日期（负线损），疑似表计故障或抄表/追补问题",
            })

        # 综合结论（客观部分明确判级；涉窃电/漏电用疑似）
        if any(r < 0 for r in rates):
            overall = "线损异常（计量异常）"
        elif avg_rate > uplimit * 1.2 or suspicions:
            overall = "线损异常，建议排查"
        elif avg_rate > uplimit:
            overall = "线损偏高，建议核查"
        else:
            overall = "线损正常"

        suggestions = []
        if overall == "线损正常":
            suggestions.append("继续常规监测，关注周期性波动即可。")
        else:
            if "疑似窃电" in [s["type"] for s in suspicions]:
                suggestions.append("建议开展现场用电检查（大用户表计封印、异常户抽查），排查窃电疑点。")
            if "疑似漏电或线损治理不到位" in [s["type"] for s in suspicions]:
                suggestions.append("核查台区低压线路老化、三相不平衡与无功补偿，必要时开展线路改造/降损治理。")
            if any(s["type"] == "计量异常" for s in suspicions):
                suggestions.append("核对台区总表与户表倍率/抄表周期，处理负线损计量异常。")
            suggestions.append("以上疑点为辅助分析，请结合现场核实后确定处理措施。")

        new_status = "疑似异常" if (overall != "线损正常" and suspicions) else "正常"
        await conn.execute(
            "UPDATE transformer_areas SET status=$2, updated_at=now() WHERE area_no=$1",
            area_no, new_status,
        )

        return {
            "mode": "line_loss",
            "sub_mode": "analyze",
            "area_no": area_no,
            "area_name": area["area_name"],
            "capacity_kva": area["capacity_kva"],
            "uplimit_pct": uplimit,
            "days": days,
            "n_readings": len(rows),
            "avg_rate": round(avg_rate, 2),
            "trend": trend,
            "recent_avg": round(recent_avg, 2),
            "anomaly_days": anomaly_days,
            "suspicions": suspicions,
            "suggestions": suggestions,
            "overall": overall,
            "status": new_status,
        }
    finally:
        await conn.close()


@tool
async def query_area_loss(area_no: str = "", date_from: str = "", date_to: str = "") -> dict:
    """查询台区清单或某台区日电量明细（只读）。

    不带 area_no 返回全部台区清单（含档案）；带 area_no 返回近段时间的日电量明细
    （含逐日线损率）。date_from/date_to 为 YYYY-MM-DD，可选。

    Args:
        area_no: 台区编号 T-序号，空=查询台区清单
        date_from: 起始日期（YYYY-MM-DD），可选
        date_to: 结束日期（YYYY-MM-DD），可选

    Returns:
        dict: areas[{area_no, area_name, substation, capacity_kva, status}] 或
              {area_no, area_name, uplimit_pct, items[{stat_date, supply_kwh, sales_kwh,
                loss_kwh, loss_rate_pct}]}
    """
    await ensure_line_loss_tables()
    conn = await connect()
    try:
        if not area_no:
            rows = await conn.fetch(
                "SELECT area_no, area_name, substation, capacity_kva, voltage_level, status "
                "FROM transformer_areas ORDER BY area_no"
            )
            return {"areas": [dict(r) for r in rows]}

        area = await conn.fetchrow(
            "SELECT area_no, area_name, substation, capacity_kva, voltage_level, status "
            "FROM transformer_areas WHERE area_no = $1", area_no
        )
        if not area:
            return {"error": f"台区 {area_no} 不存在，可调用 query_area_loss 查看台区清单"}
        where, params = ["area_no = $1"], [area_no]

        def _to_date(s: str):
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                return None

        d_from = _to_date(date_from) if date_from else None
        d_to = _to_date(date_to) if date_to else None
        if date_from and d_from is None:
            return {"error": f"起始日期 {date_from} 格式非法，应为 YYYY-MM-DD"}
        if date_to and d_to is None:
            return {"error": f"结束日期 {date_to} 格式非法，应为 YYYY-MM-DD"}
        if d_from:
            where.append(f"stat_date >= ${len(params) + 1}")
            params.append(d_from)
        if d_to:
            where.append(f"stat_date <= ${len(params) + 1}")
            params.append(d_to)
        rows = await conn.fetch(
            f"SELECT stat_date, supply_kwh, sales_kwh, loss_kwh "
            f"FROM area_meter_reading WHERE {' AND '.join(where)} ORDER BY stat_date ASC",
            *params,
        )
        items = []
        for r in rows:
            rate = r["loss_kwh"] / r["supply_kwh"] * 100 if r["supply_kwh"] > 0 else 0.0
            items.append({
                "stat_date": r["stat_date"].strftime("%Y-%m-%d"),
                "supply_kwh": r["supply_kwh"],
                "sales_kwh": r["sales_kwh"],
                "loss_kwh": r["loss_kwh"],
                "loss_rate_pct": round(rate, 2),
            })
        return {
            "area_no": area_no,
            "area_name": area["area_name"],
            "capacity_kva": area["capacity_kva"],
            "uplimit_pct": _uplimit(area["capacity_kva"]),
            "status": area["status"],
            "items": items,
        }
    finally:
        await conn.close()


ALL_LINE_LOSS_TOOLS = [
    calc_line_loss, analyze_area_loss, query_area_loss,
]
