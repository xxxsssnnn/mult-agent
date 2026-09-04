import api from './api'

// ------------------------------------------------------------------ //
// 类型定义（与 backend/app/rag/rag_agent.py / api/rag.py 对齐）
// ------------------------------------------------------------------ //

export interface RAGDocument {
  id: string
  filename: string
  file_type: string
  checksum: string
  chunk_count: number
  status: string
  error_message?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RAGCacheStats {
  enabled: number
  semantic_enabled: number
  hits: number
  misses: number
  hit_rate: number
  exact_hits: number
  semantic_hits: number
  semantic_attempts: number
  near_misses: number
  users: number
  entries: number
}

export interface RAGStats {
  collection_name: string
  chunk_count: number
  embedding_model?: string | null
  embedding_dimension?: number | null
  persist_directory?: string
  documents?: {
    total: number
    chunks_total: number
  }
  supported_formats?: string[]
  cache?: RAGCacheStats | null
}

export interface RAGRetrievedDocument {
  content: string
  source: string
  metadata?: Record<string, unknown>
}

export interface RAGSessionMeta {
  session_id?: string | null
  enabled: boolean
  context_active: boolean
  cache_bypassed: boolean
}

export interface RAGCacheMeta {
  enabled: boolean
  hit: boolean
  key?: string | null
  matched_query?: string | null
  score?: number | null
  reason?: string | null
}

export interface RAGTransformationMeta {
  enabled: boolean
  variants: string[]
  variant_count: number
}

export interface RAGRerankMeta {
  enabled: boolean
  candidates: number
  final: number
  scores?: number[] | null
}

export interface RAGQueryResponse {
  success: boolean
  query: string
  answer: string
  retrieved_documents: RAGRetrievedDocument[]
  num_retrieved: number
  context_length: number
  transformation?: RAGTransformationMeta
  rerank?: RAGRerankMeta
  cache?: RAGCacheMeta
  session?: RAGSessionMeta
}

export interface RAGIngestItem {
  filename: string
  status: 'ingested' | 'skipped_duplicate' | 'failed'
  document_id?: string | null
  chunk_count: number
  error?: string | null
}

export interface RAGIngestResponse {
  success: boolean
  num_files: number
  num_ingested: number
  num_skipped: number
  num_failed: number
  results: RAGIngestItem[]
}

export interface RAGInfo {
  success: boolean
  rag_system: {
    version: string
    supported_file_types?: string[]
    search_strategies?: string[]
    capabilities?: string[]
    reranking?: { enabled: boolean }
    query_transformation?: { enabled: boolean; num_variants: number }
    limits?: {
      max_file_size_mb: number
      chunk_size: number
      chunk_overlap: number
    }
  }
}

export const DEFAULT_RAG_FORMATS = ['.pdf', '.docx', '.txt', '.md', '.csv']

// ------------------------------------------------------------------ //
// API 客户端
// ------------------------------------------------------------------ //

export const ragApi = {
  /** 系统信息（能力/支持的格式/检索策略） */
  info() {
    return api.get<RAGInfo>('/rag/info')
  },
  /** 当前用户知识库统计 */
  stats() {
    return api.get<{ success: boolean; stats: RAGStats }>('/rag/stats')
  },
  /** 分页列出当前用户文档 */
  documents(params?: { offset?: number; limit?: number }) {
    return api.get<{
      success: boolean
      total: number
      offset: number
      limit: number
      documents: RAGDocument[]
    }>('/rag/documents', { params })
  },
  /** 上传并导入文档（幂等去重；字段名 files，可多文件） */
  ingest(formData: FormData) {
    return api.post<RAGIngestResponse>('/rag/ingest', formData, {
      headers: { 'Content-Type': undefined },
    })
  },
  /** 删除单个文档 */
  removeDocument(documentId: string) {
    return api.delete<{ success: boolean }>(`/rag/documents/${documentId}`)
  },
  /** 清空当前用户知识库 */
  clearAll() {
    return api.delete<{ success: boolean; documents_deleted: number }>('/rag/clear')
  },
  /** 查询知识库（传 session_id 进入会话版多轮问答） */
  query(payload: {
    query: string
    k?: number
    search_type?: string
    session_id?: string
  }) {
    return api.post<RAGQueryResponse>('/rag/query', payload)
  },
}

// ------------------------------------------------------------------ //
// 工具
// ------------------------------------------------------------------ //

export function genSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `kb-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}
