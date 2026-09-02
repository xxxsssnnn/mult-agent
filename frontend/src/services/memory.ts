import api from './api'

export interface MemoryEntry {
  id: string
  content: string
  memory_type: string
  strength: number
  access_count: number
  session_id?: string
  created_at?: string
  updated_at?: string
  score?: number
}

export interface MemoryTypeStat {
  count: number
  avg_strength: number
  total_access_count: number
}

export interface MemoryStats {
  total: number
  archived: number
  by_type: Record<string, MemoryTypeStat>
}

export const memoryApi = {
  list(params: {
    query?: string
    memory_type?: string
    limit?: number
    offset?: number
  }) {
    return api.get<{ success: boolean; count: number; entries: MemoryEntry[] }>(
      '/memory/entries',
      { params }
    )
  },
  stats() {
    return api.get<{ success: boolean; stats: MemoryStats }>('/memory/stats')
  },
  remove(id: string) {
    return api.delete<{ success: boolean }>(`/memory/entries/${id}`)
  },
  clearAll() {
    return api.delete<{ success: boolean; cleared: number }>('/memory/entries')
  },
}
