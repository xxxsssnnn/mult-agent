import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { UploadProps } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  DeleteOutlined,
  FileSearchOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  WechatOutlined,
} from '@ant-design/icons'
import {
  DEFAULT_RAG_FORMATS,
  genSessionId,
  ragApi,
  RAGDocument,
  RAGIngestResponse,
  RAGQueryResponse,
  RAGRetrievedDocument,
  RAGStats,
} from '../services/rag'

const { Title, Text, Paragraph } = Typography

// ------------------------------------------------------------------ //
// 类型
// ------------------------------------------------------------------ //

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  error?: boolean
  sources?: RAGRetrievedDocument[]
  extras?: {
    num_retrieved?: number
    context_active?: boolean
    cache_hit?: boolean
    cache_bypassed?: boolean
    transformed?: boolean
    reranked?: boolean
  }
}

const SEARCH_STRATEGY_OPTIONS = [
  { value: 'hybrid', label: 'hybrid · 混合检索（BM25+向量+RRF）' },
  { value: 'similarity', label: 'similarity · 向量相似度' },
  { value: 'score', label: 'score · 分数检索' },
  { value: 'mmr', label: 'mmr · 多样性检索' },
]

const STATUS_META: Record<string, { color: string; label: string }> = {
  indexed: { color: 'success', label: '已索引' },
  failed: { color: 'error', label: '失败' },
}

