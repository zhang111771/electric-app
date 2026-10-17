"""
train_prpd_cnn.py — 局放 PRPD 缺陷识别: 2D-CNN 训练 + GBDT 基线, 双划分评估

对标规范书:
  "基于深度学习优化局放模式识别" (doc_text.txt:290)
  "自动识别电树、水树、接头松动、绝缘老化等故障类型" (doc_text.txt:240)

输入: prpd_data/dataset.hdf5 (由 prpd_generate.py 生成, 8000 样本 4×2000)

训练循环逐条复刻 train_fault_locator_cnn (SEED/DEVICE/Adam/早停/深拷贝 best_state)。
GBDT 基线复刻 classify_fault 超参 (HistGradientBoosting, max_iter=400)。
双划分评估: 'severity' (test=全 HIGH, 未见最高档外推) + 'random' 80/10/10。

输出:
  prpd_cnn.pt              CNN 模型 (state_dict + classes + 归一化常量 + 分箱)
  prpd_gbdt.joblib         GBDT 模型 (dict: model/feature_names/classes, 对齐 artifact 约定)
  控制台: 两划分×两模型 4×4 混淆矩阵 + classification_report + 汇总表
          噪声敏感性报告 (按噪声档分层)

用法:
  python train_prpd_cnn.py --demo            # 冒烟: 每类 100 / 5 epoch
  python train_prpd_cnn.py                   # 全量
  python train_prpd_cnn.py --save            # 保存模型
  python train_prpd_cnn.py --h5 prpd_data/dataset.hdf5
"""

import argparse
import copy
import json
import sys
import time
from pathlib import Path

import numpy as np
import h5py
import joblib
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix, classification_report, accuracy_score

from prpd_generate import (
    CLASSES, CLASS_CODE, CLASS_ZH, SEV_ORDER, SEV_BANDS,
    N_PHI, N_Q, normalize_img, extract_descriptors, DESCRIPTOR_NAMES,
    make_splits, LABELS_DTYPE)

sys.stdout.reconfigure(encoding='utf-8')

# 训练超参 (逐条复刻 train_fault_locator_cnn)
SEED = 42
MAX_EPOCH = 40
PATIENCE = 8
BATCH = 128
LR = 1e-3
WEIGHT_DECAY = 1e-5

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
if DEVICE.type == 'cpu':
    torch.set_num_threads(min(16, max(1, (torch.multiprocessing.cpu_count() or 8) // 2)))


# ============================================================
# CNN 架构 (~135k 参数)
# ============================================================
class PRPDNet(nn.Module):
    """2D-CNN: 3 层 conv+bn+relu+pool → 全局池化 → 两层 MLP 分类."""

    def __init__(self, n_classes=4):
        super().__init__()
        self.features = nn.Sequential(
            # Conv1: 1→32, k5 s2 p2, 输入 1×128×128 → 64×64
            nn.Conv2d(1, 32, 5, stride=2, padding=2),
            nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),                        # → 32×32
            # Conv2: 32→64, k5 s2 p2 → 16×16
            nn.Conv2d(32, 64, 5, stride=2, padding=2),
            nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),                        # → 16×16
            # Conv3: 64→128, k3 (无下采样) → 14×14
            nn.Conv2d(64, 128, 3, padding=1),
            nn.BatchNorm2d(128), nn.ReLU(inplace=True),
        )
        self.classifier = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(128, 64), nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(64, n_classes),
        )

    def forward(self, x):
        return self.classifier(self.features(x))


# ============================================================
# 数据加载
# ============================================================
def load_data(h5_path):
    """加载 HDF5, 返回 counts (归一化后 float32), labels 复合数组, 元信息."""
    with h5py.File(h5_path, 'r') as f:
        counts = f['prpd_counts'][:]
        labels = f['labels'][()]
        c_norm = float(f.attrs['c_norm'])
        classes = list(f.attrs['classes'])
    return counts, labels, c_norm, classes


def prepare_tensors(counts, c_norm):
    """counts → (N,1,128,128) float32 张量 (通道 1 为 PRPD 归一化图)."""
    norm = normalize_img(counts, c_norm)
    return torch.from_numpy(norm[:, None, :, :]).to(DEVICE)


def get_labels_int(labels):
    """复合 labels → 整数类别标签."""
    return np.array([CLASS_CODE[x.decode()] for x in labels['defect_type']])


