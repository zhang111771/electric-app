<script setup lang="ts">
/** 知识库管理：统计 / 搜索 / 新增 / 编辑 / 删除 / 从 markdown 重建。 */
import { onMounted, ref, watch } from 'vue'
import {
  addKbChunk,
  deleteKbChunk,
  getKbChunks,
  getKbSources,
  getKbStats,
  rebuildKb,
  updateKbChunk,
} from '../api/kb'
import type { KbChunk, KbStats } from '../types'

const stats = ref<KbStats | null>(null)
const sources = ref<string[]>([])
const chunks = ref<KbChunk[]>([])
const search = ref('')
const sourceFilter = ref('')
const loading = ref(false)

const message = ref('')
const messageOk = ref(true)
const rebuildBusy = ref(false)

const formTitle = ref('新增知识块')
const formSource = ref('')
const formSection = ref('')
const formText = ref('')
const formBusy = ref(false)
let editId: number | null = null

const preview = (t: string) => (t.length > 90 ? `${t.slice(0, 90)}…` : t)

function setMsg(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function loadStats() {
  try {
    stats.value = await getKbStats()
  } catch {
    stats.value = null
  }
}

async function loadSources() {
  try {
    sources.value = await getKbSources()
  } catch {
    sources.value = []
  }
}

async function loadChunks() {
  loading.value = true
  try {
    chunks.value = await getKbChunks(search.value, sourceFilter.value, 300)
  } catch (e) {
    setMsg(String(e), false)
  } finally {
    loading.value = false
  }
}

async function loadAll() {
  await Promise.all([loadStats(), loadSources(), loadChunks()])
}

function resetForm() {
  editId = null
  formTitle.value = '新增知识块'
  formSource.value = ''
  formSection.value = ''
  formText.value = ''
  formBusy.value = false
}

// 搜索防抖
let searchTimer = 0
watch(search, () => {
  clearTimeout(searchTimer)
  searchTimer = window.setTimeout(loadChunks, 300)
})

async function submit() {
  const source = formSource.value.trim()
  const text = formText.value.trim()
  if (!source) return setMsg('请填写来源', false)
  if (!text) return setMsg('请填写正文内容', false)
  formBusy.value = true
  try {
    if (editId !== null) {
      await updateKbChunk(editId, {
        source,
        section: formSection.value.trim(),
        text,
      })
      setMsg('已保存修改，向量已更新')
    } else {
      const r = await addKbChunk({
        source,
        section: formSection.value.trim(),
        text,
      })
      setMsg(`已添加并编码（ID ${r.id}），立即可用于检索`)
    }
    resetForm()
    await Promise.all([loadStats(), loadChunks()])
  } catch (e) {
    setMsg(String(e), false)
  } finally {
    formBusy.value = false
  }
}

function startEdit(c: KbChunk) {
  editId = c.id
  formTitle.value = `编辑知识块 #${c.id}`
  formSource.value = c.source
  formSection.value = c.section
  formText.value = c.text
  message.value = ''
}

async function removeChunk(c: KbChunk) {
  if (!confirm(`确定删除该知识块（ID ${c.id}）？`)) return
  try {
    await deleteKbChunk(c.id)
    setMsg('已删除')
    await Promise.all([loadStats(), loadChunks(), loadSources()])
  } catch (e) {
    setMsg(String(e), false)
  }
}

async function doRebuild() {
  if (!confirm('将从 data/assets/ 下的 markdown 全量重建知识库（清空现有所有 chunk 后重新入库），确定继续？')) return
  rebuildBusy.value = true
  try {
    const r = await rebuildKb()
    setMsg(r.message)
    resetForm()
    await Promise.all([loadStats(), loadSources(), loadChunks()])
  } catch (e) {
    setMsg(String(e), false)
  } finally {
    rebuildBusy.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <el-scrollbar class="kb-view" always>
    <div class="kb-view-inner">
    <div class="kb-toolbar">
      <input
        v-model="search"
        type="text"
        placeholder="搜索内容 / 章节…"
        class="kb-search"
      />
      <select v-model="sourceFilter" class="kb-select" @change="loadChunks">
        <option value="">全部来源</option>
        <option v-for="s in sources" :key="s" :value="s">{{ s }}</option>
      </select>
      <span class="kb-stats">
        共 <b>{{ stats?.total ?? 0 }}</b> 块<template
          v-if="stats && stats.by_source.length"
        >
          · {{ stats.by_source.map((s) => `${s.source} ×${s.count}`).join(' · ') }}
        </template>
      </span>
      <button class="btn btn-ghost" @click="loadAll">刷新</button>
      <button class="btn btn-danger" :disabled="rebuildBusy" @click="doRebuild">
        {{ rebuildBusy ? '重建中…' : '从 markdown 重建' }}
      </button>
    </div>

    <div class="kb-panel">
      <h3>{{ formTitle }}</h3>
      <div class="kb-form-row">
        <input
          v-model="formSource"
          type="text"
          placeholder="来源（文件名或主题，如 06_调度规程补充.md）"
          list="kb-source-list"
        />
        <datalist id="kb-source-list">
          <option v-for="s in sources" :key="s" :value="s" />
        </datalist>
        <input
          v-model="formSection"
          type="text"
          placeholder="章节（可选，如 过负荷运行）"
        />
      </div>
      <textarea
        v-model="formText"
        placeholder="正文内容… 保存后自动编码向量，立即参与检索"
      />
      <div class="kb-btnrow">
        <button class="btn" :disabled="formBusy" @click="submit">
          {{ formBusy ? '编码中…' : editId !== null ? '保存修改' : '添加并编码' }}
        </button>
        <button v-if="editId !== null" class="btn btn-ghost" @click="resetForm">
          取消编辑
        </button>
        <span v-if="message" :class="messageOk ? 'kb-ok' : 'kb-err'">{{
          message
        }}</span>
        <span v-if="loading" class="kb-ok">加载中…</span>
      </div>
    </div>

    <div class="kb-panel">
      <h3>
        知识块列表
        <span class="kb-count">{{ chunks.length }} 条</span>
      </h3>
      <div class="kb-table-wrap">
        <el-scrollbar always>
          <table v-if="chunks.length" class="kb-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>来源</th>
                <th>章节</th>
                <th>内容</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in chunks" :key="c.id">
                <td>{{ c.id }}</td>
                <td class="kb-src">{{ c.source }}</td>
                <td>{{ c.section }}</td>
                <td class="kb-text">{{ preview(c.text) }}</td>
                <td class="kb-actions">
                  <button class="btn-sm" @click="startEdit(c)">编辑</button>
                  <button class="btn-sm btn-danger" @click="removeChunk(c)">
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="kb-empty">
            暂无知识块，可在上方新增，或「从 markdown 重建」导入 data/assets/ 下的规程文件。
          </div>
        </el-scrollbar>
      </div>
    </div>
    </div>
  </el-scrollbar>
</template>

<style scoped>
.kb-view {
  flex: 1;
  min-width: 0;
  max-width: 960px;
  width: 100%;
  margin: 0 auto;
}
.kb-view-inner {
  padding: 24px 20px;
}

.kb-toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.kb-search {
  flex: 1;
  min-width: 200px;
}

.kb-search,
.kb-select {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 14px;
  font-family: inherit;
  background: #fff;
}

.kb-stats {
  font-size: 13px;
  color: var(--muted);
}

.kb-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}

.kb-panel h3 {
  font-size: 15px;
  margin-bottom: 10px;
  font-weight: 600;
}

.kb-panel input,
.kb-panel textarea {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 14px;
  font-family: inherit;
  margin-bottom: 8px;
}

.kb-panel textarea {
  min-height: 90px;
  resize: vertical;
}

.kb-form-row {
  display: flex;
  gap: 10px;
}

.kb-form-row input {
  flex: 1;
}

.kb-btnrow {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 8px 20px;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-ghost {
  background: #fff;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-danger {
  background: #dc2626;
}

.btn-sm {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
  margin-right: 4px;
}

.kb-ok {
  color: #16a34a;
  font-size: 13px;
}

.kb-err {
  color: #dc2626;
  font-size: 13px;
}

.kb-count {
  font-weight: 400;
  color: var(--muted);
  font-size: 12px;
}

.kb-table-wrap {
  /* 横向滚动交给 el-scrollbar 处理 */
}

.kb-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.kb-table th,
.kb-table td {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}

.kb-table th {
  background: #f1f5f9;
  font-weight: 600;
  white-space: nowrap;
}

.kb-table .kb-text {
  max-width: 380px;
  word-break: break-word;
}

.kb-table .kb-src,
.kb-table .kb-actions {
  white-space: nowrap;
}

.kb-empty {
  color: var(--muted);
  text-align: center;
  padding: 30px;
}
@media (max-width: 768px) {
  .kb-view-inner {
    padding: 16px 14px;
  }
  .kb-toolbar .kb-search {
    min-width: 100%;
  }
  .kb-toolbar .kb-stats {
    flex-basis: 100%;
  }
  .kb-form-row {
    flex-direction: column;
    gap: 0;
  }
  .kb-btnrow {
    flex-wrap: wrap;
  }
}
</style>
