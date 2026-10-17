/** 电网智能体前端类型定义（与后端 server/main.py 对齐） */

/** SSE 事件（后端 /chat 返回，每行 `data: {...}`，事件间以 \n\n 分隔） */
export interface TokenEvent {
  type: 'token'
  content: string
}

export interface ErrorEvent {
  type: 'error'
  content: string
}

/** 折线图配置（后端 graph/chart.py 生成，内嵌 SVG 渲染） */
export interface ChartConfig {
  type: 'line'
  title: string
  x: string[]
  series: Array<{ name: string; data: number[] }>
}

/** 后端请求用户上传文件的表单类型 */
export type FormType = 'tdoa' | 'classify' | 'prpd' | 'detect' | 'substation' | 'defect' | 'ticket' | 'dispatch' | 'flisr' | 'line_loss'

/** 后端通过 SSE done 事件返回的表单请求（mode 区分同类型下的子表单，可选兼容旧数据） */
export interface FormRequest {
  type: FormType
  mode?: string
}

/** 用户提交表单后的诊断结果 */
export interface FormResult {
  type: FormType
  mode?: string
  data: any
}

/* ===== YOLO 线路缺陷批量检测（/api/diagnose/detect_batch） ===== */

/** 单个检测目标 */
export interface DetectItem {
  class_id: number
  class_name: string
  confidence: number
  bbox: number[]
}

/** 批量结果中的单张图片检测项 */
export interface DetectBatchResultItem {
  image_path: string
  filename?: string
  image_size?: [number, number]
  detections: DetectItem[]
  count: number
  latency_ms: number
  image_url?: string
  error?: string
}

/** 批量检测整体结果（data 字段） */
export interface DetectBatchData {
  total_images: number
  total_detections: number
  total_latency_ms: number
  engine: string
  report_url?: string
  report_name?: string
  results: DetectBatchResultItem[]
}

/* ===== 变电设备诊断（/api/diagnose/substation/*，纯规则判据 DL/T 722/596/664） ===== */

/** 油色谱三比值法结果 */
export interface DgaResult {
  mode: 'dga'
  standard: string
  voltage_band: string
  attention_values: Record<string, number>
  fault_type_zh: string
  severity: string // 正常/注意/严重/危急
  ratio_code: string | null
  ratio_values?: Record<string, number | null>
  exceeded_gases: Record<string, number>
  advice: string
  note?: string
}

/** SF6 压力/微水结果 */
export interface Sf6Result {
  mode: 'sf6'
  standard: string
  measured_pressure_MPa: number
  pressure_status: string
  moisture_status: string
  overall: string
  advice: string
  gas_chamber: string
}

/** 红外测温相对温差法结果 */
export interface IrResult {
  mode: 'ir'
  standard: string
  category: string
  hot_temp_c: number
  ref_temp_c: number
  ambient_temp_c: number
  tau1_k: number
  delta_pct: number | null
  judgment: string // 正常/一般/严重/危急
  advice: string
}

/* ===== 缺陷闭环管理（/api/defect/*） ===== */

/** 缺陷记录（与后端 _DEFECT_COLS 对齐） */
export interface DefectRecord {
  id?: number
  defect_no: string
  substation: string
  device: string
  voltage_level?: string
  defect_type: string
  description?: string
  source?: string
  severity: '一般' | '严重' | '危急'
  status: string // 已登记/已定级/已派单/处理中/已消缺/已验收/已关闭
  discoverer?: string
  assignee?: string
  result_note?: string
  plan_time?: string | null
  created_at?: string | null
}

/** 缺陷工单 */
export interface DefectWorkOrder {
  work_order_no: string
  work_type?: string
  assignee?: string
  status: string // 待执行/执行中/已完成/已验收
  result_note?: string
  plan_time?: string | null
  created_at?: string | null
}

/** 缺陷登记结果 */
export interface DefectRegisterResult {
  defect_no: string
  substation: string
  device: string
  defect_type: string
  severity: string
  status: string
  message: string
  work_order_no?: string
  error?: string
}

/** 缺陷列表结果（data 字段） */
export interface DefectListData {
  total: number
  items: DefectRecord[]
}

/** 缺陷详情（含关联工单） */
export interface DefectDetail extends DefectRecord {
  work_orders: DefectWorkOrder[]
}

