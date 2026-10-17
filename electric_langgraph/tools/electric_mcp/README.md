# 高压电缆故障诊断 MCP 服务

面向《高压电缆状态监测系统设备技术规范书》(内蒙古电力, 2026) 的
**故障诊断 MCP 服务**, 封装 3 条技术路线为 LLM 可调用 tool。

| Tool | 输入 | 输出 | 精度 |
|---|---|---|---|
| `fault_location_tdoa` | 源端/负荷端电流 CSV | 位置(m) + 时差(μs) | MAE 10.1m, 验收线 29m |
| `fault_type_classify` | 9 通道电压 CSV 或 14 维特征 JSON | 故障类型(AG/AB/ABG/ABC) + 概率 + 规则交叉验证 | 仿真 1.000 |
| `prpd_defect_classify` | PRPD φ-q CSV 或 17 维描述子 CSV | 缺陷类型(TREE/WATR/JOIN/AGNG) + 严重度 + 概率 | GBDT 0.997 / CNN 0.898 |

## 目录结构

```
electric_mcp/
├── mcp_server.py               # MCP 服务主体 (3 tool + 2 resource)
├── .mcp.json                   # Claude Code MCP 配置
├── predict_tdoa.py             # TDOA 定位核心
├── classify_fault.py           # 故障类型分类
├── predict_prpd.py             # 局放缺陷识别
├── prpd_generate.py            # PRPD 生成器 (基模块)
├── train_prpd_cnn.py           # PRPD CNN 架构
├── train_fault_locator.py      # 故障定位特征提取
├── classify_gbdt.joblib        # 故障类型模型
├── prpd_data/                  # 局放模型
│   ├── prpd_cnn.pt
│   └── prpd_gbdt.joblib
├── sample_data/                # 测试样本
│   ├── waveforms.csv           # 9 通道电压 (故障类型)
│   ├── tdoa_test_*_src/ldr.csv # 双端电流 (TDOA)
└── sample_data_prpd/
    └── prpd.csv                # PRPD φ-q 矩阵 (局放缺陷)
```

## 启动

```bash
# 依赖 (lucy-edit 环境)
pip install "mcp[cli]" torch scikit-learn h5py numpy joblib

# 启动方式 1: 直接运行 (stdio)
python mcp_server.py

# 启动方式 2: mcp CLI
mcp run mcp_server.py
mcp run mcp_server.py --transport sse     # SSE/HTTP 模式
```

## 接入 Claude Code

`.mcp.json` 已配置 `cable-diag` 服务, 重启 Claude Code 后自动加载。

**自然语言调用示例**:
```
帮我用 TDOA 定位 sample_data/tdoa_test_0_src.csv 和 tdoa_test_0_ldr.csv
识别 sample_data/waveforms.csv 的故障类型
识别 sample_data_prpd/prpd.csv 的局放缺陷类型和严重度
双引擎(CNN+GBDT)交叉验证 prpd.csv
```

## 资源 (Resource)

- `cable://system-info` — 系统信息 (电缆参数 + 算法能力 + 验收指标)
- `cable://acceptance` — 验收指标一览

## 算法报告

完整算法推导与验证见项目文档 `doc/`:
- `doc/TDOA行波测距算法与仿真验证.md`
- `doc/局放PRPD模式识别算法报告.md`
- `doc/局放源TDOA定位可行性分析.md`

## 诚实声明

1. 模型在合成/仿真数据上训练, 现场准确率以规范书第三阶段"模拟局放+实地比对+第三方检测"为准
2. TDOA 定位精度 10.1m 已在 2628 条仿真数据验证 (4 类故障×73 位置×4 档电阻)
3. 故障类型分类在仿真下饱和 (1.000), 现场可能需重训
4. PRPD 识别 GBDT 0.997 / CNN 0.898 是严重度留出 (test=全 HIGH) 泛化准确率
