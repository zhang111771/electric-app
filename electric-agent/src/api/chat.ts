/**
 * 后端 API 封装：SSE 流式聊天 + 会话管理。
 *
 * 后端地址通过 vite 代理转发（/chat、/api → http://localhost:8000）。
 */
import type {
  FeedbackRating,
  SSEHandlers,
  ThreadInfo,
  ThreadMessage,
} from '../types'

const API_BASE = '/api'

/**
 * 流式聊天 — 用 fetch + ReadableStream 解析 SSE。
 *
 * 后端事件格式（每行一条，\n\n 分隔）：
 *   data: {"type":"token","content":"..."}
 *   data: {"type":"error","content":"..."}
 *   data: {"type":"done","chart":{...}}
 */
export async function chatStream(
  message: string,
  sessionId: string,
  handlers: SSEHandlers,
): Promise<void> {
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })

  if (!res.ok || !res.body) {
    handlers.onError?.(`请求失败 (HTTP ${res.status})，请确认后端已启动`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 事件以空行 \n\n 分隔
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''

      for (const raw of events) {
        if (!raw.trim()) continue
        for (const line of raw.split('\n')) {
          if (!line.startsWith('data: ')) continue
          try {
            const evt = JSON.parse(line.slice(6))
            if (evt.type === 'token') {
              handlers.onToken?.(evt.content)
            } else if (evt.type === 'error') {
              handlers.onError?.(evt.content)
            } else if (evt.type === 'done') {
              handlers.onDone?.({
                chart: evt.chart ?? null,
                form: evt.form ?? null,
              })
            }
          } catch {
            // 忽略无法解析的分片
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 创建一个新会话 id */
export async function newSession(): Promise<string> {
  const res = await fetch(`${API_BASE}/session`)
  if (!res.ok) throw new Error(`创建会话失败: ${res.status}`)
  const data = await res.json()
  return data.session_id
}

/** 获取所有历史会话列表 */
export async function getThreads(): Promise<ThreadInfo[]> {
  const res = await fetch(`${API_BASE}/threads`)
  if (!res.ok) throw new Error(`获取会话列表失败: ${res.status}`)
  const data = await res.json()
  return data.threads ?? []
}

/** 获取指定会话的完整消息历史 */
export async function getThreadDetail(sessionId: string): Promise<ThreadMessage[]> {
  const res = await fetch(`${API_BASE}/threads/${encodeURIComponent(sessionId)}`)
  if (!res.ok) throw new Error(`获取会话详情失败: ${res.status}`)
  const data = await res.json()
  return data.messages ?? []
}

/** 删除指定会话的对话历史 */
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/session/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!res.ok) throw new Error(`删除会话失败: ${res.status}`)
}

/** 提交对 AI 回答的评分（重复提交同一条回答由后端覆盖为最新评分） */
export async function submitFeedback(
  sessionId: string,
  query: string,
  answer: string,
  rating: FeedbackRating,
): Promise<void> {
  const res = await fetch(`${API_BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      query,
      answer,
      rating,
    }),
  })
  if (!res.ok) throw new Error(`提交反馈失败: ${res.status}`)
}
