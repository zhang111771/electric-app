# YOLOv8 线路缺陷检测 MCP 服务

将 YOLOv8 TensorRT 检测能力
封装为 **MCP 服务**, LLM / LangGraph 可直接调用。模型为输电线路缺陷检测:

| 类别 | class_id | 含义 |
|---|---|---|
| `broken` | 0 | 破损 |
| `bird nest` | 1 | 鸟巢 |
| `object` | 2 | 异物 |

TensorRT engine 加速，加载失败自动回退 PyTorch 权重。

## 工具 (Tool)

| Tool | 输入 | 输出 |
|---|---|---|
| `yolo_detect` | 图片路径 + conf/iou/imgsz | 检测框 JSON (类别 + 置信度 + 像素坐标) |
| `yolo_batch_detect` | 图片路径列表 | 每张图检测框 JSON + 汇总 |
| `yolo_visualize` | 图片路径 (+ 输出路径) | 保存标注图, 返回保存路径 |

- `yolo://info` — 资源: 模型类别 / 推理引擎 / 阈值默认值

## 目录结构

```
yolo_mcp/
├── mcp_server.py               # MCP 服务主体 (3 tool + 1 resource)
├── yolo_detect.py              # 核心推理模块 (延迟加载, TensorRT 优先)
├── export.py                   # 模型导出脚本 (原始项目拷贝)
├── .mcp.json                   # Claude Code MCP 配置
├── requirements.txt
├── models/
│   ├── best.engine             # TensorRT 引擎 (GPU 专属, 本机 4060 Ti)
│   └── best.pt                 # PyTorch 权重 (兜底)
└── sample_data/
    └── test_insulator.jpg      # 示例检测图片 (合成)
```

## 启动

```bash
# 依赖 (yolo11py310 环境)
conda activate yolo11py310
pip install "mcp[cli]"   # 若环境缺 mcp 包

# 启动方式 1: 直接运行 (stdio)
python mcp_server.py

# 启动方式 2: mcp CLI
mcp run mcp_server.py
mcp run mcp_server.py --transport sse     # SSE/HTTP 模式
```

## 接入 Claude Code

`.mcp.json` 已配置 `yolo-diag` 服务 (指向 yolo11py310 环境), 复制到项目根或
`~/.claude/` 后重启 Claude Code 自动加载。

**自然语言调用示例**:
```
检测 uploads/line.jpg 的缺陷
对 server/uploads/ 下这 3 张图片批量检测线路缺陷
检测这张图片并画出标注框, 保存为 annotated.jpg
```

## 接入 LangGraph

与 `tools/electric_mcp` 同一模式: 在 `graph/` 下新建 `yolo_tools.py`, 直接调用
`yolo_detect` 模块的函数 (绕过 mcp_server.py 的 os.chdir 副作用), 用
`asyncio.to_thread` 放到线程池, 再注册到 agent 的工具列表。

## 备注

1. `best.engine` 是 TensorRT 引擎, 绑定 GPU 架构与 TRT 版本 (本机 4060 Ti / TRT 11.1),
   换机器后需重新导出: `python export.py --format engine --weights models/best.pt`
2. 模型训练数据为输电线路绝缘子缺陷图片, 现用于输电线路缺陷检测, 类别为 broken / bird nest / object
3. 检测框坐标为像素坐标系 [x1,y1,x2,y2], 与原 FastAPI 服务 `/predict` 一致
