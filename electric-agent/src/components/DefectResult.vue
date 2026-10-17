<script setup lang="ts">
/** 缺陷闭环管理结果展示：登记成功卡 + 缺陷台账列表。
 *  result.data 两种形态：
 *   - 登记结果 { defect_no, severity, status, message, ... }
 *   - 台账列表 { total, items: DefectRecord[] }
 */
import type { DefectRecord, FormResult } from '../types'

defineProps<{ result: FormResult }>()

const severityType = (s: string): 'danger' | 'warning' | 'success' | 'info' => {
  if (s === '危急') return 'danger'
  if (s === '严重') return 'warning'
  if (s === '一般') return 'info'
  return 'info'
}

const FLOW_STEPS = ['已登记', '已定级', '已派单', '处理中', '已消缺', '已验收']

/** 状态 → 闭环第几步（-1 表示已关闭/未知） */
function flowIndex(status: string): number {
  const i = FLOW_STEPS.indexOf(status)
  return i
}

const statusColor = (s: string) => (s === '已关闭' ? '#94a3b8' : '#16a34a')
</script>

<template>
  <div class="defect-result">
    <!-- 台账列表形态 -->
    <div v-if="result.data?.items !== undefined" class="defect-list">
      <div class="result-header">📋 缺陷台账（{{ result.data.total }} 条）</div>
      <div class="kanban">
        <div v-for="s in FLOW_STEPS" :key="s" class="kanban-col">
          <div class="kanban-name">{{ s }}</div>
          <div class="kanban-count" :style="{ color: statusColor(s) }">
            {{ (result.data.items as DefectRecord[]).filter(d => d.status === s).length }}
          </div>
        </div>
      </div>
      <el-table :data="result.data.items" size="small" stripe style="width: 100%">
        <el-table-column prop="defect_no" label="编号" width="130" />
        <el-table-column prop="substation" label="变电站" min-width="110" />
        <el-table-column prop="device" label="设备" min-width="90" />
        <el-table-column prop="defect_type" label="类别" min-width="80" />
        <el-table-column label="严重度" width="80">
          <template #default="{ row }">
            <el-tag :type="severityType(row.severity)" effect="light" round size="small">
              {{ row.severity }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <span :style="{ color: statusColor(row.status), fontWeight: 600 }">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="登记时间" width="130" />
      </el-table>
    </div>

    <!-- 登记结果形态 -->
    <div v-else-if="result.data?.defect_no">
      <div class="result-header">📋 缺陷登记成功</div>
      <div class="reg-card">
        <div class="reg-main">
          <div class="reg-no">{{ result.data.defect_no }}</div>
          <el-tag :type="severityType(result.data.severity)" effect="dark" round>
            {{ result.data.severity }}
          </el-tag>
        </div>
        <div class="reg-line">{{ result.data.substation }} · {{ result.data.device }} · {{ result.data.defect_type }}</div>
        <div class="reg-line muted">{{ result.data.message }}</div>
      </div>

      <!-- 闭环状态条 -->
      <div class="flow-bar">
        <template v-for="(s, i) in FLOW_STEPS" :key="s">
          <div class="flow-node" :class="{ done: result.data.status && flowIndex(result.data.status) >= i }">
            <div class="flow-dot">{{ result.data.status && flowIndex(result.data.status) > i ? '✓' : i + 1 }}</div>
            <div class="flow-label">{{ s }}</div>
          </div>
          <div v-if="i < FLOW_STEPS.length - 1" class="flow-line"
               :class="{ done: result.data.status && flowIndex(result.data.status) > i }"></div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.defect-result {
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
.reg-card {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  padding: 14px 16px;
}
.reg-main {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.reg-no {
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.reg-line {
  font-size: 13px;
  color: #334155;
  line-height: 1.8;
}
.reg-line.muted {
  color: var(--muted);
}

/* 闭环状态条 */
.flow-bar {
  display: flex;
  align-items: flex-start;
  margin-top: 18px;
  padding: 0 4px;
}
.flow-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 76px;
  flex-shrink: 0;
}
.flow-dot {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  transition: all 0.2s;
}
.flow-node.done .flow-dot {
  color: #fff;
  background: #16a34a;
  border-color: #16a34a;
}
.flow-label {
  margin-top: 6px;
  font-size: 11px;
  color: var(--muted);
}
.flow-node.done .flow-label {
  color: #16a34a;
  font-weight: 600;
}
.flow-line {
  flex: 1;
  height: 2px;
  background: #e2e8f0;
  margin-top: 13px;
  min-width: 12px;
}
.flow-line.done {
  background: #16a34a;
}

/* 台账列表 */
.kanban {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 6px;
  margin-bottom: 12px;
}
.kanban-col {
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 4px;
  text-align: center;
}
.kanban-name {
  font-size: 11px;
  color: var(--muted);
}
.kanban-count {
  font-size: 18px;
  font-weight: 700;
  margin-top: 2px;
}
@media (max-width: 768px) {
  /* 闭环状态条 6 步在手机上横向滚动 */
  .flow-bar {
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .kanban-col {
    padding: 6px 2px;
  }
}
</style>
