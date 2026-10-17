"""
YOLOv8 模型导出脚本

支持的格式: torchscript, onnx, openvino, engine (TensorRT),
            coreml, pb, tflite, edgetpu, tfjs, paddle, ncnn, imx, rknn

用法:
  # 导出为 ONNX (通用)
  python export.py --format onnx

  # 导出为 TensorRT Engine (NVIDIA GPU, 最快)
  python export.py --format engine

  # 导出为 OpenVINO (Intel CPU/VPU)
  python export.py --format openvino
"""
import argparse
from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(description="YOLOv8 Model Export")
    parser.add_argument("--weights", type=str, default="runs/detect/runs/detect/exp-9/weights/best.pt",
                        help="Path to best.pt")
    parser.add_argument("--format", type=str, default="onnx",
                        choices=["torchscript", "onnx", "openvino", "engine",
                                 "coreml", "pb", "tflite", "edgetpu",
                                 "tfjs", "paddle", "ncnn", "imx", "rknn"],
                        help="Export format")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--half", action="store_true", help="FP16 (ONNX/TensorRT/CoreML)")
    parser.add_argument("--int8", action="store_true", help="INT8 quantization")
    parser.add_argument("--dynamic", action="store_true",
                        help="Dynamic input shape (ONNX/TensorRT)")
    parser.add_argument("--simplify", action="store_true", default=True,
                        help="Simplify ONNX model")

    args = parser.parse_args()

    model = YOLO(args.weights)
    path = model.export(
        format=args.format,
        imgsz=args.imgsz,
        half=args.half,
        int8=args.int8,
        dynamic=args.dynamic,
        simplify=args.simplify,
    )
    print(f"\nExported to: {path}")


if __name__ == "__main__":
    main()
