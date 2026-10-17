"""变电设备诊断工具：油色谱三比值法 / SF6 压力微水 / 红外测温相对温差法。

纯规则实现（无模型、无重推理），判据硬编码，依据行业标准：
- 油色谱三比值法：DL/T 722-2014《变压器油中溶解气体分析和判断导则》表1（注意值）、表6（编码规则）、表7（故障类型判断）
- SF6 气体：DL/T 596《电力设备预防性试验规程》运行中微水注意值（灭弧室气室 150/250 μL/L，其他气室 250/500 μL/L）
- 红外测温：DL/T 664-2016《带电设备红外诊断应用规范》相对温差法与表 4 电流致热型设备缺陷诊断判据

安全约定：判据表出现未列入编码或数据不足以判断时，只给"疑似/注意/请人工确认"，
绝不输出武断的"正常/合格"结论。
"""
from __future__ import annotations

from langchain_core.tools import tool

# ============================================================
# 油色谱三比值法（DL/T 722-2014）
# ============================================================

# 运行中变压器油中溶解气体注意值（μL/L），按电压等级分档（DL/T 722-2014 表1）
_DGA_ATTENTION_VALUES = {
    "220": {  # 220kV 及以下
        "h2": 150, "ch4": 100, "c2h6": 100, "c2h4": 100,
        "c2h2": 5, "co": 300, "co2": 5000, "total": 150,
    },
    "330": {  # 330kV
        "h2": 150, "ch4": 100, "c2h6": 100, "c2h4": 100,
        "c2h2": 2, "co": 300, "co2": 5000, "total": 150,
    },
    "500": {  # 500kV 及以上
        "h2": 150, "ch4": 100, "c2h6": 100, "c2h4": 100,
        "c2h2": 1, "co": 300, "co2": 5000, "total": 150,
    },
}

# 三比值编码规则（表6：C2H2/C2H4, CH4/H2, C2H4/C2H6）
# 注意 DL/T 722 改良三比值：CH4/H2 区间 0.1~1 记 0、<0.1 记 1、≥1 记 2，与旧版不同。
def _code_ratio(value: float, kind: str) -> int:
    if kind == "c2h2_c2h4":
        # <0.1→0, 0.1~3→1, ≥3→2
        if value < 0.1:
            return 0
        if value < 3:
            return 1
        return 2
    if kind == "ch4_h2":
        # <0.1→1, 0.1~1→0, ≥1→2
        if value < 0.1:
            return 1
        if value < 1:
            return 0
        return 2
    # c2h4_c2h6: <1→0, 1~3→1, ≥3→2
    if value < 1:
        return 0
    if value < 3:
        return 1
    return 2


# 故障类型判断表（表7）。未列入的组合（000/010/011）走"需综合判断"分支。
# 来源：DL/T 722-2014 表7 三比值法故障类型判断。
_TABLE7 = {
    "001": "低温过热（低于150℃）",
    "020": "低温过热（150～300℃）",
    "021": "中温过热（300～700℃）",
    "002": "高温过热（高于700℃）",
    "012": "高温过热（高于700℃）",
    "022": "高温过热（高于700℃）",
    "100": "局部放电",
    "101": "电弧放电", "102": "电弧放电",
    "110": "电弧放电", "111": "电弧放电", "112": "电弧放电",
    "120": "电弧放电兼过热", "121": "电弧放电兼过热", "122": "电弧放电兼过热",
    "200": "低能放电", "201": "低能放电", "202": "低能放电",
    "210": "低能放电", "211": "低能放电", "212": "低能放电",
    "220": "低能放电兼过热", "221": "低能放电兼过热", "222": "低能放电兼过热",
}

_SEVERITY_OF_FAULT = {
    "低温过热（低于150℃）": "注意",
    "低温过热（150～300℃）": "注意",
    "中温过热（300～700℃）": "严重",
    "高温过热（高于700℃）": "严重",
    "局部放电": "注意",
    "电弧放电": "危急",
    "电弧放电兼过热": "危急",
    "低能放电": "严重",
    "低能放电兼过热": "严重",
}

