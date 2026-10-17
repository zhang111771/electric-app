<script setup lang="ts">
/** 会话列表侧栏：切换 / 新建 / 删除会话 + 对话/知识库管理模式切换。 */
import type { ThreadInfo } from '../types'

const props = defineProps<{
  threads: ThreadInfo[]
  activeId: string
  mode: 'chat' | 'kb' | 'eval'
  open: boolean
}>()
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'new'): void
  (e: 'delete', id: string): void
  (e: 'mode', m: 'chat' | 'kb' | 'eval'): void
  (e: 'close'): void
  (e: 'toggle'): void
}>()

const isMobile = () => window.innerWidth <= 768

// 模板中使用 props.open，这里显式引用以避免 TS 未使用告警
void props.open

/** 选择或新建会话后，移动端自动收起抽屉（桌面端不折叠） */
function selectAndClose(kind: 'select' | 'new', id?: string) {
  if (kind === 'new') emit('new')
  else if (id !== undefined) emit('select', id)
  if (isMobile()) emit('close')
}
</script>

<template>
  <aside class="sidebar" :class="{ open: open }">
    <div class="sidebar-head">
      <span class="logo">⚡</span>
      <div>
        <h1>电网智能体</h1>
        <p>调度 · 知识 · 数据 · 预测</p>
      </div>
      <button class="sidebar-close" :title="open ? '折叠侧栏' : '展开侧栏'" :aria-label="open ? '折叠侧栏' : '展开侧栏'" @click="emit('toggle')">
        <svg v-if="open" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M15 6l-6 6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>

    <div class="nav-tabs">
      <button :class="{ active: mode === 'chat' }" @click="emit('mode', 'chat')">
        💬 对话
      </button>
      <button :class="{ active: mode === 'kb' }" @click="emit('mode', 'kb')">
        📚 知识库
      </button>
      <button :class="{ active: mode === 'eval' }" @click="emit('mode', 'eval')">
        🔬 评测
      </button>
    </div>

    <template v-if="mode === 'chat'">
      <button class="new-btn" @click="selectAndClose('new')">＋ 新建会话</button>

      <el-scrollbar class="thread-list" always>
        <div class="thread-list-inner">
          <div
            v-for="t in threads"
            :key="t.session_id"
            class="thread-item"
            :class="{ active: t.session_id === activeId }"
            @click="selectAndClose('select', t.session_id)"
          >
            <div class="thread-title">{{ t.last_message || '（新会话）' }}</div>
            <div class="thread-meta">
              <span>{{ t.updated_at }}</span>
              <span class="del" title="删除会话" @click.stop="emit('delete', t.session_id)">🗑</span>
            </div>
          </div>
          <div v-if="!threads.length" class="thread-empty">暂无会话</div>
        </div>
      </el-scrollbar>
    </template>

    <div class="sidebar-foot">对话历史持久化到 PostgreSQL</div>
  </aside>
</template>
