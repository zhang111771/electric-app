"""
yolo_detect.py — 线路缺陷检测 (YOLOv8 TensorRT) 核心模块

封装 Ultralytics YOLOv8 推理 (TensorRT engine 优先, PyTorch 权重兜底),
供 mcp_server.py 与 LangGraph 工具层复用。模型为输电线路缺陷检测:
  - broken     破损
  - bird nest  鸟巢
  - object     异物

推理为 GPU/CPU 密集操作, asyncio 调用方应通过线程池执行 (见 mcp_server 同步 tool,
FastMCP 会自动放入 worker 线程)。

用法:
  from yolo_detect import detect, visualize, detect_batch, classes
  result = detect("photo.jpg", conf=0.25, iou=0.45)
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# 模型文件 (与 mcp_server.py 同目录下 models/)
MODELS_DIR = Path(__file__).resolve().parent / "models"
DEFAULT_ENGINE = MODELS_DIR / "best.engine"
DEFAULT_WEIGHTS = MODELS_DIR / "best.pt"

IMG_SIZE = 640
CONF_THRESH = 0.25
IOU_THRESH = 0.45

# 标注框颜色 (BGR), 按 class_id 取模
CLASS_COLORS = [
    (0, 0, 255),       # broken     → 红
    (0, 255, 0),       # bird nest  → 绿
    (255, 165, 0),     # object     → 橙
]

# 延迟加载缓存
_model: Any = None
_engine_used: str | None = None


def get_model():
    """首次调用加载模型: 优先 TensorRT engine, 加载失败/缺 tensorrt 回退 PyTorch 权重。"""
    global _model, _engine_used
    if _model is not None:
        return _model
    from ultralytics import YOLO

    # engine 推理需要 tensorrt 包; 缺包时 ultralytics 在 predict 阶段才报错,
    # 这里提前探测, 保证无 tensorrt 的环境 (如 CPU 服务端) 直接走 .pt 兜底。
    use_engine = DEFAULT_ENGINE.exists()
    if use_engine:
        try:
            import tensorrt  # noqa: F401
        except ImportError:
            print("[yolo] tensorrt 未安装, 直接使用 best.pt (torch)")
            use_engine = False

    if use_engine:
        try:
            _model = YOLO(str(DEFAULT_ENGINE), task="detect")
            _engine_used = "tensorrt"
            return _model
        except Exception as e:
            print(f"[yolo] TensorRT engine 加载失败, 回退 best.pt: {e}")
    if not DEFAULT_WEIGHTS.exists():
        raise FileNotFoundError(
            f"模型不存在: {DEFAULT_ENGINE} / {DEFAULT_WEIGHTS}\n"
            f"请先运行: python export.py --format engine"
        )
    _model = YOLO(str(DEFAULT_WEIGHTS), task="detect")
    _engine_used = "torch"
    return _model


def engine_used() -> str:
    """实际使用的推理引擎: 'tensorrt' 或 'torch'。"""
    get_model()
    return _engine_used or "torch"


def classes() -> dict[int, str]:
    """模型类别映射 {class_id: class_name}。"""
    m = get_model()
    return {int(k): v for k, v in m.names.items()}


def read_image(image_path: str) -> np.ndarray:
    """从路径读取 BGR 图像 (np.fromfile + imdecode, 兼容中文/空格路径)。"""
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {p}")
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法解码图片文件: {p}")
    return img


def parse_results(result) -> list[dict]:
    """Ultralytics 结果 → 标准 JSON 检测列表。

    每项: {class_id, class_name, confidence, bbox: [x1,y1,x2,y2]}
    """
    names = classes()
    detections = []
    boxes = result[0].boxes
    if boxes is None or len(boxes) == 0:
        return detections
    for i in range(len(boxes)):
        cls_id = int(boxes.cls[i])
        detections.append({
            "class_id": cls_id,
            "class_name": names.get(cls_id, f"unknown_{cls_id}"),
            "confidence": round(float(boxes.conf[i]), 4),
            "bbox": [round(float(v), 1) for v in boxes.xyxy[i].tolist()],
        })
    return detections


def draw_detections(image: np.ndarray, detections: list[dict]) -> np.ndarray:
    """在 BGR 图像上绘制检测框 + 类别标签。"""
    img = image.copy()
    for det in detections:
        x1, y1, x2, y2 = map(int, det["bbox"])
        cls_id = det["class_id"]
        color = CLASS_COLORS[cls_id % len(CLASS_COLORS)]

        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{det['class_name']} {det['confidence']:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - 22), (x1 + tw, y1), color, -1)
        cv2.putText(img, label, (x1, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return img


def detect(image_path: str, conf: float = CONF_THRESH,
           iou: float = IOU_THRESH, imgsz: int = IMG_SIZE) -> dict:
    """单张图片推理 → JSON 检测结果。"""
    model = get_model()
    image = read_image(image_path)

    t0 = time.perf_counter()
    results = model.predict(
        source=image, imgsz=imgsz, conf=conf, iou=iou, verbose=False,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    detections = parse_results(results)
    return {
        "image_path": str(Path(image_path).resolve()),
        "image_size": list(image.shape[:2][::-1]),  # [w, h]
        "detections": detections,
        "count": len(detections),
        "latency_ms": latency_ms,
        "engine": engine_used(),
    }


def detect_batch(image_paths: list[str], conf: float = CONF_THRESH,
                 iou: float = IOU_THRESH, imgsz: int = IMG_SIZE) -> dict:
    """批量多张图片推理 → 每张的检测结果 + 汇总。"""
    model = get_model()
    results_list = []
    total_latency = 0.0
    for image_path in image_paths:
        try:
            image = read_image(image_path)
            t0 = time.perf_counter()
            results = model.predict(
                source=image, imgsz=imgsz, conf=conf, iou=iou, verbose=False,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            total_latency += elapsed
            detections = parse_results(results)
            results_list.append({
                "image_path": str(Path(image_path).resolve()),
                "detections": detections,
                "count": len(detections),
                "latency_ms": round(elapsed, 2),
            })
        except Exception as e:
            results_list.append({
                "image_path": str(image_path),
                "error": str(e),
            })
    return {
        "total_images": len(image_paths),
        "total_detections": sum(r.get("count", 0) for r in results_list),
        "total_latency_ms": round(total_latency, 2),
        "engine": engine_used(),
        "results": results_list,
    }


def detect_visualize_batch(
    image_paths: list[str],
    output_dir: str | Path | None = None,
    conf: float = CONF_THRESH,
    iou: float = IOU_THRESH,
    imgsz: int = IMG_SIZE,
    max_side: int = 1280,
) -> dict:
    """批量多图推理, 一次推理同时产出 JSON 检测结果 + 标注 JPEG 图。

    与 detect_batch 相比多输出每张的标注图 (output_path);
    与 detect_batch + 循环 visualize 相比避免重复推理 (2N 次 → N 次),
    适合批量检测 + 报告生成的场景。

    output_dir 缺省时输出到 MODELS_DIR/annotated; 单张失败不影响其余,
    失败项记录 {"image_path", "error"}。超大图按 max_side 缩放后再画框,
    控制标注图与 HTML 报告体积。
    """
    model = get_model()
    if output_dir is None:
        output_dir = MODELS_DIR / "annotated"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results_list = []
    total_latency = 0.0
    for image_path in image_paths:
        try:
            image = read_image(image_path)
            h, w = image.shape[:2]
            t0 = time.perf_counter()
            results = model.predict(
                source=image, imgsz=imgsz, conf=conf, iou=iou, verbose=False,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            total_latency += elapsed
            detections = parse_results(results)

            vis_img = image
            if max_side and max(h, w) > max_side:
                scale = max_side / max(h, w)
                vis_img = cv2.resize(image, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_AREA)
            annotated = draw_detections(vis_img, detections)

            out = out_dir / f"{uuid.uuid4().hex[:12]}_annotated.jpg"
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise ValueError("JPEG 编码失败")
            buf.tofile(str(out))

            results_list.append({
                "image_path": str(Path(image_path).resolve()),
                "image_size": [w, h],
                "detections": detections,
                "count": len(detections),
                "latency_ms": round(elapsed, 2),
                "output_path": str(out.resolve()),
            })
        except Exception as e:
            results_list.append({
                "image_path": str(image_path),
                "error": str(e),
            })
    return {
        "total_images": len(image_paths),
        "total_detections": sum(r.get("count", 0) for r in results_list),
        "total_latency_ms": round(total_latency, 2),
        "engine": engine_used(),
        "results": results_list,
    }


def visualize(image_path: str, output_path: str | None = None,
              conf: float = CONF_THRESH, iou: float = IOU_THRESH,
              imgsz: int = IMG_SIZE) -> dict:
    """检测并绘制标注框保存为 JPEG 图片。

    output_path 缺省时在源图片同目录生成 "<原名>_annotated.jpg"。
    """
    model = get_model()
    image = read_image(image_path)
    results = model.predict(
        source=image, imgsz=imgsz, conf=conf, iou=iou, verbose=False,
    )
    detections = parse_results(results)
    annotated = draw_detections(image, detections)

    if not output_path:
        src = Path(image_path)
        output_path = str(src.with_name(f"{src.stem}_annotated.jpg"))
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("JPEG 编码失败")
    buf.tofile(str(out))

    return {
        "output_path": str(out.resolve()),
        "detections": detections,
        "count": len(detections),
        "engine": engine_used(),
    }
