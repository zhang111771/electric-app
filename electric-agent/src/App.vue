<script setup lang="ts">
/** 应用外壳：会话侧栏 + 对话主区 / 知识库管理，管理当前会话与持久化。 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { deleteSession, getThreads, newSession } from './api/chat'
import type { ThreadInfo } from './types'
import ChatView from './components/ChatView.vue'
import EvalPanel from './components/EvalPanel.vue'
import KbManager from './components/KbManager.vue'
import ThreadSidebar from './components/ThreadSidebar.vue'

const STORE_KEY = 'electric_agent_session'

const threads = ref<ThreadInfo[]>([])
const currentId = ref('')
const ready = ref(false)
const mode = ref<'chat' | 'kb' | 'eval'>('chat')

/** 侧栏开关：移动端 = 抽屉展开/收起；桌面端 = 折叠/展开 */
const sidebarOpen = ref(true)
const isMobile = ref(false)

function onResize() {
  const wasMobile = isMobile.value
  isMobile.value = window.innerWidth <= 768
  // 切到桌面端时默认展开；切到移动端时默认收起（等用户点汉堡）
  if (wasMobile !== isMobile.value) {
    sidebarOpen.value = !isMobile.value
  }
}

async function refreshThreads() {
  try {
    threads.value = await getThreads()
  } catch {
    // 后端未启动：保留空列表
  }
}

async function selectSession(id: string) {
  currentId.value = id
  localStorage.setItem(STORE_KEY, id)
}

async function createSession() {
  try {
    const id = await newSession()
    await selectSession(id)
    await refreshThreads()
  } catch {
    // ignore
  }
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
}

async function removeSession(id: string) {
  try {
    await deleteSession(id)
  } catch {
    // ignore
  }
  if (id === currentId.value) {
    await createSession()
  } else {
    await refreshThreads()
  }
}

onMounted(async () => {
  onResize()
  window.addEventListener('resize', onResize)
  await refreshThreads()
  const saved = localStorage.getItem(STORE_KEY)
  if (saved && threads.value.some((t) => t.session_id === saved)) {
    await selectSession(saved)
  } else {
    await createSession()
  }
  ready.value = true
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="app">
    <ThreadSidebar
      :threads="threads"
      :active-id="currentId"
      :mode="mode"
      :open="sidebarOpen"
      @select="selectSession"
      @new="createSession"
      @delete="removeSession"
      @mode="(m) => (mode = m)"
      @close="sidebarOpen = false"
      @toggle="toggleSidebar"
    />
    <div
      class="drawer-backdrop"
      :class="{ hidden: !sidebarOpen }"
      @click="sidebarOpen = false"
    />
    <main v-if="ready" class="main">
      <div class="mobile-header">
        <button class="hamburger" title="菜单" @click="sidebarOpen = true">☰</button>
        <span class="mh-title">电网智能体</span>
      </div>
      <ChatView
        v-if="mode === 'chat' && currentId"
        :session-id="currentId"
        @updated="refreshThreads"
      />
      <KbManager v-else-if="mode === 'kb'" />
      <EvalPanel v-else-if="mode === 'eval'" />
    </main>
  </div>
</template>
