/**
 * 电缆故障诊断 API 封装：文件上传 + 4 个诊断接口。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 */

/** 上传 CSV 文件，返回文件路径列表 */
export async function uploadDiagnoseFiles(files: File[]): Promise<string[]> {
  const formData = new FormData()
  for (const f of files) {
    formData.append('files', f)
  }
  const res = await fetch('/api/diagnose/upload', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`上传失败: ${res.status}`)
  const data = await res.json()
  return data.files ?? []
}

/** TDOA 双端电流行波故障定位 */
export async function diagnoseTdoa(
  srcFile: File,
  ldrFile: File,
  dtUs: number = 0.2,
): Promise<any> {
  const formData = new FormData()
  formData.append('src_csv', srcFile)
  formData.append('ldr_csv', ldrFile)
  formData.append('dt_us', String(dtUs))
  const res = await fetch('/api/diagnose/tdoa', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`TDOA 诊断失败: ${res.status}`)
  return res.json()
}

/** 4 类短路故障类型识别 */
export async function diagnoseClassify(
  waveformFile: File,
  ruleNode: string = 'LDR',
): Promise<any> {
  const formData = new FormData()
  formData.append('waveform_csv', waveformFile)
  formData.append('rule_node', ruleNode)
  const res = await fetch('/api/diagnose/classify', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`故障类型识别失败: ${res.status}`)
  return res.json()
}

/** 4 类局放缺陷类型识别 */
export async function diagnosePrpd(
  prpdFile: File,
  engine: string = 'gbdt',
): Promise<any> {
  const formData = new FormData()
  formData.append('prpd_csv', prpdFile)
  formData.append('engine', engine)
  const res = await fetch('/api/diagnose/prpd', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`局放缺陷识别失败: ${res.status}`)
  return res.json()
}

/** YOLO 线路缺陷检测（破损 / 鸟巢 / 异物） */
export async function diagnoseDetect(
  imageFile: File,
  conf: number = 0.25,
  iou: number = 0.45,
): Promise<any> {
  const formData = new FormData()
  formData.append('image', imageFile)
  formData.append('conf', String(conf))
  formData.append('iou', String(iou))
  const res = await fetch('/api/diagnose/detect', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`缺陷检测失败: ${res.status}`)
  return res.json()
}

/** YOLO 线路缺陷批量检测（多图，返回批次汇总 + 每图结果 + HTML 报告 URL） */
export async function diagnoseDetectBatch(
  files: File[],
  conf: number = 0.25,
  iou: number = 0.45,
): Promise<any> {
  const formData = new FormData()
  for (const f of files) {
    formData.append('files', f)
  }
  formData.append('conf', String(conf))
  formData.append('iou', String(iou))
  const res = await fetch('/api/diagnose/detect_batch', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`批量缺陷检测失败: ${res.status}`)
  return res.json()
}

/* ===== 变电设备诊断（纯规则判据，DL/T 722 / 596 / 664） ===== */

/** 变压器油色谱三比值法诊断（DL/T 722-2014） */
export async function diagnoseSubstationDga(params: {
  h2: number
  ch4: number
  c2h6: number
  c2h4: number
  c2h2: number
  co?: number
  co2?: number
  voltage_level?: number
}): Promise<any> {
  const formData = new FormData()
  formData.append('h2', String(params.h2))
  formData.append('ch4', String(params.ch4))
  formData.append('c2h6', String(params.c2h6))
  formData.append('c2h4', String(params.c2h4))
  formData.append('c2h2', String(params.c2h2))
  formData.append('co', String(params.co ?? 0))
  formData.append('co2', String(params.co2 ?? 0))
  formData.append('voltage_level', String(params.voltage_level ?? 110))
  const res = await fetch('/api/diagnose/substation/dga', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`油色谱诊断失败: ${res.status}`)
  return res.json()
}

/** SF6 气体压力与微水检查（DL/T 596） */
export async function diagnoseSubstationSf6(params: {
  measured_pressure: number
  moisture_ppmv?: number
  gas_chamber?: 'arc' | 'other'
  rated_pressure?: number
  alarm_pressure?: number
  lockout_pressure?: number
}): Promise<any> {
  const formData = new FormData()
  formData.append('measured_pressure', String(params.measured_pressure))
  formData.append('moisture_ppmv', String(params.moisture_ppmv ?? 0))
  formData.append('gas_chamber', params.gas_chamber ?? 'arc')
  formData.append('rated_pressure', String(params.rated_pressure ?? 0.6))
  formData.append('alarm_pressure', String(params.alarm_pressure ?? 0.55))
  formData.append('lockout_pressure', String(params.lockout_pressure ?? 0.5))
  const res = await fetch('/api/diagnose/substation/sf6', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`SF6 检查失败: ${res.status}`)
  return res.json()
}

/** 红外测温相对温差法诊断（DL/T 664-2016） */
export async function diagnoseSubstationIr(params: {
  hot_temp: number
  ref_temp: number
  ambient_temp: number
  device_category?: string
}): Promise<any> {
  const formData = new FormData()
  formData.append('hot_temp', String(params.hot_temp))
  formData.append('ref_temp', String(params.ref_temp))
  formData.append('ambient_temp', String(params.ambient_temp))
  formData.append('device_category', params.device_category ?? '接头和线夹')
  const res = await fetch('/api/diagnose/substation/ir', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`红外测温诊断失败: ${res.status}`)
  return res.json()
}
