<script setup lang="ts">
/** 配网 FLISR 结果：定位→隔离→恢复 操作步骤序列 + 转供容量校验。
 *  result.data = { mode:'flisr', feeder_id, feeder_name, fault_section, fault_type,
 *                   steps[{seq, phase, action, switch, why}], restored_kw, unserved_kw,
 *                   load_check, summary, note }
 *  定位铁律：辅助不拍板，所有开关操作需值班调度确认后执行。
 */
import type { FormResult } from '../types'

defineProps<{ result: FormResult }>()

/** 阶段配色：定位蓝 / 隔离橙 / 恢复绿 */
function phaseType(phase: string): 'primary' | 'warning' | 'success' | 'info' {
  if (phase === '定位') return 'primary'
  if (phase === '隔离') return 'warning'
  if (phase === '恢复') return 'success'
  return 'info'
}

function phaseLabel(phase: string): string {
  return phase === '定位' ? '① 定位' : phase === '隔离' ? '② 隔离' : '③ 恢复'
}

/** 未恢复负荷 > 0 → 告警条 */
function hasUnserved(d: any): boolean {
  return !!d && Number(d.unserved_kw) > 0
}
</script>

<template>
  <div class="flisr-result">
    <el-alert v-if="result.data?.error" type="error" :closable="false" :title="result.data.error" />
    <template v-else>
      <div class="result-header">🔧 配网 FLISR 辅助决策</div>

      <div class="report-card">
        <div class="rep-head">
          <span class="rep-feeder">{{ result.data?.feeder_id }} {{ result.data?.feeder_name }}</span>
          <el-tag type="primary" effect="dark" round>{{ result.data?.fault_section }}</el-tag>
          <el-tag
            :type="result.data?.fault_type === '瞬时' ? 'success' : 'danger'"
            effect="plain"
            round
          >{{ result.data?.fault_type }}性故障</el-tag>
        </div>
        <div class="rep-line muted">{{ result.data?.substation }} · 故障区段 {{ result.data?.fault_section }}</div>
      </div>

      <div class="section">
        <div class="section-title">📋 建议操作步骤</div>
        <div class="step-list">
          <div v-for="s in (result.data?.steps ?? [])" :key="s.seq" class="step-row">
            <span class="step-seq">{{ s.seq }}</span>
            <el-tag :type="phaseType(s.phase)" effect="plain" size="small" class="step-phase">
              {{ phaseLabel(s.phase) }}
            </el-tag>
            <span class="step-switch">{{ s.switch }}</span>
            <span class="step-action">{{ s.action }}</span>
            <span class="step-why">{{ s.why }}</span>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">🔋 转供容量校验</div>
        <div v-if="result.data?.load_check" class="load-box">
          <template v-if="result.data.load_check.ok">
            <el-tag type="success" effect="dark" round>可转供</el-tag>
            <span class="load-text">
              转供 <b>{{ result.data.load_check.transferred_kw }} kW</b> 至
              <b>{{ result.data.load_check.neighbor_feeder }}</b> 后，其总负荷
              {{ result.data.load_check.after_total_kw }} kW ≤ 限额
              {{ result.data.load_check.neighbor_limit_kw }} kW（负荷率
              {{ result.data.load_check.rate_pct }}%）
            </span>
          </template>
          <template v-else>
            <el-tag type="danger" effect="dark" round>超限</el-tag>
            <span class="load-text">
              转供后 <b>{{ result.data.load_check.neighbor_feeder }}</b> 总负荷
              {{ result.data.load_check.after_total_kw }} kW 超过限额
              {{ result.data.load_check.neighbor_limit_kw }} kW（负荷率
              {{ result.data.load_check.rate_pct }}%），需先转移负荷/限电后再合联络开关
            </span>
          </template>
        </div>
        <div v-else class="load-box">
          <el-tag type="info" effect="plain" round>无转供</el-tag>
          <span class="load-text">该馈线无联络开关或无可转供下游段，未做容量校验</span>
        </div>
      </div>

      <div class="section">
        <div class="section-title">📊 负荷恢复情况</div>
        <div class="stat-row">
          <div class="stat-item">
            <div class="stat-num green">+{{ result.data?.restored_kw ?? 0 }} kW</div>
            <div class="stat-label">可恢复负荷</div>
          </div>
          <div class="stat-item">
            <div class="stat-num" :class="hasUnserved(result.data) ? 'red' : 'green'">
              {{ result.data?.unserved_kw ?? 0 }} kW
            </div>
            <div class="stat-label">未恢复负荷</div>
          </div>
        </div>
        <div class="summary-line">{{ result.data?.summary }}</div>
      </div>

      <el-alert
        v-if="hasUnserved(result.data)"
        :title="`仍有 ${result.data?.unserved_kw} kW 负荷未能恢复供电，请安排抢修或转移负荷。`"
        type="warning"
        :closable="false"
        show-icon
      />

      <div class="disclaimer">⚠️ 以上为辅助决策建议，所有开关操作需值班调度确认后执行。</div>
    </template>
  </div>
</template>

<style scoped>
.flisr-result {
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
.rep-feeder {
  font-size: 17px;
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
.step-list {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.step-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
  border-bottom: 1px solid #f1f5f9;
}
.step-row:last-child {
  border-bottom: none;
}
.step-seq {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.step-phase {
  flex-shrink: 0;
}
.step-switch {
  flex-shrink: 0;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-weight: 600;
  color: #1e293b;
}
.step-action {
  flex-shrink: 0;
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 6px;
  color: #475569;
}
.step-why {
  color: #64748b;
  flex: 1;
}
.load-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  line-height: 1.7;
}
.load-text {
  font-size: 12px;
  color: #334155;
}
.stat-row {
  display: flex;
  gap: 16px;
  margin-bottom: 10px;
}
.stat-item {
  flex: 1;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}
.stat-num {
  font-size: 20px;
  font-weight: 700;
}
.stat-num.green {
  color: #15803d;
}
.stat-num.red {
  color: #dc2626;
}
.stat-label {
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}
.summary-line {
  font-size: 12px;
  color: #475569;
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 12px;
  line-height: 1.7;
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
@media (max-width: 768px) {
  /* 操作步骤行：手机上换行避免溢出 */
  .step-row {
    flex-wrap: wrap;
    row-gap: 4px;
  }
  .step-why {
    flex-basis: 100%;
    padding-left: 30px;
  }
  .stat-row {
    gap: 10px;
  }
}
</style>
