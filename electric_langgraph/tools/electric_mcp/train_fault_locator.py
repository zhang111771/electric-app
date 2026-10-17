"""
train_fault_locator.py — 故障位置定位:位置留出划分 + 物理特征 + GBDT 基线

基于 output/dataset.hdf5 (15960 样本, 9 通道 × 9630 点, dt=5us),
预测目标 = 连续故障位置 position_m (0~4800m)。

关键设计:
  1. 划分策略: 位置留出 (position holdout)。19 个位置档中 14 训练 / 2 验证 / 3 测试,
     测试位置(960/2400/3840m)从未出现在训练集 —— 这才是部署场景的真实泛化。
     同时跑"随机划分"作对比, 直观展示随机划分的泄漏虚高。
  2. 特征: 21 维物理特征 (每样本):
       - 9 × 稳态 RMS   (t>=24ms, 归一化到额定相电压)
       - 9 × 暂态 RMS   (t<16ms 一阶差分, 捕获行波/暂态能量)
       - 3 × FLT 过压比 (FLT相 RMS / 对应 SRC相 RMS)
     验证过的核心事实: FLT 最高相过电压与位置 r≈-1.00 (见 verify_dataset.py)。
  3. 模型: sklearn HistGradientBoostingRegressor (内置, 无需额外安装)。

用法:
  python train_fault_locator.py                     # 默认 output/dataset.hdf5
  python train_fault_locator.py <path.hdf5> --save model.joblib
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import h5py
from sklearn.ensemble import HistGradientBoostingRegressor

sys.stdout.reconfigure(encoding='utf-8')

SRC_AMPLITUDE = 179600.0            # 源相电压峰值 (V), 见 atp_batch_generator.SRC_AMPLITUDE
SRC_RMS = SRC_AMPLITUDE / np.sqrt(2.0)   # ~127 kV 额定相电压有效值
STEADY_T = 0.024                    # 稳态段起点 (s), 故障最晚 8.33ms 注入, 取 t>=24ms
TRANSIENT_T = 0.016                 # 暂态段终点 (s), 捕获 0~16ms 的行波/暂态能量
CABLE_LEN = 4800.0                  # 电缆全长 (m)
BATCH = 256                         # 逐块读 HDF5 的批大小

# 通道顺序 (见 atp_batch_generator.OUTPUT_NODES)
CH_SRC = slice(0, 3)                # SRCA1/B1/C1
CH_FLT = slice(3, 6)                # FLTA1/B1/C1
CH_LDR = slice(6, 9)                # LDRA1/B1/C1

# 位置留出: 19 档中取前/中/后各一档作测试, 中间两档作验证
# (position_m 存的是 240 逐次累加的浮点, 有 ~1e-12 误差, 一律先取整再比较)
TEST_POS = [960, 2400, 3840]
VAL_POS = [1920, 3120]

_CH_NAMES = ['SRCA', 'SRCB', 'SRCC', 'FLTA', 'FLTB', 'FLTC', 'LDRA', 'LDRB', 'LDRC']
FEAT_NAMES = ([f'{c}_steady' for c in _CH_NAMES] +
              [f'{c}_trans' for c in _CH_NAMES] +
              ['OV_A', 'OV_B', 'OV_C'])


def _rms(x):
    return np.sqrt(np.mean(x * x))


def extract_features(wf, t_arr):
    """21 维物理特征. wf: (9, N) 电压波形 (V)."""
    steady = t_arr >= STEADY_T
    trans = t_arr < TRANSIENT_T
    steady_rms = np.empty(9)
    trans_rms = np.empty(9)
    for c in range(9):
        steady_rms[c] = _rms(wf[c, steady]) / SRC_RMS
        trans_rms[c] = _rms(np.diff(wf[c, trans])) / SRC_RMS
    # FLT 各相过压比 = 故障点相电压 / 源端对应相电压 (稳态段)
    overvoltage = steady_rms[CH_FLT] / (steady_rms[CH_SRC] + 1e-12)
    return np.concatenate([steady_rms, trans_rms, overvoltage])


def make_splits(labels, mode, seed=42):
    """返回 (train_mask, val_mask, test_mask)."""
    pos = labels['position_m']
    if mode == 'position':
        n = len(labels)
        train = np.zeros(n, dtype=bool)
        val = np.zeros(n, dtype=bool)
        test = np.zeros(n, dtype=bool)
        pos = np.round(labels['position_m']).astype(int)   # 消除浮点累积误差
        for p in np.unique(pos):
            sel = pos == p
            if p in TEST_POS:
                test |= sel
            elif p in VAL_POS:
                val |= sel
            else:
                train |= sel
        return train, val, test
    rng = np.random.RandomState(seed)
    perm = rng.permutation(len(labels))
    n = len(labels)
    n_tr, n_va = int(n * 0.8), int(n * 0.1)
    train = np.zeros(n, dtype=bool); train[perm[:n_tr]] = True
    val = np.zeros(n, dtype=bool); val[perm[n_tr:n_tr + n_va]] = True
    test = np.zeros(n, dtype=bool); test[perm[n_tr + n_va:]] = True
    return train, val, test


def load_features(h5_path):
    """逐块读取波形 -> 特征矩阵 (N,21) 与目标 y (归一化位置)."""
    t0 = time.time()
    with h5py.File(h5_path, 'r') as f:
        t_arr = f['time'][:]
        labels = f['labels'][()]
        N = len(labels)
        X = np.empty((N, 21), dtype=np.float32)
        for i0 in range(0, N, BATCH):
            i1 = min(i0 + BATCH, N)
            wf = f['waveforms'][i0:i1]
            for b in range(wf.shape[0]):
                X[i0 + b] = extract_features(wf[b], t_arr)
            if (i0 // BATCH + 1) % 10 == 0:
                print(f'  特征提取 {i1}/{N}', flush=True)
    y = labels['position_m'].astype(np.float64) / CABLE_LEN   # 归一化到 [0,1)
    print(f'  特征提取完成: {N} 样本 × {X.shape[1]} 维, 耗时 {time.time()-t0:.1f}s')
    return X, y, labels, t_arr


def run_gbdt(X, y, tr, va, te, labels):
    """训练 + 返回预测 (米)."""
    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.1,
        early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=20, random_state=42,
    )
    model.fit(X[tr], y[tr])
    return {
        'model': model,
        'pred_va': model.predict(X[va]) * CABLE_LEN,
        'pred_te': model.predict(X[te]) * CABLE_LEN,
        'y_va': labels['position_m'][va],
        'y_te': labels['position_m'][te],
        'labels_va': labels[va],
        'labels_te': labels[te],
        'n_iter': model.n_iter_,
    }


def report_group(y_true, y_pred, labels, field, fmt='{:>10.1f}'):
    """按某标签字段分组打印 MAE(m)."""
    rows = []
    for g in np.unique(labels[field]):
        m = labels[field] == g
        if m.sum() == 0:
            continue
        mae = np.mean(np.abs(y_pred[m] - y_true[m]))
        gtxt = g.decode() if isinstance(g, bytes) else f'{g:g}'
        rows.append((gtxt, mae, int(m.sum())))
    w = max(len(r[0]) for r in rows)
    print('  ' + field.ljust(12) + '  组'.rjust(w) + '  MAE(m)    n')
    for g, mae, n in rows:
        print(f'  {field:<12}  {g:>{w}}  {mae:8.1f}  {n}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('h5', nargs='?', default=None,
                    help='HDF5 数据文件 (默认 output/dataset.hdf5)')
    ap.add_argument('--save', default=None, help='保存最佳模型到 joblib 文件')
    args = ap.parse_args()

    h5 = Path(args.h5) if args.h5 else Path(__file__).parent / 'output' / 'dataset.hdf5'
    if not h5.exists():
        print(f'文件不存在: {h5}')
        return 1

    print(f'=== 加载特征: {h5} ===')
    X, y, labels, _ = load_features(h5)
    N = len(labels)

    print(f'\n=== 样本数: {N} | 目标 position_m: '
          f'{labels["position_m"].min():.0f}~{labels["position_m"].max():.0f}m '
          f'({len(np.unique(labels["position_m"]))} 档, 每档 240m) ===')

    best = None
    for mode in ['position', 'random']:
        tr, va, te = make_splits(labels, mode)
        print(f'\n--- 划分: {mode} '
              f'(训练 {tr.sum()} / 验证 {va.sum()} / 测试 {te.sum()}) ---')
        if mode == 'position':
            test_pos = [int(round(p)) for p in labels['position_m'][te][::840][:5]]
            print('  测试位置(未见于训练):', f'{test_pos}m')
        r = run_gbdt(X, y, tr, va, te, labels)
        mae_va = np.mean(np.abs(r['pred_va'] - r['y_va']))
        mae_te = np.mean(np.abs(r['pred_te'] - r['y_te']))
        rmse_te = np.sqrt(np.mean((r['pred_te'] - r['y_te']) ** 2))
        print(f'  迭代 {r["n_iter"]} 轮 | 验证 MAE = {mae_va:.1f} m '
              f'| 测试 MAE = {mae_te:.1f} m | 测试 RMSE = {rmse_te:.1f} m')

        if mode == 'position':
            from sklearn.inspection import permutation_importance
            perm = permutation_importance(r['model'], X[va], y[va],
                                          n_repeats=5, random_state=42)
            order = np.argsort(perm.importances_mean)[::-1][:10]
            print('  [特征重要性] permutation 打乱后误差增量, top10:')
            for i in order:
                print(f'    {FEAT_NAMES[i]:<18s} '
                      f'{perm.importances_mean[i]:.4f} ± {perm.importances_std[i]:.4f}')
            print('\n  [分组] 测试集按故障类型:')
            report_group(r['y_te'], r['pred_te'], r['labels_te'], 'fault_type')
            print('\n  [分组] 测试集按过渡电阻 r_fault:')
            report_group(r['y_te'], r['pred_te'], r['labels_te'], 'r_fault')
            print('\n  [分组] 测试集按测试位置:')
            report_group(r['y_te'], r['pred_te'], r['labels_te'], 'position_m')
            best = r

    print('\n===== 结论对标 =====')
    print(f'验收标准: 0.5%*L + 5m = {0.005*CABLE_LEN+5:.0f} m (L=4800m)')
    print('标签网格 240m -> 连续插值误差下限约半格 120m; 报告 MAE 即连续定位能力')

    if args.save and best is not None:
        import joblib
        joblib.dump(best['model'], args.save)
        print(f'\n模型已保存: {args.save}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
