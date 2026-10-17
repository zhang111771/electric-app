# -*- coding: utf-8 -*-
"""生成模拟电网数据（设备台账 / 负荷曲线 / 告警事件），写入 PostgreSQL。

运行：python data/generate_data.py
依赖本地 PostgreSQL（连接串见 config.POSTGRES_URI），首次运行自动建库。
"""
import asyncio
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 保证能 import graph.*

from graph.db import connect

random.seed(20260813)  # 固定种子，保证可复现

SUBSTATIONS = [
    ("城东变电站", "东城区", "110kV"),
    ("城西变电站", "西城区", "110kV"),
    ("城南变电站", "南城区", "110kV"),
    ("城北变电站", "北城区", "110kV"),
    ("工业园变电站", "工业园区", "110kV"),
    ("高新区变电站", "高新区", "110kV"),
    ("大学城变电站", "大学城", "110kV"),
    ("机场变电站", "机场片区", "110kV"),
]

MANUFACTURERS = ["特变电工", "山东泰开", "西电集团", "正泰电气", "施耐德电气", "ABB", "许继电气", "南瑞集团"]

# 每小时负荷倍率（双峰曲线：上午 10-12 点、晚间 19-22 点两个高峰）
HOUR_FACTOR = [0.55, 0.50, 0.48, 0.45, 0.46, 0.52, 0.65, 0.78, 0.88, 0.92, 0.95,
               0.90, 0.82, 0.78, 0.75, 0.78, 0.85, 0.95, 1.00, 0.98, 0.90, 0.80,
               0.70, 0.62]

# 告警模板：(类型, 级别, 生成消息的函数)
ALARM_TEMPLATES = [
    ("过负荷", "严重", lambda s, d: f"{s}{d}负荷达额定容量的 105%，过负荷告警，请及时转移负荷"),
    ("过流保护动作", "紧急", lambda s, d: f"{s}{d}过流I段保护动作，开关跳闸，故障电流大"),
    ("母线电压越限", "严重", lambda s, d: f"{s}{d}母线电压越上限，电压偏高需调整无功"),
    ("主变油温越限", "严重", lambda s, d: f"{s}{d}顶层油温达 92℃，越限告警，请检查冷却装置"),
    ("单相接地", "紧急", lambda s, d: f"{s}{d}10kV母线发生单相接地告警，请立即排查接地支路"),
    ("SF6气压低", "严重", lambda s, d: f"{s}{d}SF6气体压力低告警，请补气并检查泄漏"),
    ("通讯中断", "一般", lambda s, d: f"{s}{d}与调度主站通讯中断，请检查通道"),
    ("线路跳闸", "紧急", lambda s, d: f"{s}{d}线路跳闸，重合闸未成功，请巡线查找故障点"),
    ("保护装置告警", "一般", lambda s, d: f"{s}{d}保护装置异常告警，请检查装置定值和通讯"),
    ("油位低", "严重", lambda s, d: f"{s}{d}主变油位低告警，请及时补油"),
]

SCHEMA_SQL = """
DROP TABLE IF EXISTS substations;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS load_data;
DROP TABLE IF EXISTS alarms;

CREATE TABLE substations (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    voltage_level TEXT
);
CREATE TABLE assets (
    id SERIAL PRIMARY KEY,
    asset_name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    substation TEXT,
    voltage_level TEXT,
    status TEXT,
    rated_capacity_mva REAL,
    rated_current_a REAL,
    manufacture TEXT,
    install_date TEXT,
    note TEXT
);
CREATE TABLE load_data (
    id SERIAL PRIMARY KEY,
    bus_name TEXT NOT NULL,
    substation TEXT,
    voltage_level TEXT,
    ts TEXT NOT NULL,
    load_mw REAL,
    load_mvar REAL
);
CREATE TABLE alarms (
    id SERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    substation TEXT,
    device TEXT,
    alarm_type TEXT,
    level TEXT,
    message TEXT,
    handled INTEGER
);
CREATE INDEX idx_load_bus ON load_data(bus_name, ts);
CREATE INDEX idx_assets_type ON assets(asset_type, voltage_level);
"""


def now_anchor():
    return datetime(2026, 8, 13, 8, 0, 0)  # 固定“当前时间”，保证可复现


async def gen_substations(conn):
    await conn.executemany(
        "INSERT INTO substations (name, city, voltage_level) VALUES ($1,$2,$3)", SUBSTATIONS)


