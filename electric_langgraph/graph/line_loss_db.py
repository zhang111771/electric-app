"""台区线损数据库层：台区档案 + 台区日电量两表。

业务闭环：台区线损率计算（客观判级）+ 异常疑点定位（窃电/漏电用疑点措辞，
不做武断定性）。表结构幂等创建（CREATE TABLE IF NOT EXISTS），种子数据仅
在表空时播种，绝不放入 data/generate_data.py（那里是 DROP+CREATE 全量重建）。
"""
from datetime import date, timedelta
from random import Random

import asyncpg

from .db import connect

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS transformer_areas (
        id SERIAL PRIMARY KEY,
        area_no TEXT NOT NULL UNIQUE,
        area_name TEXT NOT NULL,
        substation TEXT NOT NULL DEFAULT '',
        capacity_kva REAL NOT NULL DEFAULT 0,
        voltage_level TEXT NOT NULL DEFAULT '0.4kV',
        status TEXT NOT NULL DEFAULT '正常'
            CHECK (status IN ('正常', '疑似异常', '已派单', '已处置')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE IF NOT EXISTS area_meter_reading (
        id SERIAL PRIMARY KEY,
        area_no TEXT NOT NULL,
        stat_date DATE NOT NULL,
        supply_kwh REAL NOT NULL DEFAULT 0,
        sales_kwh REAL NOT NULL DEFAULT 0,
        loss_kwh REAL NOT NULL DEFAULT 0,
        household_cnt INT NOT NULL DEFAULT 0,
        UNIQUE (area_no, stat_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_amr_area ON area_meter_reading (area_no, stat_date)",
]

# 种子台区（开发演示用，表空时播种）：6 台区 × 近 30 天读数。
# T-001~T-004 正常（线损率 1%~5%），T-005 持续高线损（11%~14%，疑似漏电/治理不足），
# T-006 后 10 天突增（15%~25% 且售电量下降，疑似窃电）。
_AREAS = [
    # (area_no, area_name, substation, capacity_kva, base_supply, rate_lo, rate_hi, household)
    ("T-001", "城南公变1号", "城南变电站", 315, 1000, 0.02, 0.04, 180),
    ("T-002", "城东公变2号", "城东变电站", 200, 750, 0.02, 0.05, 150),
    ("T-003", "城西公变3号", "城西变电站", 100, 400, 0.02, 0.05, 90),
    ("T-004", "开发区箱变1号", "开发区开闭所", 630, 2000, 0.01, 0.03, 320),
    ("T-005", "城北公变4号", "城北变电站", 160, 700, 0.11, 0.14, 140),
    ("T-006", "城南工业园区台区", "城南变电站", 400, 1500, 0.02, 0.04, 260),
]
_T006_SPIKE_DAYS = 10  # T-006 最后 10 天线损突增（疑似窃电）
_T006_SPIKE_LO, _T006_SPIKE_HI = 0.15, 0.25
_SEED_DAYS = 30


def _gen_readings(area_no: str, base_supply: float, rate_lo: float,
                  rate_hi: float, household: int, rng: Random):
    """生成近 _SEED_DAYS 天的日电量（确定性伪随机）。返回 [(date, supply, sales, loss, hh)]。"""
    today = date.today()
    out = []
    for i in range(_SEED_DAYS):
        d = today - timedelta(days=_SEED_DAYS - 1 - i)
        supply = base_supply * (0.85 + rng.random() * 0.3)
        if area_no == "T-006" and i >= _SEED_DAYS - _T006_SPIKE_DAYS:
            # 突增段：售电量下降（疑似窃电）→ 线损率跳到 15%~25%
            rate = _T006_SPIKE_LO + rng.random() * (_T006_SPIKE_HI - _T006_SPIKE_LO)
            supply = base_supply * 0.9 * (0.85 + rng.random() * 0.15)
        else:
            rate = rate_lo + rng.random() * (rate_hi - rate_lo)
        loss = supply * rate
        sales = supply - loss
        out.append((d, round(supply, 1), round(sales, 1), round(loss, 1), household))
    return out


async def ensure_line_loss_tables() -> None:
    """台区档案 + 日电量两表幂等创建；表空时播种种子数据。"""
    conn = await connect()
    try:
        for ddl in _DDL:
            try:
                await conn.execute(ddl)
            except asyncpg.UniqueViolationError:
                pass  # 并发下另一个请求已抢先建表
        await _seed_if_empty(conn)
    finally:
        await conn.close()


async def _seed_if_empty(conn: asyncpg.Connection) -> None:
    """仅在表空时插入种子台区与近 30 天读数（并发/重启安全）。"""
    if await conn.fetchval("SELECT count(*) FROM transformer_areas") == 0:
        for area_no, name, substation, cap, base, lo, hi, hh in _AREAS:
            await conn.execute(
                """INSERT INTO transformer_areas
                   (area_no, area_name, substation, capacity_kva)
                   VALUES ($1,$2,$3,$4)
                   ON CONFLICT (area_no) DO NOTHING""",
                area_no, name, substation, cap,
            )
    if await conn.fetchval("SELECT count(*) FROM area_meter_reading") == 0:
        for area_no, name, substation, cap, base, lo, hi, hh in _AREAS:
            rng = Random(area_no)  # 确定性：同一 area_no 种子一致
            for d, supply, sales, loss, h in _gen_readings(area_no, base, lo, hi, hh, rng):
                await conn.execute(
                    """INSERT INTO area_meter_reading
                       (area_no, stat_date, supply_kwh, sales_kwh, loss_kwh, household_cnt)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (area_no, stat_date) DO NOTHING""",
                    area_no, d, supply, sales, loss, h,
                )
