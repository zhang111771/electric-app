<script setup lang="ts">
/** 调度事故研判结果：推理链 + 影响范围 + 处置建议 + 待确认问题。
 *  result.data = { mode:'dispatch', event_no, fault_type_zh, scope, reasoning[],
 *                   suggestions[], questions[], summary, judge_status }
 *  定位铁律：只出建议与疑问（辅助不拍板），不下最终事故定性。
 */
import type { FormResult } from '../types'

defineProps<{ result: FormResult }>()

/** 故障类型 → 风险色：永久/拒动/危急类红，瞬时/已恢复绿，无法判定橙 */
function faultTypeTone(t?: string): 'danger' | 'success' | 'warning' | 'info' {
  const s = t || ''
  if (/拒动|永久|危急|区内|变压器|重瓦斯/.test(s)) return 'danger'
  if (/瞬时|已恢复|重合闸成功/.test(s)) return 'success'
  if (/无法判定|待确认/.test(s)) return 'warning'
  return 'warning'
}

function statusType(s?: string) {
  if (s === '已处置') return 'info'
  if (s === '已研判') return 'success'
  return 'warning'
}
</script>

<template>
  <div class="dispatch-result">
    <el-alert v-if="result.data?.error" type="error" :closable="false" :title="result.data.error" />
    <template v-else>
      <div class="result-header">🚨 调度事故研判报告</div>

      <div class="report-card">
        <div class="rep-head">
          <div class="rep-no">{{ result.data?.event_no }}</div>
          <el-tag :type="faultTypeTone(result.data?.fault_type_zh)" effect="dark" round>
            {{ result.data?.fault_type_zh }}
          </el-tag>
          <el-tag :type="statusType(result.data?.judge_status)" effect="plain" round>
            {{ result.data?.judge_status }}
          </el-tag>
        </div>
        <div class="rep-line">
          {{ result.data?.substation }} · {{ result.data?.line_name }}
          <span v-if="result.data?.voltage_level">（{{ result.data.voltage_level }}）</span>
          · {{ result.data?.ts }}
        </div>
        <div v-if="result.data?.tripped_breaker" class="rep-line muted">
          跳闸开关：{{ result.data.tripped_breaker }}，损失负荷：{{ result.data?.loss_mw }} MW
        </div>
        <div v-if="result.data?.affected_area" class="rep-line muted">影响区域：{{ result.data.affected_area }}</div>
      </div>

      <div class="section">
        <div class="section-title">🧠 推理链</div>
        <div v-for="(r, i) in result.data?.reasoning ?? []" :key="i" class="reason-line">
          <span class="reason-idx">{{ i + 1 }}</span>{{ r }}
        </div>
      </div>

      <div class="section">
        <div class="section-title">📍 影响范围</div>
        <div class="scope-box">{{ result.data?.scope }}</div>
      </div>

      <div class="section">
        <div class="section-title">💡 处置建议</div>
        <div v-for="(s, i) in result.data?.suggestions ?? []" :key="i" class="tip-line green">▸ {{ s }}</div>
      </div>

      <div class="section">
        <div class="section-title">❓ 待确认问题</div>
        <div v-for="(q, i) in result.data?.questions ?? []" :key="i" class="tip-line blue">？ {{ q }}</div>
      </div>

      <div class="disclaimer">⚠️ 以上为辅助研判建议，最终事故定性请以值班调度/继保专业人员人工复核为准。</div>
    </template>
  </div>
</template>

<style scoped>
.dispatch-result {
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
.rep-no {
  font-size: 17px;
  font-weight: 700;
  color: var(--accent);
  font-family: 'SFMono-Regular', Consolas, monospace;
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
.reason-line {
  font-size: 12px;
  color: #334155;
  line-height: 1.8;
  display: flex;
  gap: 8px;
  padding: 4px 0;
}
.reason-idx {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 3px;
}
.scope-box {
  font-size: 13px;
  color: #334155;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  line-height: 1.7;
}
.tip-line {
  font-size: 12px;
  line-height: 1.7;
  padding: 4px 0;
}
.tip-line.green {
  color: #15803d;
}
.tip-line.blue {
  color: #1d4ed8;
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
