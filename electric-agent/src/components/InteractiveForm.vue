<script setup lang="ts">
/** 对话内嵌交互式诊断表单（Element Plus 实现）。
 *  后端 cable_agent 检测到诊断意图后通过 SSE done 事件下发，
 *  前端渲染在消息气泡中，用户填写后点击确定提交。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRawFile, UploadUserFile } from 'element-plus'
import {
  diagnoseClassify,
  diagnoseDetectBatch,
  diagnosePrpd,
  diagnoseSubstationDga,
  diagnoseSubstationIr,
  diagnoseSubstationSf6,
  diagnoseTdoa,
} from '../api/diagnose'
import { registerDefect } from '../api/defect'
import { checkTicket } from '../api/ticket'
import { judgeDispatchEvent } from '../api/dispatch'
import { listFeeders, runFlisr } from '../api/flisr'
import { analyzeAreaLoss, calcLineLoss, listAreas } from '../api/line-loss'
import type { FlisrFeeder, FormRequest, FormResult, TransArea } from '../types'

const props = defineProps<{ form: FormRequest }>()
const emit = defineEmits<{
  (e: 'submit', result: FormResult): void
}>()

const busy = ref(false)
const errorMsg = ref('')

// TDOA 表单
const tdoaSrc = ref<File | null>(null)
const tdoaLdr = ref<File | null>(null)
const dtUs = ref(0.2)

// 故障类型表单
const classifyFile = ref<File | null>(null)
const classifyRuleNode = ref('LDR')

// 局放缺陷表单
const prpdFile = ref<File | null>(null)
const prpdEngine = ref('gbdt')

// YOLO 缺陷检测表单（批量多图）
const detectFileList = ref<UploadUserFile[]>([])
const detectConf = ref(0.25)
const detectIou = ref(0.45)
const detectRawFiles = computed(() =>
  detectFileList.value.map(f => f.raw).filter((x): x is UploadRawFile => !!x))

// 变电设备诊断表单（mode: dga / sf6 / ir）
const subMode = ref<string>(props.form.mode ?? 'dga')
const dga = reactive({
  h2: null as number | null, ch4: null as number | null, c2h6: null as number | null,
  c2h4: null as number | null, c2h2: null as number | null,
  co: 0, co2: 0, voltage_level: 110,
})
const sf6 = reactive({
  measured_pressure: null as number | null, moisture_ppmv: 0,
  gas_chamber: 'arc', rated_pressure: 0.6, alarm_pressure: 0.55, lockout_pressure: 0.5,
})
const ir = reactive({
  hot_temp: null as number | null, ref_temp: null as number | null, ambient_temp: null as number | null,
  device_category: '接头和线夹',
})

// 缺陷登记表单
const defect = reactive({
  substation: '', device: '', defect_type: '',
  description: '', voltage_level: '', severity: '一般', discoverer: '',
})

// 两票审核表单
const ticketText = ref('')
const ticketType = ref<'work' | 'operation'>('work')

// 调度事故研判表单
const dispatchEventNo = ref('')

// 台区线损表单（两子模式：calc 手输参数 / analyze 选台区）
const llMode = ref<string>(props.form.mode ?? 'calc')
const llCalc = reactive({
  supply_kwh: null as number | null,
  sales_kwh: null as number | null,
  capacity_kva: 315,
})
const llAreas = ref<TransArea[]>([])
const llAreaNo = ref('')
const llDays = ref(30)

onMounted(async () => {
  if (props.form.type === 'flisr') {
    try {
      const data = await listFeeders()
      flisrFeeders.value = data.feeders ?? []
    } catch {
      // 后端未就绪时静默，下拉为空
    }
  } else if (props.form.type === 'line_loss' && llMode.value === 'analyze') {
    try {
      const data = await listAreas()
      llAreas.value = data.areas ?? []
    } catch {
      // 后端未就绪时静默，下拉为空
    }
  }
})

// 配网 FLISR 表单（馈线 + 故障区段级联，挂载时拉一次拓扑）
const flisrFeederId = ref('')
const flisrFaultSection = ref('')
const flisrFaultType = ref('永久')
const flisrFeeders = ref<FlisrFeeder[]>([])
const flisrSections = computed(() => {
  const f = flisrFeeders.value.find(x => x.feeder_id === flisrFeederId.value)
  return f ? f.sections : []
})

function onFlisrFeederChange() {
  flisrFaultSection.value = ''
}

function onDetectExceed() {
  ElMessage.warning('最多上传 20 张图片')
}

function onFile(file: File, setter: (f: File | null) => void) {
  setter(file)
}

function clearFile(setter: (f: File | null) => void) {
  setter(null)
}

const canSubmit = computed(() => {
  if (props.form.type === 'tdoa') return !!tdoaSrc.value && !!tdoaLdr.value
  if (props.form.type === 'classify') return !!classifyFile.value
  if (props.form.type === 'prpd') return !!prpdFile.value
  if (props.form.type === 'detect') return detectRawFiles.value.length > 0
  if (props.form.type === 'substation') {
    if (subMode.value === 'dga') {
      return dga.h2 !== null && dga.ch4 !== null && dga.c2h6 !== null
        && dga.c2h4 !== null && dga.c2h2 !== null
    }
    if (subMode.value === 'sf6') return sf6.measured_pressure !== null
    if (subMode.value === 'ir') {
      return ir.hot_temp !== null && ir.ref_temp !== null && ir.ambient_temp !== null
    }
  }
  if (props.form.type === 'defect') {
    return !!(defect.substation && defect.device && defect.defect_type && defect.description)
  }
  if (props.form.type === 'ticket') {
    return ticketText.value.trim().length > 0
  }
  if (props.form.type === 'dispatch') {
    return dispatchEventNo.value.trim().length > 0
  }
  if (props.form.type === 'flisr') {
    return !!flisrFeederId.value && !!flisrFaultSection.value
  }
  if (props.form.type === 'line_loss') {
    if (llMode.value === 'calc') {
      return llCalc.supply_kwh !== null && llCalc.sales_kwh !== null
    }
    if (llMode.value === 'analyze') {
      return !!llAreaNo.value
    }
  }
  return false
})

async function submit() {
  busy.value = true
  errorMsg.value = ''
  try {
    let result: any
    if (props.form.type === 'tdoa' && tdoaSrc.value && tdoaLdr.value) {
      result = await diagnoseTdoa(tdoaSrc.value, tdoaLdr.value, dtUs.value)
    } else if (props.form.type === 'classify' && classifyFile.value) {
      result = await diagnoseClassify(classifyFile.value, classifyRuleNode.value)
    } else if (props.form.type === 'prpd' && prpdFile.value) {
      result = await diagnosePrpd(prpdFile.value, prpdEngine.value)
    } else if (props.form.type === 'detect' && detectRawFiles.value.length) {
      result = await diagnoseDetectBatch(detectRawFiles.value, detectConf.value, detectIou.value)
    } else if (props.form.type === 'substation' && subMode.value === 'dga') {
      result = await diagnoseSubstationDga({
        h2: dga.h2 ?? 0, ch4: dga.ch4 ?? 0, c2h6: dga.c2h6 ?? 0,
        c2h4: dga.c2h4 ?? 0, c2h2: dga.c2h2 ?? 0,
        co: dga.co, co2: dga.co2, voltage_level: dga.voltage_level,
      })
    } else if (props.form.type === 'substation' && subMode.value === 'sf6') {
      result = await diagnoseSubstationSf6({
        measured_pressure: sf6.measured_pressure ?? 0,
        moisture_ppmv: sf6.moisture_ppmv,
        gas_chamber: sf6.gas_chamber as 'arc' | 'other',
        rated_pressure: sf6.rated_pressure,
        alarm_pressure: sf6.alarm_pressure,
        lockout_pressure: sf6.lockout_pressure,
      })
    } else if (props.form.type === 'substation' && subMode.value === 'ir') {
      result = await diagnoseSubstationIr({
        hot_temp: ir.hot_temp ?? 0,
        ref_temp: ir.ref_temp ?? 0,
        ambient_temp: ir.ambient_temp ?? 0,
        device_category: ir.device_category,
      })
    } else if (props.form.type === 'defect') {
      result = await registerDefect({
        substation: defect.substation,
        device: defect.device,
        defect_type: defect.defect_type,
        description: defect.description,
        voltage_level: defect.voltage_level,
        severity: defect.severity,
        discoverer: defect.discoverer,
      })
    } else if (props.form.type === 'ticket') {
      result = await checkTicket(ticketText.value, ticketType.value)
    } else if (props.form.type === 'dispatch') {
      result = await judgeDispatchEvent(dispatchEventNo.value.trim())
    } else if (props.form.type === 'flisr') {
      result = await runFlisr({
        feeder_id: flisrFeederId.value,
        fault_section: flisrFaultSection.value,
        fault_type: flisrFaultType.value,
      })
    } else if (props.form.type === 'line_loss' && llMode.value === 'calc') {
      result = await calcLineLoss({
        supply_kwh: llCalc.supply_kwh ?? 0,
        sales_kwh: llCalc.sales_kwh ?? 0,
        capacity_kva: llCalc.capacity_kva,
      })
    } else if (props.form.type === 'line_loss' && llMode.value === 'analyze') {
      result = await analyzeAreaLoss(llAreaNo.value, llDays.value)
    } else {
      throw new Error('表单参数不完整')
    }
    if (result.error) {
      errorMsg.value = result.error
      return
    }
    const subModeVal = props.form.type === 'substation' ? subMode.value
      : props.form.type === 'defect' ? 'register'
      : props.form.type === 'ticket' ? 'review'
      : props.form.type === 'dispatch' ? 'judge'
      : props.form.type === 'flisr' ? 'run'
      : props.form.type === 'line_loss' ? llMode.value
      : undefined
    emit('submit', { type: props.form.type, mode: subModeVal, data: result })
  } catch (e: any) {
    errorMsg.value = e.message || '诊断失败'
  } finally {
    busy.value = false
  }
}

const titleMap: Record<string, string> = {
  tdoa: '📍 TDOA 故障定位',
  classify: '⚡ 故障类型识别',
  prpd: '🔬 局放缺陷诊断',
  detect: '🔍 线路缺陷检测',
  substation: '🛠️ 变电设备诊断',
  defect: '📋 缺陷登记',
  ticket: '🧾 两票智能审核',
  dispatch: '🚨 调度事故研判',
  flisr: '🔧 配网 FLISR',
  line_loss: '📉 台区线损',
}

const descMap: Record<string, string> = {
  tdoa: '请上传源端和负荷端三相电流 CSV（列序 A B C），双端电流行波 TDOA 定位精度 MAE=10.1m。',
  classify: '请上传 9 通道电压波形 CSV（时间 + SRC/FLT/LDR 三相），4 类短路故障识别（AG/AB/ABG/ABC）。',
  prpd: '请上传 PRPD φ-q 矩阵 CSV（128×128），4 类局放缺陷识别（电树/水树/接头松动/绝缘老化）。',
  detect: '请上传线路现场图片（可一次多选），YOLOv8 批量识别破损 / 鸟巢 / 异物，并生成 HTML 检测报告。',
  substation: '油色谱三比值法 / SF6 气体 / 红外测温，基于行业标准判据的确定性诊断。',
  defect: '登记缺陷进入闭环管理：登记 → 定级 → 派单 → 处理 → 消缺 → 验收。',
  ticket: '粘贴票面做合规性辅助检查：只标差异、出建议、提疑问，不下合格性结论。',
  dispatch: '输入故障事件号，按保护动作规则推理故障类型/影响区段 + 处置建议（辅助不拍板）。',
  flisr: '选择馈线与故障区段，执行定位 → 隔离 → 恢复（FLISR），所有开关操作需值班调度确认后执行。',
  line_loss: '计算线损率判定（客观判级）或分析台区异常疑点；窃电/漏电用疑似措辞，需现场核实。',
}

const subTitleMap: Record<string, string> = {
  dga: '🛢️ 油色谱三比值法（DL/T 722）',
  sf6: '💨 SF6 压力与微水（DL/T 596）',
  ir: '🌡️ 红外测温相对温差法（DL/T 664）',
}
const subDescMap: Record<string, string> = {
  dga: '输入油中溶解气体浓度（μL/L），三比值法判断故障类型。仅当任一气体超注意值时才给结论。',
  sf6: '输入实测压力与微水，按运行中设备定值分档判断（正常/注意/报警/危急）。',
  ir: '输入热点/正常相对点/环境温度与设备类别，按相对温差 δ 判级（一般/严重/危急）。',
}
const subOptions = [
  { label: '油色谱三比值', value: 'dga' },
  { label: 'SF6 检查', value: 'sf6' },
  { label: '红外测温', value: 'ir' },
]
</script>

<template>
  <div class="interactive-form">
    <div class="form-header">
      <span class="form-title">{{ titleMap[form.type] }}</span>
      <span class="form-desc">{{ descMap[form.type] }}</span>
    </div>

    <el-form label-position="top" size="default" class="form-body">
      <!-- TDOA 表单 -->
      <template v-if="form.type === 'tdoa'">
        <el-form-item label="源端电流 CSV">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".csv"
            @change="(f: any) => onFile(f.raw, v => tdoaSrc = v)"
            @remove="() => clearFile(_ => tdoaSrc = null)"
          >
            <el-button type="primary" plain>
              <el-icon><Upload /></el-icon>选择源端文件
            </el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="负荷端电流 CSV">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".csv"
            @change="(f: any) => onFile(f.raw, v => tdoaLdr = v)"
            @remove="() => clearFile(_ => tdoaLdr = null)"
          >
            <el-button type="primary" plain>
              <el-icon><Upload /></el-icon>选择负荷端文件
            </el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="采样间隔 dt_us (μs)">
          <el-input-number v-model="dtUs" :min="0.01" :step="0.1" :precision="2" />
        </el-form-item>
      </template>

      <!-- 故障类型表单 -->
      <template v-if="form.type === 'classify'">
        <el-form-item label="9 通道电压波形 CSV">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".csv"
            @change="(f: any) => onFile(f.raw, v => classifyFile = v)"
            @remove="() => clearFile(_ => classifyFile = null)"
          >
            <el-button type="primary" plain>
              <el-icon><Upload /></el-icon>选择波形文件
            </el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="规则验证节点">
          <el-select v-model="classifyRuleNode" style="width: 180px">
            <el-option label="LDR（负荷端）" value="LDR" />
            <el-option label="FLT（故障点）" value="FLT" />
          </el-select>
        </el-form-item>
      </template>

      <!-- 局放缺陷表单 -->
      <template v-if="form.type === 'prpd'">
        <el-form-item label="PRPD φ-q 矩阵 CSV">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".csv"
            @change="(f: any) => onFile(f.raw, v => prpdFile = v)"
            @remove="() => clearFile(_ => prpdFile = null)"
          >
            <el-button type="primary" plain>
              <el-icon><Upload /></el-icon>选择 PRPD 文件
            </el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="推理引擎">
          <el-select v-model="prpdEngine" style="width: 200px">
            <el-option label="GBDT（准确率更高）" value="gbdt" />
            <el-option label="CNN" value="cnn" />
            <el-option label="双引擎对比" value="both" />
          </el-select>
        </el-form-item>
      </template>

      <!-- YOLO 缺陷检测表单 -->
      <template v-if="form.type === 'detect'">
        <el-form-item label="线路现场图片">
          <el-upload
            v-model:file-list="detectFileList"
            :auto-upload="false"
            multiple
            :limit="20"
            list-type="picture-card"
            accept=".jpg,.jpeg,.png,.bmp,.webp"
            :on-exceed="onDetectExceed"
          >
            <el-icon class="detect-plus"><Plus /></el-icon>
            <span class="detect-plus-text">选择图片（可多选）</span>
          </el-upload>
          <div class="detect-tip">支持一次多选，最多 20 张；单张不超过 10MB</div>
        </el-form-item>
        <el-form-item label="置信度阈值 conf">
          <el-input-number v-model="detectConf" :min="0.05" :max="0.95" :step="0.05" :precision="2" />
        </el-form-item>
        <el-form-item label="NMS IoU 阈值">
          <el-input-number v-model="detectIou" :min="0.05" :max="0.95" :step="0.05" :precision="2" />
        </el-form-item>
      </template>

      <!-- 变电设备诊断表单（子模式切换） -->
      <template v-if="form.type === 'substation'">
        <el-form-item label="诊断类型">
          <el-radio-group v-model="subMode">
            <el-radio-button v-for="opt in subOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="sub-title">{{ subTitleMap[subMode] }}</div>
        <div class="form-desc" style="margin-bottom: 12px">{{ subDescMap[subMode] }}</div>

        <template v-if="subMode === 'dga'">
          <div class="sub-grid">
            <el-form-item label="氢气 H₂ (μL/L)">
              <el-input-number v-model="dga.h2" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="甲烷 CH₄ (μL/L)">
              <el-input-number v-model="dga.ch4" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="乙烷 C₂H₆ (μL/L)">
              <el-input-number v-model="dga.c2h6" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="乙烯 C₂H₄ (μL/L)">
              <el-input-number v-model="dga.c2h4" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="乙炔 C₂H₂ (μL/L)">
              <el-input-number v-model="dga.c2h2" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="一氧化碳 CO (μL/L)">
              <el-input-number v-model="dga.co" :min="0" :step="1" />
            </el-form-item>
            <el-form-item label="二氧化碳 CO₂ (μL/L)">
              <el-input-number v-model="dga.co2" :min="0" :step="10" />
            </el-form-item>
            <el-form-item label="电压等级 (kV)">
              <el-input-number v-model="dga.voltage_level" :min="10" :step="110" />
            </el-form-item>
          </div>
        </template>

        <template v-else-if="subMode === 'sf6'">
          <el-form-item label="实测压力 (MPa)">
            <el-input-number v-model="sf6.measured_pressure" :min="0" :max="1" :step="0.01" :precision="2" />
          </el-form-item>
          <el-form-item label="微水含量 (μL/L，未测填 0)">
            <el-input-number v-model="sf6.moisture_ppmv" :min="0" :step="10" />
          </el-form-item>
          <el-form-item label="气室类型">
            <el-select v-model="sf6.gas_chamber" style="width: 200px">
              <el-option label="灭弧室气室（断路器）" value="arc" />
              <el-option label="其他气室" value="other" />
            </el-select>
          </el-form-item>
          <div class="sub-grid">
            <el-form-item label="额定压力 (MPa)">
              <el-input-number v-model="sf6.rated_pressure" :min="0" :max="1" :step="0.01" :precision="2" />
            </el-form-item>
            <el-form-item label="报警定值 (MPa)">
              <el-input-number v-model="sf6.alarm_pressure" :min="0" :max="1" :step="0.01" :precision="2" />
            </el-form-item>
            <el-form-item label="闭锁定值 (MPa)">
              <el-input-number v-model="sf6.lockout_pressure" :min="0" :max="1" :step="0.01" :precision="2" />
            </el-form-item>
          </div>
        </template>

        <template v-else-if="subMode === 'ir'">
          <el-form-item label="热点温度 T₁ (℃)">
            <el-input-number v-model="ir.hot_temp" :min="-40" :max="300" :step="1" />
          </el-form-item>
          <el-form-item label="正常相对应点温度 T₂ (℃)">
            <el-input-number v-model="ir.ref_temp" :min="-40" :max="300" :step="1" />
          </el-form-item>
          <el-form-item label="环境温度参照体 T₀ (℃)">
            <el-input-number v-model="ir.ambient_temp" :min="-40" :max="60" :step="1" />
          </el-form-item>
          <el-form-item label="设备类别">
            <el-select v-model="ir.device_category" style="width: 220px">
              <el-option v-for="c in ['接头和线夹','金属导线','隔离开关转头','隔离开关触头','输电导线连接器','断路器动静触头','断路器中间触头','电流互感器','套管','电容器熔丝','电容器熔丝座']" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
        </template>
      </template>

      <!-- 缺陷登记表单 -->
      <template v-if="form.type === 'defect'">
        <el-form-item label="变电站 *">
          <el-input v-model="defect.substation" placeholder="如：城东变电站" />
        </el-form-item>
        <el-form-item label="设备 *">
          <el-input v-model="defect.device" placeholder="如：1号主变" />
        </el-form-item>
        <el-form-item label="缺陷类别 *">
          <el-input v-model="defect.defect_type" placeholder="如：渗油 / 发热 / 绝缘子破损" />
        </el-form-item>
        <el-form-item label="缺陷现象描述 *">
          <el-input v-model="defect.description" type="textarea" :rows="3"
                    placeholder="描述缺陷现象、位置与严重程度" />
        </el-form-item>
        <div class="sub-grid">
          <el-form-item label="电压等级">
            <el-input v-model="defect.voltage_level" placeholder="如：110kV" />
          </el-form-item>
          <el-form-item label="严重度">
            <el-select v-model="defect.severity" style="width: 140px">
              <el-option v-for="s in ['一般', '严重', '危急']" :key="s" :label="s" :value="s" />
            </el-select>
          </el-form-item>
          <el-form-item label="发现人">
            <el-input v-model="defect.discoverer" placeholder="可选" />
          </el-form-item>
        </div>
      </template>

      <!-- 两票审核表单 -->
      <template v-if="form.type === 'ticket'">
        <el-form-item label="票种">
          <el-radio-group v-model="ticketType">
            <el-radio-button value="work">工作票</el-radio-button>
            <el-radio-button value="operation">操作票</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="票面全文 *">
          <el-input
            v-model="ticketText"
            type="textarea"
            :rows="10"
            placeholder="粘贴票面全文，包括票号、工作负责人、安全措施、步骤、签字栏等&#10;&#10;示例：&#10;工作票 编号：GZ-001&#10;第一种工作票&#10;工作负责人：张 工作许可人：刘&#10;工作班成员：A、B、C&#10;工作内容：1号主变检修&#10;安全措施：停电、接地、验电"
          />
        </el-form-item>
        <div class="ticket-rule-tip">📌 辅助检查只标差异、出建议、提疑问，不做合格性结论，票面有效性以人工复核签字为准。</div>
      </template>

      <!-- 调度事故研判表单 -->
      <template v-if="form.type === 'dispatch'">
        <el-form-item label="故障事件号 *">
          <el-input v-model="dispatchEventNo" placeholder="如：A-20260909-001" />
        </el-form-item>
        <div class="ticket-rule-tip">📌 输入事件号后按保护动作规则推理故障类型 / 影响区段 / 处置建议。示例事件：A-20260909-001（线路纵联差动）~ 004（过流重合成功）。仅出建议与疑问，事故定性以人工复核为准。</div>
      </template>

      <!-- 配网 FLISR 表单 -->
      <template v-if="form.type === 'flisr'">
        <el-form-item label="馈线 *">
          <el-select v-model="flisrFeederId" placeholder="选择馈线" style="width: 100%" @change="onFlisrFeederChange">
            <el-option
              v-for="f in flisrFeeders"
              :key="f.feeder_id"
              :label="`${f.feeder_id} ${f.feeder_name}（${f.substation}，联络 ${f.tie_to_feeder || '无'}）`"
              :value="f.feeder_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="故障区段 *">
          <el-select v-model="flisrFaultSection" placeholder="选择故障区段" style="width: 100%">
            <el-option
              v-for="s in flisrSections"
              :key="s.section_no"
              :label="`${s.section_no} ${s.section_name}（${s.load_kw}kW）`"
              :value="s.section_no"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="故障性质">
          <el-radio-group v-model="flisrFaultType">
            <el-radio-button value="永久">永久</el-radio-button>
            <el-radio-button value="瞬时">瞬时</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="ticket-rule-tip">📌 自动生成 定位 → 隔离 → 恢复 建议操作序列（含转供容量校验）。所有开关操作仅为辅助建议，需值班调度确认后执行。</div>
      </template>

      <!-- 台区线损表单（两子模式） -->
      <template v-if="form.type === 'line_loss'">
        <el-form-item label="分析类型">
          <el-radio-group v-model="llMode">
            <el-radio-button value="calc">算线损率</el-radio-button>
            <el-radio-button value="analyze">分析台区</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="llMode === 'calc'">
          <div class="sub-grid">
            <el-form-item label="供电量 (kWh) *">
              <el-input-number v-model="llCalc.supply_kwh" :min="0" :step="100" />
            </el-form-item>
            <el-form-item label="售电量 (kWh) *">
              <el-input-number v-model="llCalc.sales_kwh" :min="0" :step="100" />
            </el-form-item>
            <el-form-item label="台区容量 (kVA)">
              <el-select v-model="llCalc.capacity_kva" style="width: 140px">
                <el-option v-for="c in [50, 100, 160, 200, 315, 400, 630]" :key="c" :label="`${c} kVA`" :value="c" />
              </el-select>
            </el-form-item>
          </div>
          <div class="ticket-rule-tip">📌 线损率 = (供电量−售电量)/供电量×100%，按容量分档判定 正常/偏高/超标；售>供或 >60% 走护栏结论。</div>
        </template>

        <template v-else-if="llMode === 'analyze'">
          <el-form-item label="台区 *">
            <el-select v-model="llAreaNo" placeholder="选择台区" style="width: 100%">
              <el-option
                v-for="a in llAreas"
                :key="a.area_no"
                :label="`${a.area_no} ${a.area_name}（${a.capacity_kva}kVA · ${a.status}）`"
                :value="a.area_no"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="分析窗口 (天)">
            <el-input-number v-model="llDays" :min="7" :max="90" :step="1" />
          </el-form-item>
          <div class="ticket-rule-tip">📌 分析均值/趋势/突增日/持续超标/负线损，输出疑点（疑似窃电/漏电用疑似措辞）+ 建议，需现场核实。</div>
        </template>
      </template>
    </el-form>

    <el-alert v-if="errorMsg" :title="errorMsg" type="error" :closable="false" show-icon style="margin-bottom: 12px" />

    <div class="form-actions">
      <el-button
        type="primary"
        :loading="busy"
        :disabled="!canSubmit || busy"
        @click="submit"
      >
        {{ busy ? '诊断中…' : '开始诊断' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.interactive-form {
  margin-top: 12px;
  padding: 20px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.form-header {
  margin-bottom: 16px;
}
.form-title {
  font-size: 15px;
  font-weight: 600;
  display: block;
  margin-bottom: 4px;
}
.form-desc {
  font-size: 12px;
  color: var(--muted);
  display: block;
}
.form-body :deep(.el-form-item) {
  margin-bottom: 16px;
}
.form-actions {
  display: flex;
  justify-content: center;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.detect-plus {
  font-size: 22px;
  color: var(--accent);
}
.detect-plus-text {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}
.detect-tip {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
  margin-top: 6px;
}
.ticket-rule-tip {
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  padding: 8px 10px;
  line-height: 1.6;
}
@media (max-width: 768px) {
  .interactive-form {
    padding: 14px;
  }
  /* 固定宽度下拉改为占满，避免手机上溢出 */
  .interactive-form :deep(.el-select) {
    width: 100% !important;
  }
  /* 单选按钮组允许换行 */
  .interactive-form :deep(.el-radio-group) {
    display: flex;
    flex-wrap: wrap;
  }
  /* 附参数区堆叠为单列 */
  .sub-grid {
    grid-template-columns: 1fr;
  }
  /* 上传按钮区适配 */
  .detect-tip {
    word-break: break-all;
  }
}
</style>