async def gen_assets(conn):
    """约 90 台设备：主变 / 母线 / 断路器 / 隔离开关 / 线路。"""
    rows = []
    capacities = [31.5, 40, 50, 63]
    for s_name, _, _ in SUBSTATIONS:
        # 每站 2 台主变（其中 1 台备用，制造一点“检修/备用”状态）
        for k in (1, 2):
            cap = random.choice(capacities)
            status = "运行" if k == 1 else random.choice(["运行", "备用", "检修"])
            rated_i = round(cap * 1000 / (1.732 * 110) * 1000, 1) / 1000  # ≈ A，110kV 侧额定电流
            rows.append((f"{s_name}{k}号主变", "主变压器", s_name, "110/35/10kV",
                         status, cap, round(rated_i, 1), random.choice(MANUFACTURERS),
                         f"{random.randint(2006, 2022)}-{random.randint(1,12):02d}",
                         "油浸式有载调压变压器"))
        # 母线
        rows.append((f"{s_name}110kV母线", "母线", s_name, "110kV", "运行", None, None,
                     "西电集团", f"{random.randint(2006, 2020)}-{random.randint(1,12):02d}", "双母线接线"))
        rows.append((f"{s_name}10kV母线I段", "母线", s_name, "10kV", "运行", None, None,
                     "西电集团", f"{random.randint(2006, 2020)}-{random.randint(1,12):02d}", "单母线分段"))
        # 断路器
        for k in (1, 2, 3):
            rows.append((f"{s_name}{k}号断路器", "断路器", s_name,
                         "110kV" if k <= 2 else "10kV", "运行", None,
                         round(random.uniform(630, 2000), 0), random.choice(MANUFACTURERS),
                         f"{random.randint(2010, 2023)}-{random.randint(1,12):02d}",
                         "SF6 断路器" if k <= 2 else "真空断路器"))
        # 隔离开关
        for k in (1, 2):
            rows.append((f"{s_name}{k}号隔离开关", "隔离开关", s_name, "110kV", "运行", None, None,
                         "山东泰开", f"{random.randint(2010, 2023)}-{random.randint(1,12):02d}", "GW4 型户外隔离开关"))
        # 10kV 配电线路
        rows.append((f"{s_name}10kV出线{k}回", "配电线路", s_name, "10kV", "运行", None, None,
                     "正泰电气", f"{random.randint(2012, 2023)}-{random.randint(1,12):02d}", "电缆线路"))
    # 110kV 输电线路（连接各站，名称“城X线”）
    pairs = [("城东变电站", "城西变电站"), ("城南变电站", "城北变电站"), ("工业园变电站", "高新区变电站"),
             ("大学城变电站", "机场变电站"), ("城东变电站", "工业园变电站"), ("城西变电站", "机场变电站"),
             ("城南变电站", "大学城变电站"), ("城北变电站", "高新区变电站"), ("城东变电站", "高新区变电站"),
             ("城西变电站", "大学城变电站")]
    for a, b in pairs:
        name = f"{a[:2]}—{b[:2]}线路"
        rows.append((name, "输电线路", f"{a}/{b}", "110kV", "运行", None, None,
                     "西电集团", f"{random.randint(2008, 2021)}-{random.randint(1,12):02d}", "架空线路"))
    await conn.executemany("""INSERT INTO assets (asset_name, asset_type, substation, voltage_level,
                      status, rated_capacity_mva, rated_current_a, manufacture, install_date, note)
                      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""", rows)


async def gen_load(conn):
    """每个主变生成最近 30 天逐小时负荷曲线（双峰 + 工作日/周末 + 随机噪声 + 缓慢增长）。"""
    anchor = now_anchor()
    cursor = await conn.fetch(
        "SELECT asset_name, substation, rated_capacity_mva FROM assets WHERE asset_type='主变压器'")
    rows = []
    for name, station, cap in cursor:
        cap = cap or 50.0
        base = cap * random.uniform(0.35, 0.55)  # 平均负荷率约 35%~55%
        for day in range(30, 0, -1):
            day0 = anchor - timedelta(days=day)
            is_weekend = day0.weekday() >= 5
            growth = (30 - day) * 0.001  # 全月缓慢爬升
            for h in range(24):
                ts = day0 + timedelta(hours=h)
                hour_f = HOUR_FACTOR[h]
                weekend_f = 0.85 if is_weekend else 1.0
                noise = random.uniform(0.93, 1.07)
                load = base * hour_f * weekend_f * (1 + growth) * noise
                rows.append((name, station, "110kV", ts.strftime("%Y-%m-%d %H:%M:%S"),
                             round(load, 2), round(load * 0.30, 2)))
    await conn.executemany("""INSERT INTO load_data (bus_name, substation, voltage_level, ts, load_mw, load_mvar)
                        VALUES ($1,$2,$3,$4,$5,$6)""", rows)


async def gen_alarms(conn):
    anchor = now_anchor()
    rows = []
    stations = [s[0] for s in SUBSTATIONS]
    devices = ["1号主变", "2号主变", "110kV母线", "1号断路器", "3号断路器", "10kV母线I段", "1号出线"]
    for _ in range(45):
        d = anchor - timedelta(hours=random.randint(1, 30 * 24))
        station = random.choice(stations)
        device = random.choice(devices)
        tpl = random.choice(ALARM_TEMPLATES)
        alarm_type, level, msg_fn = tpl
        msg = msg_fn(station, device)
        handled = 1 if level in ("一般",) else random.choice([0, 0, 1])
        rows.append((d.strftime("%Y-%m-%d %H:%M:%S"), station, device, alarm_type, level, msg, handled))
    rows.sort(key=lambda r: r[0])
    await conn.executemany("""INSERT INTO alarms (ts, substation, device, alarm_type, level, message, handled)
                        VALUES ($1,$2,$3,$4,$5,$6,$7)""", rows)


async def main():
    conn = await connect()
    try:
        await conn.execute(SCHEMA_SQL)
        await gen_substations(conn)
        await gen_assets(conn)
        await gen_load(conn)
        await gen_alarms(conn)
        counts = dict(zip(
            ("substations", "assets", "load_data", "alarms"),
            [await conn.fetchval(f"SELECT COUNT(*) FROM {t}") for t in
             ("substations", "assets", "load_data", "alarms")]))
    finally:
        await conn.close()
    print(f"[OK] 模拟数据已写入 PostgreSQL：{counts}")


if __name__ == "__main__":
    asyncio.run(main())
