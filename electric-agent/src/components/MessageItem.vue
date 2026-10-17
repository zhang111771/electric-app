<script setup lang="ts">
/** 单条消息气泡：用户/智能体 + 轻量 Markdown 渲染 + 图表/表单/结果。 */
import { computed } from 'vue'
import type { ChatMessage, FeedbackRating, FormResult } from '../types'
import LineChart from './LineChart.vue'
import InteractiveForm from './InteractiveForm.vue'
import DiagnoseResult from './DiagnoseResult.vue'
import BatchDiagnoseResult from './BatchDiagnoseResult.vue'
import DefectResult from './DefectResult.vue'
import TicketResult from './TicketResult.vue'
import DispatchResult from './DispatchResult.vue'
import FlisrResult from './FlisrResult.vue'
import LineLossResult from './LineLossResult.vue'

const props = defineProps<{ message: ChatMessage }>()
const emit = defineEmits<{
  (e: 'form-submit', result: FormResult): void
  (e: 'retry'): void
  (e: 'feedback', rating: FeedbackRating): void
}>()

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** 行内样式：加粗 / 【重点】/ 引用来源 */
function renderInline(s: string): string {
  let r = esc(s)
  r = r.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  r = r.replace(/【([^】]+)】/g, '<b>【$1】</b>')
  return r
}

/** 块级渲染：标题 / 分隔线 / 列表 / 表格保持原样 */
const html = computed(() => {
  const lines = (props.message.content || '').split('\n')
  const out: string[] = []
  for (const line of lines) {
    const t = line.trim()
    if (/^#{1,3}\s/.test(t)) {
      out.push(`<div class="md-head">${renderInline(t.replace(/^#+\s*/, ''))}</div>`)
    } else if (/^-{3,}\s*$/.test(t)) {
      out.push('<hr class="md-hr"/>')
    } else if (/^[-*]\s/.test(t)) {
      out.push(`<div class="md-li">• ${renderInline(t.replace(/^[-*]\s*/, ''))}</div>`)
    } else if (t.startsWith('|')) {
      out.push(`<div class="md-table">${renderInline(t)}</div>`)
    } else if (t === '') {
      out.push('<div class="md-blank"></div>')
    } else {
      out.push(`<div class="md-line">${renderInline(t)}</div>`)
    }
  }
  return out.join('')
})

function onFormSubmit(result: FormResult) {
  emit('form-submit', result)
}

/** 检测消息是否为错误消息（错误也可点踩收集信号） */
const isError = computed(() => {
  const c = props.message.content || ''
  return props.message.role === 'assistant' &&
    (c.startsWith('（未收到回复') || c.startsWith('出错了'))
})

/** 是否显示评分按钮：已完成的 assistant 消息（含错误消息，点踩是重要信号） */
const canRate = computed(() => {
  return props.message.role === 'assistant' &&
    !props.message.streaming &&
    !!props.message.content
})

function onFeedback(rating: FeedbackRating) {
  emit('feedback', rating)
}
</script>

<template>
  <div class="row" :class="message.role">
    <div class="avatar" :class="message.role">
      <svg v-if="message.role === 'user'" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="8" r="4" fill="currentColor" />
        <path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
      </svg>
      <svg v-else viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M13 2 6 14h5l-1 8 7-12h-5l1-8z" fill="currentColor" />
      </svg>
    </div>
    <div class="bubble">
      <div v-if="message.streaming && !message.content" class="typing">思考中…</div>
      <div v-else v-html="html" />

      <!-- 交互式表单（后端请求用户上传文件） -->
      <InteractiveForm
        v-if="message.form && !message.formResult"
        :form="message.form"
        @submit="onFormSubmit"
      />

      <!-- 诊断结果（提交后显示）：批量检测按数据形态分发，其余走单张卡片 -->
      <BatchDiagnoseResult
        v-if="message.formResult?.type === 'detect'
          && message.formResult?.data?.total_images !== undefined"
        :result="message.formResult"
      />
      <DefectResult
        v-else-if="message.formResult?.type === 'defect'"
        :result="message.formResult"
      />
      <TicketResult
        v-else-if="message.formResult?.type === 'ticket'"
        :result="message.formResult"
      />
      <DispatchResult
        v-else-if="message.formResult?.type === 'dispatch'"
        :result="message.formResult"
      />
      <FlisrResult
        v-else-if="message.formResult?.type === 'flisr'"
        :result="message.formResult"
      />
      <LineLossResult
        v-else-if="message.formResult?.type === 'line_loss'"
        :result="message.formResult"
      />
      <DiagnoseResult
        v-else-if="message.formResult"
        :result="message.formResult"
      />

      <LineChart v-if="message.chart" :chart="message.chart" />

      <!-- 评分（评测数据闭环）：错误消息也可点踩收集失败信号 -->
      <div v-if="canRate" class="feedback">
        <button
          class="fb-btn"
          :class="{ active: message.rating === 'up' }"
          title="回答有帮助"
          @click="onFeedback('up')"
        >👍</button>
        <button
          class="fb-btn"
          :class="{ active: message.rating === 'down' }"
          title="回答不满意"
          @click="onFeedback('down')"
        >👎</button>
        <span v-if="message.rating" class="fb-hint">
          {{ message.rating === 'up' ? '已记录，感谢反馈' : '已记录，我们会改进' }}
        </span>
      </div>

      <button v-if="isError" class="retry-btn" @click="emit('retry')">🔄 重试</button>
    </div>
  </div>
</template>

<style scoped>
.feedback { display: flex; gap: 6px; align-items: center; margin-top: 10px; }
.fb-btn {
  width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
  background: #f1f5f9; border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px; cursor: pointer; opacity: 0.55; transition: all 0.15s;
}
.fb-btn:hover { opacity: 1; background: #e2e8f0; }
.fb-btn.active { opacity: 1; background: var(--accent); border-color: var(--accent); }
.fb-hint { font-size: 12px; color: #94a3b8; }
.retry-btn {
  margin-top: 10px;
  padding: 5px 14px;
  background: #f1f5f9;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  color: var(--accent);
  transition: all 0.15s;
}
.retry-btn:hover {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
</style>
