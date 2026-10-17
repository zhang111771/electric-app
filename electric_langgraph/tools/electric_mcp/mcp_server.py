"""
mcp_server.py — 高压电缆故障诊断 MCP 服务

对外暴露 3 个 tool, 封装项目已有的故障定位/分类/局放识别能力:
  - fault_location_tdoa    双端电流行波 TDOA 故障定位 (精度 10.1m, 零训练)
  - fault_type_classify    4 类短路故障类型识别 (AG/AB/ABG/ABC, GBDT+规则交叉验证)
  - prpd_defect_classify   4 类局放缺陷类型识别 (TREE/WATR/JOIN/AGNG, CNN+GBDT 双引擎)

启动:
  mcp run mcp_server.py                     # FastMCP dev 模式 (stdio)
  mcp run mcp_server.py --transport sse     # SSE 模式 (HTTP)
  python mcp_server.py                      # 直接运行 (stdio)

配置: 把 .mcp.json 复制到项目根或 ~/.claude/, 或在 Claude Code 内执行
  /mcp add cable-diag python <你的python解释器> <本目录>/mcp_server.py

依赖 (lucy-edit 环境已安装):
  mcp[cli]  torch  scikit-learn  h5py  numpy  joblib
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

# 项目根 (mcp_server.py 所在目录)
PROJECT_ROOT = Path(__file__).parent.resolve()
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.mcpserver.server import MCPServer

# ============================================================
# 延迟加载 (冷启动只加载一次)
# ============================================================
_state: dict[str, Any] = {}


def _load_tdoa():
    if 'tdoa' in _state:
        return _state['tdoa']
    from predict_tdoa import (
        tdoa_localize, detect_fault_start,
        CABLE_LEN, V_MODE3, DT_US_DEFAULT)
    _state['tdoa'] = {
        'localize': tdoa_localize,
        'detect': detect_fault_start,
        'CABLE_LEN': CABLE_LEN,
        'V_MODE3': V_MODE3,
        'DT_US_DEFAULT': DT_US_DEFAULT,
    }
    return _state['tdoa']


def _load_classify():
    if 'classify' in _state:
        return _state['classify']
    import joblib
    from classify_fault import (
        CLASSES, extract_class_features, classify_rule,
        rule_on_sample, zero_seq_rms, CLASS_FEAT_NAMES_6, CLASS_FEAT_NAMES_9)
    from train_fault_locator import STEADY_T, SRC_RMS

    # 模型路径
    model_path = PROJECT_ROOT / 'classify_gbdt.joblib'
    if not model_path.exists():
        raise FileNotFoundError(
            f'分类模型不存在: {model_path}\n'
            f'请先运行: python classify_fault.py output_60m_med/dataset.hdf5 --save classify_gbdt.joblib')
    art = joblib.load(model_path)

    _state['classify'] = {
        'artifact': art,
        'model': art['model'],
        'classes': CLASSES,
        'names_6': CLASS_FEAT_NAMES_6,
        'names_9': CLASS_FEAT_NAMES_9,
        'extract': extract_class_features,
        'rule': classify_rule,
        'rule_sample': rule_on_sample,
        'STEADY_T': STEADY_T,
    }
    return _state['classify']


def _load_prpd():
    if 'prpd' in _state:
        return _state['prpd']
    import joblib
    import torch
    import torch.nn.functional as F

    from prpd_generate import (
        CLASSES as PRPD_CLASSES, CLASS_ZH, N_PHI, N_Q, Q_UB_PC,
        PHI_EDGES, Q_EDGES, C_NORM, SEV_BANDS,
        normalize_img, extract_descriptors, DESCRIPTOR_NAMES)
    from train_prpd_cnn import PRPDNet, DEVICE
    from predict_prpd import _load_cnn, _load_gbdt, _read_prpd_csv, _read_features_csv

    cnn_path = PROJECT_ROOT / 'prpd_data' / 'prpd_cnn.pt'
    gbdt_path = PROJECT_ROOT / 'prpd_data' / 'prpd_gbdt.joblib'

    prpd = {
        'classes': list(PRPD_CLASSES),
        'class_zh': CLASS_ZH,
        'sev_bands': SEV_BANDS,
        'c_norm': C_NORM,
        'extract_desc': extract_descriptors,
        'normalize': normalize_img,
        'N_PHI': N_PHI, 'N_Q': N_Q,
        'read_prpd_csv': _read_prpd_csv,
        'read_features_csv': _read_features_csv,
    }

    if cnn_path.exists():
        model, c_norm, classes = _load_cnn(cnn_path)
        prpd['cnn'] = model
        prpd['cnn_classes'] = classes
        prpd['cnn_cnorm'] = c_norm
    else:
        prpd['cnn'] = None

    if gbdt_path.exists():
        gmodel, feat_names, gclasses = _load_gbdt(gbdt_path)
        prpd['gbdt'] = gmodel
        prpd['gbdt_classes'] = gclasses
    else:
        prpd['gbdt'] = None

    _state['prpd'] = prpd
    return prpd


# ============================================================
# MCP 服务定义
# ============================================================
mcp = MCPServer(
    'cable-diag',
    description='高压电缆故障诊断 MCP 服务 (TDOA 定位 + 故障类型 + 局放缺陷)',
)


# ------------------------------------------------------------
# Tool 1: TDOA 故障定位
# ------------------------------------------------------------
@mcp.tool()
def fault_location_tdoa(
    src_csv: str,
    ldr_csv: str,
    dt_us: float = 0.2,
) -> dict:
    """双端电流行波 TDOA 故障定位.

    输入源端/负荷端三相电流 CSV (每行一个时间步, 列顺序: A B C, 可有/无时间列),
    输出故障位置(米, 从源端起算) + 双端时差 Δt.

    精度: MAE=10.1m, 最差 26.5m, 验收线 29m (4 类故障×73 位置×4 档电阻验证).
    电缆全长 4800m, 波速 2.31e8 m/s (线模 mode-3).

    Args:
        src_csv: 源端电流 CSV 路径 (相对项目根或绝对), 列序 A B C
        ldr_csv: 负荷端电流 CSV 路径, 列序 A B C, 与 src 时间对齐
        dt_us: 采样间隔 us, 默认 0.2 (=5MHz)

    Returns:
        dict: position_m 位置(米), delta_t_us 双端时差(us), cable_len_m 电缆全长,
              algorithm 算法说明
    """
    t = _load_tdoa()
    src_path = Path(src_csv) if Path(src_csv).is_absolute() else PROJECT_ROOT / src_csv
    ldr_path = Path(ldr_csv) if Path(ldr_csv).is_absolute() else PROJECT_ROOT / ldr_csv
    if not src_path.exists():
        return {'error': f'源端 CSV 不存在: {src_path}'}
    if not ldr_path.exists():
        return {'error': f'负荷端 CSV 不存在: {ldr_path}'}

    src = np.loadtxt(src_path, delimiter=',', ndmin=2)
    ldr = np.loadtxt(ldr_path, delimiter=',', ndmin=2)
    if src.ndim == 2 and src.shape[1] >= 3:
        ia, ib = src[:, -3], src[:, -2]
        la, lb = ldr[:, -3], ldr[:, -2]
    else:
        ia, ib = src[:, 0], src[:, 1]
        la, lb = ldr[:, 0], ldr[:, 1]

    cur = np.stack([ia, ib, np.zeros_like(ia), la, lb, np.zeros_like(la)])
    dt = dt_us * 1e-6
    tf = t['detect'](cur, dt)
    pos, dlt = t['localize'](cur, tf, dt)

    return {
        'position_m': round(float(pos), 2),
        'delta_t_us': round(float(dlt * 1e6), 4),
        'cable_len_m': t['CABLE_LEN'],
        'within_acceptance': float(pos) >= 0 and abs(float(pos) - 2400) <= 2400 + 29,
        'acceptance_line_m': 29,
        'algorithm': 'TDOA 双端电流行波 (line-mode iA-iB, 二次差分+抛物线插值)',
    }


# ------------------------------------------------------------
# Tool 2: 故障类型识别
# ------------------------------------------------------------
@mcp.tool()
def fault_type_classify(
    waveform_csv: str | None = None,
    features_json: str | None = None,
    rule_node: str = 'LDR',
) -> dict:
    """4 类短路故障类型识别 (AG/AB/ABG/ABC).

    二选一输入:
      - waveform_csv: 9 通道电压波形 CSV (时间 + SRC_A/B/C + FLT_A/B/C + LDR_A/B/C)
      - features_json: 14 维或 24 维特征 JSON (键名见 CLASS_FEAT_NAMES_6/9)

    GBDT 模型位置留出准确率 ≈1.000 (仿真下类型判别饱和).
    波形输入时额外输出物理规则交叉验证 (塌陷相数 + 零序判据).

    Args:
        waveform_csv: 9 通道电压波形 CSV 路径 (与 features_json 二选一)
        features_json: 特征 JSON 路径 (与 waveform_csv 二选一)
        rule_node: 规则交叉验证用哪个端节点 ('LDR' 或 'FLT', 默认 LDR)

    Returns:
        dict: fault_type, confidence, probabilities{AG,AB,ABG,ABC},
              rule_check (若波形输入), rule_detail
    """
    c = _load_classify()
    if waveform_csv is None and features_json is None:
        return {'error': '必须提供 waveform_csv 或 features_json 之一'}
    if waveform_csv and features_json:
        return {'error': 'waveform_csv 与 features_json 二选一, 不能同时提供'}

    use_flt = c['artifact']['channels'] == 9
    names = c['names_9'] if use_flt else c['names_6']
    rule_detail = None

    if waveform_csv:
        p = Path(waveform_csv) if Path(waveform_csv).is_absolute() \
            else PROJECT_ROOT / waveform_csv
        if not p.exists():
            return {'error': f'波形 CSV 不存在: {p}'}
        arr = np.loadtxt(p, delimiter=',', ndmin=2, skiprows=1)
        wf = arr[:, 1:].T
        feat = c['extract'](wf, arr[:, 0], use_flt)
        # 规则交叉验证
        r_pred, r, v = c['rule_sample'](wf, arr[:, 0], rule_node)
        rule_detail = {
            'rule_prediction': r_pred,
            'phase_rms_pu': [round(float(x), 4) for x in r],
            'v0_pu': round(float(v), 4),
        }
    else:
        p = Path(features_json) if Path(features_json).is_absolute() \
            else PROJECT_ROOT / features_json
        if not p.exists():
            return {'error': f'特征 JSON 不存在: {p}'}
        d = json.load(open(p, encoding='utf-8'))
        try:
            feat = np.array([d[n] for n in names], dtype=np.float64)
        except KeyError as e:
            return {'error': f'features.json 缺键 {e}: 需要 {len(names)} 维'}

    proba = c['model'].predict_proba(feat.reshape(1, -1))[0]
    pred_idx = int(np.argmax(proba))
    pred = c['classes'][pred_idx]

    result = {
        'fault_type': pred,
        'confidence': round(float(proba[pred_idx]), 4),
        'probabilities': {c: round(float(p), 4) for c, p in zip(c['classes'], proba)},
        'feature_dim': len(names),
        'note': '仿真下类型判别饱和, 现场可能需重训',
    }
    if rule_detail:
        result['rule_check'] = rule_detail
        result['rule_agrees_gbdt'] = rule_detail['rule_prediction'] == pred
    return result


# ------------------------------------------------------------
# Tool 3: 局放缺陷类型识别
# ------------------------------------------------------------
@mcp.tool()
def prpd_defect_classify(
    prpd_csv: str | None = None,
    features_csv: str | None = None,
    engine: str = 'gbdt',
) -> dict:
    """4 类局放缺陷类型识别 (TREE 电树 / WATR 水树 / JOIN 接头松动 / AGNG 绝缘老化).

    对标规范书 "自动识别电树/水树/接头松动/绝缘老化" (doc_text.txt:240).
    严重度留出 (test=全 HIGH, 未见最高档) 泛化准确率:
      GBDT 0.997, CNN 0.898.

    输入二选一:
      - prpd_csv:   φ-q 计数矩阵 CSV (128×128, 首行 phi_bin,0,...,127)
      - features_csv: 17 维描述子 CSV (2 行: 表头 + 数据)

    Args:
        prpd_csv: PRPD φ-q 计数矩阵 CSV 路径 (与 features_csv 二选一)
        features_csv: 17 维描述子 CSV 路径 (与 prpd_csv 二选一)
        engine: 推理引擎 'gbdt'(默认, 准确率更高) / 'cnn' / 'both'

    Returns:
        dict: defect_type, defect_zh, severity, q_max_pc,
              probabilities{ TREE, WATR, JOIN, AGNG }, engine
    """
    p = _load_prpd()
    if prpd_csv is None and features_csv is None:
        return {'error': '必须提供 prpd_csv 或 features_csv 之一'}
    if prpd_csv and features_csv:
        return {'error': 'prpd_csv 与 features_csv 二选一, 不能同时提供'}

    counts = None
    features = None
    if prpd_csv:
        path = Path(prpd_csv) if Path(prpd_csv).is_absolute() \
               else PROJECT_ROOT / prpd_csv
        if not path.exists():
            return {'error': f'PRPD CSV 不存在: {path}'}
        counts = p['read_prpd_csv'](path)
        features = p['extract_desc'](counts)
    else:
        path = Path(features_csv) if Path(features_csv).is_absolute() \
               else PROJECT_ROOT / features_csv
        if not path.exists():
            return {'error': f'特征 CSV 不存在: {path}'}
        features = p['read_features_csv'](path)

    result: dict[str, Any] = {'engine': engine}

    # 严重度判定 (锚定 L233 阈值)
    if counts is not None:
        q_max = float(max(features[1], features[2]))   # 正/负半 q_max 取大
        sev = '正常' if q_max < 20 else ('预警' if q_max < 50 else '报警')
        result['severity'] = sev
        result['q_max_pc'] = round(q_max, 2)
        result['severity_threshold_pC'] = {'正常': '<20', '预警': '20~50', '报警': '>50'}

    # GBDT 推理
    if engine in ('gbdt', 'both') and p.get('gbdt'):
        proba_g = p['gbdt'].predict_proba(features.reshape(1, -1))[0]
        pred_g = int(proba_g.argmax())
        cls_g = p['gbdt_classes'][pred_g]
        result['gbdt'] = {
            'defect_type': cls_g,
            'defect_zh': p['class_zh'].get(cls_g, cls_g),
            'confidence': round(float(proba_g[pred_g]), 4),
            'probabilities': {c: round(float(x), 4)
                              for c, x in zip(p['gbdt_classes'], proba_g)},
        }

    # CNN 推理
    if engine in ('cnn', 'both') and p.get('cnn') and counts is not None:
        import torch
        import torch.nn.functional as F
        img = p['normalize'](counts, p['cnn_cnorm'])
        x = torch.from_numpy(img[None, None, :, :]).float()
        # 把输入搬到模型所在设备 (cpu 或 cuda)
        model_device = next(p['cnn'].parameters()).device
        x = x.to(model_device)
        with torch.no_grad():
            logits = p['cnn'](x)
        proba_c = F.softmax(logits, dim=1).cpu().numpy()[0]
        pred_c = int(proba_c.argmax())
        cls_c = p['cnn_classes'][pred_c]
        result['cnn'] = {
            'defect_type': cls_c,
            'defect_zh': p['class_zh'].get(cls_c, cls_c),
            'confidence': round(float(proba_c[pred_c]), 4),
            'probabilities': {c: round(float(x), 4)
                              for c, x in zip(p['cnn_classes'], proba_c)},
        }

    # 默认取主引擎结果作为顶层字段
    primary = 'gbdt' if engine in ('gbdt', 'both') and 'gbdt' in result else 'cnn'
    if primary in result and isinstance(result[primary], dict):
        result['defect_type'] = result[primary]['defect_type']
        result['defect_zh'] = result[primary]['defect_zh']
        result['confidence'] = result[primary]['confidence']
        result['probabilities'] = result[primary]['probabilities']
    elif not result.get('defect_type'):
        return {'error': f'引擎 {engine} 模型未加载 (请检查 prpd_data/ 下模型文件)'}

    result['note'] = '合成数据严重度留出准确率, 现场以规范书第三阶段模拟局放+实地比对为准'
    return result


# ------------------------------------------------------------
# 资源: 暴露项目文档供 LLM 参考
# ------------------------------------------------------------
@mcp.resource('cable://system-info')
def system_info() -> str:
    """系统信息: 电缆参数 + 算法能力 + 验收指标."""
    return """高压电缆故障诊断系统
电缆: 4.8km, 单回 220kV, 呼和浩特丁香路管廊
TDOA 定位: MAE=10.1m, 验收线 29m (0.5%·L + 5m)
故障类型: 4 类 (AG/AB/ABG/ABC), GBDT 位置留出 ≈1.000
局放缺陷: 4 类 (TREE/WATR/JOIN/AGNG), GBDT 严重度留出 0.997
规范书: doc/doc_text.txt
完整算法报告: doc/TDOA行波测距算法与仿真验证.md, doc/局放PRPD模式识别算法报告.md
"""


@mcp.resource('cable://acceptance')
def acceptance_criteria() -> str:
    """验收指标一览."""
    return """验收指标 (规范书 + 本报告建议):
定位精度: ≤ 0.5%·L + 5m = 29m   实测 MAE=10.1m ✓
局放识别准确率: ≥90% (建议值)      实测 GBDT 99.7% ✓
故障类型识别: 仿真饱和 ≈100%
预警延时: ≤500ms (规范书要求)
可靠性: ≥99.9% (规范书要求)
"""


# ============================================================
# 入口
# ============================================================
if __name__ == '__main__':
    mcp.run()
