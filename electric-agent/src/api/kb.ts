/**
 * 知识库管理 API（后端 server/main.py /api/kb/*，经 vite 代理转发到 :8000）。
 */
import type {
  KbChunk,
  KbChunkPayload,
  KbRebuildResult,
  KbStats,
} from '../types'

const API_BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // 非 JSON 响应，保留状态码
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

/** 知识库统计：总块数 + 各来源数量 */
export function getKbStats(): Promise<KbStats> {
  return request(`${API_BASE}/kb/stats`)
}

/** 所有已入库的来源名（下拉选择用） */
export async function getKbSources(): Promise<string[]> {
  const r = await request<{ sources: string[] }>(`${API_BASE}/kb/sources`)
  return r.sources
}

/** 列出知识块，支持按内容搜索 / 按来源筛选（倒序，最新在前） */
export async function getKbChunks(
  q = '',
  source = '',
  limit = 300,
): Promise<KbChunk[]> {
  const params = new URLSearchParams({ q, source, limit: String(limit) })
  const r = await request<{ chunks: KbChunk[] }>(`${API_BASE}/kb/chunks?${params}`)
  return r.chunks
}

/** 新增单个知识块（自动编码向量入库） */
export function addKbChunk(payload: KbChunkPayload): Promise<{ id: number }> {
  return request(`${API_BASE}/kb/chunks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 编辑知识块（改了正文会自动重新编码向量） */
export function updateKbChunk(
  id: number,
  payload: Partial<KbChunkPayload>,
): Promise<{ id: number }> {
  return request(`${API_BASE}/kb/chunks/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 删除单个知识块 */
export function deleteKbChunk(id: number): Promise<{ ok: boolean }> {
  return request(`${API_BASE}/kb/chunks/${id}`, { method: 'DELETE' })
}

/** 从 data/assets/ 的 markdown 全量重建知识库（清空后重新入库） */
export function rebuildKb(): Promise<KbRebuildResult> {
  return request(`${API_BASE}/kb/rebuild`, { method: 'POST' })
}