_ADVICE_OF_FAULT = {
    "低温过热（低于150℃）": "低热源持续发热，建议缩短油色谱检测周期，结合产气速率跟踪。",
    "低温过热（150～300℃）": "分接开关接触不良、铁心多点接地等可能，建议结合油温与绕组直阻检查。",
    "中温过热（300～700℃）": "接头/引线过热可能，建议尽快安排停电检查，测量接触电阻。",
    "高温过热（高于700℃）": "严重过热、环流发热可能，应尽快安排停电处理，防止故障扩大。",
    "局部放电": "绝缘内部存在气隙/毛刺等低能量放电，建议加强局放检测与色谱跟踪，关注产气速率。",
    "电弧放电": "存在放电性故障（匝间/层间放电、相间闪络等），应立即停电检查处理。",
    "电弧放电兼过热": "放电伴随过热，属严重缺陷，应立即停电检查处理。",
    "低能放电": "火花放电（不同电位间放电），建议尽快安排检查，防止发展成电弧放电。",
    "低能放电兼过热": "火花放电伴过热，建议尽快停电检查处理。",
}


def _dga_voltage_band(voltage_level: int) -> str:
    if voltage_level >= 500:
        return "500"
    if voltage_level >= 330:
        return "330"
    return "220"


@tool
async def oil_dga_three_ratio(
    h2: float,
    ch4: float,
    c2h6: float,
    c2h4: float,
    c2h2: float,
    co: float = 0,
    co2: float = 0,
    voltage_level: int = 110,
) -> dict:
    """变压器油色谱三比值法诊断（DL/T 722-2014）。

    输入油中溶解气体浓度（单位 μL/L）: h2 氢气 / ch4 甲烷 / c2h6 乙烷 /
    c2h4 乙烯 / c2h2 乙炔 / co 一氧化碳 / co2 二氧化碳（后两者可选，用于固体绝缘辅助判断）。
    输出三位比值编码、故障类型判断、严重度与处置建议。

    仅当任一特征气体浓度超过注意值时才给出故障类型；浓度正常时提示"暂不适用"。
    未列入判断表的编码不武断下结论，提示综合判断并请人工确认。

    Args:
        h2: 氢气浓度 (μL/L)
        ch4: 甲烷浓度 (μL/L)
        c2h6: 乙烷浓度 (μL/L)
        c2h4: 乙烯浓度 (μL/L)
        c2h2: 乙炔浓度 (μL/L)
        co: 一氧化碳浓度 (μL/L)，默认 0
        co2: 二氧化碳浓度 (μL/L)，默认 0
        voltage_level: 设备电压等级 kV，用于选取注意值档（≤220 / 330 / ≥500），默认 110

    Returns:
        dict: mode='dga', ratio_code(3位), fault_type_zh, severity, exceeded_gases,
              attention_values, advice, standard
    """
    band = _dga_voltage_band(voltage_level)
    av = _DGA_ATTENTION_VALUES[band]
    total = ch4 + c2h6 + c2h4 + c2h2

    gases = {"h2": h2, "ch4": ch4, "c2h6": c2h6, "c2h4": c2h4, "c2h2": c2h2}
    exceeded = {k: round(v, 2) for k, v in gases.items() if v > av[k]}
    if total > av["total"]:
        exceeded["total"] = round(total, 2)

    base = {
        "mode": "dga",
        "standard": "DL/T 722-2014",
        "voltage_band": f"{band}kV" + ("及以上" if band == "500" else "及以下" if band == "220" else ""),
        "attention_values": {
            **{k: av[k] for k in ("h2", "ch4", "c2h6", "c2h4", "c2h2")},
            "total": av["total"],
        },
    }

    # 护栏：特征气体均未超注意值 → 不给故障结论
    if not exceeded:
        return {
            **base,
            "fault_type_zh": "浓度正常",
            "severity": "正常",
            "ratio_code": None,
            "exceeded_gases": {},
            "advice": "各特征气体浓度均未超过注意值，三比值法暂不适用，建议按周期继续常规监测。",
            "note": "CO/CO2 用于固体绝缘老化辅助判断，若持续偏高需另行关注。",
        }

    # 计算三位比值编码
    r1 = c2h2 / c2h4 if c2h4 else float("inf")
    r2 = ch4 / h2 if h2 else float("inf")
    r3 = c2h4 / c2h6 if c2h6 else float("inf")
    code = f"{_code_ratio(r1, 'c2h2_c2h4')}{_code_ratio(r2, 'ch4_h2')}{_code_ratio(r3, 'c2h4_c2h6')}"

    ratio_values = {
        "c2h2_c2h4": round(r1, 3) if r1 != float("inf") else None,
        "ch4_h2": round(r2, 3) if r2 != float("inf") else None,
        "c2h4_c2h6": round(r3, 3) if r3 != float("inf") else None,
    }

    fault = _TABLE7.get(code)
    if fault is None:
        # 未列入判断表（如 000/010/011）：按标准要求综合判断，不武断下结论
        return {
            **base,
            "fault_type_zh": "编码未列入判断表",
            "severity": "注意",
            "ratio_code": code,
            "ratio_values": ratio_values,
            "exceeded_gases": exceeded,
            "advice": (
                f"三比值编码 {code} 未列入 DL/T 722-2014 表7，且特征气体已超注意值"
                f"（{', '.join(exceeded)}）。三比值法存在边界敏感与复合故障盲区，"
                "请结合产气速率、产气速率趋势、绝缘试验及局部放电检测综合判断，务必人工确认。"
            ),
            "note": "建议复测确认数据，并核对产气速率（绝对产气速率与相对产气速率）及 CO2/CO 比值。",
        }

    severity = _SEVERITY_OF_FAULT[fault]
    advice = _ADVICE_OF_FAULT[fault]
    notes = []
    if co > av["co"] or co2 > av["co2"]:
        co2co = round(co2 / co, 2) if co else None
        notes.append(
            "CO/CO2 偏高可能涉及固体绝缘（纸/木）过热：CO2/CO < 3 通常提示固体绝缘劣化，"
            f"本次 CO2/CO = {co2co}，请结合产气速率确认。" if co2co is not None else
            "CO/CO2 偏高可能涉及固体绝缘（纸/木）过热，请结合产气速率确认。"
        )
    if h2 > av["h2"] and all(gases[k] <= av[k] for k in ("ch4", "c2h6", "c2h4", "c2h2")):
        notes.append("H2 单高而烃类较低，疑似低能放电或受潮，请结合乙炔与微水含量确认。")

    return {
        **base,
        "fault_type_zh": fault,
        "severity": severity,
        "ratio_code": code,
        "ratio_values": ratio_values,
        "exceeded_gases": exceeded,
        "advice": advice,
        "note": " ".join(notes) if notes else "",
    }


