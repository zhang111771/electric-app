# syntax=docker/dockerfile:1
# 电网智能体（electric_langgraph）镜像
#
# 默认 CPU 推理：torch 装 CPU 版，YOLO 自动回退 best.pt（PyTorch）。
# GPU 加速（YOLO 走 CUDA）：见 docs/Docker部署.md / docker-compose.gpu.yml，
# 构建时用 --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 覆盖。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# opencv(cv2) 运行所需系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 先装 CPU 版 torch/torchvision：避免后面 ultralytics 把 PyPI 的 CUDA 版 torch
#（约 2GB）当作依赖拉回来。换 GPU 时仅需覆盖 TORCH_INDEX_URL 重新构建。
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --index-url "${TORCH_INDEX_URL}" torch torchvision

# 核心依赖（langgraph / fastapi / asyncpg / langgraph-checkpoint-postgres ...）
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 算法与推理依赖：
#   - electric_mcp 三算法：numpy / scikit-learn / h5py / joblib
#   - YOLO 缺陷检测：ultralytics + opencv(headless，免图形库)
#   - scikit-learn 固定 1.7.2（classify_gbdt.joblib / prpd_gbdt.joblib 用它训练）
#   - tensorrt 刻意不装 → yolo_detect 自动回退 best.pt（见 tools/yolo_mcp/yolo_detect.py）
RUN pip install "openai" "numpy" "joblib" "h5py" \
                "scikit-learn==1.7.2" \
                "opencv-python-headless>=4.8.0" \
                "ultralytics>=8.3.0"

# 应用代码（.dockerignore 已剔除 .env / 本机 TensorRT 引擎 / 缓存 / 运行期数据）
COPY config.py sitecustomize.py ./
COPY graph ./graph
COPY server ./server
COPY data ./data
COPY tools ./tools
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x docker/entrypoint.sh \
    && sed -i 's/\r$//' docker/entrypoint.sh   # 防止 Windows 编辑器带入 CRLF 破坏 bash

# 运行期可写目录（诊断标注图 / HTML 检测报告）
RUN mkdir -p /app/server/uploads /app/server/reports

EXPOSE 6006
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]
