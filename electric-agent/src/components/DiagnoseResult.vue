<script setup lang="ts">
/** 对话内嵌诊断结果展示（Element Plus 实现）。
 *  用户提交 InteractiveForm 后，结果渲染在消息气泡中。
 */
import type { FormResult } from '../types'
import { clsColor, clsName } from '../constants/detect'

// 模板直接按名使用 result 属性，无需在脚本中持有 props
defineProps<{ result: FormResult }>()

function sevColor(sev: string) {
  if (sev === '报警' || sev === '危急') return '#dc2626'
  if (sev === '预警' || sev === '注意' || sev === '严重' || sev === '一般') return '#f59e0b'
  return '#16a34a'
}

/** 变电诊断严重度/状态 → el-tag 类型色 */
function subTagType(level: string): 'danger' | 'warning' | 'success' | 'info' {
  if (level === '危急' || level === '报警') return 'danger'
  if (level === '注意' || level === '严重' || level === '一般' || level === '预警') return 'warning'
  if (level === '正常') return 'success'
  return 'info'
}

function faultPercent(r: any): number {
  if (!r?.cable_len_m) return 0
  return Math.min(100, Math.max(0, (r.position_m / r.cable_len_m) * 100))
}

const titleMap: Record<string, string> = {
  tdoa: '📍 TDOA 故障定位结果',
  classify: '⚡ 故障类型识别结果',
  prpd: '🔬 局放缺陷诊断结果',
  detect: '🔍 线路缺陷检测结果',
  substation: '🛠️ 变电设备诊断结果',
}
</script>

