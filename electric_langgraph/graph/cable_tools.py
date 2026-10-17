"""高压电缆故障诊断工具：封装 electric_mcp 的 3 个诊断能力为 LangChain tools。

工具来源: tools/electric_mcp/ 下的故障定位 / 类型分类 / PRPD 局放识别算法。
直接在主进程中调用底层模块 (predict_tdoa / classify_fault / predict_prpd 等),
绕过 mcp_server.py 的 os.chdir 副作用。

CPU 密集的推理部分 (numpy / torch) 通过 asyncio.to_thread 放到线程池,
避免阻塞 LangGraph 的事件循环。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.tools import tool

# electric_mcp 目录（底层模块所在）
_MCP_ROOT = Path(__file__).resolve().parents[1] / "tools" / "electric_mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

# yolo_mcp 目录（YOLO 缺陷检测底层模块所在）
_YOLO_ROOT = Path(__file__).resolve().parents[1] / "tools" / "yolo_mcp"
if str(_YOLO_ROOT) not in sys.path:
    sys.path.insert(0, str(_YOLO_ROOT))


def _resolve(path_str: str) -> Path:
    """相对路径以 electric_mcp 目录为基准，绝对路径原样返回。"""
    p = Path(path_str)
    return p if p.is_absolute() else _MCP_ROOT / p


# ------------------------------------------------------------
# 延迟加载（首次调用时一次性装入内存，后续复用）
# ------------------------------------------------------------
_state: dict[str, Any] = {}


def _load_tdoa():
    if "tdoa" in _state:
        return _state["tdoa"]
    from predict_tdoa import (
        CABLE_LEN, DT_US_DEFAULT, V_MODE3, detect_fault_start, tdoa_localize,
    )
    _state["tdoa"] = {
        "localize": tdoa_localize,
        "detect": detect_fault_start,
        "CABLE_LEN": CABLE_LEN,
    }
    return _state["tdoa"]


def _load_classify():
    if "classify" in _state:
        return _state["classify"]
    import joblib
    from classify_fault import (
        CLASS_FEAT_NAMES_6, CLASS_FEAT_NAMES_9, CLASSES,
        extract_class_features, rule_on_sample,
    )
    from train_fault_locator import STEADY_T  # noqa: F401

    model_path = _MCP_ROOT / "classify_gbdt.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"分类模型不存在: {model_path}")
    art = joblib.load(model_path)
    _state["classify"] = {
        "artifact": art,
        "model": art["model"],
        "classes": CLASSES,
        "names_6": CLASS_FEAT_NAMES_6,
        "names_9": CLASS_FEAT_NAMES_9,
        "extract": extract_class_features,
        "rule_sample": rule_on_sample,
    }
    return _state["classify"]


def _load_prpd():
    if "prpd" in _state:
        return _state["prpd"]
    from prpd_generate import (
        C_NORM, CLASS_ZH, CLASSES as PRPD_CLASSES,
        SEV_BANDS, extract_descriptors, normalize_img,
    )
    from predict_prpd import _load_cnn, _load_gbdt, _read_features_csv, _read_prpd_csv
    from train_prpd_cnn import PRPDNet  # noqa: F401

    cnn_path = _MCP_ROOT / "prpd_data" / "prpd_cnn.pt"
    gbdt_path = _MCP_ROOT / "prpd_data" / "prpd_gbdt.joblib"

    prpd: dict[str, Any] = {
        "classes": list(PRPD_CLASSES),
        "class_zh": CLASS_ZH,
        "sev_bands": SEV_BANDS,
        "c_norm": C_NORM,
        "extract_desc": extract_descriptors,
        "normalize": normalize_img,
        "read_prpd_csv": _read_prpd_csv,
        "read_features_csv": _read_features_csv,
    }
    if cnn_path.exists():
        model, c_norm, classes = _load_cnn(cnn_path)
        prpd["cnn"] = model
        prpd["cnn_classes"] = classes
        prpd["cnn_cnorm"] = c_norm
    if gbdt_path.exists():
        gmodel, feat_names, gclasses = _load_gbdt(gbdt_path)
        prpd["gbdt"] = gmodel
        prpd["gbdt_classes"] = gclasses
    _state["prpd"] = prpd
    return prpd


def _load_yolo():
    """加载 yolo_detect 模块（惰性 import, 内部延迟加载 YOLO 模型并缓存）。

    与 electric_mcp 一致: 直接 import 底层模块, 绕过 mcp_server.py 的 os.chdir 副作用。
    yolo_detect 的模型加载 (TensorRT engine / .pt 兜底) 在首次推理时才发生。
    """
    if "yolo" not in _state:
        import yolo_detect
        _state["yolo"] = yolo_detect
    return _state["yolo"]


def get_yolo_module():
    """公开入口: 供 server/main.py 复用同一模块实例 (避免双模块双模型缓存)。"""
    return _load_yolo()


# ============================================================
# Tool 1: TDOA 故障定位
# ============================================================
@tool
async def fault_location_tdoa(src_csv: str, ldr_csv: str, dt_us: float = 0.2) -> dict:
    """双端电流行波 TDOA 故障定位。

    输入源端 / 负荷端三相电流 CSV (每行一个时间步, 列顺序 A B C, 可有/无时间列),
    输出故障位置 (米, 从源端起算) + 双端时差 Δt。

    精度: MAE=10.1m, 验收线 29m (4 类故障×73 位置×4 档电阻验证)。
    电缆全长 4800m, 波速 2.31e8 m/s (线模 mode-3)。

    Args:
        src_csv: 源端电流 CSV 路径 (相对 tools/electric_mcp/ 或绝对路径), 列序 A B C
        ldr_csv: 负荷端电流 CSV 路径, 列序 A B C, 与 src 时间对齐
        dt_us: 采样间隔 us, 默认 0.2 (=5MHz)

    Returns:
        dict: position_m 位置(米), delta_t_us 双端时差(us),
              within_acceptance 是否满足验收线, algorithm 算法说明
    """
    def _run() -> dict:
        t = _load_tdoa()
        src_path, ldr_path = _resolve(src_csv), _resolve(ldr_csv)
        if not src_path.exists():
            return {"error": f"源端 CSV 不存在: {src_path}"}
        if not ldr_path.exists():
            return {"error": f"负荷端 CSV 不存在: {ldr_path}"}
        src = np.loadtxt(src_path, delimiter=",", ndmin=2)
        ldr = np.loadtxt(ldr_path, delimiter=",", ndmin=2)
        if src.ndim == 2 and src.shape[1] >= 3:
            ia, ib = src[:, -3], src[:, -2]
            la, lb = ldr[:, -3], ldr[:, -2]
        else:
            ia, ib = src[:, 0], src[:, 1]
            la, lb = ldr[:, 0], ldr[:, 1]
        cur = np.stack([ia, ib, np.zeros_like(ia), la, lb, np.zeros_like(la)])
        dt = dt_us * 1e-6
        tf = t["detect"](cur, dt)
        pos, dlt = t["localize"](cur, tf, dt)
        return {
            "position_m": round(float(pos), 2),
            "delta_t_us": round(float(dlt * 1e6), 4),
            "cable_len_m": t["CABLE_LEN"],
            "within_acceptance": float(pos) >= 0 and abs(float(pos) - 2400) <= 2400 + 29,
            "acceptance_line_m": 29,
            "algorithm": "TDOA 双端电流行波 (line-mode iA-iB, 二次差分+抛物线插值)",
        }

    return await asyncio.to_thread(_run)


# ============================================================
# Tool 2: 故障类型识别
# ============================================================
@tool
async def fault_type_classify(
    waveform_csv: str | None = None,
    features_json: str | None = None,
    rule_node: str = "LDR",
) -> dict:
    """4 类短路故障类型识别 (AG/AB/ABG/ABC)。

    二选一输入:
      - waveform_csv: 9 通道电压波形 CSV (时间 + SRC_A/B/C + FLT_A/B/C + LDR_A/B/C)
      - features_json: 14 维或 24 维特征 JSON

    GBDT 模型位置留出准确率 ≈1.000 (仿真下类型判别饱和)。
    波形输入时额外输出物理规则交叉验证 (塌陷相数 + 零序判据)。

    Args:
        waveform_csv: 9 通道电压波形 CSV 路径 (与 features_json 二选一)
        features_json: 特征 JSON 路径 (与 waveform_csv 二选一)
        rule_node: 规则交叉验证用哪个端节点 ('LDR' 或 'FLT', 默认 LDR)

    Returns:
        dict: fault_type, confidence, probabilities, rule_check (若波形输入)
    """
    def _run() -> dict:
        c = _load_classify()
        if waveform_csv is None and features_json is None:
            return {"error": "必须提供 waveform_csv 或 features_json 之一"}
        if waveform_csv and features_json:
            return {"error": "waveform_csv 与 features_json 二选一"}

        use_flt = c["artifact"]["channels"] == 9
        names = c["names_9"] if use_flt else c["names_6"]
        rule_detail = None

        if waveform_csv:
            p = _resolve(waveform_csv)
            if not p.exists():
                return {"error": f"波形 CSV 不存在: {p}"}
            arr = np.loadtxt(p, delimiter=",", ndmin=2, skiprows=1)
            wf = arr[:, 1:].T
            feat = c["extract"](wf, arr[:, 0], use_flt)
            r_pred, r, v = c["rule_sample"](wf, arr[:, 0], rule_node)
            rule_detail = {
                "rule_prediction": r_pred,
                "phase_rms_pu": [round(float(x), 4) for x in r],
                "v0_pu": round(float(v), 4),
            }
        else:
            p = _resolve(features_json)
            if not p.exists():
                return {"error": f"特征 JSON 不存在: {p}"}
            d = json.loads(p.read_text(encoding="utf-8"))
            try:
                feat = np.array([d[n] for n in names], dtype=np.float64)
            except KeyError as e:
                return {"error": f"features.json 缺键 {e}: 需要 {len(names)} 维"}

        proba = c["model"].predict_proba(feat.reshape(1, -1))[0]
        pred_idx = int(np.argmax(proba))
        pred = c["classes"][pred_idx]
        result = {
            "fault_type": pred,
            "confidence": round(float(proba[pred_idx]), 4),
            "probabilities": {cls: round(float(p), 4) for cls, p in zip(c["classes"], proba)},
            "feature_dim": len(names),
            "note": "仿真下类型判别饱和, 现场可能需重训",
        }
        if rule_detail:
            result["rule_check"] = rule_detail
            result["rule_agrees_gbdt"] = rule_detail["rule_prediction"] == pred
        return result

    return await asyncio.to_thread(_run)


# ============================================================
# Tool 3: 局放缺陷类型识别
# ============================================================
@tool
async def prpd_defect_classify(
    prpd_csv: str | None = None,
    features_csv: str | None = None,
    engine: str = "gbdt",
) -> dict:
    """4 类局放缺陷类型识别 (TREE 电树 / WATR 水树 / JOIN 接头松动 / AGNG 绝缘老化)。

    严重度: 正常(<20pC) / 预警(20~50pC) / 报警(>50pC)。
    严重度留出泛化准确率: GBDT 0.997, CNN 0.898。

    Args:
        prpd_csv: PRPD φ-q 计数矩阵 CSV 路径 (128×128, 与 features_csv 二选一)
        features_csv: 17 维描述子 CSV 路径 (与 prpd_csv 二选一)
        engine: 推理引擎 'gbdt'(默认) / 'cnn' / 'both'

    Returns:
        dict: defect_type, defect_zh, severity, q_max_pc, probabilities, engine
    """
    def _run() -> dict:
        p = _load_prpd()
        if prpd_csv is None and features_csv is None:
            return {"error": "必须提供 prpd_csv 或 features_csv 之一"}
        if prpd_csv and features_csv:
            return {"error": "prpd_csv 与 features_csv 二选一"}

        counts = None
        features = None
        if prpd_csv:
            path = _resolve(prpd_csv)
            if not path.exists():
                return {"error": f"PRPD CSV 不存在: {path}"}
            counts = p["read_prpd_csv"](path)
            features = p["extract_desc"](counts)
        else:
            path = _resolve(features_csv)
            if not path.exists():
                return {"error": f"特征 CSV 不存在: {path}"}
            features = p["read_features_csv"](path)

        result: dict[str, Any] = {"engine": engine}

        if counts is not None:
            q_max = float(max(features[1], features[2]))
            sev = "正常" if q_max < 20 else ("预警" if q_max < 50 else "报警")
            result["severity"] = sev
            result["q_max_pc"] = round(q_max, 2)
            result["severity_threshold_pC"] = {"正常": "<20", "预警": "20~50", "报警": ">50"}

        if engine in ("gbdt", "both") and p.get("gbdt"):
            proba_g = p["gbdt"].predict_proba(features.reshape(1, -1))[0]
            pred_g = int(proba_g.argmax())
            cls_g = p["gbdt_classes"][pred_g]
            result["gbdt"] = {
                "defect_type": cls_g,
                "defect_zh": p["class_zh"].get(cls_g, cls_g),
                "confidence": round(float(proba_g[pred_g]), 4),
                "probabilities": {c: round(float(x), 4)
                                  for c, x in zip(p["gbdt_classes"], proba_g)},
            }

        if engine in ("cnn", "both") and p.get("cnn") and counts is not None:
            import torch
            import torch.nn.functional as F
            img = p["normalize"](counts, p["cnn_cnorm"])
            x = torch.from_numpy(img[None, None, :, :]).float()
            model_device = next(p["cnn"].parameters()).device
            x = x.to(model_device)
            with torch.no_grad():
                logits = p["cnn"](x)
            proba_c = F.softmax(logits, dim=1).cpu().numpy()[0]
            pred_c = int(proba_c.argmax())
            cls_c = p["cnn_classes"][pred_c]
            result["cnn"] = {
                "defect_type": cls_c,
                "defect_zh": p["class_zh"].get(cls_c, cls_c),
                "confidence": round(float(proba_c[pred_c]), 4),
                "probabilities": {c: round(float(x), 4)
                                  for c, x in zip(p["cnn_classes"], proba_c)},
            }

        primary = "gbdt" if engine in ("gbdt", "both") and "gbdt" in result else "cnn"
        if primary in result and isinstance(result[primary], dict):
            result["defect_type"] = result[primary]["defect_type"]
            result["defect_zh"] = result[primary]["defect_zh"]
            result["confidence"] = result[primary]["confidence"]
            result["probabilities"] = result[primary]["probabilities"]
        elif not result.get("defect_type"):
            return {"error": f"引擎 {engine} 模型未加载 (请检查 prpd_data/ 下模型文件)"}

        result["note"] = "合成数据严重度留出准确率, 现场以规范书第三阶段模拟局放+实地比对为准"
        return result

    return await asyncio.to_thread(_run)


# ============================================================
# Tool 4: YOLO 线路缺陷检测
# ============================================================
@tool
async def line_defect_detect(
    image_path: str,
    conf: float = 0.25,
    iou: float = 0.45,
    imgsz: int = 640,
) -> dict:
    """输电线路缺陷检测 (YOLOv8).

    识别 3 类缺陷: broken 破损 / bird nest 鸟巢 / object 异物。
    输入线路巡检图片路径, 输出每个检测框的类别 + 置信度 + 像素坐标。
    推理引擎: TensorRT (若可用) / PyTorch 兜底, 见返回的 engine 字段。

    Args:
        image_path: 线路现场图片路径 (相对 tools/yolo_mcp/ 或绝对路径, jpg/png/bmp/webp)
        conf: 置信度阈值, 默认 0.25
        iou: NMS IoU 阈值, 默认 0.45
        imgsz: 推理输入尺寸, 默认 640

    Returns:
        dict: image_path, image_size [w,h], detections[{class_id, class_name,
              confidence, bbox:[x1,y1,x2,y2]}], count, latency_ms, engine
    """
    def _run() -> dict:
        y = _load_yolo()
        p = Path(image_path)
        if not p.exists():
            return {"error": f"图片不存在: {p}"}
        try:
            return y.detect(str(p), conf=conf, iou=iou, imgsz=imgsz)
        except Exception as e:
            return {"error": f"检测失败: {e}"}

    return await asyncio.to_thread(_run)


ALL_CABLE_TOOLS = [
    fault_location_tdoa, fault_type_classify, prpd_defect_classify,
    line_defect_detect,
]
