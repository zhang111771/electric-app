<script setup lang="ts">
/** 评测面板：各诊断工具客观指标卡片 + 历史记录 + 一键重跑。 */
import { onMounted, ref } from 'vue'
import { getEval, runEval } from '../api/eval'
import type {
  ClassifyEvalSummary,
  DetectEvalSummary,
  EvalReport,
  TdoaEvalSummary,
} from '../types'

const loading = ref(false)
const running = ref(false)
const latest = ref<EvalReport | null>(null)
const history = ref<any[]>([])
const errorMsg = ref('')

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await getEval(20)
    latest.value = data.latest
    history.value = data.history
  } catch (e: any) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function onRun() {
  if (running.value) return
  running.value = true
  errorMsg.value = ''
  try {
    latest.value = await runEval(['tdoa', 'classify', 'prpd'])
    await load()
  } catch (e: any) {
    errorMsg.value = e.message || '评测失败'
  } finally {
    running.value = false
  }
}

function fmt(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined) return '-'
  return v.toFixed(digits)
}

function pct(v: number | null | undefined) {
  if (v === null || v === undefined) return '-'
  return (v * 100).toFixed(1) + '%'
}

onMounted(load)
</script>

<template>
  <el-scrollbar class="eval-panel" always>
    <div class="eval-panel-inner">
    <div class="eval-head">
      <div>
        <h2>🔬 诊断工具客观评测</h2>
        <p class="sub">基于真值集的量化评测，是 RL 数据闭环的基线与验证标准。</p>
      </div>
      <button class="run-btn" :disabled="running" @click="onRun">
        {{ running ? '评测中…' : '▶ 开始评测' }}
      </button>
    </div>

    <div v-if="errorMsg" class="err">{{ errorMsg }}</div>
    <div v-else-if="loading" class="loading">加载中…</div>
    <div v-else-if="!latest" class="empty">暂无评测报告，点击右上角开始首次评测。</div>

    <template v-else>
      <div class="report-meta">
        <span>版本：{{ latest.version }}</span>
        <span>时间：{{ latest.timestamp }}</span>
        <span>覆盖工具：{{ latest.tools_tested.join('、') }}</span>
      </div>

      <div class="cards">
        <!-- TDOA -->
        <div v-if="latest.results.tdoa" class="card tdoa">
          <div class="card-title">📍 TDOA 故障定位</div>
          <div class="metrics">
            <div class="metric">
              <div class="m-val">{{ fmt((latest.results.tdoa.summary as TdoaEvalSummary).mae_m) }} m</div>
              <div class="m-label">MAE 平均误差</div>
            </div>
            <div class="metric">
              <div class="m-val">{{ fmt((latest.results.tdoa.summary as TdoaEvalSummary).max_ae_m) }} m</div>
              <div class="m-label">最大误差</div>
            </div>
            <div class="metric">
              <div class="m-val">
                {{ pct((latest.results.tdoa.summary as TdoaEvalSummary).acceptance_pass_rate) }}
              </div>
              <div class="m-label">
                验收线通过率 ({{ (latest.results.tdoa.summary as TdoaEvalSummary).threshold_m }}m)
              </div>
            </div>
          </div>
          <div class="card-foot">
            样本 {{ (latest.results.tdoa.summary as TdoaEvalSummary).n_success }}
            / {{ (latest.results.tdoa.summary as TdoaEvalSummary).n_total }}
          </div>
        </div>

        <!-- classify -->
        <div v-if="latest.results.classify" class="card classify">
          <div class="card-title">⚡ 故障类型识别</div>
          <div class="metrics">
            <div class="metric big">
              <div class="m-val">
                {{ pct((latest.results.classify.summary as ClassifyEvalSummary).accuracy) }}
              </div>
              <div class="m-label">准确率</div>
            </div>
          </div>
          <div class="card-foot">
            样本 {{ (latest.results.classify.summary as ClassifyEvalSummary).n_success }}
            / {{ (latest.results.classify.summary as ClassifyEvalSummary).n_total }}
          </div>
        </div>

        <!-- PRPD -->
        <div v-if="latest.results.prpd" class="card prpd">
          <div class="card-title">🔬 局放缺陷识别</div>
          <div class="metrics">
            <div class="metric big">
              <div class="m-val">
                {{ pct((latest.results.prpd.summary as ClassifyEvalSummary).accuracy) }}
              </div>
              <div class="m-label">准确率</div>
            </div>
          </div>
          <div class="card-foot">
            样本 {{ (latest.results.prpd.summary as ClassifyEvalSummary).n_success }}
            / {{ (latest.results.prpd.summary as ClassifyEvalSummary).n_total }}
          </div>
        </div>

        <!-- YOLO detect -->
        <div v-if="latest.results.detect" class="card detect">
          <div class="card-title">🔍 线路缺陷检测</div>
          <div class="metrics">
            <div class="metric big">
              <div class="m-val">
                {{ pct((latest.results.detect.summary as DetectEvalSummary).normal_sample_fpr) }}
              </div>
              <div class="m-label">正常样本误报率</div>
            </div>
          </div>
          <div class="card-foot">
            样本 {{ (latest.results.detect.summary as DetectEvalSummary).n_success }}
            / {{ (latest.results.detect.summary as DetectEvalSummary).n_total }}
          </div>
        </div>
      </div>

      <!-- 历史记录 -->
      <div v-if="history.length" class="history">
        <h3>📊 历史评测</h3>
        <el-scrollbar always>
          <table>
            <thead>
              <tr>
                <th>报告名</th>
                <th>时间</th>
                <th>版本</th>
                <th>工具</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="h in history" :key="h.name">
                <td>{{ h.name }}</td>
                <td>{{ h.timestamp }}</td>
                <td>{{ h.version }}</td>
                <td>{{ h.tools_tested.join('、') }}</td>
              </tr>
            </tbody>
          </table>
        </el-scrollbar>
      </div>
    </template>
    </div>
  </el-scrollbar>
