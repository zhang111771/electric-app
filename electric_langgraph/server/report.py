"""server/report.py — 输电线路缺陷检测 HTML 报告生成（纯标准库, 零第三方依赖）。

生成自包含 HTML: 标注图以 base64 data-URI 内嵌, 浏览器直接打开即可预览,
点击报告内「打印 / 另存为 PDF」或 Ctrl+P 导出整洁 PDF。
类别配色对齐 tools/yolo_mcp/yolo_detect.py 的 CLASS_COLORS。
"""
from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

# 类别中文名 / 十六进制色（对齐前端 CLASS_COLOR 与后端 CLASS_COLORS）
CLASS_ZH = {0: "破损", 1: "鸟巢", 2: "异物"}
CLASS_COLOR_HEX = {0: "#dc2626", 1: "#16a34a", 2: "#f59e0b"}

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       color: #1e293b; margin: 0; padding: 32px; background: #fff; line-height: 1.6; }
h1 { font-size: 22px; margin: 0 0 4px; color: #0f172a; }
h2 { font-size: 16px; margin: 24px 0 10px; padding-bottom: 6px;
     border-bottom: 2px solid #e2e8f0; color: #0f172a; }
.meta { font-size: 12px; color: #64748b; margin-bottom: 16px; }
.btn-print { float: right; padding: 8px 16px; font-size: 13px; cursor: pointer;
             background: #2563eb; color: #fff; border: 0; border-radius: 6px; }
.btn-print:hover { background: #1d4ed8; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
                gap: 10px; }
.summary-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; }
.summary-card .label { font-size: 12px; color: #64748b; }
.summary-card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
.class-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.class-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px;
              border-radius: 999px; font-size: 13px; color: #fff; }
.img-block { margin: 10px 0 18px; }
.img-block h3 { font-size: 14px; margin: 0 0 8px; color: #334155; }
.img-block .img-badge { font-size: 12px; color: #64748b; margin-left: 8px; font-weight: 400; }
.img-block img { max-width: 100%; max-height: 560px; border: 1px solid #e2e8f0;
                 border-radius: 8px; display: block; background: #f8fafc; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
th, td { border: 1px solid #e2e8f0; padding: 7px 10px; text-align: left; }
th { background: #f1f5f9; color: #334155; font-weight: 600; }
.clean { color: #16a34a; font-size: 13px; }
.failed { color: #dc2626; font-size: 13px; }
.highlight { color: #dc2626; font-weight: 600; }
.conclusion { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
              padding: 14px 16px; font-size: 13px; margin-top: 8px; }
footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid #e2e8f0;
         font-size: 12px; color: #94a3b8; }
@media print {
  .no-print { display: none !important; }
  body { padding: 0; }
  .img-block img { max-height: none; page-break-inside: avoid; }
  .img-block, .summary-grid { page-break-inside: avoid; }
}
"""


def _base64_jpeg(path: str) -> str:
    """读取 JPEG 文件转 base64 data-URI（Path.read_bytes 兼容中文路径）。"""
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def _cls_zh(det: dict) -> str:
    return CLASS_ZH.get(det["class_id"], det.get("class_name") or f"类别{det['class_id']}")


def _class_stats(batch: dict) -> list[dict]:
    """按类别汇总检出数量。"""
    counts: dict[int, int] = {}
    for r in batch.get("results", []):
        for det in r.get("detections", []):
            counts[det["class_id"]] = counts.get(det["class_id"], 0) + 1
    return [{"class_id": cid, "name": CLASS_ZH.get(cid, f"类别{cid}"),
             "count": n} for cid, n in sorted(counts.items())]


def _det_rows_html(detections: list[dict]) -> str:
    rows = []
    for i, det in enumerate(detections, 1):
        conf = f"{det['confidence'] * 100:.1f}%"
        bbox = ", ".join(str(round(v)) for v in det["bbox"])
        rows.append(
            f"<tr><td>{i}</td><td>{html.escape(_cls_zh(det))}</td>"
            f"<td>{conf}</td><td>{html.escape(bbox)}</td></tr>"
        )
    return (
        "<table><thead><tr><th>序号</th><th>类别</th><th>置信度</th>"
        "<th>坐标框 (x1,y1,x2,y2)</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def build_detect_report(
    batch: dict,
    conf: float = 0.25,
    iou: float = 0.45,
    title: str = "输电线路缺陷检测报告",
    timestamp: str | None = None,
) -> str:
    """由批量检测结果生成自包含 HTML 报告, 返回 HTML 字符串。

    batch 为 detect_visualize_batch 的返回 (results 已补 filename/image_url),
    标注图路径取自各成功项 output_path。
    """
    results = batch.get("results", [])
    total_images = batch.get("total_images", len(results))
    total_detections = batch.get("total_detections", 0)
    total_latency_ms = batch.get("total_latency_ms", 0.0)
    engine = batch.get("engine", "torch")
    engine_zh = "TensorRT" if engine == "tensorrt" else "PyTorch"

    ok_items = [r for r in results if "error" not in r]
    defect_items = [r for r in ok_items if r.get("count", 0) > 0]
    clean_items = [r for r in ok_items if not r.get("count")]
    failed_items = [r for r in results if "error" in r]
    stats = _class_stats(batch)
    high_risk = [r for r in results
                 if any(d["confidence"] >= 0.8 for d in r.get("detections", []))]

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- 摘要区 ----
    class_chips = "".join(
        f'<span class="class-chip" style="background:{CLASS_COLOR_HEX.get(s["class_id"], "#2563eb")}">'
        f'{html.escape(s["name"])} {s["count"]} 处</span>'
        for s in stats
    )
    summary = f"""
    <section>
      <div class="summary-grid">
        <div class="summary-card"><div class="label">检测图片</div><div class="value">{total_images}</div></div>
        <div class="summary-card"><div class="label">检出缺陷</div><div class="value">{total_detections}</div></div>
        <div class="summary-card"><div class="label">存在缺陷图片</div><div class="value">{len(defect_items)}</div></div>
        <div class="summary-card"><div class="label">无缺陷图片</div><div class="value">{len(clean_items)}</div></div>
        <div class="summary-card"><div class="label">推理耗时</div><div class="value">{total_latency_ms:.0f}<span style="font-size:12px;font-weight:400"> ms</span></div></div>
        <div class="summary-card"><div class="label">推理引擎</div><div class="value" style="font-size:16px">{engine_zh}</div></div>
      </div>
      {f'<div class="class-chips">{class_chips}</div>' if class_chips else ""}
    </section>"""

    # ---- 逐图区段 ----
    images_html = []
    for idx, r in enumerate(results, 1):
        name = html.escape(r.get("filename") or Path(r.get("image_path", "")).name or f"图片{idx}")
        if "error" in r:
            images_html.append(
                f'<div class="img-block"><h3>图片 {idx}：{name}'
                f'<span class="img-badge">检测失败</span></h3>'
                f'<p class="failed">{html.escape(r["error"])}</p></div>'
            )
            continue
        dets = r.get("detections", [])
        if dets:
            images_html.append(
                f'<div class="img-block"><h3>图片 {idx}：{name}'
                f'<span class="img-badge">检出 {r.get("count", 0)} 处缺陷 · '
                f'{r.get("latency_ms", 0):.0f}ms</span></h3>'
                f'<img src="{_base64_jpeg(r["output_path"])}" alt="{name}"/>'
                f'{_det_rows_html(dets)}</div>'
            )
        else:
            images_html.append(
                f'<div class="img-block"><h3>图片 {idx}：{name}'
                f'<span class="img-badge">{r.get("latency_ms", 0):.0f}ms</span></h3>'
                f'<img src="{_base64_jpeg(r["output_path"])}" alt="{name}"/>'
                f'<p class="clean">✓ 未检测到缺陷</p></div>'
            )
    images_section = f"<section><h2>检测明细</h2>{''.join(images_html)}</section>"

    # ---- 结论区 ----
    conclusion_parts = [
        f"本次共检测 <b>{total_images}</b> 张图片，其中 <b>{len(defect_items)}</b> 张存在缺陷，"
        f"共检出 <b>{total_detections}</b> 处缺陷。"
    ]
    if stats:
        detail = "、".join(f"{s['name']} {s['count']} 处" for s in stats)
        conclusion_parts.append(f"缺陷类别分布：{detail}。")
    if high_risk:
        risk_names = "、".join(
            html.escape(r.get("filename") or Path(r.get("image_path", "")).name)
            for r in high_risk
        )
        conclusion_parts.append(
            f'<span class="highlight">注意：{len(high_risk)} 张图片存在高置信度缺陷 '
            f'(≥80%)，建议优先安排人工复核：{risk_names}。</span>'
        )
    if failed_items:
        conclusion_parts.append(
            f"另有 {len(failed_items)} 张图片因格式/解码问题未能完成检测，建议确认后重新上传。"
        )
    conclusion = (
        '<section><h2>结论</h2><div class="conclusion">'
        + "<br/>".join(conclusion_parts)
        + "<br/>本报告由 AI 模型自动生成，仅供参考，请结合现场情况综合研判。</div></section>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
  <button class="btn-print no-print" onclick="window.print()">🖨 打印 / 另存为 PDF</button>
  <h1>{html.escape(title)}</h1>
  <div class="meta">生成时间：{html.escape(timestamp)} · 引擎：{engine_zh} · conf={conf} · iou={iou}</div>
</header>
{summary}
{images_section}
{conclusion}
<footer>电网智能体 · 输电线路缺陷检测 · 报告自动生成</footer>
</body>
</html>"""