<template>
  <div class="diagnose-result">
    <div class="result-header">{{ titleMap[result.type] }}</div>

    <!-- TDOA 结果 -->
    <template v-if="result.type === 'tdoa'">
      <div class="result-grid">
        <div class="result-card main">
          <div class="card-label">故障位置</div>
          <div class="card-value big">{{ result.data.position_m }}<span class="unit">m</span></div>
        </div>
        <div class="result-card">
          <div class="card-label">双端时差</div>
          <div class="card-value">{{ result.data.delta_t_us }}<span class="unit">μs</span></div>
        </div>
        <div class="result-card" :class="result.data.within_acceptance ? 'ok' : 'fail'">
          <div class="card-label">验收</div>
          <div class="card-value">{{ result.data.within_acceptance ? '✓ 通过' : '✗ 未通过' }}</div>
        </div>
      </div>

      <!-- 电缆示意图 -->
      <div class="cable-viz">
        <div class="cable-line"></div>
        <div class="cable-end src">源端</div>
        <div class="cable-end ldr">负荷端</div>
        <div class="cable-fault" :style="{ left: `${faultPercent(result.data)}%` }">
          <div class="fault-dot"></div>
          <div class="fault-label">{{ result.data.position_m }}m</div>
        </div>
        <div class="cable-len">全长 {{ result.data.cable_len_m }}m</div>
      </div>
    </template>

    <!-- 故障类型结果 -->
    <template v-else-if="result.type === 'classify'">
      <div class="result-grid">
        <div class="result-card main">
          <div class="card-label">故障类型</div>
          <div class="card-value big">{{ result.data.fault_type }}</div>
        </div>
        <div class="result-card">
          <div class="card-label">置信度</div>
          <div class="card-value">{{ (result.data.confidence * 100).toFixed(1) }}%</div>
        </div>
        <div class="result-card" :class="result.data.rule_agrees_gbdt ? 'ok' : 'warn'">
          <div class="card-label">规则验证</div>
          <div class="card-value">{{ result.data.rule_agrees_gbdt ? '✓ 一致' : '⚠ 不一致' }}</div>
        </div>
      </div>
      <div class="proba-section" v-if="result.data.probabilities">
        <div class="proba-title">概率分布</div>
        <div v-for="(p, cls) in result.data.probabilities" :key="cls" class="proba-row">
          <span class="proba-name">{{ cls }}</span>
          <div class="proba-track">
            <div class="proba-fill" :style="{ width: `${p * 100}%` }"></div>
          </div>
          <span class="proba-val">{{ (p * 100).toFixed(1) }}%</span>
        </div>
      </div>
    </template>

    <!-- 局放缺陷结果 -->
    <template v-else-if="result.type === 'prpd'">
      <div class="result-grid">
        <div class="result-card main">
          <div class="card-label">缺陷类型</div>
          <div class="card-value big">{{ result.data.defect_zh }}<span class="unit">{{ result.data.defect_type }}</span></div>
        </div>
        <div class="result-card">
          <div class="card-label">置信度</div>
          <div class="card-value">{{ (result.data.confidence * 100).toFixed(1) }}%</div>
        </div>
        <div class="result-card" :style="{ borderColor: sevColor(result.data.severity) }">
          <div class="card-label">严重度</div>
          <div class="card-value" :style="{ color: sevColor(result.data.severity) }">{{ result.data.severity }}</div>
        </div>
        <div class="result-card" v-if="result.data.q_max_pc !== undefined">
          <div class="card-label">q_max</div>
          <div class="card-value">{{ result.data.q_max_pc }}<span class="unit">pC</span></div>
        </div>
      </div>
      <div class="proba-section" v-if="result.data.probabilities">
        <div class="proba-title">概率分布</div>
        <div v-for="(p, cls) in result.data.probabilities" :key="cls" class="proba-row">
          <span class="proba-name">{{ cls }}</span>
          <div class="proba-track">
            <div class="proba-fill" :style="{ width: `${p * 100}%` }"></div>
          </div>
          <span class="proba-val">{{ (p * 100).toFixed(1) }}%</span>
        </div>
      </div>
    </template>

    <!-- YOLO 缺陷检测结果 -->
    <template v-else-if="result.type === 'detect'">
      <div class="result-grid">
        <div class="result-card main">
          <div class="card-label">检出缺陷</div>
          <div class="card-value big">{{ result.data.count }}<span class="unit">个</span></div>
        </div>
        <div class="result-card">
          <div class="card-label">推理引擎</div>
          <div class="card-value" style="font-size: 16px">{{ result.data.engine === 'tensorrt' ? 'TensorRT' : 'PyTorch' }}</div>
        </div>
        <div class="result-card">
          <div class="card-label">推理耗时</div>
          <div class="card-value">{{ result.data.latency_ms }}<span class="unit">ms</span></div>
        </div>
      </div>

      <!-- 标注图 -->
      <img v-if="result.data.image_url" :src="result.data.image_url" class="detect-img" alt="标注结果" />

      <!-- 目标列表 -->
      <div v-if="result.data.detections?.length" class="det-list">
        <div
          v-for="(det, i) in result.data.detections"
          :key="i"
          class="det-item"
          :style="{ borderLeftColor: clsColor(det) }"
        >
          <span class="det-dot" :style="{ background: clsColor(det) }"></span>
          <span class="det-name">{{ clsName(det) }}</span>
          <span class="det-conf">{{ (det.confidence * 100).toFixed(1) }}%</span>
          <span class="det-bbox">{{ ((det.bbox as number[]) ?? []).map((v: number) => Math.round(v)).join(', ') }}</span>
        </div>
      </div>
      <el-empty v-else description="未检测到缺陷目标" :image-size="60" style="margin-top: 8px" />
    </template>

    <!-- 变电设备诊断结果（dga / sf6 / ir 子模式） -->
    <template v-else-if="result.type === 'substation'">
      <div v-if="result.data.mode === 'dga'">
        <div class="result-grid">
          <div class="result-card main">
            <div class="card-label">故障类型（三比值）</div>
            <div class="card-value big">{{ result.data.fault_type_zh }}</div>
            <div v-if="result.data.ratio_code" class="code-chip">编码 {{ result.data.ratio_code }}</div>
          </div>
          <div class="result-card" :style="{ borderColor: sevColor(result.data.severity) }">
            <div class="card-label">严重度</div>
            <el-tag :type="subTagType(result.data.severity)" effect="light" round style="margin-top: 6px">
              {{ result.data.severity }}
            </el-tag>
          </div>
          <div class="result-card">
            <div class="card-label">电压等级档</div>
            <div class="card-value" style="font-size: 16px">{{ result.data.voltage_band }}</div>
          </div>
        </div>

        <div v-if="result.data.exceeded_gases && Object.keys(result.data.exceeded_gases).length" class="det-list">
          <div class="proba-title">超注意值气体（μL/L）</div>
          <div v-for="(v, name) in result.data.exceeded_gases" :key="name" class="det-item" style="border-left-color: #f59e0b">
            <span class="det-dot" style="background: #f59e0b"></span>
            <span class="det-name">{{ name }}</span>
            <span class="det-conf">{{ v }}</span>
            <span class="det-bbox">注意值 {{ result.data.attention_values?.[name] ?? '—' }}</span>
          </div>
        </div>

        <div class="advice-box">{{ result.data.advice }}</div>
        <div v-if="result.data.note" class="note-box">💡 {{ result.data.note }}</div>
      </div>

      <div v-else-if="result.data.mode === 'sf6'">
        <div class="result-grid">
          <div class="result-card main">
            <div class="card-label">实测压力</div>
            <div class="card-value big">{{ result.data.measured_pressure_MPa }}<span class="unit">MPa</span></div>
          </div>
          <div class="result-card" :style="{ borderColor: sevColor(result.data.pressure_status) }">
            <div class="card-label">压力状态</div>
            <el-tag :type="subTagType(result.data.pressure_status)" effect="light" round style="margin-top: 6px">
              {{ result.data.pressure_status }}
            </el-tag>
          </div>
          <div class="result-card" :style="{ borderColor: sevColor(result.data.moisture_status) }">
            <div class="card-label">微水状态</div>
            <el-tag :type="subTagType(result.data.moisture_status)" effect="light" round style="margin-top: 6px">
              {{ result.data.moisture_status }}
            </el-tag>
          </div>
        </div>
        <div class="advice-box">{{ result.data.advice }}</div>
      </div>

      <div v-else-if="result.data.mode === 'ir'">
        <div class="result-grid">
          <div class="result-card main">
            <div class="card-label">设备类别</div>
            <div class="card-value big" style="font-size: 20px">{{ result.data.category }}</div>
          </div>
          <div class="result-card" :style="{ borderColor: sevColor(result.data.judgment) }">
            <div class="card-label">判级</div>
            <el-tag :type="subTagType(result.data.judgment)" effect="light" round style="margin-top: 6px">
              {{ result.data.judgment }}
            </el-tag>
          </div>
          <div class="result-card" v-if="result.data.delta_pct !== null && result.data.delta_pct !== undefined">
            <div class="card-label">相对温差 δ</div>
            <div class="card-value" style="font-size: 18px">{{ result.data.delta_pct.toFixed(1) }}<span class="unit">%</span></div>
          </div>
        </div>
        <div class="det-list" style="margin-top: 12px; padding: 10px 14px; line-height: 1.9; font-size: 13px">
          <div>热点温度 T₁ = <b>{{ result.data.hot_temp_c }}℃</b>，正常相对点 T₂ = {{ result.data.ref_temp_c }}℃，参照体 T₀ = {{ result.data.ambient_temp_c }}℃</div>
          <div>温升 τ₁ = {{ result.data.tau1_k }}K<span v-if="result.data.delta_pct !== null && result.data.delta_pct !== undefined">，相对温差 δ = {{ result.data.delta_pct.toFixed(1) }}%</span></div>
        </div>
        <div class="advice-box">{{ result.data.advice }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.diagnose-result {
  margin-top: 12px;
  width: 100%;
  max-width: 650px;
}
.result-header {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
}
.result-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.result-card.main {
  border-color: var(--accent);
  border-width: 2px;
}
.result-card.ok {
  border-color: #16a34a;
  background: #f0fdf4;
}
.result-card.fail {
  border-color: #dc2626;
  background: #fef2f2;
}
.result-card.warn {
  border-color: #f59e0b;
  background: #fffbeb;
}
.card-label {
  font-size: 12px;
  color: var(--muted);
}
.card-value {
  font-size: 20px;
  font-weight: 600;
  margin-top: 4px;
}
.card-value.big {
  font-size: 24px;
  color: var(--accent);
}
.unit {
  font-size: 13px;
  font-weight: 400;
  color: var(--muted);
  margin-left: 2px;
}

/* 电缆示意图 */
.cable-viz {
  position: relative;
  margin-top: 14px;
  height: 72px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid var(--border);
  padding: 16px 24px 0;
}
.cable-line {
  position: absolute;
  left: 24px;
  right: 24px;
  top: 32px;
  height: 4px;
  background: var(--accent);
  border-radius: 2px;
}
.cable-end {
  position: absolute;
  top: 42px;
  font-size: 11px;
  color: var(--muted);
}
.cable-end.src { left: 20px; }
.cable-end.ldr { right: 20px; }
.cable-fault {
  position: absolute;
  top: 12px;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
}
.fault-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #e4572e;
  border: 2px solid #fff;
  box-shadow: 0 0 0 2px #e4572e;
}
.fault-label {
  margin-top: 4px;
  font-size: 11px;
  font-weight: 600;
  color: #e4572e;
  background: #fff;
  padding: 1px 5px;
  border-radius: 4px;
  white-space: nowrap;
}
.cable-len {
  position: absolute;
  bottom: 4px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--muted);
}

