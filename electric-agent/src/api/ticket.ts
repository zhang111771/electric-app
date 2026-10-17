/**
 * 两票智能审核 API 封装（后端 /api/ticket/check）。
 *
 * 后端地址通过 vite 代理转发（/api → http://localhost:8000）。
 * 结果只含差异项 + 建议 + 疑问，不返回合格性结论。
 */

/** 审核一张工作票/操作票 */
export async function checkTicket(
  ticketText: string,
  ticketType: 'work' | 'operation' = 'work',
): Promise<any> {
  const res = await fetch('/api/ticket/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ ticket_text: ticketText, ticket_type: ticketType }),
  })
  if (!res.ok) throw new Error(`审核失败: ${res.status}`)
  return res.json()
}
