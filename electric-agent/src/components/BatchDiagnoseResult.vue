<script setup lang="ts">
/** 对话内嵌 YOLO 线路缺陷【批量】检测结果展示（Element Plus 实现）。
 *  与单张 DiagnoseResult 区分：批次汇总 + 逐图折叠卡片 + HTML 报告入口。
 */
import { computed, ref } from 'vue'
import type { DetectBatchData, DetectBatchResultItem, FormResult } from '../types'
import { clsColor, clsIdName, clsName } from '../constants/detect'

const props = defineProps<{ result: FormResult }>()

const d = computed<DetectBatchData>(() => props.result.data)
const items = computed<DetectBatchResultItem[]>(() => d.value.results ?? [])
const okItems = computed(() => items.value.filter(r => !r.error))
const failItems = computed(() => items.value.filter(r => r.error))
const defectItems = computed(() => okItems.value.filter(r => r.count > 0))

// 折叠面板默认全部收起，多图时避免一次性渲染过多大图
const activeNames = ref<string[]>([])

/** 按类别汇总检出数量（用于顶部色块） */
const classStats = computed(() => {
  const m: Record<number, number> = {}
  for (const r of okItems.value) {
    for (const det of r.detections) {
      m[det.class_id] = (m[det.class_id] ?? 0) + 1
    }
  }
  return Object.entries(m)
    .map(([cid, count]) => ({ classId: Number(cid), count }))
    .sort((a, b) => b.count - a.count)
})

function basename(p: string) {
  return p.split(/[\\/]/).pop() ?? p
}

function displayName(r: DetectBatchResultItem, i: number) {
  return r.filename || basename(r.image_path) || `图片 ${i + 1}`
}

function openReport() {
  if (d.value.report_url) window.open(d.value.report_url, '_blank')
}
</script>

<template>
  <div class="batch-result">
    <div class="result-header">🔍 线路缺陷批量检测结果</div>

    <!-- 批次汇总 -->
    <div class="result-grid">
      <div class="result-card main">
        <div class="card-label">检测图片</div>
        <div class="card-value big">{{ d.total_images }}<span class="unit">张</span></div>
      </div>
      <div class="result-card">
        <div class="card-label">检出缺陷</div>
        <div class="card-value big">{{ d.total_detections }}<span class="unit">个</span></div>
      </div>
      <div class="result-card" :class="defectItems.length ? 'warn' : 'ok'">
        <div class="card-label">存在缺陷</div>
        <div class="card-value">{{ defectItems.length }}<span class="unit">张</span></div>
      </div>
      <div class="result-card">
        <div class="card-label">推理引擎</div>
        <div class="card-value" style="font-size: 16px">{{ d.engine === 'tensorrt' ? 'TensorRT' : 'PyTorch' }}</div>
      </div>
      <div class="result-card">
        <div class="card-label">推理耗时</div>
        <div class="card-value">{{ d.total_latency_ms }}<span class="unit">ms</span></div>
      </div>
    </div>

    <!-- 类别分布 -->
    <div v-if="classStats.length" class="class-chips">
      <span
        v-for="s in classStats"
        :key="s.classId"
        class="class-chip"
        :style="{ background: clsColor({ class_id: s.classId }) }"
      >
        {{ clsIdName(s.classId) }} {{ s.count }} 处
      </span>
    </div>

    <!-- 报告入口 -->
    <div class="report-actions">
      <el-button type="primary" @click="openReport">📄 查看 / 下载检测报告</el-button>
      <span class="report-hint">自包含 HTML，可在新标签页打印 / 另存为 PDF</span>
    </div>

    <!-- 逐图折叠卡片 -->
    <el-collapse v-model="activeNames" class="img-collapse">
      <el-collapse-item
        v-for="(r, i) in okItems"
        :key="i"
        :name="String(i)"
        :title="`图片 ${i + 1}：${displayName(r, i)}${r.count ? ` — 检出 ${r.count} 处` : ' — 无缺陷'}`"
      >
        <img v-if="r.image_url" :src="r.image_url" class="det-img" loading="lazy" alt="标注结果" />
        <div v-if="r.detections?.length" class="det-list">
          <div
            v-for="(det, j) in r.detections"
            :key="j"
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
      </el-collapse-item>
    </el-collapse>

    <!-- 失败项 -->
    <el-alert
      v-if="failItems.length"
      :title="`${failItems.length} 张图片检测失败：${failItems.map(r => r.filename || basename(r.image_path) || '未命名').join('、')}`"
      type="warning"
      :closable="false"
      show-icon
      style="margin-top: 12px"
    />
  </div>
</template>

<style scoped>
.batch-result {
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
.class-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.class-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 13px;
  color: #fff;
}
.report-actions {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.report-hint {
  font-size: 12px;
  color: var(--muted);
}
.img-collapse {
  margin-top: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.det-img {
  display: block;
  width: 100%;
  max-width: 600px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
.det-list {
  margin-top: 10px;
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
@media (max-width: 768px) {
  .report-actions {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }
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
