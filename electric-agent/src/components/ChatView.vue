<script setup lang="ts">
/** 对话主区域：消息列表 + SSE 流式收发 + 会话历史加载。
 *
 *  localStorage 持久化：每次消息变化都写入 localStorage（key 按 sessionId），
 *  刷新页面后从 localStorage 恢复完整状态（含 formResult、摘要消息等），
 *  仅当 localStorage 无数据时才从后端加载。
 *
 *  表单结果处理：InteractiveForm 提交后 emit form-submit，ChatView 将
 *  诊断结果作为新的 bot 消息 push 到 messages 数组中显示。
 */
import { nextTick, reactive, ref, watch } from 'vue'
import { chatStream, getThreadDetail, submitFeedback } from '../api/chat'
import type { ChartConfig, ChatMessage, FeedbackRating, FormRequest, FormResult } from '../types'
import MessageItem from './MessageItem.vue'

const props = defineProps<{
  sessionId: string
}>()
const emit = defineEmits<{
  (e: 'updated'): void
}>()

const LS_KEY = 'electric_msgs_'

const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
/** el-scrollbar 暴露给父组件的实例（wrapRef 为真实滚动容器） */
interface ScrollbarInstance {
  wrapRef: HTMLElement
  setScrollTop(v: number): void
}
const listEl = ref<ScrollbarInstance>()
const historyLoaded = ref(false) // 历史消息是否加载完毕

/** 保存当前会话消息到 localStorage（序列化时剥离 reactive 代理，只保留纯数据） */
function saveLocal() {
  try {
    const data = messages.value.map((m) => ({
      role: m.role,
      content: m.content,
      chart: m.chart ?? null,
      form: m.form ?? null,
      formResult: m.formResult ?? null,
      rating: m.rating ?? null,
      streaming: false,
    }))
    localStorage.setItem(LS_KEY + props.sessionId, JSON.stringify(data))
  } catch {
    // localStorage 满了或不可用时静默
  }
}

/** 从 localStorage 加载消息 */
function loadLocal(): ChatMessage[] | null {
  try {
    const raw = localStorage.getItem(LS_KEY + props.sessionId)
    if (!raw) return null
    return JSON.parse(raw)
  } catch {
    return null
  }
}

/** 清除该会话的 localStorage 缓存 */
function clearLocal() {
  try {
    localStorage.removeItem(LS_KEY + props.sessionId)
  } catch {
    // ignore
  }
}

function scrollBottom() {
  nextTick(() => {
    const sb = listEl.value
    if (sb) sb.setScrollTop(sb.wrapRef.scrollHeight)
  })
}

/** 切换会话：优先从 localStorage 恢复，无缓存时才从后端加载 */
watch(
  () => props.sessionId,
  async () => {
    messages.value = []
    historyLoaded.value = false

    // 1. 优先从 localStorage 恢复（保留前端修改：formResult、摘要消息等）
    const cached = loadLocal()
    if (cached && cached.length > 0) {
      messages.value = cached.map((m) => reactive({
        role: m.role,
        content: m.content,
        chart: m.chart ?? null,
        form: null,           // 不清除 form：如果用户已提交，formResult 会覆盖表单显示
        formResult: m.formResult ?? null,
        rating: m.rating ?? null,
      }))
      scrollBottom()
      historyLoaded.value = true
      return
    }

    // 2. localStorage 无数据，从后端加载
    clearLocal()
    try {
      const msgs = await getThreadDetail(props.sessionId)
      messages.value = msgs.map((m) => reactive({
        role: m.role,
        content: m.content,
        chart: (m as any).chart ?? null,
        form: null,             // 不从后端恢复 form（避免刷新后表单重新出现）
        formResult: null,
        rating: null,
      }))
      saveLocal()
      scrollBottom()
    } catch {
      // 后端未就绪时静默，保留空会话
    }
    historyLoaded.value = true
  },
  { immediate: true },
)

