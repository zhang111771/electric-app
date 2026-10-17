"""调度事故研判数据库层：故障事件档案 + 保护动作明细两张表。

业务闭环：故障事件（待研判 → 已研判 → 已处置），保护动作明细是自动研判的输入。
表结构幂等创建（CREATE TABLE IF NOT EXISTS），种子数据仅在表空时播种，
绝不放入 data/generate_data.py（那里是 DROP+CREATE 全量重建，会清空真实数据）。
"""
from datetime import datetime

import asyncpg

from .db import connect

# 事件状态机：key=当前状态，value=允许流转到的状态列表
JUDGE_FLOW = {
    "待研判": ["已研判"],
    "已研判": ["已处置"],
    "已处置": [],
}

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS fault_events (
        id SERIAL PRIMARY KEY,
        event_no TEXT NOT NULL UNIQUE,
        ts TIMESTAMPTZ NOT NULL,
        substation TEXT NOT NULL,
        line_name TEXT NOT NULL,
        voltage_level TEXT NOT NULL DEFAULT '',
        tripped_breaker TEXT NOT NULL DEFAULT '',
        loss_mw REAL NOT NULL DEFAULT 0,
        affected_area TEXT NOT NULL DEFAULT '',
        judge_status TEXT NOT NULL DEFAULT '待研判'
            CHECK (judge_status IN ('待研判', '已研判', '已处置')),
        fault_type TEXT NOT NULL DEFAULT '',
        handling_note TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE IF NOT EXISTS protection_actions (
        id SERIAL PRIMARY KEY,
        event_no TEXT NOT NULL,
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        substation TEXT NOT NULL DEFAULT '',
        device TEXT NOT NULL DEFAULT '',
        prot_name TEXT NOT NULL,
        act TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pa_event ON protection_actions (event_no)",
]

# 种子事件（开发演示用，表空时播种）：4 起不同性质的跳闸事件，保护动作明细各异
_EVENTS = [
    {
        "event_no": "A-20260909-001",
        "ts": "2026-09-09 09:12:00",
        "substation": "城东变电站",
        "line_name": "城上线",
        "voltage_level": "110kV",
        "tripped_breaker": "城上断路器",
        "loss_mw": 28.5,
        "affected_area": "城东工业园区",
        "actions": [
            ("城上线", "线路纵联差动保护", "动作", "两侧纵联差动保护动作，判定为线路区内故障"),
        ],
    },
    {
        "event_no": "A-20260909-002",
        "ts": "2026-09-09 10:05:00",
        "substation": "城东变电站",
        "line_name": "1号主变",
        "voltage_level": "110kV",
        "tripped_breaker": "1号主变高压侧断路器",
        "loss_mw": 12.0,
        "affected_area": "城东变电站 10kV 侧部分负荷",
        "actions": [
            ("1号主变", "变压器差动保护", "动作", "主变差动保护动作，判别为变压器区内故障"),
            ("1号主变高压侧断路器", "过流保护", "动作", "高压侧后备过流保护动作"),
        ],
    },
    {
        "event_no": "A-20260909-003",
        "ts": "2026-09-09 14:30:00",
        "substation": "城西变电站",
        "line_name": "城西线",
        "voltage_level": "35kV",
        "tripped_breaker": "城西断路器",
        "loss_mw": 8.2,
        "affected_area": "城西镇南部用户",
        "actions": [
            ("城西线", "距离保护Ⅰ段", "动作", "距离保护Ⅰ段动作，故障点位于本线路近区"),
            ("城西线", "零序保护", "动作", "零序保护动作，判断为接地故障"),
            ("城西线断路器", "重合闸", "重合失败", "一次自动重合闸失败，判为永久性故障"),
        ],
    },
    {
        "event_no": "A-20260909-004",
        "ts": "2026-09-09 16:48:00",
        "substation": "城南变电站",
        "line_name": "城南线",
        "voltage_level": "10kV",
        "tripped_breaker": "城南出线断路器",
        "loss_mw": 3.6,
        "affected_area": "城南片区临时停电",
        "actions": [
            ("城南线", "过流保护", "动作", "过流保护动作，跳闸"),
            ("城南线断路器", "重合闸", "重合成功", "一次自动重合闸成功，线路恢复供电"),
        ],
    },
]


async def ensure_dispatch_tables() -> None:
    """故障事件 + 保护动作明细两张表幂等创建；表空时播种种子数据。"""
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
    """仅在表空时插入种子事件与保护动作（并发/重启安全）。"""
    if await conn.fetchval("SELECT count(*) FROM fault_events") == 0:
        for ev in _EVENTS:
            ts = datetime.strptime(ev["ts"], "%Y-%m-%d %H:%M:%S")
            await conn.execute(
                """INSERT INTO fault_events
                   (event_no, ts, substation, line_name, voltage_level,
                    tripped_breaker, loss_mw, affected_area)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (event_no) DO NOTHING""",
                ev["event_no"], ts, ev["substation"], ev["line_name"],
                ev["voltage_level"], ev["tripped_breaker"], ev["loss_mw"],
                ev["affected_area"],
            )
    if await conn.fetchval("SELECT count(*) FROM protection_actions") == 0:
        for ev in _EVENTS:
            ts = datetime.strptime(ev["ts"], "%Y-%m-%d %H:%M:%S")
            for device, prot, act, desc in ev["actions"]:
                await conn.execute(
                    """INSERT INTO protection_actions
                       (event_no, ts, substation, device, prot_name, act, description)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    ev["event_no"], ts, ev["substation"], device, prot, act, desc,
                )
