"""
predict_tdoa.py — 双端电流行波 TDOA 故障定位器 (确定性物理公式, 零训练)

定位原理:
    故障行波以电缆最快模态 (线模 mode-3) 向两端传播:
        源端到达  t_src = t_fault + pos / v
        负荷端到达 t_ldr = t_fault + (L - pos) / v
    双端时差 (与故障时刻 t_fault 无关):
        Δt = t_ldr - t_src = (L - 2·pos) / v
    位置:
        pos = (L - v·Δt) / 2            (从源端起算, 米)

    已由仿真验证 (短窗 5MHz, 2628 条):
        4 类故障 (AG/AB/ABG/ABC) × 73 位置 (240~4560m)
        × 4 档过渡电阻 (0.01~10000Ω) 全部 r=-1.000
        TDOA MAE = 10.1 m, 最差 26.5 m, 全部 < 29 m 验收线

到达检测 (两级, 鲁棒):
    1. 线模电流 ia - ib (对全部故障类型统一, 装置只需 A/B 相电流差)
    2. 二次差分 |d²i/dt²|: 对短路电流斜坡 (一阶差分恒定大) 免疫,
       只响应行波尖峰 -> 高阻/相间故障下不失效
    3. 窗内相对阈值 + 回溯段起点 + 局部峰值 + 抛物线亚采样插值

输入支持:
  (1) HDF5 数据集批量评估 (生成器 --current --dt-us 0.2 输出):
        python predict_tdoa.py tdoa_full/dataset.hdf5
  (2) 双端波形 CSV (实际装置接口, 每行一个时间步):
        python predict_tdoa.py --src src_i.csv --ldr ldr_i.csv --dt-us 0.2
        (CSV 首行为表头或纯数据; 两文件时间对齐, 采样间隔 --dt-us)

输出:
    批量: 按类型/电阻分组的定位误差报告 + 达标判定
    单点: 位置估计 (米)
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

# ---------------- 物理参数 (来自 ATP 仿真模型) ----------------
CABLE_LEN = 4800.0          # 电缆全长 (m)
V_MODE3 = 2.31e8            # 最快线模波速 (m/s), MODES 输出 2.30950e8,
                            # 已由 2628 条仿真数据拟合验证分毫不差
DT_US_DEFAULT = 0.2         # 默认采样间隔 (us) = 5MHz


# ============================================================
# 核心检测与定位
# ============================================================
def arrival_second_diff(wf, t_fault, dt, win_us=150, k=0.3):
    """单端电流首波到达时刻 (二次差分 + 抛物线插值).

    wf:     1D 电流波形 (线模 ia-ib)
    t_fault: 故障注入采样点 (确定检测窗口起点; 短窗数据恒 0)
    dt:     采样间隔 (s)
    win_us: 检测窗口 (故障后若干 us, 覆盖行波首波, 不含反射)
    返回:   首波到达时刻 (s)
    """
    i0 = t_fault
    i1 = min(len(wf) - 1, t_fault + int(win_us * 1e-6 / dt))
    seg = wf[i0:i1 + 1]
    d2 = np.abs(np.diff(seg, n=2))              # 二次差分: 只留行波尖峰
    if d2.max() == 0:
        return (i0 + 1) * dt
    thr = k * d2.max()
    j = int(np.argmax(d2 > thr)) if (d2 > thr).any() else int(np.argmax(d2))
    for jj in range(j, 0, -1):                  # 回溯超阈值段起点
        if d2[jj] < thr:
            j = jj + 1
            break
    for jj in range(j, len(d2) - 1):            # 段内首个局部峰值
        if d2[jj] >= d2[jj + 1]:
            j = jj
            break
    peak = i0 + j
    if 1 <= j <= len(d2) - 2:                   # 抛物线亚采样插值
        y0, y1, y2 = d2[j - 1], d2[j], d2[j + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            peak = i0 + j + 0.5 * (y0 - y2) / den
    return peak * dt


def detect_fault_start(cur, dt, mode='auto'):
    """检测故障注入采样点 (窗口起点).

    mode='auto' : 电流突变量启动 —— 源端三相差分能量首个超阈值的局部峰,
                  对高阻/相间故障鲁棒 (用一阶差分能量包络, 不用全局最大)。
    mode=0      : 已知注入时刻在 0 (短窗仿真数据)。
    返回: 采样点索引
    """
    if mode == 0:
        return 0
    d = np.abs(np.diff(cur[0:3], axis=0)).sum(axis=1)   # 三相差分能量
    d = np.convolve(d, np.ones(5) / 5, mode='same')     # 5 点平滑去噪
    thr = 0.5 * d.max()
    idx = np.flatnonzero(d > thr)
    if len(idx) == 0:
        return int(np.argmax(d))
    first = int(idx[0])
    for j in range(first, 0, -1):               # 回溯到段起点
        if d[j] < thr:
            first = j + 1
            break
    return max(0, first - 10)                   # 留 10 点基线余量


def tdoa_localize(cur, t_fault, dt, v=V_MODE3, cable_len=CABLE_LEN,
                  win_us=150, k=0.3):
    """双端定位: 输入 6 通道电流 [源A B C, 负荷A B C], 输出位置 (m).

    线模 ia-ib 对全部故障类型统一; Δt = t_ldr - t_src 与故障时刻无关。
    """
    ts = arrival_second_diff(cur[0] - cur[1], t_fault, dt, win_us, k)
    tl = arrival_second_diff(cur[3] - cur[4], t_fault, dt, win_us, k)
    dlt = tl - ts
    pos = (cable_len - v * dlt) / 2.0
    # 物理约束: 位置必在 [0, L] 内 (检测失败时给出边界值便于发现)
    return float(np.clip(pos, 0.0, cable_len)), float(dlt)


# ============================================================
# 批量评估 (HDF5)
# ============================================================
def evaluate_h5(h5_path, detail=10):
    """批量评估 HDF5 数据集, 输出分组统计 + 每样本位置估计明细.

    detail: 打印前 N 条每样本明细 (位置估计 vs 真值); 0 表示全部打印.
    """
    import h5py
    with h5py.File(h5_path, 'r') as f:
        cur = f['currents'][:]
        lab = f['labels'][()]
        t = f['time'][:]
    dt = t[1]
    N = len(lab)
    pos_true = lab['position_m'].astype(np.float64)
    rf = lab['r_fault'].astype(np.float64)
    ftype = np.array([x.decode() for x in lab['fault_type']])
    print(f'批量评估 {h5_path} : {N} 条, dt={dt*1e6:.2f}us ({(1/dt)/1e6:.1f}MHz), '
          f'L={CABLE_LEN:.0f}m')

    dlt = np.zeros(N)
    pos_est = np.zeros(N)
    for i in range(N):
        pos_est[i], dlt[i] = tdoa_localize(cur[i], 0, dt)

    err = np.abs(pos_est - pos_true)
    print(f'\n{"分组":<14} {"n":>5} {"r":>7} {"MAE":>7} {"p95":>7} {"max":>7} {"达标":>6}')
    groups = [('全部', np.ones(N, bool))]
    for ft in ['AG', 'AB', 'ABG', 'ABC']:
        groups.append((ft, ftype == ft))
    for rfv in sorted(set(rf)):
        groups.append((f'r={rfv:g}Ω', rf == rfv))
    for name, mask in groups:
        e = err[mask]
        r = np.corrcoef(dlt[mask], pos_true[mask])[0, 1]
        ok = '✓' if e.mean() < 29 and e.max() < 29 else '✗'
        print(f'{name:<14} {int(mask.sum()):>5} {r:+.3f} {e.mean():>7.1f} '
              f'{np.percentile(e,95):>7.1f} {e.max():>7.1f} {ok:>6}')
    print(f'\n结论: 全部 MAE={err.mean():.1f} m, max={err.max():.1f} m, '
          f'验收 29m -> {"全部通过 ✓" if err.max() < 29 else "存在超限 ✗"}')

    # ---- 每样本位置估计明细 ----
    n_show = N if detail == 0 else min(detail, N)
    print(f'\n=== 每样本明细 (位置估计 vs 真值) 前 {n_show}/{N} 条 ===')
    print(f'{"#":>4} {"类型":>4} {"r(Ω)":>7} {"真值(m)":>8} {"估计(m)":>8} '
          f'{"误差(m)":>7} {"达标":>4}')
    for i in range(n_show):
        ok = '✓' if err[i] < 29 else '✗'
        print(f'{i:>4} {ftype[i]:>4} {rf[i]:>7g} {pos_true[i]:>8.1f} '
              f'{pos_est[i]:>8.1f} {err[i]:>7.1f} {ok:>4}')
    return float(err.mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('h5', nargs='?', default=None,
                    help='HDF5 数据集 (批量评估)')
    ap.add_argument('--src', default=None, help='源端电流 CSV (实际装置接口)')
    ap.add_argument('--ldr', default=None, help='负荷端电流 CSV')
    ap.add_argument('--dt-us', type=float, default=DT_US_DEFAULT,
                    help='采样间隔 (us), 默认 0.2')
    ap.add_argument('--start-us', type=float, default=None,
                    help='故障后窗口起点 (us); 缺省自动检测突变量')
    args = ap.parse_args()

    if args.h5:
        evaluate_h5(args.h5)
        return 0
    if args.src and args.ldr:
        src = np.loadtxt(args.src, delimiter=',', ndmin=2)
        ldr = np.loadtxt(args.ldr, delimiter=',', ndmin=2)
        if src.ndim == 2 and src.shape[1] >= 3:    # 三相 [A B C] 或 [t A B C]
            ia = src[:, -3]; ib = src[:, -2]
            la = ldr[:, -3]; lb = ldr[:, -2]
        else:
            ia, ib = src[:, 0], src[:, 1]
            la, lb = ldr[:, 0], ldr[:, 1]
        cur = np.stack([ia, ib, np.zeros_like(ia), la, lb, np.zeros_like(la)])
        dt = args.dt_us * 1e-6
        tf = detect_fault_start(cur, dt) if args.start_us is None \
            else int(args.start_us * 1e-6 / dt)
        pos, dlt = tdoa_localize(cur, tf, dt)
        print(f'故障时差 Δt = {dlt*1e6:.3f} us')
        print(f'故障位置   = {pos:.1f} m (从源端起算, 电缆全长 {CABLE_LEN:.0f} m)')
        return 0
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