/** 发送一段文本：push 用户消息 + bot 占位，然后走 SSE 流式 */
async function sendText(text: string) {
  if (!text.trim() || sending.value) return
  sending.value = true

  messages.value.push(reactive({ role: 'user', content: text, chart: null, form: null, formResult: null }))
  const botMsg = reactive<ChatMessage>({ role: 'assistant', content: '', streaming: true, chart: null, form: null, formResult: null })
  messages.value.push(botMsg)
  scrollBottom()

  let full = ''
  let chart: ChartConfig | null = null
  let form: FormRequest | null = null
  let receivedDone = false

  await chatStream(text, props.sessionId, {
    onToken: (c) => {
      full += c
      botMsg.content = full
      scrollBottom()
    },
    onError: (c) => {
      botMsg.streaming = false
      botMsg.content = c
      saveLocal()
    },
    onDone: (data: { chart?: ChartConfig | null; form?: FormRequest | null }) => {
      receivedDone = true
      chart = data.chart ?? null
      form = data.form ?? null
      botMsg.streaming = false
      if (chart) botMsg.chart = chart
      if (form) botMsg.form = form
      saveLocal()
      scrollBottom()
    },
  })

  sending.value = false
  if (!receivedDone) {
    botMsg.streaming = false
    botMsg.content = '（未收到回复，请确认后端服务已启动）'
  } else if (!full && !chart && !form) {
    // 后端正常响应但无内容（如 agent 返回空），给一个友好提示
    botMsg.content = '好的，收到。'
  }
  saveLocal()
  emit('updated')
  scrollBottom()
}

function send() {
  sendText(input.value.trim())
  input.value = ''
}

/** 重试：找到该 bot 消息前面的用户消息，重新发送 */
function retryFrom(botMsg: ChatMessage) {
  const idx = messages.value.indexOf(botMsg)
  if (idx > 0) {
    const prev = messages.value[idx - 1]
    if (prev.role === 'user') {
      sendText(prev.content)
    }
  }
}

/** 评分反馈：找到该 bot 消息前面的用户消息作为 query 一并提交；可改评（后端 upsert） */
async function rateMessage(botMsg: ChatMessage, rating: FeedbackRating) {
  if (botMsg.rating === rating) return // 重复点击同一评分不重复提交
  botMsg.rating = rating
  saveLocal()
  const idx = messages.value.indexOf(botMsg)
  const prev = idx > 0 ? messages.value[idx - 1] : null
  const query = prev && prev.role === 'user' ? prev.content : ''
  try {
    await submitFeedback(props.sessionId, query, botMsg.content, rating)
  } catch {
    // 提交失败静默（本地状态已记录，网络恢复后可重新点击改评）
  }
}

/** 将诊断结果作为新的 bot 消息 push 到 messages 中 */
function pushResultMessage(formMsg: ChatMessage, result: FormResult) {
  // 在原消息上记录 formResult（表单替换为结果卡片）
  formMsg.formResult = result

  // 批量检测：结果卡片已包含完整汇总，不再额外推送摘要消息；
  // 其余类型仍推送文本摘要（不携带 formResult，避免重复渲染结果卡片）
  if (!(result.type === 'detect' && result.data?.total_images !== undefined)) {
    const summary = buildResultSummary(result)
    messages.value.push(reactive({
      role: 'assistant',
      content: summary,
      chart: null,
      form: null,
      formResult: null,
    }))
  }
  saveLocal()
  scrollBottom()
}