/* ===== 两票智能审核（/api/ticket/check） ===== */

/** 单项差异：level 决定配色（missing 红 / warning 橙 / question 蓝） */
export interface TicketCheckItem {
  level: 'missing' | 'warning' | 'question'
  item: string
  detail?: string
  clause?: string
  suggestion?: string
}

/** 两票审核结果（data 字段，不设 verdict 字段） */
export interface TicketCheckResult {
  mode: 'ticket'
  ticket_type: 'work' | 'operation'
  summary: string
  items: TicketCheckItem[]
  questions?: TicketCheckItem[]
  narrative?: string
  disclaimer: string
}

/* ===== 调度事故研判（/api/dispatch/*） ===== */

/** 故障事件台账行（与后端 _EVENT_COLS 对齐） */
export interface DispatchEvent {
  id?: number
  event_no: string
  ts: string
  substation: string
  line_name: string
  voltage_level?: string
  tripped_breaker?: string
  loss_mw?: number
  affected_area?: string
  judge_status: string // 待研判/已研判/已处置
  fault_type?: string
  updated_at?: string | null
}

/** 故障事件列表（data 字段） */
export interface DispatchListData {
  total: number
  items: DispatchEvent[]
}

/** 事件研判报告（data 字段，辅助不拍板：只出推理链+建议+疑问） */
export interface DispatchReport {
  mode: 'dispatch'
  event_no: string
  ts: string
  substation: string
  line_name: string
  voltage_level?: string
  tripped_breaker?: string
  loss_mw?: number
  affected_area?: string
  judge_status: string
  fault_type_zh: string
  scope: string
  reasoning: string[]
  suggestions: string[]
  questions: string[]
  summary: string
  error?: string
}

/* ===== 配网 FLISR（/api/flisr/*） ===== */

/** 馈线段（拓扑顺序） */
export interface FlisrSection {
  section_no: string
  section_name: string
  load_kw: number
  up_switch: string
  down_switch: string
  is_lateral: boolean
}

/** 馈线档案（含各段拓扑，前端下拉数据） */
export interface FlisrFeeder {
  feeder_id: string
  feeder_name: string
  substation: string
  voltage_level: string
  source_breaker: string
  tie_breaker: string
  tie_to_feeder: string
  rated_capacity_kva: number
  load_limit_kw: number
  total_load_kw: number
  sections: FlisrSection[]
}

/** 馈线清单（data 字段） */
export interface FlisrFeedersData {
  mode: 'flisr'
  feeders: FlisrFeeder[]
}

/** 单个操作步骤 */
export interface FlisrStep {
  seq: number
  phase: string // 定位/隔离/恢复
  action: string // 判定/拉开/合闸/暂不合闸/确认断开/无联络
  switch: string
  why: string
}

/** 转供容量校验结果 */
export interface FlisrLoadCheck {
  transferred_kw: number
  neighbor_feeder: string
  neighbor_current_kw: number
  neighbor_limit_kw: number
  after_total_kw: number
  rate_pct: number
  ok: boolean
}

/** FLISR 运行结果（data 字段，辅助不拍板：所有操作需人工确认） */
export interface FlisrRunResult {
  mode: 'flisr'
  feeder_id: string
  feeder_name: string
  substation: string
  fault_section: string
  fault_type: string
  steps: FlisrStep[]
  restored_kw: number
  unserved_kw: number
  load_check: FlisrLoadCheck | null
  summary: string
  note: string
  error?: string
}

/* ===== 台区线损（/api/line-loss/*） ===== */

/** 台区档案（清单行） */
export interface TransArea {
  area_no: string
  area_name: string
  substation: string
  capacity_kva: number
  voltage_level: string
  status: string // 正常/疑似异常/已派单/已处置
}

/** 台区清单（data 字段） */
export interface LineLossAreasData {
  areas: TransArea[]
}

/** 某台区日电量明细行 */
export interface AreaReading {
  stat_date: string
  supply_kwh: number
  sales_kwh: number
  loss_kwh: number
  loss_rate_pct: number
}