# ============================================================
# SF6 气体状态检查（压力 + 微水）
# ============================================================

# 运行中 SF6 设备微水注意值（μL/L，体积分数），DL/T 596《电力设备预防性试验规程》
_SF6_MOISTURE_LIMITS = {
    "arc": {"notice": 150, "alarm": 250},   # 灭弧室气室（断路器）
    "other": {"notice": 250, "alarm": 500},  # 其他气室（隔离/接地开关、母线等）
}


@tool
async def sf6_gas_check(
    measured_pressure: float,
    moisture_ppmv: float = 0,
    gas_chamber: str = "arc",
    rated_pressure: float = 0.60,
    alarm_pressure: float = 0.55,
    lockout_pressure: float = 0.50,
) -> dict:
    """SF6 气体压力与微水含量状态检查（运行中设备）。

    压力分四档：≥额定值正常 / 低于额定待补气(注意) / 低于报警值(报警) / 低于闭锁值(危急闭锁)。
    微水按气室类型分档：灭弧室气室 >150μL/L 注意、>250μL/L 报警；其他气室 >250 注意、>500 报警。

    Args:
        measured_pressure: 实测压力 (MPa，表压)
        moisture_ppmv: 微水含量 (μL/L / ppmv)，默认 0 表示未测
        gas_chamber: 气室类型 'arc'(灭弧室/断路器) 或 'other'(其他气室)，默认 arc
        rated_pressure: 额定压力 MPa，默认 0.60（可按设备铭牌/密度继电器定值覆盖）
        alarm_pressure: 报警定值 MPa，默认 0.55
        lockout_pressure: 闭锁定值 MPa，默认 0.50

    Returns:
        dict: mode='sf6', pressure_status, moisture_status, overall, advice, standard
    """
    def _press_status(p: float) -> tuple[str, str]:
        if p >= rated_pressure:
            return "正常", "压力正常"
        if p >= alarm_pressure:
            return "注意", "压力低于额定值，建议补气并检查是否有泄漏"
        if p >= lockout_pressure:
            return "报警", "压力已低于报警定值，应立即补气并安排查漏"
        return "危急", "压力已低于闭锁定值，设备可能闭锁操作，应立即处理并安排停电检修"

    press_status, press_advice = _press_status(measured_pressure)

    result: dict = {
        "mode": "sf6",
        "standard": "DL/T 596",
        "measured_pressure_MPa": measured_pressure,
        "rated_pressure_MPa": rated_pressure,
        "alarm_pressure_MPa": alarm_pressure,
        "lockout_pressure_MPa": lockout_pressure,
        "pressure_status": press_status,
        "pressure_advice": press_advice,
        "gas_chamber": "灭弧室气室" if gas_chamber == "arc" else "其他气室",
        "overall": press_status,
        "advice": press_advice,
    }

    if moisture_ppmv > 0:
        lim = _SF6_MOISTURE_LIMITS.get(gas_chamber, _SF6_MOISTURE_LIMITS["other"])
        if moisture_ppmv > lim["alarm"]:
            moisture_status = "报警"
            moisture_advice = f"微水 {moisture_ppmv:.0f}μL/L 超过报警值 {lim['alarm']}μL/L，水分超标将危及绝缘与灭弧性能，应尽快处理"
        elif moisture_ppmv > lim["notice"]:
            moisture_status = "注意"
            moisture_advice = f"微水 {moisture_ppmv:.0f}μL/L 超过注意值 {lim['notice']}μL/L，建议缩短检测周期并跟踪趋势"
        else:
            moisture_status = "正常"
            moisture_advice = f"微水 {moisture_ppmv:.0f}μL/L 在正常范围（≤{lim['notice']}μL/L）"
        result["moisture_ppmv"] = moisture_ppmv
        result["moisture_limits"] = {"notice": lim["notice"], "alarm": lim["alarm"]}
        result["moisture_status"] = moisture_status
        result["moisture_advice"] = moisture_advice
        # 综合：取压力/微水更高一级
        severity_order = {"正常": 0, "注意": 1, "报警": 2, "危急": 3}
        if severity_order[moisture_status] > severity_order[press_status]:
            result["overall"] = moisture_status
            result["advice"] = f"压力：{press_advice}；微水：{moisture_advice}。"
    else:
        result["moisture_status"] = "未测"
        result["moisture_advice"] = "未提供微水数据，建议补测。"

    if result["overall"] == "危急":
        result["advice"] = "危急！" + result["advice"] + " 涉及设备安全，应立即上报并安排停电处理，严禁强行操作。"

    return result