# ============================================================
# 训练循环 (逐条复刻 train_fault_locator_cnn)
# ============================================================
def train_cnn(model, tr_idx, va_idx, x_all, y_all, epochs=MAX_EPOCH, patience=PATIENCE,
              lr=LR, verbose=True):
    """手写 batch 循环, Adam + 早停(验证准确率最大化), 深拷贝 best_state."""
    torch.manual_seed(SEED); np.random.seed(SEED)
    x_tr, y_tr = x_all[tr_idx], torch.from_numpy(y_all[tr_idx].astype(np.int64)).to(DEVICE)
    x_va, y_va = x_all[va_idx], torch.from_numpy(y_all[va_idx].astype(np.int64)).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=WEIGHT_DECAY)
    best_acc = 0.0
    best_state = None
    bad = 0
    t0 = time.time()
    for ep in range(1, epochs + 1):
        model.train()
        perm = np.random.permutation(len(y_tr))
        total_loss, correct, n_seen = 0.0, 0, 0
        for b0 in range(0, len(y_tr), BATCH):
            bi = perm[b0:b0 + BATCH]
            xb, yb = x_tr[bi], y_tr[bi]
            opt.zero_grad()
            logits = model(xb)
            loss = F.cross_entropy(logits, yb)
            loss.backward()
            opt.step()
            total_loss += float(loss) * len(yb)
            correct += (logits.argmax(1) == yb).sum().item()
            n_seen += len(yb)
        tr_loss = total_loss / n_seen
        tr_acc = correct / n_seen
        model.eval()
        with torch.no_grad():
            va_logits = model(x_va)
        va_acc = float((va_logits.argmax(1) == y_va).float().mean())
        if verbose:
            print(f'  epoch {ep:02d}/{epochs}  tr_loss={tr_loss:.4f} tr_acc={tr_acc:.3f} '
                  f'va_acc={va_acc:.4f}  [{time.time()-t0:.1f}s]')
        if va_acc > best_acc:
            best_acc = va_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                print(f'  早停: {patience} epoch 未改善, 最优 va_acc={best_acc:.4f}')
                break
    model.load_state_dict(best_state)
    return model, best_acc


# ============================================================
# 预测 & 评估
# ============================================================
def predict_idx(model, x_all, idx, chunk=512):
    """批量预测, 返回 logits 和 argmax (仿 train_fault_locator_cnn)."""
    model.eval()
    outs, idxs = [], []
    with torch.no_grad():
        for b0 in range(0, len(idx), chunk):
            bi = idx[b0:b0 + chunk]
            outs.append(model(x_all[bi]).cpu())
            idxs.append(bi)
    logits = torch.cat(outs, dim=0)
    return logits.numpy(), np.concatenate(idxs)


def evaluate(y_true, y_pred, labels=CLASSES, tag=''):
    """4×4 混淆矩阵 + classification_report(digits=4) + 总体准确率."""
    if tag:
        print(f'\n===== {tag} =====')
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(labels))))
    print('混淆矩阵:')
    header = '       ' + ''.join(f'{labels[j]:>7}' for j in range(len(labels)))
    print(header)
    for i in range(len(labels)):
        print(f'  {labels[i]:>5} ' + ''.join(f'{cm[i, j]:7d}' for j in range(len(labels))))
    cr = classification_report(y_true, y_pred, target_names=labels, digits=4, zero_division=0)
    print(cr)
    acc = float(accuracy_score(y_true, y_pred))
    print(f'  总体准确率: {acc:.4f}')
    return acc


def report_by_group(y_true, y_pred, labels, field_values, tag='', mask=None):
    """按 field_values 分组打印准确率 (如按严重度/噪声档分层)."""
    if mask is None:
        mask = np.ones(len(y_true), bool)
    groups = sorted(set(field_values[mask]))
    print(f'\n===== {tag} (按 {field_values.dtype.names if hasattr(field_values, "dtype") else "field"} 分组) =====')
    header = f'{"group":>10} {"n":>6} {"acc":>8}'
    print(header)
    for g in groups:
        gm = (field_values == g) & mask
        if gm.sum() == 0:
            continue
        acc = float(accuracy_score(y_true[gm], y_pred[gm]))
        print(f'{str(g):>10} {int(gm.sum()):>6} {acc:>8.4f}')


