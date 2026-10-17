"""配网 FLISR 数据库层：馈线档案 + 馈线段两表。

业务闭环：馈线故障自动定位→隔离→恢复(FLISR)，馈线段记录拓扑边界开关，
转供依赖 tie_breaker/tie_to_feeder 与各段负荷做容量校验。
表结构幂等创建（CREATE TABLE IF NOT EXISTS），种子数据仅在表空时播种，
绝不放入 data/generate_data.py（那里是 DROP+CREATE 全量重建，会清空真实数据）。
"""
import asyncpg

from .db import connect

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS feeders (
        id SERIAL PRIMARY KEY,
        feeder_id TEXT NOT NULL UNIQUE,
        feeder_name TEXT NOT NULL,
        substation TEXT NOT NULL,
        voltage_level TEXT NOT NULL DEFAULT '10kV',
        source_breaker TEXT NOT NULL DEFAULT '',
        tie_breaker TEXT NOT NULL DEFAULT '',
        tie_to_feeder TEXT NOT NULL DEFAULT '',
        rated_capacity_kva REAL NOT NULL DEFAULT 0,
        load_limit_kw REAL NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE IF NOT EXISTS feeder_sections (
        id SERIAL PRIMARY KEY,
        feeder_id TEXT NOT NULL,
        seq INT NOT NULL,
        section_no TEXT NOT NULL,
        section_name TEXT NOT NULL DEFAULT '',
        load_kw REAL NOT NULL DEFAULT 0,
        up_switch TEXT NOT NULL DEFAULT '',
        down_switch TEXT NOT NULL DEFAULT '',
        is_lateral BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (feeder_id, section_no)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_fsec ON feeder_sections (feeder_id, seq)",
]

# 种子馈线（开发演示用，表空时播种）：3 条 10kV 馈线，L1/L2 通过联络开关互供，L3 独立无联络
# 每段含 seq 顺序、负荷、上游开关(up_switch)与下游开关(down_switch)，末段 down 多为联络开关
_FEEDERS = [
    {
        "feeder_id": "L1",
        "feeder_name": "城东Ⅰ线",
        "substation": "城东变电站",
        "voltage_level": "10kV",
        "source_breaker": "城东Ⅰ线出线断路器",
        "tie_breaker": "L1-L2联络开关",
        "tie_to_feeder": "L2",
        "rated_capacity_kva": 8000,
        "load_limit_kw": 5200,
        "sections": [
            ("L1-S1", "主干一段", 900, "城东Ⅰ线出线断路器", "L1-分段开关1", False),
            ("L1-S2", "主干二段", 1100, "L1-分段开关1", "L1-分段开关2", False),
            ("L1-S3", "主干三段", 800, "L1-分段开关2", "L1-L2联络开关", False),
        ],
    },
    {
        "feeder_id": "L2",
        "feeder_name": "城东Ⅱ线",
        "substation": "城东变电站",
        "voltage_level": "10kV",
        "source_breaker": "城东Ⅱ线出线断路器",
        "tie_breaker": "L1-L2联络开关",
        "tie_to_feeder": "L1",
        "rated_capacity_kva": 6300,
        "load_limit_kw": 4000,
        "sections": [
            ("L2-S1", "主干一段", 700, "城东Ⅱ线出线断路器", "L2-分段开关1", False),
            ("L2-S2", "主干二段", 1300, "L2-分段开关1", "L2-分段开关2", False),
            ("L2-S3", "主干三段", 600, "L2-分段开关2", "L1-L2联络开关", False),
        ],
    },
    {
        "feeder_id": "L3",
        "feeder_name": "城西线",
        "substation": "城西变电站",
        "voltage_level": "10kV",
        "source_breaker": "城西出线断路器",
        "tie_breaker": "",
        "tie_to_feeder": "",
        "rated_capacity_kva": 4000,
        "load_limit_kw": 3000,
        "sections": [
            ("L3-S1", "主干一段", 500, "城西出线断路器", "L3-分段开关1", False),
            ("L3-S2", "主干二段", 900, "L3-分段开关1", "L3-末端开关", False),
        ],
    },
]


async def ensure_flisr_tables() -> None:
    """馈线 + 馈线段两表幂等创建；表空时播种种子数据。"""
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
    """仅在表空时插入种子馈线与其馈线段（并发/重启安全）。"""
    if await conn.fetchval("SELECT count(*) FROM feeders") == 0:
        for f in _FEEDERS:
            await conn.execute(
                """INSERT INTO feeders
                   (feeder_id, feeder_name, substation, voltage_level, source_breaker,
                    tie_breaker, tie_to_feeder, rated_capacity_kva, load_limit_kw)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (feeder_id) DO NOTHING""",
                f["feeder_id"], f["feeder_name"], f["substation"], f["voltage_level"],
                f["source_breaker"], f["tie_breaker"], f["tie_to_feeder"],
                f["rated_capacity_kva"], f["load_limit_kw"],
            )
            for seq, (sec, name, load, up, down, lat) in enumerate(f["sections"], start=1):
                await conn.execute(
                    """INSERT INTO feeder_sections
                       (feeder_id, seq, section_no, section_name, load_kw,
                        up_switch, down_switch, is_lateral)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                       ON CONFLICT (feeder_id, section_no) DO NOTHING""",
                    f["feeder_id"], seq, sec, name, load, up, down, lat,
                )
