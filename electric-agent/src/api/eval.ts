/** 诊断工具客观评测 API（后端 /api/eval）。 */
import type { EvalReport, EvalResponse } from '../types'

const API_BASE = '/api'

/** 获取最新评测报告 + 历史记录 */
export async function getEval(history = 10): Promise<EvalResponse> {
  const res = await fetch(`${API_BASE}/eval?history=${history}`)
  if (!res.ok) throw new Error(`获取评测结果失败: ${res.status}`)
  return res.json()
}

/** 触发一次评测，返回完整报告 */
export async function runEval(tools?: string[]): Promise<EvalReport> {
  const qs = tools?.length ? `?tools=${tools.join(',')}` : ''
  const res = await fetch(`${API_BASE}/eval/run${qs}`, { method: 'POST' })
  if (!res.ok) throw new Error(`评测失败: ${res.status}`)
  return res.json()
}