# ============================================================
# 训练流水线
# ============================================================
def run_split(h5_path, mode, demo=False, save=False):
    """单种划分: 训练 CNN + GBDT, 打印评估 + 噪声分层, 可选保存."""
    print(f'\n{"=" * 60}\n划分模式: {mode} ({("全量" if not demo else "冒烟")})')
    counts, labels, c_norm, classes = load_data(h5_path)
    if demo:
        n_take = 100
        keep = np.zeros(len(labels), bool)
        for cls in classes:
            m = np.array([x.decode() for x in labels['defect_type']]) == cls
            n_cls = int(m.sum())
            take = min(n_take, n_cls)
            idx = np.flatnonzero(m)[:take]
            keep[idx] = True
        counts, labels = counts[keep], labels[keep]
    y = get_labels_int(labels)
    tr, va, te = make_splits(labels, mode)
    print(f'  样本: {tr.sum()} train / {va.sum()} val / {te.sum()} test')

    # CNN
    print('\n--- CNN 训练 ---')
    x_all = prepare_tensors(counts, c_norm)
    torch.manual_seed(SEED); np.random.seed(SEED)
    model = PRPDNet(len(classes)).to(DEVICE)
    model, best_va = train_cnn(model, tr, va, x_all, y, epochs=5 if demo else MAX_EPOCH)
    print(f'  CNN best va_acc = {best_va:.4f}')

    # CNN 评估 (全索引预测, tr/te 用掩码切)
    all_idx = np.arange(len(y))
    logits, _ = predict_idx(model, x_all, all_idx)
    pred_all = logits.argmax(1)
    y_tr, pred_tr = y[tr], pred_all[tr]
    y_te, pred_te = y[te], pred_all[te]
    print(f'\n  CNN train acc = {accuracy_score(y_tr, pred_tr):.4f}')
    evaluate(y_te, pred_te, labels=classes, tag=f'CNN test ({mode})')

    # 噪声分层 (h5 中有 noise_lvl 字段)
    noise_vals = np.array([x.decode() for x in labels['noise_lvl']])
    report_by_group(y, pred_all, classes, noise_vals, tag=f'CNN ({mode}) 按噪声档')

    # GBDT 基线
    print('\n--- GBDT 基线 (17 维描述子) ---')
    X = np.stack([extract_descriptors(c) for c in counts])
    gbdt = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.1, max_leaf_nodes=31,
        early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=20, random_state=42)
    gbdt.fit(X[tr], y[tr])
    print(f'  GBDT 训练 n_iter = {gbdt.n_iter_}')
    pred_te_g = gbdt.predict(X[te])
    evaluate(y[te], pred_te_g, labels=classes, tag=f'GBDT test ({mode})')
    report_by_group(y, gbdt.predict(X), classes, noise_vals, tag=f'GBDT ({mode}) 按噪声档')

    # 保存
    if save:
        out_dir = Path(h5_path).parent
        # CNN: 保存完整 artifact (state_dict + metadata)
        cnn_path = out_dir / 'prpd_cnn.pt'
        torch.save({
            'state_dict': model.state_dict(),
            'classes': classes, 'c_norm': c_norm,
            'n_phi': N_PHI, 'n_q': N_Q,
        }, cnn_path)
        print(f'\n  CNN 已保存 -> {cnn_path}')
        # GBDT: 对齐 artifact 约定 (model/feature_names/classes)
        gbdt_path = out_dir / 'prpd_gbdt.joblib'
        joblib.dump({
            'model': gbdt, 'feature_names': DESCRIPTOR_NAMES,
            'classes': classes,
        }, gbdt_path)
        print(f'  GBDT 已保存 -> {gbdt_path}')

    return {'cnn_va': best_va}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--h5', default='prpd_data/dataset.hdf5')
    ap.add_argument('--demo', action='store_true', help='冒烟: 每类 100, 5 epoch')
    ap.add_argument('--save', action='store_true', help='保存模型 artifact')
    ap.add_argument('--no-gbdt', action='store_true', help='跳过 GBDT (快跑 CNN)')
    args = ap.parse_args()

    torch.manual_seed(SEED); np.random.seed(SEED)
    print('===== 局放 PRPD 缺陷识别训练 =====')
    print(f'  DEVICE={DEVICE} | classes={CLASSES}')
    print(f'  CNN params ≈ {sum(p.numel() for p in PRPDNet().parameters()):,}')

    # 严重度留出 (test=全 HIGH, 未见最高严重度档)
    run_split(args.h5, 'severity', demo=args.demo, save=args.save)
    # 随机划分 (80/10/10)
    run_split(args.h5, 'random', demo=args.demo, save=args.save)

    print('\n===== 完成 =====')
    if not args.demo:
        print('  诚实声明:')
        print('   合成数据 = 可行性上界, 现场以规范书第三阶段"模拟局放+实地比对"为准')
        print('  ② 随机划分可能因"亮度=严重度"虚高, 严重度留出才是真实形状泛化 → 并排对比')
        print('  ③ 规范书无准确率考核值, 建议: 缺陷类型识别准确率 ≥90%, 测试方法 4×4 混淆矩阵')
    return 0


if __name__ == '__main__':
    sys.exit(main())