let msgSeq = 0
function nextMsgId(): string {
  msgSeq += 1
  return `msg-${Date.now()}-${msgSeq}`
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function extractErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

// ------------------------------------------------------------------ //
// 页面
// ------------------------------------------------------------------ //

export default function KnowledgeBase() {
  const [stats, setStats] = useState<RAGStats | null>(null)
  const [docs, setDocs] = useState<RAGDocument[]>([])
  const [docsLoading, setDocsLoading] = useState(false)
  const [supportedFormats, setSupportedFormats] = useState<string[]>(DEFAULT_RAG_FORMATS)

  // 会话版问答（Phase 5）：session_id 贯穿多轮追问
  const [sessionId, setSessionId] = useState<string>(() => genSessionId())
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [strategy, setStrategy] = useState('hybrid')

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // ------------------------------------------------------------------ //
  // 数据加载
  // ------------------------------------------------------------------ //

  const loadStats = useCallback(async () => {
    try {
      const { data } = await ragApi.stats()
      setStats(data.stats)
    } catch {
      // 统计失败不影响页面其它功能
    }
  }, [])

  const loadDocs = useCallback(async () => {
    setDocsLoading(true)
    try {
      const { data } = await ragApi.documents({ offset: 0, limit: 200 })
      setDocs(data.documents)
    } catch (err) {
      message.error(extractErrorDetail(err, '加载文档列表失败'))
    } finally {
      setDocsLoading(false)
    }
  }, [])

  const loadInfo = useCallback(async () => {
    try {
      const { data } = await ragApi.info()
      const types = data.rag_system?.supported_file_types
      if (types?.length) setSupportedFormats(types)
    } catch {
      // 信息接口不可用时使用默认支持格式
    }
  }, [])

  useEffect(() => {
    loadDocs()
    loadStats()
    loadInfo()
  }, [loadDocs, loadStats, loadInfo])

  // 消息滚动到底部
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending])

  // ------------------------------------------------------------------ //
  // 会话操作
  // ------------------------------------------------------------------ //

  const startNewSession = () => {
    setSessionId(genSessionId())
    setMessages([])
    setInput('')
  }

  const handleSend = useCallback(
    async (raw: string) => {
      const query = raw.trim()
      if (!query || sending) return
      if (stats && (stats.documents?.total ?? 0) === 0) {
        message.warning('知识库为空，请先上传文档再提问')
        return
      }

      const sid = sessionId
      const userMsg: ChatMessage = { id: nextMsgId(), role: 'user', content: query }
      setMessages((prev) => [...prev, userMsg])
      setInput('')
      setSending(true)

      try {
        const { data } = await ragApi.query({
          query,
          search_type: strategy,
          session_id: sid,
        })
        const ans = buildAssistantMessage(data)
        setMessages((prev) => [...prev, ans])
        if (data.session?.session_id) setSessionId(data.session.session_id)
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: nextMsgId(),
            role: 'assistant',
            content: extractErrorDetail(err, '查询失败，请稍后重试'),
            error: true,
          },
        ])
      } finally {
        setSending(false)
        inputRef.current?.focus()
      }
    },
    [sessionId, strategy, sending, stats]
  )

  // ------------------------------------------------------------------ //
  // 文档管理
  // ------------------------------------------------------------------ //

  const refreshAll = () => {
    loadDocs()
    loadStats()
  }

  const handleRemoveDoc = async (id: string) => {
    try {
      await ragApi.removeDocument(id)
      message.success('文档已删除')
      refreshAll()
    } catch (err) {
      message.error(extractErrorDetail(err, '删除失败'))
    }
  }

  const handleClearAll = async () => {
    try {
      const { data } = await ragApi.clearAll()
      message.success(`已清空知识库（删除 ${data.documents_deleted ?? 0} 份文档）`)
      refreshAll()
    } catch (err) {
      message.error(extractErrorDetail(err, '清空失败'))
    }
  }

  const handleIngestDone = useCallback(
    (resp: RAGIngestResponse) => {
      const ok = resp.results.filter((r) => r.status === 'ingested')
      const dup = resp.results.filter((r) => r.status === 'skipped_duplicate')
      const failed = resp.results.filter((r) => r.status === 'failed')
      if (ok.length) message.success(`成功导入 ${ok.length} 份文档`)
      if (dup.length) message.info(`${dup.length} 份内容重复已跳过（幂等去重）`)
      if (failed.length) {
        message.warning(
          `${failed.length} 份导入失败：${failed.map((r) => `${r.filename}: ${r.error ?? '未知错误'}`).join('；')}`
        )
      }
      refreshAll()
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [docs, stats]
  )

  const uploadRequest: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    const formData = new FormData()
    formData.append('files', file as File)
    try {
      const { data } = await ragApi.ingest(formData)
      handleIngestDone(data)
      onSuccess?.(data)
    } catch (err) {
      message.error(extractErrorDetail(err, '导入失败'))
      onError?.(err as Error)
    }
  }

  // ------------------------------------------------------------------ //
  // 表格 / 渲染
  // ------------------------------------------------------------------ //

  const columns: ColumnsType<RAGDocument> = [
    {
      title: '文件名',
      dataIndex: 'filename',
      key: 'filename',
      ellipsis: true,
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'file_type',
      key: 'file_type',
      width: 90,
      render: (t: string) => <Tag>{t.replace('.', '').toUpperCase()}</Tag>,
    },
    {
      title: '分块',
      dataIndex: 'chunk_count',
      key: 'chunk_count',
      width: 80,
      align: 'right',
      render: (v: number) => <Text>{v ?? 0}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => {
        const meta = STATUS_META[s] ?? { color: 'default', label: s }
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '导入时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (v: string) => <Text type="secondary">{formatTime(v)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 70,
      render: (_, record) => (
        <Popconfirm
          title="删除该文档？"
          description="其向量分块将一并移除"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleRemoveDoc(record.id)}
        >
          <Button type="text" danger size="small" icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  const chatHeight = useMemo(() => 'calc(100vh - 356px)', [])

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <FileSearchOutlined style={{ marginRight: 8 }} />
            知识库问答
          </Title>
          <Text type="secondary">
            RAG 多轮会话问答：上传文档建立专属知识库，可连续追问（Phase 5 session 记忆）
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={refreshAll}>
              刷新
            </Button>
            <Popconfirm
              title="清空整个知识库？"
              description="全部文档及其向量分块将被删除，此操作不可撤销"
              okText="清空"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={handleClearAll}
            >
              <Button danger icon={<DeleteOutlined />}>
                清空知识库
              </Button>
            </Popconfirm>
          </Space>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="知识库文档" value={stats?.documents?.total ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="向量分块" value={stats?.documents?.chunks_total ?? stats?.chunk_count ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="语义缓存命中率"
              value={stats?.cache ? `${Math.round((stats.cache.hit_rate ?? 0) * 100)}%` : '-'}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="当前会话轮次" value={messages.filter((m) => m.role === 'user').length} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        {/* ---------------- 左：知识库管理 ---------------- */}
        <Col xs={24} lg={10}>
          <Card
            size="small"
            title="知识库管理"
            extra={
              <Text type="secondary">
                支持 {supportedFormats.join(' ')}
              </Text>
            }
            style={{ height: chatHeight, display: 'flex', flexDirection: 'column' }}
            styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
          >
            <Upload.Dragger
              multiple
              accept={supportedFormats.join(',')}
              showUploadList={false}
              customRequest={uploadRequest}
              style={{ marginBottom: 12 }}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
              <p className="ant-upload-hint">多文件批量导入 · 相同内容自动跳过 · 单文件大小受服务端限制</p>
            </Upload.Dragger>

            <Table<RAGDocument>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={docs}
              loading={docsLoading}
              pagination={false}
              locale={{ emptyText: <Empty description="暂无文档，上传后即可提问" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
              style={{ flex: 1 }}
              scroll={{ y: `calc(${chatHeight} - 300px)` }}
            />
          </Card>
        </Col>

        {/* ---------------- 右：会话问答 ---------------- */}
        <Col xs={24} lg={14}>
          <Card
            size="small"
            title={
              <Space>
                <WechatOutlined />
                会话问答
                {sessionId && (
                  <Tag color={messages.length > 0 ? 'processing' : 'default'}>
                    {messages.length > 0 ? '多轮会话进行中' : '新会话'}
                  </Tag>
                )}
              </Space>
            }
            extra={
              <Space>
                <Select
                  size="small"
                  value={strategy}
                  onChange={setStrategy}
                  options={SEARCH_STRATEGY_OPTIONS}
                  style={{ width: 260 }}
                />
                <Button size="small" icon={<PlusOutlined />} onClick={startNewSession}>
                  新建会话
                </Button>
              </Space>
            }
            style={{ height: chatHeight, display: 'flex', flexDirection: 'column' }}
            styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
          >
            {/* 消息区 */}
            <div
              ref={scrollRef}
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '8px 12px',
                background: '#fafafa',
                borderRadius: 8,
                marginBottom: 12,
              }}
            >
              {messages.length === 0 && !sending ? (
                <Empty
                  style={{ marginTop: 48 }}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <Space direction="vertical" size={4}>
                      <Text type="secondary">向知识库提问，可连续追问（自动带入上下文）</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        会话 ID：{sessionId.slice(0, 8)}… · 回答下方展开「引用片段」
                      </Text>
                    </Space>
                  }
                />
              ) : (
                messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
                ))
              )}
              {sending && (
                <div style={{ display: 'flex', margin: '8px 0' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Spin size="small" />
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      正在检索并生成回答…
                    </Text>
                  </div>
                </div>
              )}
            </div>

            {/* 输入区 */}
            <div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      handleSend(input)
                    }
                  }}
                  placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
                  style={{
                    flex: 1,
                    minHeight: 48,
                    maxHeight: 120,
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px solid #d9d9d9',
                    resize: 'none',
                    outline: 'none',
                    fontSize: 14,
                    lineHeight: 1.6,
                    fontFamily: 'inherit',
                  }}
                />
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={sending}
                  onClick={() => handleSend(input)}
                  style={{ height: 48 }}
                >
                  发送
                </Button>
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}

