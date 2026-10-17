/**
 * 调度事故研判 API 封装（后端 /api/dispatch/*）。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 * 研判结果只含推理链 + 建议 + 疑问（辅助不拍板），不下最终事故定性。
 */

/** 研判一次故障事件（保护动作 → 故障类型/区段 → 处理建议） */
export async function judgeDispatchEvent(eventNo: string): Promise<any> {
  const res = await fetch('/api/dispatch/judge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ event_no: eventNo }),
  })
  if (!res.ok) throw new Error(`研判失败: ${res.status}`)
  return res.json()
}

/** 查询故障事件台账（多条件 AND） */
export async function listDispatchEvents(params: {
  status?: string
  substation?: string
  keyword?: string
  limit?: number
}): Promise<any> {
  const q = new URLSearchParams()
  if (params.status) q.append('status', params.status)
  if (params.substation) q.append('substation', params.substation)
  if (params.keyword) q.append('keyword', params.keyword)
  q.append('limit', String(params.limit ?? 50))
  const res = await fetch(`/api/dispatch/events?${q.toString()}`, { method: 'GET' })
  if (!res.ok) throw new Error(`查询失败: ${res.status}`)
  return res.json()
}

/** 推进故障事件状态（已处置；待研判事件必须先研判） */
export async function updateDispatchStatus(
  eventNo: string,
  action: string = '已处置',
  note: string = '',
): Promise<any> {
  const res = await fetch(`/api/dispatch/event/${encodeURIComponent(eventNo)}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ action, note }),
  })
  if (!res.ok) throw new Error(`状态更新失败: ${res.status}`)
  return res.json()
}
