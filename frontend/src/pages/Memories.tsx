import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { DatabaseOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { memoryApi, MemoryEntry, MemoryStats } from '../services/memory'

const { Title, Text } = Typography

const TYPE_COLORS: Record<string, string> = {
  fact: 'blue',
  preference: 'purple',
  procedural: 'green',
  event: 'orange',
}

const TYPE_LABELS: Record<string, string> = {
  fact: '事实',
  preference: '偏好',
  procedural: '流程',
  event: '事件',
}

const DEFAULT_TYPE_OPTIONS = [
  { value: 'fact', label: '事实' },
  { value: 'preference', label: '偏好' },
  { value: 'procedural', label: '流程' },
  { value: 'event', label: '事件' },
]

function typeColor(type: string): string {
  return TYPE_COLORS[type] ?? 'default'
}

function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type
}

function formatTime(value?: string): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function Memories() {
  const [loading, setLoading] = useState(false)
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [stats, setStats] = useState<MemoryStats | null>(null)
  const [query, setQuery] = useState('')
  const [memoryType, setMemoryType] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const pageSize = 10

  const loadStats = useCallback(async () => {
    try {
      const { data } = await memoryApi.stats()
      setStats(data.stats)
    } catch {
      // 统计失败不影响列表展示
    }
  }, [])

  const loadEntries = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await memoryApi.list({
        query: query || undefined,
        memory_type: memoryType,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      })
      setEntries(data.entries)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error(detail || '加载记忆失败')
    } finally {
      setLoading(false)
    }
  }, [query, memoryType, page])

  useEffect(() => {
    loadEntries()
  }, [loadEntries])

  useEffect(() => {
    loadStats()
  }, [loadStats])

  const handleSearch = (value: string) => {
    setQuery(value.trim())
    setPage(1)
  }

  const handleRemove = async (id: string) => {
    try {
      await memoryApi.remove(id)
      message.success('记忆已删除')
      loadEntries()
      loadStats()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error(detail || '删除失败')
    }
  }

  const handleClearAll = async () => {
    try {
      const { data } = await memoryApi.clearAll()
      message.success(`已归档 ${data.cleared} 条记忆`)
      loadEntries()
      loadStats()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error(detail || '清空失败')
    }
  }

  const columns: ColumnsType<MemoryEntry> = [
    {
      title: '记忆内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'memory_type',
      key: 'memory_type',
      width: 110,
      render: (type: string) => <Tag color={typeColor(type)}>{typeLabel(type)}</Tag>,
    },
    {
      title: '强度',
      dataIndex: 'strength',
      key: 'strength',
      width: 140,
      render: (value: number) => (
        <Progress
          percent={Math.round((value ?? 0) * 100)}
          size="small"
          status={value >= 0.7 ? 'success' : value >= 0.3 ? 'active' : 'exception'}
        />
      ),
    },
    {
      title: '命中次数',
      dataIndex: 'access_count',
      key: 'access_count',
      width: 90,
      render: (value: number) => <Text>{value ?? 0}</Text>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 160,
      render: (value: string) => <Text type="secondary">{formatTime(value)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Popconfirm
          title="删除该记忆？"
          description="删除后将不再参与检索"
          okText="删除"
          cancelText="取消"
          onConfirm={() => handleRemove(record.id)}
        >
          <Button type="text" danger icon={<DeleteOutlined />} size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const typeRows = Object.entries(stats?.by_type ?? {}).map(([type, s]) => ({
    type,
    ...s,
  }))

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <DatabaseOutlined style={{ marginRight: 8 }} />
            记忆管理
          </Title>
          <Text type="secondary">查看与维护 AI 长期记忆条目（跨会话）</Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => { loadEntries(); loadStats() }}>
              刷新
            </Button>
            <Popconfirm
              title="清空全部记忆？"
              description="全部记忆将被归档且不再参与检索，此操作不可撤销"
              okText="清空"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={handleClearAll}
            >
              <Button danger icon={<DeleteOutlined />}>
                清空全部记忆
              </Button>
            </Popconfirm>
          </Space>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8} md={6}>
          <Card size="small">
            <Statistic title="有效记忆" value={stats?.total ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={8} md={6}>
          <Card size="small">
            <Statistic title="已归档" value={stats?.archived ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={8} md={12}>
          <Card size="small">
            <Space size="large" wrap>
              {typeRows.length > 0 ? (
                typeRows.map((row) => (
                  <div key={row.type}>
                    <Tag color={typeColor(row.type)}>{typeLabel(row.type)}</Tag>
                    <Text type="secondary">{row.count} 条</Text>
                  </div>
                ))
              ) : (
                <Text type="secondary">暂无记忆条目</Text>
              )}
            </Space>
          </Card>
        </Col>
      </Row>

      <Card>
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col xs={24} md={12}>
            <Input.Search
              placeholder="搜索记忆内容（关键词）"
              allowClear
              enterButton="搜索"
              onSearch={handleSearch}
            />
          </Col>
          <Col xs={24} md={8}>
            <Select
              placeholder="全部类型"
              allowClear
              style={{ width: '100%' }}
              options={DEFAULT_TYPE_OPTIONS}
              value={memoryType}
              onChange={(v?: string) => {
                setMemoryType(v)
                setPage(1)
              }}
            />
          </Col>
        </Row>

        <Table<MemoryEntry>
          rowKey="id"
          columns={columns}
          dataSource={entries}
          loading={loading}
          locale={{ emptyText: <Empty description="暂无记忆，对话中会自动沉淀记忆" /> }}
          pagination={{
            current: page,
            pageSize,
            total: stats?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </div>
  )
}