// ------------------------------------------------------------------ //
// 消息气泡
// ------------------------------------------------------------------ //

function buildAssistantMessage(data: RAGQueryResponse): ChatMessage {
  const extras: ChatMessage['extras'] = {
    num_retrieved: data.num_retrieved,
    context_active: data.session?.context_active,
    cache_hit: data.cache?.hit,
    cache_bypassed: data.session?.cache_bypassed,
    transformed: data.transformation?.enabled && (data.transformation.variant_count ?? 0) > 0,
    reranked: data.rerank?.enabled,
  }
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: data.answer || '（未返回回答内容）',
    sources: data.retrieved_documents ?? [],
    extras,
  }
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  const sources = message.sources ?? []
  const extras = message.extras

  const badges: { color: string; label: string }[] = []
  if (extras?.num_retrieved !== undefined) {
    badges.push({ color: 'blue', label: `检索 ${extras.num_retrieved} 片段` })
  }
  if (extras?.cache_hit) badges.push({ color: 'gold', label: '缓存命中' })
  if (extras?.context_active) badges.push({ color: 'purple', label: '会话上下文' })
  if (extras?.cache_bypassed) badges.push({ color: 'orange', label: '缓存旁路' })
  if (extras?.transformed) badges.push({ color: 'cyan', label: '查询扩展' })
  if (extras?.reranked) badges.push({ color: 'green', label: '重排' })

  const citationItems = (sources as RAGRetrievedDocument[]).map((doc, i) => ({
    key: `${message.id}-src-${i}`,
    label: (
      <Text style={{ fontSize: 12 }}>
        [{i + 1}] {doc.source || '未知来源'}
      </Text>
    ),
    children: <SourceDetail doc={doc} />,
  }))

  return (
    <div style={{ display: 'flex', margin: '10px 0', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div
        style={{
          maxWidth: '88%',
          padding: '8px 12px',
          borderRadius: 12,
          background: isUser ? '#1677ff' : '#fff',
          color: isUser ? '#fff' : 'rgba(0,0,0,0.88)',
          boxShadow: isUser ? 'none' : '0 1px 2px rgba(0,0,0,0.06)',
          border: isUser ? 'none' : '1px solid #f0f0f0',
          overflow: 'hidden',
        }}
      >
        <Paragraph
          style={{
            marginBottom: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            color: 'inherit',
          }}
        >
          {message.content}
        </Paragraph>

        {!isUser && badges.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <Space size={[4, 4]} wrap>
              {badges.map((b) => (
                <Tag key={b.label} color={b.color} style={{ marginInlineEnd: 0, fontSize: 11 }}>
                  {b.label}
                </Tag>
              ))}
            </Space>
          </div>
        )}

        {!isUser && citationItems.length > 0 && (
          <Collapse
            ghost
            size="small"
            style={{ marginTop: 8, background: 'transparent' }}
            items={citationItems}
          />
        )}

        {!isUser && message.error && (
          <Tag color="error" style={{ marginTop: 6 }}>
            查询出错
          </Tag>
        )}
      </div>
    </div>
  )
}

function SourceDetail({ doc }: { doc: RAGRetrievedDocument }) {
  const meta = doc.metadata ?? {}
  const metaKeys = Object.keys(meta).filter((k) => !['filename', 'source'].includes(k))
  return (
    <div style={{ background: '#fafafa', borderRadius: 6, padding: '4px 10px' }}>
      <Paragraph
        style={{
          margin: '6px 0',
          fontSize: 13,
          color: 'rgba(0,0,0,0.75)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {doc.content}
      </Paragraph>
      {metaKeys.length > 0 && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          元数据：{metaKeys.map((k) => `${k}=${String(meta[k])}`).join(' · ')}
        </Text>
      )}
    </div>
  )
}
