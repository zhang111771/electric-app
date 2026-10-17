"""
predict_prpd.py — 单样本局放 PRPD 缺陷识别预测

对标规范书:
  "自动识别电树、水树、接头松动、绝缘老化等故障类型" (doc_text.txt:240)
  "基于深度学习优化局放模式识别" (doc_text.txt:290)

输入 (两种接口, 同 predict.py 范式):
  ① 原始 φ-q 计数 CSV (3 列: phi_deg, q_pc, count  或  矩阵 CSV 行头=phi bin, 列头=q bin)
  ② prpd_generate.py 的 17 维 features.csv (走 GBDT)
  ③ 直接给 h5 中的索引

输出:
  类别 code (TREE/WATR/JOIN/AGNG) + 中文名 + 概率向量

用法:
  python predict_prpd.py sample_data_prpd/prpd.csv       # CNN: φ-q 计数 CSV
  python predict_prpd.py sample_data_prpd/features.csv   # GBDT: 17 维描述子
  python predict_prpd.py --h5 prpd_data/dataset.hdf5 --index 5
  python predict_prpd.py --gbdt sample_data_prpd/features.csv --true TREE   # GBDT + 真实标签对比
  python predict_prpd.py --gbdt sample_data_prpd/prpd.csv --model prpd_cnn.pt --true TREE
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import h5py
import joblib
import torch
import torch.nn.functional as F

from prpd_generate import (
    CLASSES, CLASS_CODE, CLASS_ZH, N_PHI, N_Q, Q_UB_PC,
    C_NORM, PHI_EDGES, Q_EDGES, PHI_CENTERS, Q_CENTERS,
    normalize_img, extract_descriptors, DESCRIPTOR_NAMES, LABELS_DTYPE)
from train_prpd_cnn import PRPDNet, DEVICE

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_CNN = 'prpd_data/prpd_cnn.pt'
DEFAULT_GBDT = 'prpd_data/prpd_gbdt.joblib'
DEFAULT_H5 = 'prpd_data/dataset.hdf5'


def _load_cnn(model_path):
    """加载 CNN artifact, 返回 (model, c_norm, classes)."""
    ckpt = torch.load(model_path, map_location=DEVICE, weights_only=False)
    model = PRPDNet(len(ckpt['classes'])).to(DEVICE)
    model.load_state_dict(ckpt['state_dict'])
    model.eval()
    c_norm = float(ckpt.get('c_norm', C_NORM))
    classes = list(ckpt['classes'])
    return model, c_norm, classes


def _load_gbdt(model_path):
    """加载 GBDT artifact, 返回 (model, feature_names, classes)."""
    art = joblib.load(model_path)
    return art['model'], art.get('feature_names', DESCRIPTOR_NAMES), list(art['classes'])


def _read_prpd_csv(path):
    """读取 φ-q 计数 CSV (129 行: 1 表头 + 128 数据行, 每行 129 列: phi_bin + 128 q bins).
    返回 counts (128,128) float32."""
    lines = Path(path).read_text(encoding='utf-8').strip().split('\n')
    if lines[0].startswith('phi_bin'):
        lines = lines[1:]
    assert len(lines) == N_PHI, f'期望 {N_PHI} 行, 实际 {len(lines)}'
    counts = np.zeros((N_PHI, N_Q), np.float32)
    for i, line in enumerate(lines):
        parts = line.split(',')
        for j in range(1, len(parts)):
            counts[i, j - 1] = float(parts[j])
    return counts


def _read_features_csv(path):
    """读取 17 维描述子 CSV (2 行: 表头 + 1 数据行). 返回 desc (17,) float64."""
    lines = Path(path).read_text(encoding='utf-8').strip().split('\n')
    if lines[0].startswith('log') or lines[0].startswith('描述') or ',' not in lines[0]:
        lines = lines[1:]
    vals = [float(v) for v in lines[0].split(',')]
    assert len(vals) == len(DESCRIPTOR_NAMES), f'期望 {len(DESCRIPTOR_NAMES)} 维, 实际 {len(vals)}'
    return np.array(vals, np.float64)


def _detect_format(path):
    """检测 CSV 是 φ-q 计数还是 17 维描述子."""
    first_line = Path(path).read_text(encoding='utf-8').strip().split('\n')[0]
    if first_line.startswith('phi_bin'):
        return 'prpd'
    # 描述子首列是 'log_n_total' 或中文 'log1p(总脉冲计数)'
    if 'log' in first_line.lower() or first_line.startswith('log'):
        return 'features'
    # 数值行: 看列数
    parts = first_line.split(',')
    if len(parts) == len(DESCRIPTOR_NAMES):
        return 'features'
    if len(parts) == N_Q + 1:
        return 'prpd'
    raise ValueError(f'无法检测 CSV 格式: {path} (首行: {first_line[:60]})')


def predict_cnn(model, counts, c_norm):
    """CNN 单样本预测, 返回 (pred_idx, proba)."""
    img = normalize_img(counts, c_norm)
    x = torch.from_numpy(img[None, None, :, :]).float().to(DEVICE)
    with torch.no_grad():
        logits = model(x)
    proba = F.softmax(logits, dim=1).cpu().numpy()[0]
    pred = int(proba.argmax())
    return pred, proba


def predict_gbdt(model, desc):
    """GBDT 单样本预测, 返回 (pred_idx, proba)."""
    X = desc.reshape(1, -1)
    proba = model.predict_proba(X)[0]
    return int(proba.argmax()), proba


def _format_result(pred_idx, proba, classes, true_label=None):
    """格式化预测结果."""
    cls_code = classes[pred_idx]
    cls_zh = CLASS_ZH.get(cls_code, cls_code)
    proba_str = ', '.join(f'{classes[i]}={p:.4f}' for i, p in enumerate(proba))
    print(f'  预测: {cls_code} ({cls_zh}) | 概率: [{proba_str}]')
    if true_label is not None:
        correct = (true_label == cls_code)
        print(f'  真实: {true_label} {"✓ 正确" if correct else "✗ 错误"}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', nargs='?', help='输入文件: φ-q CSV / features CSV / h5 索引配合 --h5')
    ap.add_argument('--h5', default=DEFAULT_H5, help='HDF5 路径 (配合 --index)')
    ap.add_argument('--index', type=int, help='从 h5 取第 index 个样本')
    ap.add_argument('--cnn', default=DEFAULT_CNN, help='CNN 模型路径')
    ap.add_argument('--gbdt', default=DEFAULT_GBDT, help='GBDT 模型路径')
    ap.add_argument('--model', help='指定模型路径 (CNN 或 GBDT, 自动检测)')
    ap.add_argument('--true', help='真实标签 (TREE/WATR/JOIN/AGNG)')
    ap.add_argument('--engine', choices=['cnn', 'gbdt', 'both'], default='both',
                    help='预测引擎 (默认 both)')
    args = ap.parse_args()

    # 确定输入
    if args.index is not None:
        with h5py.File(args.h5, 'r') as f:
            counts = f['prpd_counts'][args.index]
            lab = f['labels'][args.index]
            true_label = lab['defect_type'].decode()
        print(f'===== 预测样本 #{args.index} (h5) =====')
        print(f'真实: {true_label} ({CLASS_ZH.get(true_label, "?")})')
        prpd_csv = None
        features = extract_descriptors(counts)
    elif args.input:
        fmt = _detect_format(args.input)
        if fmt == 'prpd':
            counts = _read_prpd_csv(args.input)
            prpd_csv = args.input
            features = extract_descriptors(counts)
        else:
            features = _read_features_csv(args.input)
            prpd_csv = None
            counts = None
        true_label = args.true
        print(f'===== 预测 {args.input} (格式: {fmt}) =====')
    else:
        ap.error('需要 input 或 --index')
        return 1

    # CNN 预测
    if args.engine in ('cnn', 'both') and counts is not None:
        try:
            cnn_path = args.model if args.model else args.cnn
            model, c_norm, classes = _load_cnn(cnn_path)
            pred_idx, proba = predict_cnn(model, counts, c_norm)
            _format_result(pred_idx, proba, classes, true_label)
        except FileNotFoundError:
            print(f'  CNN 模型不存在: {cnn_path} (请先 python train_prpd_cnn.py --save)')

    # GBDT 预测
    if args.engine in ('gbdt', 'both'):
        try:
            gbdt_path = args.model if args.model else args.gbdt
            gmodel, feat_names, classes = _load_gbdt(gbdt_path)
            pred_idx, proba = predict_gbdt(gmodel, features)
            _format_result(pred_idx, proba, classes, true_label)
        except FileNotFoundError:
            print(f'  GBDT 模型不存在: {gbdt_path} (请先 python train_prpd_cnn.py --save)')

    return 0


if __name__ == '__main__':
    sys.exit(main())
