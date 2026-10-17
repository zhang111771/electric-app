"""缺陷闭环管理工具：登记 / 查询 / 状态流转 / 详情。

状态机见 defect_db.DEFECT_FLOW，非法流转一律返回 error 而不是静默跳过。
所有工具幂等建表后直接操作 PostgreSQL（asyncpg 原生异步，无需 to_thread）。
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from .db import connect
from .defect_db import (
    DEFECT_ACTIONS, DEFECT_FLOW, ensure_defect_tables, next_defect_no,
    next_work_order_no,
)

# 一个缺陷单的子集：记录主键 id、no、详情供工具返回
_DEFECT_COLS = (
    "id, defect_no, substation, device, voltage_level, defect_type, description, "
    "source, severity, status, discoverer, assignee, result_note, "
    "to_char(plan_time AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS plan_time, "
    "to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS created_at"
)


def _parse_plan_time(plan_time) -> datetime | None:
    """把 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' 文本转成 datetime 对象（asyncpg 需要），
    非法/空返回 None。"""
    if not plan_time:
        return None
    s = str(plan_time).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@tool
async def register_defect(
    substation: str,
    device: str,
    defect_type: str,
    description: str,
    voltage_level: str = "",
    severity: str = "一般",
    source: str = "巡检",
    discoverer: str = "",
    plan_time: str = "",
) -> dict:
    """登记一条设备缺陷并进入闭环管理。

    创建缺陷记录，状态置为『已登记』，生成缺陷编号 D-YYYYMMDD-序号。

    Args:
        substation: 变电站名称，如 城东变电站
        device: 具体设备，如 1号主变 / 220kV 城东线隔离开关
        defect_type: 缺陷类别，如 渗油 / 发热 / 绝缘子破损
        description: 缺陷现象描述
        voltage_level: 电压等级，如 110kV / 220kV
        severity: 定级 一般/严重/危急，默认 一般
        source: 来源，默认 巡检
        discoverer: 发现人
        plan_time: 计划消缺时间（可选，'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'）

    Returns:
        dict: defect_no, severity, status, substation, device
    """
    if severity not in ("一般", "严重", "危急"):
        return {"error": f"严重度必须为 一般/严重/危急，收到: {severity}"}
    if not substation or not device or not defect_type or not description:
        return {"error": "变电站/设备/类别/描述为必填项"}
    await ensure_defect_tables()
    conn = await connect()
    try:
        no = await next_defect_no(conn)
        await conn.execute(
            """INSERT INTO defect_records
               (defect_no, substation, device, voltage_level, defect_type, description,
                source, severity, status, discoverer, plan_time)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'已登记',$9,$10)""",
            no, substation, device, voltage_level, defect_type, description,
            source, severity, discoverer, _parse_plan_time(plan_time),
        )
        return {
            "defect_no": no,
            "substation": substation,
            "device": device,
            "defect_type": defect_type,
            "severity": severity,
            "status": "已登记",
            "message": f"缺陷 {no} 登记成功，已进入闭环管理。",
        }
    finally:
        await conn.close()


@tool
async def query_defects(
    status: str = "",
    severity: str = "",
    substation: str = "",
    keyword: str = "",
    limit: int = 20,
) -> dict:
    """按条件查询缺陷台账（只读）。

    多条件为 AND 关系；keyword 模糊匹配设备/类别/描述。

    Args:
        status: 状态筛选（已登记/已定级/已派单/处理中/已消缺/已验收/已关闭），空=全部
        severity: 严重度筛选（一般/严重/危急），空=全部
        substation: 变电站名模糊匹配
        keyword: 设备/类别/描述关键词
        limit: 最多返回条数，默认 20

    Returns:
        dict: total, items[{defect_no, substation, device, severity, status, ...}]
    """
    await ensure_defect_tables()
    where, params = [], []
    if status:
        if status not in DEFECT_FLOW:
            return {"error": f"非法状态 {status}，可选: {list(DEFECT_FLOW)}"}
        where.append(f"status = ${len(params) + 1}")
        params.append(status)
    if severity:
        if severity not in ("一般", "严重", "危急"):
            return {"error": "severity 必须为 一般/严重/危急"}
        where.append(f"severity = ${len(params) + 1}")
        params.append(severity)
    if substation:
        where.append(f"substation ILIKE ${len(params) + 1}")
        params.append(f"%{substation}%")
    if keyword:
        where.append(
            f"(device ILIKE ${len(params) + 1} OR defect_type ILIKE ${len(params) + 1} "
            f"OR description ILIKE ${len(params) + 1})"
        )
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
    sql = f"SELECT {_DEFECT_COLS} FROM defect_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
    params.append(limit)
    conn = await connect()
    try:
        rows = await conn.fetch(sql, *params)
        return {"total": len(rows), "items": [dict(r) for r in rows]}
    finally:
        await conn.close()


@tool
async def update_defect_status(
    defect_no: str,
    action: str,
    severity: str = "",
    assignee: str = "",
    result_note: str = "",
    plan_time: str = "",
) -> dict:
    """推进缺陷状态（状态机校验）。

    动作→目标状态：定级→已定级，派单→已派单（并创建缺陷工单），
    开始处理→处理中，消缺→已消缺，验收→已验收，关闭→已关闭。
    非法流转（如未定级直接派单）返回 error。

    Args:
        defect_no: 缺陷编号 D-YYYYMMDD-序号
        action: 定级/派单/开始处理/消缺/验收/关闭
        severity: 定级动作时的严重度（一般/严重/危急）
        assignee: 派单/开始处理时的责任人
        result_note: 消缺/验收时的处理结果备注
        plan_time: 计划消缺时间（派单时可填）

    Returns:
        dict: defect_no, old_status, new_status, message
    """
    if action not in DEFECT_ACTIONS:
        return {"error": f"非法动作 {action}，可选: {list(DEFECT_ACTIONS)}"}
    await ensure_defect_tables()
    conn = await connect()
    try:
        row = await conn.fetchrow(
            "SELECT id, status, severity FROM defect_records WHERE defect_no = $1",
            defect_no,
        )
        if not row:
            return {"error": f"缺陷 {defect_no} 不存在"}
        old = row["status"]
        target = DEFECT_ACTIONS[action]
        if target not in DEFECT_FLOW.get(old, []):
            return {"error": f"状态流转非法: {old} → {target}（当前 {old} 只能转 {DEFECT_FLOW.get(old)}）"}

        # 定级：写入 severity
        if action == "定级":
            if severity not in ("一般", "严重", "危急"):
                return {"error": "定级动作必须提供 severity（一般/严重/危急）"}
            await conn.execute(
                "UPDATE defect_records SET severity=$2, status=$3, updated_at=now() "
                "WHERE defect_no=$1",
                defect_no, severity, target,
            )
        # 派单：写入 assignee + 创建工单
        elif action == "派单":
            if not assignee:
                return {"error": "派单动作必须提供 assignee（责任人）"}
            wo = await next_work_order_no(conn)
            await conn.execute(
                "UPDATE defect_records SET assignee=$2, status=$3, "
                "plan_time=COALESCE($4, plan_time), updated_at=now() "
                "WHERE defect_no=$1",
                defect_no, assignee, target, _parse_plan_time(plan_time),
            )
            await conn.execute(
                """INSERT INTO defect_work_orders
                   (work_order_no, defect_id, work_type, assignee, plan_time, status)
                   VALUES ($1, $2, '消缺', $3, $4, '待执行')""",
                wo, row["id"], assignee, _parse_plan_time(plan_time),
            )
            return {
                "defect_no": defect_no, "old_status": old, "new_status": target,
                "work_order_no": wo,
                "message": f"缺陷 {defect_no} 已派单（{wo}），责任人 {assignee}。",
            }
        # 开始处理 / 消缺 / 验收 / 关闭
        else:
            sets = ["status=$2"]
            params = [defect_no, target]
            if action == "开始处理" and assignee:
                sets.append("assignee=$3")
                params.append(assignee)
            if action in ("消缺", "验收") and result_note:
                sets.append("result_note=$3")
                params.append(result_note)
            sets.append("updated_at=now()")
            await conn.execute(
                f"UPDATE defect_records SET {', '.join(sets)} WHERE defect_no=$1",
                *params,
            )
        return {
            "defect_no": defect_no, "old_status": old, "new_status": target,
            "message": f"缺陷 {defect_no} 状态已更新: {old} → {target}。",
        }
    finally:
        await conn.close()


@tool
async def get_defect(defect_no: str) -> dict:
    """查询单条缺陷详情及其关联工单（只读）。"""
    await ensure_defect_tables()
    conn = await connect()
    try:
        row = await conn.fetchrow(
            f"SELECT {_DEFECT_COLS} FROM defect_records WHERE defect_no = $1", defect_no
        )
        if not row:
            return {"error": f"缺陷 {defect_no} 不存在"}
        orders = await conn.fetch(
            """SELECT work_order_no, work_type, assignee, status, result_note,
                      to_char(plan_time AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS plan_time,
                      to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS created_at
               FROM defect_work_orders WHERE defect_id = $1 ORDER BY id""",
            row["id"],
        )
        return {**dict(row), "work_orders": [dict(o) for o in orders]}
    finally:
        await conn.close()


ALL_DEFECT_TOOLS = [
    register_defect, query_defects, update_defect_status, get_defect,
]