/** 线损率计算/判定结果（客观计算可明确判级；窃电/漏电用疑似） */
export interface LineLossCalcResult {
  mode: 'line_loss'
  sub_mode: 'calc'
  supply_kwh: number
  sales_kwh: number
  capacity_kva: number
  uplimit_pct: number
  loss_kwh: number
  loss_rate_pct: number
  judgment: string // 正常/偏高/超标/计量异常/疑似窃电…
  advice: string[]
  note: string
  error?: string
}

/** 异常疑点 */
export interface LineLossSuspicion {
  type: string // 疑似窃电/疑似漏电或线损治理不到位/计量异常
  evidence: string
}

/** 异常日 */
export interface LineLossAnomalyDay {
  stat_date: string
  rate: number
  supply_kwh: number
  sales_kwh: number
  reason: string
}

/** 台区线损分析结果（窃电/漏电用疑似措辞，辅助不拍板） */
export interface LineLossAnalyzeResult {
  mode: 'line_loss'
  sub_mode: 'analyze'
  area_no: string
  area_name: string
  capacity_kva: number
  uplimit_pct: number
  days: number
  n_readings: number
  avg_rate: number
  trend: string // 上升/下降/平稳
  recent_avg: number
  anomaly_days: LineLossAnomalyDay[]
  suspicions: LineLossSuspicion[]
  suggestions: string[]
  overall: string
  status: string
  error?: string
}

export interface DoneEvent {
  type: 'done'
  chart?: ChartConfig | null
  form?: FormRequest | null
}

export type SSEEvent = TokenEvent | ErrorEvent | DoneEvent

/** SSE 解析回调 */
export interface SSEHandlers {
  onToken?: (content: string) => void
  onError?: (content: string) => void
  onDone?: (data: { chart?: ChartConfig | null; form?: FormRequest | null }) => void
}

/** 会话列表中的单个会话（后端 /api/threads） */
export interface ThreadInfo {
  session_id: string
  last_message: string
  updated_at: string
}

/** 用户对 AI 回答的评分 */
export type FeedbackRating = 'up' | 'down'

/** 一条对话消息（前端渲染用） */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  chart?: ChartConfig | null
  streaming?: boolean // 正在流式输出中
  form?: FormRequest | null // 后端请求用户上传文件的表单
  formResult?: FormResult | null // 用户提交表单后的诊断结果
  rating?: FeedbackRating | null // 用户对该回答的评分（评测数据闭环）
}

/** 会话详情消息（后端 /api/threads/{session_id}） */
export interface ThreadMessage {
  role: 'user' | 'assistant'
  content: string
  chart?: ChartConfig | null
  form?: FormRequest | null
  formResult?: FormResult | null
}

/* ===== 知识库管理（后端 /api/kb/*） ===== */

/** 知识库统计 */
export interface KbStats {
  total: number
  by_source: Array<{ source: string; count: number }>
}

/** 单个知识块 */
export interface KbChunk {
  id: number
  source: string
  section: string
  text: string
  len: number
}

/** 新增/编辑知识块载荷 */
export interface KbChunkPayload {
  source: string
  section: string
  text: string
}

/** 全量重建结果 */
export interface KbRebuildResult {
  count: number
  message: string
}

/* ===== 客观评测（后端 /api/eval） ===== */

/** TDOA 评测摘要 */
export interface TdoaEvalSummary {
  n_total: number
  n_success: number
  mae_m: number | null
  max_ae_m: number | null
  acceptance_pass_rate: number | null
  threshold_m: number
}

/** 分类/PRPD 评测摘要 */
export interface ClassifyEvalSummary {
  n_total: number
  n_success: number
  accuracy: number | null
}

/** YOLO 检测评测摘要 */
export interface DetectEvalSummary {
  n_total: number
  n_success: number
  normal_sample_fpr: number | null
}

/** 单工具评测结果 */
export interface ToolEvalResult {
  summary: TdoaEvalSummary | ClassifyEvalSummary | DetectEvalSummary | Record<string, any>
  details: any[]
  note?: string
}

/** 完整评测报告 */
export interface EvalReport {
  version: string
  timestamp: string
  tools_tested: string[]
  results: Record<string, ToolEvalResult>
}

/** 历史评测记录元信息 */
export interface EvalHistoryItem {
  name: string
  timestamp: string
  version: string
  tools_tested: string[]
}

/** 评测整体响应 */
export interface EvalResponse {
  latest: EvalReport | null
  history: EvalHistoryItem[]
}
