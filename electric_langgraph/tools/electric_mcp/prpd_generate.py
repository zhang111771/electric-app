"""
prpd_generate.py — 局放 PRPD (相位分辨局放, φ-q-n) 合成数据生成器 + 基模块

对标规范书:
  - "智能诊断: 自动识别电树、水树、接头松动、绝缘老化等故障类型" (doc_text.txt:240)
  - "基于深度学习优化局放模式识别" (doc_text.txt:290)
  - 放电量阈值 正常<20pC / 预警20~50pC / 报警>50pC (doc_text.txt:233)

背景: 项目无任何 PRPD 实测数据, 且 ATP 只能给传输信道、给不了缺陷源模式 →
必须用统计物理模型合成 φ-q 直方图 (这是规范书"模拟局放"方法论的直接延伸)。
本文件是"基模块"(仿 train_fault_locator.py 被下游 import 的范式):
  - 4 类缺陷的统计生成模型 (相位集中度/半周期不对称/幅值分布形状/放电率 编码类别)
  - 严重度三档 LOW(8~20)/MED(20~50)/HIGH(50~150)pC 直接锚定规范书 L233 阈值
  - 全局固定归一化 log1p(count)/log1p(C_NORM) —— 保留幅值(严重度)信息,
    使"严重度留出"抗泄漏划分有意义
  - 17 维统计描述子 (GBDT 基线用), 严重度/随机双划分
被 train_prpd_cnn.py / predict_prpd.py / export_prpd_sample.py import。

用法:
  python prpd_generate.py --demo          # 冒烟 (每类 50)
  python prpd_generate.py                 # 全量 (每类 2000 -> 8000 样本)
  python prpd_generate.py --out prpd_data/dataset.hdf5 --n 2000 --seed 42
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import h5py

sys.stdout.reconfigure(encoding='utf-8')

# ---------------- 类别与分箱 ----------------
CLASSES = ['TREE', 'WATR', 'JOIN', 'AGNG']
CLASS_CODE = {c: i for i, c in enumerate(CLASSES)}
CLASS_ZH = {'TREE': '电树', 'WATR': '水树', 'JOIN': '接头松动', 'AGNG': '绝缘老化'}

N_PHI = 128                 # 相位分箱 (0~360°)
N_Q = 128                   # 放电量分箱 (0~200 pC)
Q_UB_PC = 200.0
PHI_EDGES = np.linspace(0, 360, N_PHI + 1)
Q_EDGES = np.linspace(0, Q_UB_PC, N_Q + 1)
Q_CENTERS = 0.5 * (Q_EDGES[:-1] + Q_EDGES[1:])
PHI_CENTERS = 0.5 * (PHI_EDGES[:-1] + PHI_EDGES[1:])

# 严重度三档 = 规范书 L233 阈值: <20 正常 / 20~50 预警 / >50 报警
SEV_ORDER = ['LOW', 'MED', 'HIGH']
SEV_BANDS = {'LOW': (8, 20), 'MED': (20, 50), 'HIGH': (50, 150)}

N_CYCLES = 50               # PRPD 观测窗口 (50 工频周期 = 1s)
C_NORM = 1000.0             # 全局归一化参考计数 (写入 h5 attrs 供复现)

NOISE_LEVELS = {'LOW': 0.06, 'MED': 0.18, 'HIGH': 0.45}   # 低量程噪声底 (泊松均值/格)

DEFAULT_N_PER_CLASS = 2000
DEFAULT_OUT = 'prpd_data/dataset.hdf5'
SEED = 42

LABELS_DTYPE = np.dtype([
    ('defect_type', 'S4'), ('severity', 'S4'), ('q_max_pc', 'f8'),
    ('n_pulses', 'i8'), ('asym_coef', 'f8'), ('noise_lvl', 'S4'),
    ('seed', 'i8')])


# ============================================================
# 统计生成模型 (每类可区分但有类内抖动)
# ============================================================
def gen_class_params(cls, rng):
    """按类别采样该类样本的统计参数 (每样本一次, 带类内随机抖动).

    q_shape = Beta(a, b) 形状参数 —— 编码放电量分布形态 (类别判别核心):
      a>b 幅值偏高, a≈b 近似均匀, a<b 幅值偏低 (弥散小脉冲)。
    判别依据全部在形状 (相位集中度 κ / 半周期不对称 asym / 幅值形态 q_shape /
    放电率 λ), 幅值档 (严重度) 四类共用 -> 幅值不泄类。
    """
    u = rng.uniform
    if cls == 'TREE':    # 电树/内部气隙: 对称双翼, 上升沿集中, 幅值偏高
        return dict(phi_pos=45.0 + u(-10, 10), kappa=6.0 + u(-2, 2),
                    q_shape=(2.0 + u(-0.4, 0.4), 1.2 + u(-0.2, 0.2)),
                    asym=0.50 + u(-0.04, 0.04), lam=500.0 * u(0.7, 1.3),
                    scatter=0.02 + u(-0.01, 0.01), pos_mode=None)
    if cls == 'WATR':    # 水树: 类似气隙但更宽更弥散, 轻微不对称
        return dict(phi_pos=50.0 + u(-12, 12), kappa=3.2 + u(-1.0, 1.0),
                    q_shape=(1.2 + u(-0.3, 0.3), 1.2 + u(-0.3, 0.3)),
                    asym=0.46 + u(-0.06, 0.06), lam=260.0 * u(0.7, 1.4),
                    scatter=0.03 + u(-0.01, 0.01), pos_mode=None)
    if cls == 'JOIN':    # 接头松动(表面放电/电晕): 单极性 + 极集中 + 幅值一致
        return dict(phi_pos=90.0 + u(-15, 15), kappa=9.0 + u(-3, 3),
                    q_shape=(9.0 + u(-2, 2), 1.0 + u(-0.3, 0.3)),
                    asym=0.03 + u(-0.02, 0.02), lam=320.0 * u(0.7, 1.3),
                    scatter=0.04 + u(-0.01, 0.01), pos_mode='uniform')
    # AGNG 绝缘老化: 双极性弥散扩散, 幅值偏低的弥散小脉冲, 放电率最低
    return dict(phi_pos=90.0 + u(-25, 25), kappa=0.6 + u(-0.3, 0.3),
                q_shape=(1.0 + u(-0.2, 0.2), 3.0 + u(-0.6, 0.6)),
                asym=0.50 + u(-0.05, 0.05), lam=220.0 * u(0.6, 1.5),
                scatter=0.03 + u(-0.01, 0.01), pos_mode=None)


def _vm_phases(rng, n, center_deg, kappa, mode=None, lo=0.0, hi=360.0):
    """用 von Mises 抽 n 个相位 (度, 0~360); mode='uniform' 改均匀 [lo,hi)."""
    if n <= 0:
        return np.empty(0)
    if mode == 'uniform':
        return rng.uniform(lo, hi, n)
    x = rng.vonmises(0.0, kappa, n)                 # [-pi, pi), numpy1.24 签名 (mu, kappa, size)
    return np.degrees(x + np.deg2rad(center_deg)) % 360.0


def gen_sample(cls, rng):
    """生成一个 PRPD 样本 -> (counts (128,128) float32, labels_dict).

    流程: 抽严重度档(各 1/3) -> q_max 档内对数均匀 -> Poisson 脉冲数 ->
    按不对称分正/负半周期 -> von Mises 抽相位 -> Weibull 抽幅值并缩放使
    实际 max q == q_max(标签与图严格一致) -> 散射脉冲 + 低量程噪声底。
    """
    p = gen_class_params(cls, rng)
    sev = rng.choice(SEV_ORDER)
    q_lo, q_hi = SEV_BANDS[sev]
    q_max = float(np.exp(rng.uniform(np.log(q_lo), np.log(q_hi))))
    n = int(rng.poisson(p['lam'] * N_CYCLES))
    n_pos = int(rng.binomial(n, p['asym']))
    n_neg = n - n_pos

    phi_pos = p['phi_pos']
    phi_neg = (phi_pos + 180.0) % 360.0
    if p['pos_mode'] == 'uniform':                  # JOIN 正半残余: 相位随机的弱放电
        phases = np.concatenate([
            _vm_phases(rng, n_pos, 0.0, 0.0, 'uniform', 0.0, 180.0),
            _vm_phases(rng, n_neg, phi_neg, p['kappa'])])
    else:
        phases = np.concatenate([
            _vm_phases(rng, n_pos, phi_pos, p['kappa']),
            _vm_phases(rng, n_neg, phi_neg, p['kappa'])])
    a, b = p['q_shape']
    mags = q_max * rng.beta(a, b, size=n)           # Beta 幅值形态, 范围 [0, q_max)
    mags[np.argmax(mags)] = q_max                   # 钉住最大脉冲 -> 实际 max q == q_max (标签严格一致)

    n_scat = int(rng.binomial(n, p['scatter']))     # 散射大脉冲 (随机 φ, q~U[0,q_max])
    if n_scat > 0:
        phases = np.concatenate([phases, rng.uniform(0, 360, n_scat)])
        mags = np.concatenate([mags, rng.uniform(0, q_max, n_scat)])

    counts, _, _ = np.histogram2d(phases, mags, bins=[PHI_EDGES, Q_EDGES])

    mu_noise = NOISE_LEVELS[rng.choice(list(NOISE_LEVELS))]   # 低量程噪声底
    low_q = Q_CENTERS < 0.2 * q_max
    floor = rng.poisson(mu_noise, size=(N_PHI, N_Q)) * low_q[None, :]
    counts = counts + floor

    labels = dict(defect_type=cls, severity=sev, q_max_pc=q_max, n_pulses=n,
                  asym_coef=p['asym'], noise_lvl=str(mu_noise)[:4],
                  seed=int(rng.integers(0, 2 ** 31)))
    return counts.astype(np.float32), labels


# ============================================================
# 图像归一化 / 描述子 / 划分
# ============================================================
def normalize_img(counts, c_norm=C_NORM):
    """全局固定归一化 (保留幅值=严重度信息, 不逐图归一化)."""
    return np.clip(np.log1p(counts) / np.log1p(c_norm), 0.0, 1.0).astype(np.float32)


def _circ_stats(ph_sum):
    """相位分布的圆均值/圆标准差 (度). ph_sum: (128,) 每相位格计数."""
    if ph_sum.sum() == 0:
        return 0.0, 0.0
    w = ph_sum / ph_sum.sum()
    ang = np.deg2rad(PHI_CENTERS)
    s, c = (w * np.sin(ang)).sum(), (w * np.cos(ang)).sum()
    r = np.hypot(s, c)
    mean = np.degrees(np.arctan2(s, c)) % 360.0
    std = np.degrees(np.sqrt(max(-2 * np.log(max(r, 1e-12)), 0.0)))
    return float(mean), float(std)


def _weighted_moments(q_hist):
    """按 q 直方图算均值/标准差/偏度/峰度 (加权)."""
    total = q_hist.sum()
    if total == 0:
        return 0.0, 0.0, 0.0, 0.0
    w = q_hist / total
    m = float((w * Q_CENTERS).sum())
    v = float((w * (Q_CENTERS - m) ** 2).sum())
    std = float(np.sqrt(v)) if v > 0 else 0.0
    if std <= 1e-12:
        return m, 0.0, 0.0, 0.0
    z = (Q_CENTERS - m) / std
    skew = float((w * z ** 3).sum())
    kurt = float((w * z ** 4).sum())
    return m, std, skew, kurt


DESCRIPTOR_NAMES = [
    'log_n_total', 'q_max_pos_pc', 'q_max_neg_pc', 'q_mean_pos_pc', 'q_mean_neg_pc',
    'asym_ratio', 'asym_log', 'unipolarity', 'phase_cm_pos_deg', 'phase_cm_neg_deg',
    'phase_std_pos_deg', 'phase_std_neg_deg', 'q_skew', 'q_kurt',
    'occupied_frac', 'entropy', 'symmetry_corr']


def extract_descriptors(counts):
    """从原始 count 图计算 17 维统计描述子 (部署可现算, 固定列序)."""
    counts = np.asarray(counts, np.float64)
    total = counts.sum()
    if total == 0:
        return np.zeros(len(DESCRIPTOR_NAMES))
    ph_sum = counts.sum(axis=1)                       # (128,) 相位分布
    pos = ph_sum[:N_PHI // 2]
    neg = ph_sum[N_PHI // 2:]
    pos_sum, neg_sum = float(pos.sum()), float(neg.sum())
    q_pos = counts[:N_PHI // 2].sum(axis=0)           # 正半周期 q 分布
    q_neg = counts[N_PHI // 2:].sum(axis=0)
    q_all = counts.sum(axis=0)

    def q_max(q_hist):
        nz = np.flatnonzero(q_hist > 0)
        return float(Q_CENTERS[nz].max()) if len(nz) else 0.0

    m_pos, _, _, _ = _weighted_moments(q_pos)
    m_neg, _, _, _ = _weighted_moments(q_neg)
    _, _, q_skew, q_kurt = _weighted_moments(q_all)

    cm_p, cs_p = _circ_stats(np.concatenate([pos, np.zeros(N_PHI // 2)]))
    cm_n, cs_n = _circ_stats(np.concatenate([np.zeros(N_PHI // 2), neg]))

    denom = max(pos_sum, neg_sum, 1e-9)
    unip = min(pos_sum, neg_sum) / denom
    asym_ratio = pos_sum / denom
    w = ph_sum / total
    entropy = float(-(w * np.log2(np.maximum(w, 1e-12))).sum()) / np.log2(N_PHI)

    occupied = float((counts > 0).mean())
    if pos_sum > 0 and neg_sum > 0 and len(pos) > 1:
        sym = float(np.corrcoef(pos, neg[::-1])[0, 1])
        sym = sym if np.isfinite(sym) else 0.0
    else:
        sym = 0.0

    return np.array([
        float(np.log1p(total)), q_max(q_pos), q_max(q_neg), m_pos, m_neg,
        asym_ratio, float(np.log1p(pos_sum) - np.log1p(neg_sum)), unip,
        cm_p, cm_n, cs_p, cs_n, q_skew, q_kurt,
        occupied, entropy, sym], dtype=np.float64)


def make_splits(labels, mode, seed=SEED):
    """抗泄漏划分: 'severity' 测试=全 HIGH (未见最高严重度档, 外推泛化);
    'random' 80/10/10 洗牌. 返回 (train, val, test) 布尔掩码."""
    n = len(labels)
    if mode == 'severity':
        sev = np.array([x.decode() for x in labels['severity']])
        te = sev == 'HIGH'
        rs = np.random.RandomState(seed)
        idx = np.flatnonzero(~te)
        rs.shuffle(idx)
        n_val = max(1, int(0.10 * len(idx)))
        va = np.zeros(n, bool)
        va[idx[:n_val]] = True
        tr = (~te) & (~va)
        return tr, va, te
    rs = np.random.RandomState(seed)
    perm = rs.permutation(n)
    n_tr, n_va = int(0.8 * n), int(0.1 * n)
    tr = np.zeros(n, bool); va = np.zeros(n, bool); te = np.zeros(n, bool)
    tr[perm[:n_tr]] = True
    va[perm[n_tr:n_tr + n_va]] = True
    te[perm[n_tr + n_va:]] = True
    return tr, va, te


# ============================================================
# 数据集构建与自检
# ============================================================
def build_dataset(n_per_class, out_h5, seed=SEED):
    """均衡生成 N=4·n_per_class 样本并写 HDF5 (含自描述 attrs)."""
    rng = np.random.default_rng(seed)
    n = n_per_class * len(CLASSES)
    t0 = time.time()
    with h5py.File(out_h5, 'w') as f:
        f.create_dataset('phi_edges', data=PHI_EDGES)
        f.create_dataset('q_edges', data=Q_EDGES)
        d = f.create_dataset('prpd_counts', (n, N_PHI, N_Q), np.float32,
                             chunks=(min(256, n), N_PHI, N_Q))
        lab = f.create_dataset('labels', (n,), dtype=LABELS_DTYPE)
        for i, cls in enumerate(CLASSES):
            for j in range(n_per_class):
                idx = i * n_per_class + j
                c, l = gen_sample(cls, rng)
                d[idx] = c
                lab[idx] = (l['defect_type'], l['severity'], l['q_max_pc'],
                            l['n_pulses'], l['asym_coef'], l['noise_lvl'], l['seed'])
        f.attrs['classes'] = CLASSES
        f.attrs['n_per_class'] = n_per_class
        f.attrs['c_norm'] = C_NORM
        f.attrs['q_ub_pc'] = Q_UB_PC
        f.attrs['n_phi'] = N_PHI
        f.attrs['n_q'] = N_Q
        f.attrs['sev_bands'] = str(SEV_BANDS)
        f.attrs['n_cycles'] = N_CYCLES
    print(f'  已生成 {n} 样本 -> {out_h5} (耗时 {time.time()-t0:.1f}s)')
    return self_check(out_h5)


def self_check(h5_path):
    """自检: 每类样本数/严重度均衡/图非空/判别模式合理性。

    判别性断言 (合成模型应满足, 否则生成器有误):
      JOIN 单极性 unip < 0.30;  正半周期相位圆std 有序 TREE < WATR < AGNG。
    """
    with h5py.File(h5_path, 'r') as f:
        counts = f['prpd_counts'][:]
        labels = f['labels'][()]
    n = len(labels)
    types = np.array([x.decode() for x in labels['defect_type']])
    sevs = np.array([x.decode() for x in labels['severity']])
    print(f'  自检 {h5_path}: {n} 样本')
    ok = True
    half_std = {}
    for cls in CLASSES:
        m = types == cls
        sub = counts[m]
        nonempty = (sub.sum(axis=(1, 2)) > 0).mean()
        pos_b = sub[:, :N_PHI // 2].sum(axis=(1, 2))
        neg_b = sub[:, N_PHI // 2:].sum(axis=(1, 2))
        unip = float((np.minimum(pos_b, neg_b) / np.maximum(pos_b, neg_b)).mean())
        pstd = []
        for c in sub:
            v = np.zeros(N_PHI)
            v[:N_PHI // 2] = c[:N_PHI // 2].sum(axis=1)
            _, st = _circ_stats(v)
            pstd.append(st)
        half_std[cls] = float(np.mean(pstd))
        sev_frac = {s: float((sevs[m] == s).mean()) for s in SEV_ORDER}
        print(f'  {cls:>4} {CLASS_ZH[cls]}: n={int(m.sum()):>4} '
              f'严重度L/M/H={sev_frac["LOW"]:.0%}/{sev_frac["MED"]:.0%}/{sev_frac["HIGH"]:.0%} '
              f'非空={nonempty:.0%} 单极性={unip:.2f} 正半圆std={half_std[cls]:.1f}°')
        ok &= nonempty > 0.99
    ok &= half_std['JOIN'] > 0  # 占位 (JOIN 用单极性判)
    unip_join = _class_unip(counts, types, 'JOIN')
    ok &= unip_join < 0.30                      # JOIN 单极性
    ok &= half_std['TREE'] < half_std['WATR']   # 电树翼窄于水树
    ok &= half_std['AGNG'] > 45.0               # 老化弥散
    print(f'  自检 {"通过 ✓" if ok else "失败 ✗"}')
    return ok


def _class_unip(counts, types, cls):
    sub = counts[types == cls]
    pos_b = sub[:, :N_PHI // 2].sum(axis=(1, 2))
    neg_b = sub[:, N_PHI // 2:].sum(axis=(1, 2))
    return float((np.minimum(pos_b, neg_b) / np.maximum(pos_b, neg_b)).mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n', type=int, default=DEFAULT_N_PER_CLASS,
                    help=f'每类样本数 (默认 {DEFAULT_N_PER_CLASS})')
    ap.add_argument('--out', default=DEFAULT_OUT, help='输出 HDF5 路径')
    ap.add_argument('--seed', type=int, default=SEED, help='随机种子')
    ap.add_argument('--demo', action='store_true', help='冒烟: 每类 50')
    args = ap.parse_args()

    n = 50 if args.demo else args.n
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f'===== 局放 PRPD 合成数据生成 =====')
    print(f'类别: {", ".join(CLASS_ZH[c] for c in CLASSES)} | 每类 {n} | '
          f'图像 {N_PHI}×{N_Q} | 严重度档锚定规范书 L233 (20/50pC)')
    ok = build_dataset(n, out, args.seed)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
