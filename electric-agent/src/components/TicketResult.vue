<script setup lang="ts">
/** 两票智能审核结果：差异清单卡片（按 level 分色）+ 固定免责声明条。
 *  result.data = { mode:'ticket', ticket_type, summary, items, questions, narrative?, disclaimer }
 */
import type { FormResult, TicketCheckItem } from '../types'

defineProps<{ result: FormResult }>()

const LEVEL_META: Record<TicketCheckItem['level'], { label: string; color: string; bg: string }> = {
  missing: { label: '疑似缺项', color: '#dc2626', bg: '#fef2f2' },
  warning: { label: '顺序存疑', color: '#d97706', bg: '#fffbeb' },
  question: { label: '需人工确认', color: '#2563eb', bg: '#eff6ff' },
}

const ticketTypeName = (t?: string) => (t === 'operation' ? '操作票' : '工作票')

function levelMeta(level: any) {
  return LEVEL_META[level as TicketCheckItem['level']] ?? LEVEL_META.missing
}
</script>

<template>
  <div class="ticket-result">
    <div class="result-header">🧾 {{ ticketTypeName(result.data?.ticket_type) }}智能审核</div>

    <div class="ticket-summary">{{ result.data?.summary }}</div>

    <div v-if="result.data?.narrative" class="narrative">{{ result.data.narrative }}</div>

    <!-- 差异清单 -->
    <div v-if="result.data?.items?.length" class="item-list">
      <div
        v-for="(it, i) in result.data.items"
        :key="i"
        class="ticket-item"
        :style="{ borderLeftColor: levelMeta(it.level).color, background: levelMeta(it.level).bg }"
      >
        <div class="item-head">
          <span class="item-badge" :style="{ background: levelMeta(it.level).color }">
            {{ levelMeta(it.level).label }}
          </span>
          <span class="item-title">{{ it.item }}</span>
        </div>
        <div v-if="it.detail" class="item-detail">{{ it.detail }}</div>
        <div v-if="it.suggestion" class="item-suggestion">💡 建议：{{ it.suggestion }}</div>
        <div v-if="it.clause" class="item-clause">📖 条款：{{ it.clause }}</div>
      </div>
    </div>

    <el-empty v-else description="按规则逐项核对未检出明显缺项；仍需人工按规程复核票面有效性。" :image-size="60" />

    <!-- 固定免责声明 -->
    <div class="disclaimer">⚠️ {{ result.data?.disclaimer }}</div>
  </div>
</template>

<style scoped>
.ticket-result {
  margin-top: 12px;
  width: 100%;
  max-width: 650px;
}
.result-header {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.ticket-summary {
  font-size: 13px;
  color: #334155;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  line-height: 1.7;
  margin-bottom: 10px;
}
.narrative {
  font-size: 13px;
  line-height: 1.8;
  color: #334155;
  white-space: pre-line;
  margin-bottom: 12px;
}
.item-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ticket-item {
  border-left: 4px solid;
  border-radius: 8px;
  padding: 10px 12px;
}
.item-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.item-badge {
  flex-shrink: 0;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  border-radius: 4px;
  padding: 2px 7px;
  margin-top: 1px;
}
.item-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  line-height: 1.6;
}
.item-detail {
  font-size: 12px;
  color: #475569;
  line-height: 1.7;
  margin-top: 6px;
}
.item-suggestion {
  font-size: 12px;
  color: #334155;
  line-height: 1.7;
  margin-top: 4px;
}
.item-clause {
  font-size: 11px;
  color: var(--muted);
  margin-top: 4px;
}
.disclaimer {
  margin-top: 12px;
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border: 1px dashed #f59e0b;
  border-radius: 6px;
  padding: 8px 10px;
  line-height: 1.7;
}
</style>
