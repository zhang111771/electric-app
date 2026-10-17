/**
 * 台区线损 API 封装（后端 /api/line-loss/*）。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 * 线损率判定为客观计算；"窃电/漏电"用疑似措辞，需现场核实。
 */

/** 查询台区清单 */
export async function listAreas(): Promise<any> {
  const res = await fetch('/api/line-loss/areas', { method: 'GET' })
  if (!res.ok) throw new Error(`查询失败: ${res.status}`)
  return res.json()
}

/** 查询台区清单（不带 area_no）或某台区日电量明细（带 area_no） */
export async function queryAreaReadings(
  areaNo: string = '',
  dateFrom: string = '',
  dateTo: string = '',
): Promise<any> {
  const q = new URLSearchParams()
  if (areaNo) q.append('area_no', areaNo)
  if (dateFrom) q.append('date_from', dateFrom)
  if (dateTo) q.append('date_to', dateTo)
  const res = await fetch(`/api/line-loss/readings?${q.toString()}`, { method: 'GET' })
  if (!res.ok) throw new Error(`查询失败: ${res.status}`)
  return res.json()
}

/** 计算线损率并判定（FormData） */
export async function calcLineLoss(params: {
  supply_kwh: number
  sales_kwh: number
  capacity_kva: number
}): Promise<any> {
  const fd = new FormData()
  fd.append('supply_kwh', String(params.supply_kwh))
  fd.append('sales_kwh', String(params.sales_kwh))
  fd.append('capacity_kva', String(params.capacity_kva))
  const res = await fetch('/api/line-loss/calc', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(`计算失败: ${res.status}`)
  return res.json()
}

/** 分析台区近 N 天线损（均值/趋势/突增/持续超标/负线损 + 疑点） */
export async function analyzeAreaLoss(areaNo: string, days: number = 30): Promise<any> {
  const res = await fetch('/api/line-loss/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ area_no: areaNo, days }),
  })
  if (!res.ok) throw new Error(`分析失败: ${res.status}`)
  return res.json()
}
