"""缺陷闭环管理数据库层：缺陷台账 + 缺陷工单两张表。

业务闭环：登记 → 定级 → 派单 → 处理 → 消缺 → 验收 → 关闭（可中途关闭作废）。
表结构幂等创建（CREATE TABLE IF NOT EXISTS），由工具/端点惰性触发，
绝不放入 data/generate_data.py（那里是 DROP+CREATE 全量重建，会清空真实数据）。
"""
import asyncpg

from .db import connect

# 状态机：key=当前状态，value=允许流转到的状态列表
DEFECT_FLOW = {
    "已登记": ["已定级", "已关闭"],
    "已定级": ["已派单", "已关闭"],
    "已派单": ["处理中", "已关闭"],
    "处理中": ["已消缺", "已关闭"],
    "已消缺": ["已验收", "已关闭"],
    "已验收": ["已关闭"],
    "已关闭": [],
}

# 动作 → 目标状态（用于 update_defect_status 的 action 参数）
DEFECT_ACTIONS = {
    "定级": "已定级",
    "派单": "已派单",
    "开始处理": "处理中",
    "消缺": "已消缺",
    "验收": "已验收",
    "关闭": "已关闭",
}

SEVERITIES = ("一般", "严重", "危急")

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS defect_records (
        id SERIAL PRIMARY KEY,
        defect_no TEXT NOT NULL UNIQUE,
        substation TEXT NOT NULL,
        device TEXT NOT NULL,
        voltage_level TEXT NOT NULL DEFAULT '',
        defect_type TEXT NOT NULL,
        description TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '巡检',
        severity TEXT NOT NULL DEFAULT '一般'
            CHECK (severity IN ('一般', '严重', '危急')),
        status TEXT NOT NULL DEFAULT '已登记'
            CHECK (status IN ('已登记', '已定级', '已派单', '处理中', '已消缺', '已验收', '已关闭')),
        discoverer TEXT NOT NULL DEFAULT '',
        assignee TEXT NOT NULL DEFAULT '',
        result_note TEXT NOT NULL DEFAULT '',
        plan_time TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE IF NOT EXISTS defect_work_orders (
        id SERIAL PRIMARY KEY,
        work_order_no TEXT NOT NULL UNIQUE,
        defect_id INTEGER NOT NULL REFERENCES defect_records(id) ON DELETE CASCADE,
        work_type TEXT NOT NULL DEFAULT '',
        assignee TEXT NOT NULL DEFAULT '',
        plan_time TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT '待执行'
            CHECK (status IN ('待执行', '执行中', '已完成', '已验收')),
        result_note TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
]


async def ensure_defect_tables() -> None:
    """缺陷两张表幂等创建；并发首建时忽略 UniqueViolationError。"""
    conn = await connect()
    try:
        for ddl in _DDL:
            try:
                await conn.execute(ddl)
            except asyncpg.UniqueViolationError:
                pass  # 并发下另一个请求已抢先建表
    finally:
        await conn.close()


async def next_defect_no(conn: asyncpg.Connection) -> str:
    """生成缺陷编号 D-YYYYMMDD-三位序号（当日计数 +1）。"""
    today = "D-" + __import__("datetime").date.today().strftime("%Y%m%d")
    n = await conn.fetchval(
        "SELECT count(*) FROM defect_records WHERE defect_no LIKE $1 || '%'", today
    )
    return f"{today}-{int(n or 0) + 1:03d}"


async def next_work_order_no(conn: asyncpg.Connection) -> str:
    """生成工单编号 WO-YYYYMMDD-三位序号。"""
    today = "WO-" + __import__("datetime").date.today().strftime("%Y%m%d")
    n = await conn.fetchval(
        "SELECT count(*) FROM defect_work_orders WHERE work_order_no LIKE $1 || '%'", today
    )
    return f"{today}-{int(n or 0) + 1:03d}"
