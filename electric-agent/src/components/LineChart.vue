<script setup lang="ts">
/** 内嵌 SVG 折线图：渲染后端返回的 ChartConfig（零外部依赖）。 */
import { computed } from 'vue'
import type { ChartConfig } from '../types'

const props = defineProps<{ chart: ChartConfig }>()

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

const svg = computed(() => {
  const { title, x, series } = props.chart
  if (!series?.length) return ''
  const w = 560
  const h = 200
  const pad = 42
  const all = series.flatMap((s) => s.data).filter((v) => typeof v === 'number')
  if (!all.length) return ''
  const min = Math.min(...all)
  const max = Math.max(...all)
  const range = max - min || 1
  const colors = ['#e4572e', '#2563eb', '#16a34a', '#7c3aed']
  const px = (i: number) => pad + (i * (w - pad * 2)) / Math.max(x.length - 1, 1)
  const py = (v: number) => h - pad - ((v - min) / range) * (h - pad * 2)

  let s = `<svg viewBox="0 0 ${w} ${h}" style="background:#fafafa;border:1px solid #e5e7eb;border-radius:8px" width="100%">`
  // 横向网格 + Y 轴刻度
  for (let g = 0; g <= 4; g++) {
    const y = pad + (g * (h - 2 * pad)) / 4
    const v = max - (g * range) / 4
    s += `<line x1="${pad}" y1="${y}" x2="${w - pad}" y2="${y}" stroke="#e5e7eb"/>`
    s += `<text x="${pad - 6}" y="${y + 4}" font-size="11" fill="#9ca3af" text-anchor="end">${v.toFixed(1)}</text>`
  }
  // 折线
  series.forEach((ser, si) => {
    const c = colors[si % colors.length]
    const pts = ser.data.map((v, i) => `${px(i).toFixed(1)},${py(Number(v)).toFixed(1)}`).join(' ')
    s += `<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round"/>`
    s += `<circle cx="${px(ser.data.length - 1).toFixed(1)}" cy="${py(Number(ser.data[ser.data.length - 1])).toFixed(1)}" r="3" fill="${c}"/>`
  })
  // X 轴刻度（抽样避免拥挤）
  const step = Math.max(1, Math.floor(x.length / 6))
  x.forEach((lab, i) => {
    if (i % step === 0) {
      s += `<text x="${px(i)}" y="${h - 12}" font-size="10" fill="#9ca3af" text-anchor="middle">${esc(lab)}</text>`
    }
  })
  // 图例 + 标题
  series.forEach((ser, si) => {
    const c = colors[si % colors.length]
    s += `<rect x="${w - 180 + si * 90}" y="12" width="10" height="10" fill="${c}"/>`
    s += `<text x="${w - 166 + si * 90}" y="21" font-size="11" fill="#374151">${esc(ser.name)}</text>`
  })
  s += `<text x="${pad}" y="16" font-size="13" font-weight="600" fill="#1f2937">${esc(title ?? '')}</text>`
  s += '</svg>'
  return s
})
</script>

<template>
  <div class="line-chart" v-html="svg" />
</template>
