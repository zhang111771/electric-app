/**
 * 配网 FLISR API 封装（后端 /api/flisr/*）。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 * FLISR 结果为建议操作步骤（定位/隔离/恢复），所有开关操作需人工确认后执行。
 */

/** 查询馈线拓扑清单（含各段负荷与边界开关，供下拉选择） */
export async function listFeeders(): Promise<any> {
  const res = await fetch('/api/flisr/feeders', { method: 'GET' })
  if (!res.ok) throw new Error(`查询失败: ${res.status}`)
  return res.json()
}

/** 执行馈线故障定位→隔离→恢复（FLISR）辅助决策 */
export async function runFlisr(payload: {
  feeder_id: string
  fault_section: string
  fault_type?: string
}): Promise<any> {
  const res = await fetch('/api/flisr/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`执行失败: ${res.status}`)
  return res.json()
}