# ============================================================
# 红外测温相对温差法（DL/T 664-2016）
# ============================================================

# 电流致热型设备缺陷诊断判据（DL/T 664-2016 表4）。按设备类别分三档：
#   [注意阈值K, 严重(热点℃或δ), 危急(热点℃或δ)]
_IR_CRITERIA = {
    "接头和线夹":      {"notice_k": 15, "severe": (80, 80), "critical": (110, 95)},
    "金属导线":        {"notice_k": 15, "severe": (80, 80), "critical": (110, 95)},
    "隔离开关转头":     {"notice_k": 15, "severe": (90, 80), "critical": (130, 95)},
    "隔离开关触头":     {"notice_k": 15, "severe": (90, 80), "critical": (130, 95)},
    "输电导线连接器":    {"notice_k": 15, "severe": (90, 80), "critical": (130, 95)},
    "断路器动静触头":    {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
    "断路器中间触头":    {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
    "电流互感器内连接":   {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
    "套管柱头":        {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
    "电容器熔丝":       {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
    "电容器熔丝座":      {"notice_k": 10, "severe": (55, 80), "critical": (80, 95)},
}

# 允许用户传入的类别名 → 判据表键
_IR_CATEGORY_ALIASES = {
    "接头和线夹": "接头和线夹", "接头": "接头和线夹", "线夹": "接头和线夹", "连接线夹": "接头和线夹",
    "金属导线": "金属导线", "导线": "金属导线",
    "隔离开关转头": "隔离开关转头", "隔离转头": "隔离开关转头", "转头": "隔离开关转头",
    "隔离开关触头": "隔离开关触头", "隔离触头": "隔离开关触头", "触头": "隔离开关触头",
    "输电导线连接器": "输电导线连接器", "耐张线夹": "输电导线连接器", "接续管": "输电导线连接器",
    "断路器动静触头": "断路器动静触头", "断路器": "断路器动静触头", "动静触头": "断路器动静触头",
    "断路器中间触头": "断路器中间触头", "中间触头": "断路器中间触头",
    "电流互感器内连接": "电流互感器内连接", "电流互感器": "电流互感器内连接", "CT": "电流互感器内连接",
    "套管柱头": "套管柱头", "套管": "套管柱头",
    "电容器熔丝": "电容器熔丝", "熔丝": "电容器熔丝",
    "电容器熔丝座": "电容器熔丝座", "熔丝座": "电容器熔丝座",
}


@tool
async def ir_temperature_anomaly(
    hot_temp: float,
    ref_temp: float,
    ambient_temp: float,
    device_category: str = "接头和线夹",
) -> dict:
    """红外测温相对温差法诊断（DL/T 664-2016）。

    温升 τ1 = 热点温度 - 环境温度，τ2 = 正常相对应点温度 - 环境温度，
    相对温差 δ = (τ1 - τ2) / τ1 × 100%。
    当热点温升 τ1 < 15K 时不宜采用相对温差法（改用表面温度/同类比较法）。

    判据按设备类别分三档：接头线夹/金属导线、隔离开关触头/输电导线连接器、
    断路器触头/CT内连接/套管柱头/电容器熔丝。严重与危急以"热点温度"或"δ"任一满足判定。

    Args:
        hot_temp: 发热点温度 T1 (℃)
        ref_temp: 正常相对应点温度 T2 (℃)
        ambient_temp: 环境温度参照体温度 T0 (℃)
        device_category: 设备类别（如 '接头和线夹' / '隔离开关触头' / '断路器动静触头' /
            '电流互感器' / '套管' / '输电导线连接器'），默认 '接头和线夹'

    Returns:
        dict: mode='ir', delta_pct, tau1, tau2, judgment(正常/一般/严重/危急),
              category, advice, standard
    """
    crit = _IR_CRITERIA.get(
        _IR_CATEGORY_ALIASES.get(device_category, ""),
        _IR_CRITERIA["接头和线夹"],
    )
    category_name = next(
        (k for k in _IR_CRITERIA if _IR_CATEGORY_ALIASES.get(device_category) == k),
        "接头和线夹",
    )

    tau1 = hot_temp - ambient_temp
    tau2 = ref_temp - ambient_temp
    delta = (tau1 - tau2) / tau1 * 100.0 if tau1 > 0 else float("nan")

    base = {
        "mode": "ir",
        "standard": "DL/T 664-2016",
        "category": category_name,
        "hot_temp_c": hot_temp,
        "ref_temp_c": ref_temp,
        "ambient_temp_c": ambient_temp,
        "tau1_k": round(tau1, 1),
        "tau2_k": round(tau2, 1),
        "delta_pct": round(delta, 1) if tau1 > 0 else None,
    }

    # 护栏：温升不足 15K 时相对温差法不适用
    if tau1 < 15:
        return {
            **base,
            "judgment": "正常",
            "advice": (
                f"热点温升 {tau1:.1f}K 小于 15K，不宜采用相对温差法，"
                "建议按表面温度法或同类设备比较法复核，并关注负荷电流与温升变化趋势。"
            ),
            "note": "相对温差法仅适用于温升明显（≥15K）的电流致热型缺陷。",
        }

    severe_temp, severe_delta = crit["severe"]
    critical_temp, critical_delta = crit["critical"]
    notice_k = crit["notice_k"]

    # 危急：热点温度或 δ 任一达危急值
    if hot_temp > critical_temp or delta >= critical_delta:
        judgment = "危急"
        advice = (
            f"热点温度 {hot_temp:.1f}℃ / 相对温差 δ={delta:.1f}%，"
            f"达到危急缺陷判据（> {critical_temp}℃ 或 δ≥{critical_delta}%）。"
            "缺陷设备应立即报告并尽快安排停电处理，防止故障扩大。"
        )
    elif hot_temp > severe_temp or delta >= severe_delta:
        judgment = "严重"
        advice = (
            f"热点温度 {hot_temp:.1f}℃ / 相对温差 δ={delta:.1f}%，"
            f"达到严重缺陷判据（> {severe_temp}℃ 或 δ≥{severe_delta}%）。"
            "应尽快安排处理，并缩短复测周期跟踪。"
        )
    elif hot_temp > ambient_temp and delta > 0:
        # 存在温升但未达严重：按温差是否超过注意阈值
        if delta > notice_k:
            judgment = "一般"
            advice = (
                f"相对温差 δ={delta:.1f}% 超过注意值（温差 >{notice_k}K），"
                "属于一般缺陷，建议纳入检修计划并结合负荷/温度趋势跟踪。"
            )
        else:
            judgment = "正常"
            advice = "存在一定温升但未达到注意值，建议保持常规巡视关注。"
    else:
        judgment = "正常"
        advice = "未发现明显温升，红外检测正常。"

    return {**base, "judgment": judgment, "advice": advice}


ALL_SUBSTATION_TOOLS = [oil_dga_three_ratio, sf6_gas_check, ir_temperature_anomaly]