function buildResultSummary(r: FormResult): string {
  const d = r.data
  if (r.type === 'tdoa') {
    return `**TDOA 故障定位完成**\n\n` +
      `- 故障位置：**${d.position_m}m**\n` +
      `- 双端时差：${d.delta_t_us}μs\n` +
      `- 验收线：${d.within_acceptance ? '✓ 通过' : '✗ 未通过'}\n\n` +
      `电缆全长 ${d.cable_len_m}m，如需进一步分析请告知。`
  } else if (r.type === 'classify') {
    return `**故障类型识别完成**\n\n` +
      `- 故障类型：**${d.fault_type}**\n` +
      `- 置信度：${(d.confidence * 100).toFixed(1)}%\n` +
      `- 规则交叉验证：${d.rule_agrees_gbdt ? '✓ 一致' : '⚠ 不一致'}\n\n` +
      `如需进一步分析请告知。`
  } else if (r.type === 'prpd') {
    return `**局放缺陷诊断完成**\n\n` +
      `- 缺陷类型：**${d.defect_zh}**（${d.defect_type}）\n` +
      `- 置信度：${(d.confidence * 100).toFixed(1)}%\n` +
      `- 严重度：${d.severity}\n` +
      (d.q_max_pc !== undefined ? `- q_max：${d.q_max_pc}pC\n\n` : '\n') +
      `如需进一步分析请告知。`
  } else if (r.type === 'detect') {
    // 批量检测：返回 total_images 即为批量结果
    if (d.total_images !== undefined) {
      return `**线路缺陷批量检测完成**\n\n` +
        `- 检测图片：**${d.total_images}** 张\n` +
        `- 检出缺陷：**${d.total_detections}** 个\n` +
        `- 推理引擎：${d.engine === 'tensorrt' ? 'TensorRT' : 'PyTorch'}，总耗时 ${d.total_latency_ms}ms\n\n` +
        `每张图标注与检测框见上方结果卡片，可查看 / 下载 HTML 检测报告。`
    }
    return `**线路缺陷检测完成**\n\n` +
      `- 检出缺陷：**${d.count}** 个\n` +
      `- 推理引擎：${d.engine === 'tensorrt' ? 'TensorRT' : 'PyTorch'}，耗时 ${d.latency_ms}ms\n\n` +
      `标注图片与检测框见上方结果卡片，如需进一步分析请告知。`
  } else if (r.type === 'substation') {
    const m = d.mode
    if (m === 'dga') {
      return `**油色谱三比值法诊断完成**\n\n` +
        `- 故障类型：**${d.fault_type_zh}**（三比值编码 ${d.ratio_code ?? '—'}）\n` +
        `- 严重度：**${d.severity}**\n` +
        (d.exceeded_gases && Object.keys(d.exceeded_gases).length
          ? `- 超注意值气体：${Object.entries(d.exceeded_gases).map(([k, v]) => `${k}=${v}`).join('、')}\n\n`
          : '\n') +
        `判据：${d.standard}，${d.advice}`
    } else if (m === 'sf6') {
      return `**SF6 气体检查完成**\n\n` +
        `- 实测压力：**${d.measured_pressure_MPa} MPa**（${d.pressure_status}）\n` +
        `- 微水状态：${d.moisture_status}\n` +
        `- 综合：**${d.overall}**\n\n` +
        `${d.advice}`
    } else if (m === 'ir') {
      return `**红外测温相对温差法诊断完成**\n\n` +
        `- 设备类别：**${d.category}**\n` +
        `- 热点温度：${d.hot_temp_c}℃，温升 τ₁=${d.tau1_k}K` +
        (d.delta_pct !== null && d.delta_pct !== undefined ? `，相对温差 δ=${d.delta_pct.toFixed(1)}%` : '') + `\n` +
        `- 判级：**${d.judgment}**\n\n` +
        `${d.advice}`
    }
  } else if (r.type === 'defect') {
    if (d?.defect_no) {
      return `**缺陷登记成功**\n\n` +
        `- 缺陷编号：**${d.defect_no}**\n` +
        `- 设备：${d.substation} ${d.device}（${d.defect_type}）\n` +
        `- 严重度：**${d.severity}**，状态：${d.status}\n\n` +
        `已进入闭环管理，下一步可推进：定级 → 派单 → 处理 → 消缺 → 验收。`
    }
    if (d?.items !== undefined) {
      const brief = (d.items as any[]).slice(0, 5)
        .map((x) => `${x.defect_no}(${x.device}/${x.severity}/${x.status})`).join('、')
      return `**缺陷台账查询完成**\n\n` +
        `- 共 **${d.total}** 条记录\n` +
        `- ${brief}\n\n` +
        `详细状态分布见上方结果卡片。`
    }
  } else if (r.type === 'ticket') {
    const items = d?.items ?? []
    const nm = items.filter((x: any) => x.level === 'missing').length
    const nw = items.filter((x: any) => x.level === 'warning').length
    return `**${d?.ticket_type === 'operation' ? '操作票' : '工作票'}智能审核完成**\n\n` +
      `- 共 **${items.length}** 处需关注点（疑似缺项 ${nm}、顺序存疑 ${nw}）\n\n` +
      `${d?.summary ?? ''}\n\n` +
      `⚠️ ${d?.disclaimer ?? ''}`
  } else if (r.type === 'dispatch') {
    return `**调度事故研判完成**\n\n` +
      `- 事件：**${d?.event_no}**（${d?.substation} · ${d?.line_name}，${d?.ts}）\n` +
      `- 研判：**${d?.fault_type_zh}**\n` +
      `- 影响范围：${d?.scope}\n\n` +
      `${d?.summary ?? ''}`
  } else if (r.type === 'flisr') {
    return `**配网 FLISR 辅助决策完成**\n\n` +
      `- 馈线：**${d?.feeder_id} ${d?.feeder_name}**，故障区段 **${d?.fault_section}**（${d?.fault_type}性）\n` +
      `- 操作步骤：**${(d?.steps ?? []).length}** 步（定位/隔离/恢复）\n` +
      `- 可恢复 **${d?.restored_kw}** kW，未恢复 **${d?.unserved_kw}** kW\n\n` +
      `${d?.summary ?? ''}\n\n` +
      `⚠️ ${d?.note ?? ''}`
  } else if (r.type === 'line_loss' && d?.sub_mode === 'calc') {
    return `**台区线损率计算完成**\n\n` +
      `- 线损率：**${d?.loss_rate_pct}%**（判定：${d?.judgment}）\n` +
      `- 损失电量：${d?.loss_kwh} kWh（上限 ${d?.uplimit_pct}%）\n\n` +
      `⚠️ ${d?.note ?? ''}`
  } else if (r.type === 'line_loss' && d?.sub_mode === 'analyze') {
    const susp = (d?.suspicions ?? []).map((s: any) => s.type).join('、')
    return `**台区线损分析完成**\n\n` +
      `- 台区：**${d?.area_no} ${d?.area_name}**，综合：${d?.overall}\n` +
      `- 平均线损率：**${d?.avg_rate}%**（上限 ${d?.uplimit_pct}%），趋势：${d?.trend}\n` +
      `- 异常日：${(d?.anomaly_days ?? []).length} 天${susp ? `，疑点：${susp}` : ''}\n\n` +
      `疑点为辅助分析，需现场核实后确定处理措施。`
  }
  return '诊断完成。'
}

