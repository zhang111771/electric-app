"""
mcp_server.py — 线路缺陷检测 MCP 服务 (YOLOv8 TensorRT)

对外暴露 3 个 tool, 封装 tools/electric_langgraph/ 下 TensorRT 检测能力:
  - yolo_detect       单张图片检测 → JSON (类别 / 置信度 / 坐标框)
  - yolo_batch_detect 批量图片检测 → JSON
  - yolo_visualize    检测 + 绘制标注框保存为图片, 返回保存路径

模型: 输电线路缺陷检测 (broken 破损 / bird nest 鸟巢 / object 异物)
TensorRT engine 加速，加载失败自动回退 PyTorch 权重。

启动:
  mcp run mcp_server.py                     # FastMCP dev 模式 (stdio)
  mcp run mcp_server.py --transport sse     # SSE 模式 (HTTP)
  python mcp_server.py                      # 直接运行 (stdio)

配置: 把 .mcp.json 复制到项目根或 ~/.claude/, 或在 Claude Code 内执行
  /mcp add yolo-diag python <你的python解释器>
         <本目录>/mcp_server.py

依赖 (yolo11py310 环境已安装):
  mcp[cli]  ultralytics  tensorrt  opencv-python  numpy
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')

# 项目根 (mcp_server.py 所在目录)
PROJECT_ROOT = Path(__file__).parent.resolve()
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.mcpserver.server import MCPServer

from yolo_detect import (
    CONF_THRESH, IMG_SIZE, IOU_THRESH,
    classes, detect, detect_batch, engine_used, visualize,
)

# ============================================================
# 工具函数
# ============================================================

def _resolve(path: str) -> Path:
    """相对路径以 yolo_mcp 目录为基准, 绝对路径原样返回。"""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _check_image(path: str) -> Path | dict:
    """校验图片路径存在, 返回 Path; 非法时返回 {'error': ...}。"""
    p = _resolve(path)
    if not p.exists():
        return {'error': f'图片不存在: {p}'}
    if not p.is_file():
        return {'error': f'不是文件: {p}'}
    return p


# ============================================================
# MCP 服务定义
# ============================================================
mcp = MCPServer(
    'yolo-diag',
    description='线路缺陷检测 MCP 服务 (YOLOv8 TensorRT: 破损 / 鸟巢 / 异物)',
)


# ------------------------------------------------------------
# Tool 1: 单张图片检测
# ------------------------------------------------------------
@mcp.tool()
def yolo_detect(
    image_path: str,
    conf: float = CONF_THRESH,
    iou: float = IOU_THRESH,
    imgsz: int = IMG_SIZE,
) -> dict:
    """输电线路缺陷检测 (单张图片).

    识别 3 类缺陷: broken 破损 / bird nest 鸟巢 / object 异物.
    TensorRT engine 加速, 返回每个检测框的类别 + 置信度 + 像素坐标.

    Args:
        image_path: 图片路径 (相对 tools/yolo_mcp/ 或绝对路径, jpg/png/bmp/webp)
        conf: 置信度阈值, 默认 0.25
        iou: NMS IoU 阈值, 默认 0.45
        imgsz: 推理输入尺寸, 默认 640

    Returns:
        dict: image_path, image_size [w,h], detections[{class_id,class_name,
              confidence,bbox:[x1,y1,x2,y2]}], count, latency_ms, engine
    """
    checked = _check_image(image_path)
    if isinstance(checked, dict):
        return checked
    try:
        return detect(str(checked), conf=conf, iou=iou, imgsz=imgsz)
    except Exception as e:
        return {'error': f'检测失败: {e}'}


# ------------------------------------------------------------
# Tool 2: 批量图片检测
# ------------------------------------------------------------
@mcp.tool()
def yolo_batch_detect(
    image_paths: list[str],
    conf: float = CONF_THRESH,
    iou: float = IOU_THRESH,
    imgsz: int = IMG_SIZE,
) -> dict:
    """批量检测多张图片 (线路缺陷).

    每张图片返回检测框列表; 单张失败不影响其余图片, 错误记录在对应项.

    Args:
        image_paths: 图片路径列表 (相对 tools/yolo_mcp/ 或绝对路径)
        conf: 置信度阈值, 默认 0.25
        iou: NMS IoU 阈值, 默认 0.45
        imgsz: 推理输入尺寸, 默认 640

    Returns:
        dict: total_images, total_detections, total_latency_ms, engine,
              results[{image_path, detections, count, latency_ms}]
    """
    paths = []
    for path in image_paths:
        checked = _check_image(path)
        if isinstance(checked, dict):
            paths.append(str(_resolve(path)))
        else:
            paths.append(str(checked))
    try:
        return detect_batch(paths, conf=conf, iou=iou, imgsz=imgsz)
    except Exception as e:
        return {'error': f'批量检测失败: {e}'}


# ------------------------------------------------------------
# Tool 3: 检测 + 绘制标注框
# ------------------------------------------------------------
@mcp.tool()
def yolo_visualize(
    image_path: str,
    output_path: str | None = None,
    conf: float = CONF_THRESH,
    iou: float = IOU_THRESH,
    imgsz: int = IMG_SIZE,
) -> dict:
    """检测并绘制标注框, 保存为 JPEG 图片.

    适合需要把检测结果可视化展示给用户 (前端 <img> 直接引用 output_path).

    Args:
        image_path: 源图片路径 (相对 tools/yolo_mcp/ 或绝对路径)
        output_path: 标注图保存路径; 缺省在源图同目录生成 "<原名>_annotated.jpg"
        conf: 置信度阈值, 默认 0.25
        iou: NMS IoU 阈值, 默认 0.45
        imgsz: 推理输入尺寸, 默认 640

    Returns:
        dict: output_path, count, detections, engine
    """
    checked = _check_image(image_path)
    if isinstance(checked, dict):
        return checked
    try:
        out = str(_resolve(output_path)) if output_path else None
        return visualize(str(checked), output_path=out, conf=conf, iou=iou, imgsz=imgsz)
    except Exception as e:
        return {'error': f'可视化检测失败: {e}'}


# ------------------------------------------------------------
# 资源: 暴露模型信息供 LLM 参考
# ------------------------------------------------------------
@mcp.resource('yolo://info')
def yolo_info() -> str:
    """模型信息: 类别 + 引擎 + 阈值默认值."""
    try:
        cls = classes()
        names = ', '.join(f'{k}:{v}' for k, v in sorted(cls.items()))
        eng = engine_used()
    except Exception as e:
        names, eng = 'N/A', f'未加载 ({e})'
    return f"""线路缺陷检测 (YOLOv8)
类别: {names}
推理引擎: {eng} (TensorRT 优先, 失败回退 PyTorch)
默认阈值: conf={CONF_THRESH}, iou={IOU_THRESH}, imgsz={IMG_SIZE}
检测框坐标: 像素坐标系 [x1,y1,x2,y2]
"""


# ============================================================
# 入口
# ============================================================
if __name__ == '__main__':
    mcp.run()