/* YOLO 缺陷检测结果 */
.detect-img {
  display: block;
  width: 100%;
  max-width: 620px;
  margin-top: 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
.det-list {
  margin-top: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 0;
}
.det-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-left: 3px solid transparent;
}
.det-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.det-name {
  font-size: 13px;
  font-weight: 600;
  min-width: 40px;
}
.det-conf {
  font-size: 13px;
  color: var(--accent);
  font-weight: 600;
  min-width: 60px;
}
.det-bbox {
  font-size: 12px;
  color: var(--muted);
  font-family: 'SFMono-Regular', Consolas, monospace;
}

/* 概率分布 */
.proba-section {
  margin-top: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.proba-title {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 10px;
}
.proba-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.proba-name {
  width: 50px;
  font-size: 13px;
  font-weight: 500;
}
.proba-track {
  flex: 1;
  height: 8px;
  background: #f3f4f6;
  border-radius: 4px;
  overflow: hidden;
}
.proba-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 4px;
  transition: width 0.3s;
}
.proba-val {
  width: 50px;
  text-align: right;
  font-size: 12px;
  color: var(--muted);
}

/* 变电设备诊断结果 */
.code-chip {
  display: inline-block;
  margin-top: 8px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  color: var(--accent);
  background: #f0f7ff;
  border: 1px solid #bfdbfe;
  border-radius: 5px;
  padding: 2px 8px;
}
.advice-box {
  margin-top: 12px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.8;
  color: #92400e;
}
.note-box {
  margin-top: 10px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
}

/* 移动端：检测条目换行、坐标允许折行 */
@media (max-width: 768px) {
  .det-item {
    flex-wrap: wrap;
    row-gap: 4px;
  }
  .det-bbox {
    flex-basis: 100%;
    padding-left: 20px;
    word-break: break-all;
  }
  .det-conf {
    margin-left: auto;
  }
}
</style>
