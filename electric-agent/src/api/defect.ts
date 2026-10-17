/**
 * 缺陷闭环管理 API 封装（后端 /api/defect/*）。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 */

/** 登记一条缺陷 */
export async function registerDefect(params: {
  substation: string
  device: string
  defect_type: string
  description: string
  voltage_level?: string
  severity?: string
  source?: string
  discoverer?: string
  plan_time?: string
}): Promise<any> {
  const formData = new FormData()
  formData.append('substation', params.substation)
  formData.append('device', params.device)
  formData.append('defect_type', params.defect_type)
  formData.append('description', params.description)
  formData.append('voltage_level', params.voltage_level ?? '')
  formData.append('severity', params.severity ?? '一般')
  formData.append('source', params.source ?? '巡检')
  formData.append('discoverer', params.discoverer ?? '')
  formData.append('plan_time', params.plan_time ?? '')
  const res = await fetch('/api/defect/register', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`登记失败: ${res.status}`)
  return res.json()
}

/** 查询缺陷台账（多条件 AND） */
export async function queryDefects(params: {
  status?: string
  severity?: string
  substation?: string
  keyword?: string
  limit?: number
}): Promise<any> {
  const q = new URLSearchParams()
  if (params.status) q.append('status', params.status)
  if (params.severity) q.append('severity', params.severity)
  if (params.substation) q.append('substation', params.substation)
  if (params.keyword) q.append('keyword', params.keyword)
  q.append('limit', String(params.limit ?? 20))
  const res = await fetch(`/api/defect/list?${q.toString()}`, { method: 'GET' })
  if (!res.ok) throw new Error(`查询失败: ${res.status}`)
  return res.json()
}

/** 推进缺陷状态（状态机校验） */
export async function updateDefectStatus(params: {
  defect_no: string
  action: string
  severity?: string
  assignee?: string
  result_note?: string
  plan_time?: string
}): Promise<any> {
  const formData = new FormData()
  formData.append('defect_no', params.defect_no)
  formData.append('action', params.action)
  formData.append('severity', params.severity ?? '')
  formData.append('assignee', params.assignee ?? '')
  formData.append('result_note', params.result_note ?? '')
  formData.append('plan_time', params.plan_time ?? '')
  const res = await fetch('/api/defect/status', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`状态更新失败: ${res.status}`)
  return res.json()
}

/** 查询单条缺陷详情及关联工单 */
export async function getDefectDetail(defectNo: string): Promise<any> {
  const res = await fetch(`/api/defect/detail/${encodeURIComponent(defectNo)}`, {
    method: 'GET',
  })
  if (!res.ok) throw new Error(`详情查询失败: ${res.status}`)
  return res.json()
}
