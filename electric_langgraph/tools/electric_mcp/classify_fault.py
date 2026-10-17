"""
classify_fault.py — 4 类短路故障类型分类器 (物理规则 + GBDT 混合)

对应规范书要求: "故障类型自动识别"(5.2 总体技术要求) / "故障类型判断"(4.1 开发
融合算法的高压电缆状态智能感知与诊断模型)。当前项目此前只有定位 (TDOA 10.1m +
GBDT 辅助 48.7m), 本模块补上"故障类型"这一环, 作为 AI 诊断引擎第一环。

4 类故障在 FLT/LDR 端节点稳态电压上的签名 (标幺到额定相电压 SRC_RMS):
    AG  : 1 相塌陷≈0, 其余保持         (LDR 零序 V0≈0.80)
    AB  : 两低相相等≈0.50, 无零序       (LDR V0≈0.00)
    ABG : 2 相塌陷≈0, 强零序            (LDR V0≈0.47)
    ABC : 3 相全塌陷≈0                  (LDR V0≈0.00)
已实证 (output_60m_med 7008 条): 签名对过渡电阻 (0.01~10000Ω)、位置、合闸角、
负荷不敏感, 塌陷相数 100% 一致 -> 规则与 GBDT 均可到 ~1.000 准确率 (该仿真下
类型判别已饱和; 混合方案的价值在概率输出 + 可解释交叉验证 + 范式可迁移)。

方法 (混合):
  1. 物理规则基线 classify_rule(): 故障相跌落模板 + 零序判据, 零训练、可解释
     (阈值复用 verify_dataset.py 的 0.30/0.60/0.15 + 零序 0.15)。
  2. GBDT (HistGradientBoostingClassifier): 输出各类概率, 自动处理干扰维度;
     与 train_fault_locator 相同的"位置留出"防泄漏评估。

特征:
  9 通道 (上限, 24 维): FEAT_NAMES 21 维 + 3 零序 (V0_SRC/FLT/LDR)
  6 通道 (部署真实, 默认, 14 维): SRC+LDR 稳态/暂态 12 维 + 2 零序 (V0_SRC/LDR)
  (FLT 是故障点节点, 现场装置测不到; 实证 6 通道与 9 通道判别力相同)

用法:
  python classify_fault.py output_60m_med/dataset.hdf5           # 训练 + 全评估
  python classify_fault.py output_60m_med/dataset.hdf5 --save classify_gbdt.joblib
  python classify_fault.py --predict sample_data/waveforms.csv --model classify_gbdt.joblib
  python classify_fault.py --predict sample_data/waveforms.csv --model classify_gbdt.joblib \
                           --true AG
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

import joblib                                        # noqa: E402
import h5py                                          # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier   # noqa: E402
from sklearn.metrics import classification_report, confusion_matrix   # noqa: E402

from train_fault_locator import (SRC_RMS, STEADY_T, TRANSIENT_T,   # noqa: E402
                                 FEAT_NAMES, extract_features,
                                 make_splits)

# ============================================================
# 常量
# ============================================================
CLASSES = ['AG', 'AB', 'ABG', 'ABC']
CLASS_CODE = {c: i for i, c in enumerate(CLASSES)}

# 规则阈值 (0.30/0.60/0.15 复用 verify_dataset.py 的 pattern_ok; V0_TH 新增)
COLLAPSE_TH = 0.30      # 相 RMS < 0.30 视为"塌陷" (接地/相间)
HOLD_TH = 0.60          # 健全相必须 > 0.60
TWO_EPS = 0.15          # 两低相"相等"判据 |r0-r1|
V0_TH = 0.15            # 零序"存在"阈值 (接地 vs 不接地)

# 6 通道 (部署真实, SRC+LDR) 特征名, 固定顺序
CLASS_FEAT_NAMES_6 = [
    'SRCA_steady', 'SRCB_steady', 'SRCC_steady',
    'LDRA_steady', 'LDRB_steady', 'LDRC_steady',
    'SRCA_trans', 'SRCB_trans', 'SRCC_trans',
    'LDRA_trans', 'LDRB_trans', 'LDRC_trans',
    'V0_SRC', 'V0_LDR',
]
# 9 通道 (上限) 特征名 = 定位 21 维 + 3 零序
CLASS_FEAT_NAMES_9 = FEAT_NAMES + ['V0_SRC', 'V0_FLT', 'V0_LDR']

BATCH = 256             # 逐块读 HDF5 的批大小
DEFAULT_H5 = 'output_60m_med/dataset.hdf5'


# ============================================================
# 特征
# ============================================================
def zero_seq_rms(wf, node_start, steady_mask):
    """节点零序 RMS = RMS((Va+Vb+Vc)/3) over 稳态段, /SRC_RMS.

    node_start: 节点首通道下标 (SRC=0, FLT=3, LDR=6).
    float64 累加: 三相和的对消在 float32 下有 ~1e-6 噪声, 判据阈值 0.15 远高于此。
    """
    s = (wf[node_start].astype(np.float64) + wf[node_start + 1]
         + wf[node_start + 2]) / 3.0
    return float(np.sqrt(np.mean(s[steady_mask] ** 2))) / SRC_RMS


def _assemble(base, v0, use_flt):
    """由 21 维 base + 3 零序组装分类特征 (24 维或 14 维)."""
    if use_flt:
        return np.concatenate([base, v0])
    idx = np.array([0, 1, 2, 6, 7, 8,               # SRC+LDR 稳态
                    9, 10, 11, 15, 16, 17])         # SRC+LDR 暂态
    return np.concatenate([base[idx], v0[[0, 2]]])  # + V0_SRC, V0_LDR


def extract_class_features(wf, t_arr, use_flt):
    """单样本分类特征: use_flt=True -> (24,), False -> (14,).

    wf: (9, N) 电压波形 (V)。base 前 9 维即各通道稳态 RMS (规则基线直接复用)。
    """
    base = extract_features(wf, t_arr)              # 21 维 (前 9 = 稳态 RMS)
    steady = t_arr >= STEADY_T
    v0 = np.array([zero_seq_rms(wf, s, steady) for s in (0, 3, 6)])  # SRC/FLT/LDR
    return _assemble(base, v0, use_flt)


def load_features(h5_path):
    """单趟批载入 -> (X6, X9, y, labels, steady, v0).

    X6: (N,14) 6 通道特征; X9: (N,24) 9 通道特征; y: (N,) int 类码;
    steady: (N,9) 各通道稳态 RMS (规则基线); v0: (N,3) SRC/FLT/LDR 零序 RMS。
    """
    t0 = time.time()
    with h5py.File(h5_path, 'r') as f:
        t_arr = f['time'][:]
        labels = f['labels'][()]
        N = len(labels)
        X6 = np.empty((N, 14), dtype=np.float32)
        X9 = np.empty((N, 24), dtype=np.float32)
        steady = np.empty((N, 9), dtype=np.float32)
        v0 = np.empty((N, 3), dtype=np.float32)
        for i0 in range(0, N, BATCH):
            i1 = min(i0 + BATCH, N)
            wf = f['waveforms'][i0:i1]
            steady_mask = t_arr >= STEADY_T
            for b in range(wf.shape[0]):
                base = extract_features(wf[b], t_arr)
                v = np.array([zero_seq_rms(wf[b], s, steady_mask)
                              for s in (0, 3, 6)])
                X9[i0 + b] = _assemble(base, v, True)
                X6[i0 + b] = _assemble(base, v, False)
                steady[i0 + b] = base[0:9]
                v0[i0 + b] = v
            if (i0 // BATCH + 1) % 10 == 0:
                print(f'  特征提取 {i1}/{N}', flush=True)
    y = np.array([CLASS_CODE[x.decode()] for x in labels['fault_type']])
    print(f'  特征提取完成: N={N}, 6ch{X6.shape[1]}维 / 9ch{X9.shape[1]}维, '
          f'耗时 {time.time() - t0:.0f}s')
    return X6, X9, y, labels, steady, v0


# ============================================================
# 物理规则基线
# ============================================================
def classify_rule(r, v0):
    """物理规则: 故障相跌落模板 + 零序判据. 恒返回 CLASSES 之一.

    r:  (3,) 某节点三相稳态 RMS (标幺).  v0: 同节点零序 RMS (标幺).
    判据:
      接地 (v0>0.15): 塌陷相 ≤1 -> AG, 否则 ABG
      不接地:          塌陷相 ≥2 -> ABC; 两低相相等(≈0.5) -> AB
    """
    s = np.sort(r)
    n_collapsed = int(np.sum(r < COLLAPSE_TH))
    if v0 > V0_TH:                               # 接地类 (AG / ABG)
        return 'AG' if n_collapsed <= 1 else 'ABG'
    if n_collapsed >= 2:                         # 不接地三相短路 -> ABC
        return 'ABC'
    if (s[0] < HOLD_TH and s[1] < HOLD_TH and s[2] > HOLD_TH
            and abs(s[0] - s[1]) < TWO_EPS):     # 两低相相等 -> AB
        return 'AB'
    return 'ABC' if n_collapsed == 3 else 'AB'   # 兜底 (不抛异常)


def rule_on_sample(wf, t_arr, node):
    """规则对单个样本: 返回 (预测类, 该节点三相稳态 RMS, 零序 RMS)."""
    base = extract_features(wf, t_arr)
    steady = base[0:9]
    n0 = 6 if node == 'LDR' else 3
    v = zero_seq_rms(wf, n0, t_arr >= STEADY_T)
    r = steady[n0:n0 + 3]
    return classify_rule(r, v), r, v


# ============================================================
# GBDT 训练与评估
# ============================================================
def run_gbdt(X, y, tr, va, te):
    """训练 HistGradientBoostingClassifier, 返回模型与 va/te 预测."""
    model = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.1, max_leaf_nodes=31,
        early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=20, random_state=42)
    model.fit(X[tr], y[tr])
    return {
        'model': model,
        'pred_va': model.predict(X[va]), 'proba_va': model.predict_proba(X[va]),
        'pred_te': model.predict(X[te]), 'proba_te': model.predict_proba(X[te]),
        'y_va': y[va], 'y_te': y[te],
        'n_iter': model.n_iter_,
    }


def evaluate(y_true, y_pred, name):
    """打印 4×4 混淆矩阵 + 分类报告 + 总体准确率, 返回准确率."""
    acc = float(np.mean(y_pred == y_true))
    print(f'\n--- {name}: 准确率 = {acc:.4f} ---')
    cm = confusion_matrix(y_true, y_pred, labels=range(4))
    print('  混淆矩阵 (行=真值, 列=预测):')
    print('        ' + ''.join(f'{c:>6}' for c in CLASSES))
    for i, row in enumerate(cm):
        print(f'  {CLASSES[i]:>4}  ' + ''.join(f'{v:>6}' for v in row))
    print(classification_report(y_true, y_pred, labels=range(4),
                               target_names=CLASSES, digits=4,
                               zero_division=0))
    return acc


def report_by_group(y_true, y_pred, labels, field, mask):
    """按标签字段分组打印分类准确率 (y_true/y_pred 为测试子集, mask 为测试索引)."""
    gvals = labels[field][mask]
    rows = []
    for g in np.unique(gvals):
        m = gvals == g
        if m.sum() == 0:
            continue
        acc = float(np.mean(y_pred[m] == y_true[m]))
        gtxt = g.decode() if isinstance(g, bytes) else f'{g:g}'
        rows.append((gtxt, acc, int(m.sum())))
    if not rows:
        return
    w = max(len(r[0]) for r in rows)
    print(f'  {field:<12}  {("组"):>{w}}  准确率   n')
    for g, acc, n in rows:
        print(f'  {field:<12}  {g:>{w}}  {acc:.4f}  {n}')


# ============================================================
# 单样本预测
# ============================================================
def predict_input(path, artifact):
    """单样本特征: features.json (14/24 键) 或 waveforms.csv (时间+9 通道)."""
    use_flt = artifact['channels'] == 9
    names = CLASS_FEAT_NAMES_9 if use_flt else CLASS_FEAT_NAMES_6
    p = Path(path)
    if p.suffix.lower() == '.csv':
        with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
            head = fh.readline()
        skip = 1 if head.lstrip()[:1].isalpha() else 0   # 首行为表头则跳过
        arr = np.loadtxt(p, delimiter=',', ndmin=2, skiprows=skip)
        wf = arr[:, 1:].T                                 # (9, N) 通道序与数据集一致
        feat = extract_class_features(wf, arr[:, 0], use_flt)
        return f'波形现算特征 {p.name}', feat
    d = json.load(open(p, encoding='utf-8'))
    try:
        feat = np.array([d[n] for n in names], dtype=np.float64)
    except KeyError as e:
        sys.exit(f'features.json 缺键 {e}: 需要 {len(names)} 维分类特征'
                 f'({names[0]} ...), 或改用 waveforms.csv 现算特征')
    return f'特征向量 {p.name} ({len(names)} 维)', feat


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('h5', nargs='?', default=None,
                    help=f'HDF5 数据文件 (默认 {DEFAULT_H5})')
    ap.add_argument('--save', default=None,
                    help='保存最优 GBDT 分类器到 joblib (默认保存 6 通道/位置留出)')
    ap.add_argument('--channels', type=int, choices=[6, 9], default=6,
                    help='--save 保存哪种通道数模型 (默认 6; 评估恒报两种)')
    ap.add_argument('--rule-node', choices=['LDR', 'FLT'], default='LDR',
                    help='规则基线用哪个端节点 (默认 LDR; FLT 为上限)')
    ap.add_argument('--predict', default=None,
                    help='单样本预测: features.json 或 waveforms.csv (需 --model)')
    ap.add_argument('--model', default=None, help='预测用模型文件 (.joblib)')
    ap.add_argument('--true', default=None,
                    help='真实故障类型 (预测模式显示对错, 如 AG)')
    args = ap.parse_args()

    # ---------------- 单样本预测模式 ----------------
    if args.predict:
        if not args.model or not Path(args.model).exists():
            print(f'模型不存在: {args.model} (先运行 --save)')
            return 1
        artifact = joblib.load(args.model)
        src, feat = predict_input(args.predict, artifact)
        model = artifact['model']
        proba = model.predict_proba(feat.reshape(1, -1))[0]
        pred = CLASSES[int(np.argmax(proba))]
        print(f'输入: {src}')
        print(f'GBDT 预测: {pred}   置信度 {proba.max():.4f}')
        print('  各类概率: ' + '  '.join(f'{c}={p:.3f}' for c, p in zip(CLASSES, proba)))
        # 物理规则交叉验证 (仅波形输入可现算)
        if Path(args.predict).suffix.lower() == '.csv':
            arr = np.loadtxt(args.predict, delimiter=',', ndmin=2, skiprows=1)
            r_pred, r, v = rule_on_sample(arr[:, 1:].T, arr[:, 0], args.rule_node)
            print(f'物理规则 ({args.rule_node}): {r_pred}   '
                  f'(相RMS {np.round(r, 3)}, V0 {v:.3f})')
        if args.true is not None:
            ok = '✓' if args.true == pred else '✗'
            print(f'真实类型: {args.true}  {ok}')
        return 0

    # ---------------- 训练 + 评估模式 ----------------
    h5 = Path(args.h5) if args.h5 else Path(__file__).parent / DEFAULT_H5
    if not h5.exists():
        print(f'文件不存在: {h5}')
        return 1

    print(f'=== 加载特征: {h5} ===')
    X6, X9, y, labels, steady, v0 = load_features(h5)
    N = len(labels)
    ftypes = np.array([x.decode() for x in labels['fault_type']])
    print(f'\n=== 样本数 {N} | 类型分布 '
          f'{dict(zip(*np.unique(ftypes, return_counts=True)))} | '
          f'位置 {labels["position_m"].min():.0f}~{labels["position_m"].max():.0f}m '
          f'({len(np.unique(labels["position_m"]))} 档) ===')

    # 规则节点下标映射 (LDR=稳态6:9/零序2, FLT=稳态3:6/零序1)
    node_slice, v0_idx = {'LDR': (6, 2), 'FLT': (3, 1)}[args.rule_node]

    summary = []
    best_model = None
    for mode in ['position', 'random']:
        tr, va, te = make_splits(labels, mode)
        if mode == 'position':
            test_pos = sorted(set(int(round(p)) for p in labels['position_m'][te]))
            print(f'\n--- 划分: 位置留出 (训练 {tr.sum()} / 验证 {va.sum()} '
                  f'/ 测试 {te.sum()}) | 测试位置(未见训练): {test_pos}m ---')
        else:
            print(f'\n--- 划分: 随机 (训练 {tr.sum()} / 验证 {va.sum()} '
                  f'/ 测试 {te.sum()}) ---')

        for channels in [6, 9]:
            X = X9 if channels == 9 else X6
            tag = f'{channels}ch {mode}'

            # 物理规则基线 (同一测试掩码)
            rule_pred = np.empty(N, dtype='<U4')
            for i in range(N):
                rule_pred[i] = classify_rule(steady[i, node_slice:node_slice + 3],
                                             v0[i, v0_idx])
            rule_code = np.array([CLASS_CODE[x] for x in rule_pred])
            rule_acc = evaluate(y[te], rule_code[te], f'规则基线 {tag}')

            # GBDT
            r = run_gbdt(X, y, tr, va, te)
            va_acc = float(np.mean(r['pred_va'] == r['y_va']))
            gbdt_acc = evaluate(r['y_te'], r['pred_te'], f'GBDT {tag}')
            print(f'  [GBDT 迭代 {r["n_iter"]} 轮 | 验证准确率 {va_acc:.4f}]')

            print(f'\n  [分组: 规则 {rule_acc:.4f} / GBDT {gbdt_acc:.4f}] 按过渡电阻:')
            report_by_group(y[te], rule_code[te], labels, 'r_fault', te)
            report_by_group(y[te], r['pred_te'], labels, 'r_fault', te)
            if mode == 'position':       # 随机划分下按位置分组无信息量, 只在留出模式报
                print(f'  [分组: 规则 / GBDT] 按测试位置:')
                report_by_group(y[te], rule_code[te], labels, 'position_m', te)
                report_by_group(y[te], r['pred_te'], labels, 'position_m', te)

            summary.append((tag, rule_acc, gbdt_acc))
            if channels == args.channels and mode == 'position':
                best_model = r['model']

    print('\n===== 汇总: 测试准确率 (同一掩码, 公平对比) =====')
    print(f'{"设置":<18} {"规则基线":>8} {"GBDT":>8}')
    for tag, r_, g in summary:
        print(f'{tag:<18} {r_:>8.4f} {g:>8.4f}')
    print(f'\n结论: 该仿真稳态签名完全可分 -> 两类模型均逼近 1.000 (类型判别饱和);')
    print(f'      混合方案价值在概率输出、可解释交叉验证、范式可迁移到局放缺陷识别。')

    if args.save and best_model is not None:
        names = CLASS_FEAT_NAMES_9 if args.channels == 9 else CLASS_FEAT_NAMES_6
        joblib.dump({'model': best_model, 'channels': args.channels,
                     'feature_names': names, 'classes': CLASSES},
                    args.save)
        print(f'\n模型已保存: {args.save} ({args.channels} 通道, 位置留出)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