</template>

<style scoped>
.eval-panel { height: 100%; box-sizing: border-box; }
.eval-panel-inner { padding: 24px 32px; }
.eval-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
.eval-head h2 { margin: 0 0 4px; font-size: 20px; }
.eval-head .sub { margin: 0; color: #64748b; font-size: 13px; }
.run-btn { padding: 8px 18px; background: var(--accent); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; transition: opacity 0.15s; }
.run-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.err { padding: 12px 16px; background: #fef2f2; color: #b91c1c; border-radius: 8px; margin-bottom: 16px; }
.loading, .empty { padding: 48px; text-align: center; color: #94a3b8; }

.report-meta { display: flex; gap: 16px; font-size: 12px; color: #94a3b8; margin-bottom: 16px; }

.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 28px; }
.card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; display: flex; flex-direction: column; gap: 14px; }
.card-title { font-weight: 600; font-size: 15px; }
.metrics { display: flex; gap: 14px; flex-wrap: wrap; }
.metric { flex: 1; min-width: 80px; }
.metric.big { flex: 1; }
.m-val { font-size: 26px; font-weight: 700; color: var(--accent); }
.m-label { font-size: 12px; color: #64748b; margin-top: 2px; }
.card-foot { font-size: 12px; color: #94a3b8; border-top: 1px solid #f1f5f9; padding-top: 10px; }

.history h3 { font-size: 16px; margin: 0 0 12px; }
.history table { width: 100%; border-collapse: collapse; font-size: 13px; }
.history th, .history td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #f1f5f9; }
.history th { background: #f8fafc; color: #64748b; font-weight: 500; }
@media (max-width: 768px) {
  .eval-panel-inner { padding: 16px 14px; }
  .eval-head { flex-wrap: wrap; gap: 10px; }
  .eval-head .run-btn { flex: 1; }
  .report-meta { flex-wrap: wrap; gap: 6px 14px; }
  .history table, .history th, .history td { font-size: 12px; }
}
</style>
