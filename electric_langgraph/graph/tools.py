"""智能体工具：SQL 查询、负荷预测、数据库 schema 描述。"""
import json
import random
from datetime import datetime, timedelta

from langchain_core.tools import tool

from .db import connect

_MAX_ROWS = 200


async def get_schema() -> str:
    """返回数据库表结构描述，供数据智能体理解可用数据。"""
    conn = await connect()
    try:
        tables = [r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name")]
        out = []
        for t in tables:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position", t)]
            out.append(f"表 {t} 列: {', '.join(cols)}")
        return "\n".join(out)
    finally:
        await conn.close()


async def get_data_dict() -> str:
    """数据字典：关键字段的取值枚举，避免智能体猜错枚举值导致低效查询。"""
    conn = await connect()
    try:
        lines = []
        for sql, label in [
            ("SELECT DISTINCT asset_type FROM assets", "assets.asset_type 取值"),
            ("SELECT DISTINCT voltage_level FROM assets", "assets.voltage_level 取值"),
            ("SELECT DISTINCT status FROM assets", "assets.status 取值"),
            ("SELECT DISTINCT name FROM substations", "substations.name 取值(变电站)"),
            ("SELECT DISTINCT alarm_type FROM alarms", "alarms.alarm_type 取值"),
            ("SELECT DISTINCT level FROM alarms", "alarms.level 取值"),
        ]:
            vals = [str(r[0]) for r in await conn.fetch(sql)]
            lines.append(f"{label}: {', '.join(vals)}")
        return "\n".join(lines)
    finally:
        await conn.close()


@tool
async def query_sql(sql: str) -> str:
    """对电网模拟数据库执行只读 SELECT 查询并返回结果（JSON 数组）。

    数据库表：substations(变电站), assets(设备台账), load_data(逐小时负荷), alarms(告警事件)。
    只能执行 SELECT 查询。查询结果最多返回 {max_rows} 行。
    """
    sql = (sql or "").strip()
    if not sql.lower().startswith("select"):
        return json.dumps({"error": "仅允许 SELECT 查询"}, ensure_ascii=False)
    if ";" in sql.rstrip(";"):
        return json.dumps({"error": "仅允许单条查询"}, ensure_ascii=False)
    conn = await connect()
    try:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")  # 数据库层面兜底禁止写
            rows = await conn.fetch(sql)
        return json.dumps([dict(r) for r in rows[:_MAX_ROWS]], ensure_ascii=False, default=str)
    except Exception as e:  # 返回给模型，让它修正 SQL
        return json.dumps({"error": f"查询失败: {e}"}, ensure_ascii=False)
    finally:
        await conn.close()


async def _load_buses() -> list[str]:
    conn = await connect()
    try:
        return [r[0] for r in await conn.fetch("SELECT DISTINCT bus_name FROM load_data")]
    finally:
        await conn.close()


@tool
async def load_forecast(bus_name: str, hours: int = 24) -> str:
    """对指定负荷节点做未来 N 小时（默认24）负荷预测，返回历史近24小时与预测序列（JSON）。

    预测基于该节点最近7天历史负荷的季节规律（按小时均值）+ 微弱增长趋势 + 随机扰动。
    参数 bus_name 可用节点名称，例如“城东变电站1号主变”。
    """
    hours = max(1, min(72, int(hours)))
    buses = await _load_buses()
    bus = bus_name if bus_name in buses else next((b for b in buses if bus_name in b), None)
    if not bus:
        return json.dumps({"error": f"未找到负荷节点 '{bus_name}'", "available": buses[:10]},
                          ensure_ascii=False)

    conn = await connect()
    try:
        rows = await conn.fetch(
            "SELECT ts, load_mw FROM load_data WHERE bus_name=$1 ORDER BY ts DESC LIMIT 168",
            bus)  # 最近 7 天
    finally:
        await conn.close()

    if not rows:
        return json.dumps({"error": f"节点 {bus} 无历史负荷数据"}, ensure_ascii=False)

    latest = None
    by_hour: dict[int, list[float]] = {}
    hist: list[list] = []
    for r in rows:  # 已按时间倒序
        dt = datetime.fromisoformat(r["ts"])
        if latest is None:
            latest = dt
        by_hour.setdefault(dt.hour, []).append(r["load_mw"])
        if len(hist) < 24:
            hist.append([dt.strftime("%m-%d %H:%M"), round(r["load_mw"], 2)])

    profile = {h: sum(v) / len(v) for h, v in by_hour.items()}
    growth = 0.012  # 微弱爬升趋势
    forecast: list[list] = []
    t = latest + timedelta(hours=1)
    for k in range(hours):
        val = profile.get(t.hour, 1.0) * (1 + growth * k / 24) * random.uniform(0.97, 1.03)
        forecast.append([t.strftime("%m-%d %H:%M"), round(val, 2)])
        t += timedelta(hours=1)

    return json.dumps({"bus": bus, "hours": hours, "history": hist, "forecast": forecast},
                      ensure_ascii=False)
