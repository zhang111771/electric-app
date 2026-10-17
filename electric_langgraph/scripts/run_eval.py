"""诊断工具客观评测脚本（RL 数据闭环的基础）。

读取 data/eval/ground_truth.json，逐条调用对应诊断工具，与真值对比，
输出汇总指标 + 逐样本结果到 data/eval/reports/eval_<timestamp>.json。

用法：
    python scripts/run_eval.py                       # 跑全部
    python scripts/run_eval.py --tools tdoa,classify # 只跑指定工具
    python scripts/run_eval.py --latest              # 只打印最近一份报告的摘要

指标：
    - TDOA: MAE / MaxAE / 验收线通过率 (29m)
    - classify: accuracy / 混淆矩阵
    - PRPD: accuracy / 各类 recall
    - YOLO detect: 检出准确率（有真值框时）/ 正常样本无误报率
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import config  # noqa: E402
from graph.cable_tools import (  # noqa: E402
    fault_location_tdoa, fault_type_classify, line_defect_detect,
    prpd_defect_classify,
)

GT_PATH = ROOT / "data" / "eval" / "ground_truth.json"
REPORT_DIR = ROOT / "data" / "eval" / "reports"


def _resolve(p: str) -> str:
    """相对路径以项目根为基准解析，返回绝对路径字符串（给工具调用）。"""
    path = Path(p)
    return str(path if path.is_absolute() else ROOT / path)


# ============================================================
# 各工具评测函数
# ============================================================

async def eval_tdoa(samples: list[dict]) -> dict:
    """TDOA 定位：统计 MAE / MaxAE / 验收线通过率。"""
    errors = []
    details = []
    for s in samples:
        result = await fault_location_tdoa.ainvoke({
            "src_csv": _resolve(s["src_csv"]),
            "ldr_csv": _resolve(s["ldr_csv"]),
            "dt_us": s.get("dt_us", 0.2),
        })
        if "error" in result:
            details.append({"id": s["id"], "error": result["error"]})
            continue
        pred = float(result["position_m"])
        true = float(s["true_position_m"])
        err = abs(pred - true)
        errors.append(err)
        details.append({
            "id": s["id"],
            "pred_m": round(pred, 2),
            "true_m": true,
            "abs_error_m": round(err, 2),
            "within_acceptance": result.get("within_acceptance"),
        })

    n = len(errors)
    summary = {
        "n_total": len(samples),
        "n_success": n,
        "mae_m": round(float(np.mean(errors)), 2) if errors else None,
        "max_ae_m": round(float(np.max(errors)), 2) if errors else None,
        "acceptance_pass_rate": None,
        "threshold_m": config.TDOA_ACCEPTANCE_M if hasattr(config, "TDOA_ACCEPTANCE_M") else 29,
    }
    if errors:
        passed = sum(1 for e in errors if e <= summary["threshold_m"])
        summary["acceptance_pass_rate"] = round(passed / n, 4)
    return {"summary": summary, "details": details}


async def eval_classify(samples: list[dict]) -> dict:
    """故障类型分类：accuracy + 混淆矩阵（行=真值，列=预测）。"""
    classes = ["AG", "AB", "ABG", "ABC"]
    correct = 0
    total = 0
    confusion = {c: {c2: 0 for c2 in classes} for c in classes}
    details = []
    for s in samples:
        result = await fault_type_classify.ainvoke({
            "waveform_csv": _resolve(s["waveform_csv"]),
            "rule_node": s.get("rule_node", "LDR"),
        })
        if "error" in result:
            details.append({"id": s["id"], "error": result["error"]})
            continue
        pred = result["fault_type"]
        true = s["true_type"]
        total += 1
        if pred == true:
            correct += 1
        if true in confusion and pred in confusion[true]:
            confusion[true][pred] += 1
        details.append({
            "id": s["id"],
            "pred": pred,
            "true": true,
            "correct": pred == true,
            "confidence": round(float(result.get("confidence", 0)), 4),
            "rule_agrees": result.get("rule_agrees_gbdt"),
        })
    summary = {
        "n_total": len(samples),
        "n_success": total,
        "accuracy": round(correct / total, 4) if total else None,
        "confusion_matrix": confusion,
    }
    return {"summary": summary, "details": details}


async def eval_prpd(samples: list[dict]) -> dict:
    """PRPD 局放缺陷识别：accuracy + 各类 recall。"""
    classes = ["TREE", "WATR", "JOIN", "AGNG"]
    correct = 0
    total = 0
    per_class_tp = {c: 0 for c in classes}
    per_class_total = {c: 0 for c in classes}
    details = []
    for s in samples:
        result = await prpd_defect_classify.ainvoke({
            "prpd_csv": _resolve(s["prpd_csv"]),
            "engine": s.get("engine", "gbdt"),
        })
        if "error" in result:
            details.append({"id": s["id"], "error": result["error"]})
            continue
        pred = result["defect_type"]
        true = s["true_defect"]
        total += 1
        if pred == true:
            correct += 1
            per_class_tp[true] = per_class_tp.get(true, 0) + 1
        per_class_total[true] = per_class_total.get(true, 0) + 1
        details.append({
            "id": s["id"],
            "pred": pred,
            "true": true,
            "correct": pred == true,
            "confidence": round(float(result.get("confidence", 0)), 4),
            "severity": result.get("severity"),
        })
    recall = {}
    for c in classes:
        t = per_class_total.get(c, 0)
        recall[c] = round(per_class_tp[c] / t, 4) if t else None
    summary = {
        "n_total": len(samples),
        "n_success": total,
        "accuracy": round(correct / total, 4) if total else None,
        "per_class_recall": recall,
    }
    return {"summary": summary, "details": details}


async def eval_detect(samples: list[dict]) -> dict:
    """YOLO 缺陷检测：当前种子集只有正常样本，先统计无误报率。

    后续补充带真实标注框的样本后，可扩展 mAP / precision / recall。
    """
    fp_samples = 0  # 正常样本但检出了缺陷（误报）
    total_normal = 0
    details = []
    for s in samples:
        result = await line_defect_detect.ainvoke({
            "image_path": _resolve(s["image"]),
            "conf": s.get("conf", 0.25),
            "iou": s.get("iou", 0.45),
        })
        if "error" in result:
            details.append({"id": s["id"], "error": result["error"]})
            continue
        pred_count = int(result.get("count", 0))
        true_count = int(s.get("true_count", 0))
        if true_count == 0:
            total_normal += 1
            if pred_count > 0:
                fp_samples += 1
        details.append({
            "id": s["id"],
            "pred_count": pred_count,
            "true_count": true_count,
            "false_positive": (true_count == 0 and pred_count > 0),
        })
    summary = {
        "n_total": len(samples),
        "n_success": len(details),
        "normal_sample_fpr": round(fp_samples / total_normal, 4) if total_normal else None,
        "note": "当前仅统计正常样本误报率；补充带框真值后可扩展 mAP。",
    }
    return {"summary": summary, "details": details}


EVALUATORS = {
    "tdoa": eval_tdoa,
    "classify": eval_classify,
    "prpd": eval_prpd,
    "detect": eval_detect,
}


# ============================================================
# 主流程
# ============================================================

def load_ground_truth() -> dict:
    if not GT_PATH.exists():
        raise FileNotFoundError(f"真值文件不存在: {GT_PATH}")
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


async def run_eval(tools: list[str] | None = None) -> dict:
    """跑全量或指定工具的评测，返回完整报告 dict。"""
    gt = load_ground_truth()
    samples_by_tool: dict[str, list[dict]] = {"tdoa": [], "classify": [], "prpd": [], "detect": []}
    for s in gt["samples"]:
        t = s["tool"]
        if t not in samples_by_tool:
            continue
        if tools and t not in tools:
            continue
        samples_by_tool[t].append(s)

    results: dict = {}
    for tool, evaluator in EVALUATORS.items():
        if tools and tool not in tools:
            continue
        if not samples_by_tool[tool]:
            results[tool] = {"summary": {"n_total": 0, "n_success": 0},
                             "details": [], "note": "无样本"}
            continue
        results[tool] = await evaluator(samples_by_tool[tool])

    report = {
        "version": gt.get("version", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tools_tested": list(results.keys()),
        "results": results,
    }
    return report


def save_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"eval_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同时维护一份 latest.json
    (REPORT_DIR / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_summary(report: dict) -> None:
    print(f"\n{'='*60}")
    print(f" 评测报告  version={report.get('version')}  time={report.get('timestamp')}")
    print(f"{'='*60}")
    for tool, data in report.get("results", {}).items():
        s = data.get("summary", {})
        print(f"\n【{tool}】  样本 {s.get('n_total')}  成功 {s.get('n_success')}")
        if tool == "tdoa":
            print(f"  MAE = {s.get('mae_m')} m   MaxAE = {s.get('max_ae_m')} m")
            print(f"  验收线通过率 = {s.get('acceptance_pass_rate')} "
                  f"(阈值 {s.get('threshold_m')}m)")
        elif tool in ("classify", "prpd"):
            print(f"  Accuracy = {s.get('accuracy')}")
        elif tool == "detect":
            print(f"  正常样本误报率 = {s.get('normal_sample_fpr')}")
    print(f"\n{'='*60}\n")


def main() -> None:
    import asyncio
    ap = argparse.ArgumentParser(description="诊断工具客观评测")
    ap.add_argument("--tools", default=None,
                    help="只跑指定工具，逗号分隔（tdoa,classify,prpd,detect）")
    ap.add_argument("--latest", action="store_true",
                    help="只打印最近一份报告的摘要，不重新评测")
    args = ap.parse_args()

    if args.latest:
        latest = REPORT_DIR / "latest.json"
        if not latest.exists():
            print("暂无评测报告")
            return
        print_summary(json.loads(latest.read_text(encoding="utf-8")))
        return

    tools = [t.strip() for t in args.tools.split(",")] if args.tools else None
    t0 = time.time()
    report = asyncio.run(run_eval(tools))
    path = save_report(report)
    print_summary(report)
    print(f"报告已保存: {path}  (耗时 {time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