const quickActions = [
  { label: '📍 TDOA 定位', text: '帮我做 TDOA 定位' },
  { label: '⚡ 故障类型', text: '帮我识别故障类型' },
  { label: '🔬 局放缺陷', text: '帮我分析局放缺陷' },
  { label: '🔍 批量缺陷检测', text: '帮我检测线路缺陷' },
  { label: '🛢️ 油色谱诊断', text: '帮我做油色谱三比值诊断' },
  { label: '💨 SF6 检查', text: '帮我检查 SF6 压力' },
  { label: '🌡️ 红外测温', text: '帮我做红外测温诊断' },
  { label: '📋 缺陷登记', text: '帮我登记一个缺陷' },
  { label: '🔎 缺陷查询', text: '查询一下目前有哪些严重缺陷' },
  { label: '🧾 审核工作票', text: '帮我审核一张工作票' },
  { label: '🧾 审核操作票', text: '帮我审核操作票' },
  { label: '🚨 调度研判', text: '帮我做调度事故研判' },
  { label: '🚨 研判最新', text: '研判一下最新的故障事件' },
  { label: '🔧 配网 FLISR', text: '帮我做配网故障隔离恢复' },
  { label: '🗺️ 馈线拓扑', text: '查看馈线拓扑' },
  { label: '📉 算线损率', text: '帮我算一下线损率' },
  { label: '📉 分析线损', text: '帮我分析台区线损异常' },
]

function quickSend(text: string) {
  if (!sending.value) sendText(text)
}
</script>

<template>
  <div class="chat-pane">
    <el-scrollbar ref="listEl" class="msg-list" always>
      <div class="msg-list-inner">
        <div v-if="!messages.length" class="empty">
          <div class="empty-icon">⚡</div>
          <p>我是电网智能体，可回答规程问题、查询设备与负荷数据、预测负荷、诊断电缆故障。</p>
          <p class="empty-hint">
            试试：帮我做 TDOA 定位 ／ 油色谱三比值诊断 ／ 登记一个缺陷 ／ 审核工作票 ／ 研判故障事件 ／ 配网故障隔离恢复 ／ 台区线损分析 ／ 变压器过负荷运行有什么规定？
          </p>
        </div>
        <MessageItem
          v-for="(m, i) in messages"
          :key="i"
          :message="m"
          @form-submit="(result) => pushResultMessage(m, result)"
          @retry="() => retryFrom(m)"
          @feedback="(r: FeedbackRating) => rateMessage(m, r)"
        />
      </div>
    </el-scrollbar>

    <div class="quick-actions">
      <button
        v-for="q in quickActions"
        :key="q.label"
        class="quick-btn"
        @click="quickSend(q.text)"
      >
        {{ q.label }}
      </button>
    </div>

    <div class="input-bar">
      <textarea
        v-model="input"
        rows="1"
        placeholder="输入问题…（Enter 发送，Shift+Enter 换行）"
        @keydown.enter.exact.prevent="send"
      />
      <button :disabled="sending || !input.trim()" @click="send">
        {{ sending ? '回复中…' : '发送' }}
      </button>
    </div>
  </div>
</template>
