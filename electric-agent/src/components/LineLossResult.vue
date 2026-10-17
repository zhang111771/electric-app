<script setup lang="ts">
/** 台区线损结果：calc 线损率判定卡 / analyze 台区分析卡（两子模式）。
 *  result.data = { mode:'line_loss', sub_mode:'calc'|'analyze', ... }
 *  客观计算（线损率/均值/趋势）明确判级；涉窃电/漏电一律用疑似/疑点措辞（辅助不拍板）。
 */
import type { FormResult } from '../types'

defineProps<{ result: FormResult }>()

const d = (r: FormResult) => r.data

/** 判定 → 风险色：正常绿 / 偏高橙 / 超标·计量异常·疑似红 / 居中信息 */
function judgmentTone(j?: string): 'success' | 'warning' | 'danger' | 'info' {
  const s = j || ''
  if (/正常/.test(s)) return 'success'
  if (/偏高/.test(s)) return 'warning'
  if (/超标|异常|疑似|窃电|漏电|计量/.test(s)) return 'danger'
  return 'info'
}

/** 疑点配色：疑似窃电红 / 疑似漏电橙 / 计量异常紫 */
function suspType(s?: string): 'danger' | 'warning' | 'primary' {
  const t = s || ''
  if (/窃电/.test(t)) return 'danger'
  if (/漏电/.test(t)) return 'warning'
  return 'primary'
}

function overallTone(o?: string): 'success' | 'warning' | 'danger' | 'info' {
  const s = o || ''
  if (/正常/.test(s)) return 'success'
  if (/偏高/.test(s)) return 'warning'
  return 'danger'
}
</script>

<template>
  <div class="line-loss-result">
    <el-alert v-if="result.data?.error" type="error" :closable="false" :title="result.data.error" />
    <template v-else-if="result.data?.sub_mode === 'calc'">
      <div class="result-header">📉 台区线损率计算</div>

      <div class="report-card">
        <div class="rep-head">
          <span class="rep-rate">{{ d(result)?.loss_rate_pct }}%</span>
          <el-tag :type="judgmentTone(d(result)?.judgment)" effect="dark" round>
            {{ d(result)?.judgment }}
          </el-tag>
        </div>
        <div class="rep-line muted">
          供电 {{ d(result)?.supply_kwh }} kWh − 售电 {{ d(result)?.sales_kwh }} kWh
          = 损失 {{ d(result)?.loss_kwh }} kWh
        </div>
        <div class="rep-line muted">
          台区容量 {{ d(result)?.capacity_kva }}kVA，合理线损率上限 {{ d(result)?.uplimit_pct }}%
        </div>
      </div>

      <div class="section">
        <div class="section-title">💡 建议</div>
        <div v-for="(s, i) in (d(result)?.advice ?? [])" :key="i" class="tip-line green">▸ {{ s }}</div>
      </div>

      <div class="disclaimer">⚠️ {{ d(result)?.note }}</div>
    </template>

    <template v-else-if="result.data?.sub_mode === 'analyze'">
      <div class="result-header">📉 台区线损分析报告</div>

      <div class="report-card">
        <div class="rep-head">
          <span class="rep-feeder">{{ d(result)?.area_no }} {{ d(result)?.area_name }}</span>
          <el-tag :type="overallTone(d(result)?.overall)" effect="dark" round>
            {{ d(result)?.overall }}
          </el-tag>
          <el-tag type="info" effect="plain" round>状态：{{ d(result)?.status }}</el-tag>
        </div>
        <div class="rep-line muted">
          {{ d(result)?.n_readings }} 天样本 · 平均线损率
          <b>{{ d(result)?.avg_rate }}%</b>（上限 {{ d(result)?.uplimit_pct }}%）· 趋势<b>{{ d(result)?.trend }}</b>
        </div>
      </div>

      <div v-if="(d(result)?.anomaly_days ?? []).length" class="section">
        <div class="section-title">🚩 异常日（{{ d(result)?.anomaly_days.length }} 天）</div>
        <div class="anomaly-list">
          <div v-for="(a, i) in d(result)?.anomaly_days ?? []" :key="i" class="anomaly-row">
            <span class="anomaly-date">{{ a.stat_date }}</span>
            <span class="anomaly-rate">{{ a.rate }}%</span>
            <el-tag type="danger" effect="plain" size="small">{{ a.reason }}</el-tag>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">🔍 异常疑点</div>
        <div v-if="(d(result)?.suspicions ?? []).length" class="susp-list">
          <div v-for="(s, i) in d(result)?.suspicions ?? []" :key="i" class="susp-row">
            <el-tag :type="suspType(s.type)" effect="dark" round size="small">{{ s.type }}</el-tag>
            <span class="susp-evidence">{{ s.evidence }}</span>
          </div>
        </div>
        <div v-else class="susp-row none">未发现明显异常疑点。</div>
      </div>

      <div class="section">
        <div class="section-title">💡 建议</div>
        <div v-for="(s, i) in (d(result)?.suggestions ?? [])" :key="i" class="tip-line green">▸ {{ s }}</div>
      </div>

      <div class="disclaimer">⚠️ 线损率/均值为客观计算；"窃电/漏电"为疑似判断，需现场核实后确定处理措施。</div>
    </template>
  </div>
</template>

<style scoped>
.line-loss-result {
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
.report-card {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.rep-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.rep-rate {
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
}
.rep-feeder {
  font-size: 16px;
  font-weight: 700;
  color: var(--accent);
}
.rep-line {
  font-size: 13px;
  color: #334155;
  line-height: 1.8;
}
.rep-line.muted {
  color: var(--muted);
}
.section {
  margin-top: 14px;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 8px;
}
.tip-line {
  font-size: 12px;
  line-height: 1.7;
  padding: 4px 0;
  color: #15803d;
}
.anomaly-list {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.anomaly-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  font-size: 12px;
  border-bottom: 1px solid #f1f5f9;
}
.anomaly-row:last-child {
  border-bottom: none;
}
.anomaly-date {
  font-family: 'SFMono-Regular', Consolas, monospace;
  color: #475569;
  width: 90px;
}
.anomaly-rate {
  font-weight: 700;
  color: #dc2626;
  width: 50px;
}
.susp-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.susp-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  line-height: 1.7;
}
.susp-row.none {
  color: var(--muted);
  font-size: 12px;
}
.susp-evidence {
  font-size: 12px;
  color: #334155;
  flex: 1;
}
.disclaimer {
  margin-top: 14px;
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border: 1px dashed #f59e0b;
  border-radius: 6px;
  padding: 8px 10px;
  line-height: 1.7;
}
</style>
