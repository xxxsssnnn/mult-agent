import { Table, Button, Space, Tag, Progress } from 'antd'
import { EyeOutlined, StopOutlined } from '@ant-design/icons'

export default function Tasks() {
  const columns = [
    {
      title: '任务ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 100,
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => {
        const colorMap: Record<string, string> = {
          pending: 'orange',
          running: 'blue',
          completed: 'green',
          failed: 'red',
          cancelled: 'default',
        }
        return <Tag color={colorMap[status] || 'default'}>{status}</Tag>
      },
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      render: (priority: number) => (
        <Progress
          percent={priority * 10}
          showInfo={false}
          strokeColor={priority > 7 ? '#ff4d4f' : priority > 4 ? '#faad14' : '#52c41a'}
        />
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Space size="middle">
          <Button icon={<EyeOutlined />} size="small">
            查看
          </Button>
          {record.status === 'running' && (
            <Button icon={<StopOutlined />} danger size="small">
              取消
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const dataSource = [
    {
      key: '1',
      task_id: 'task-001',
      title: '代码生成任务',
      description: '生成用户认证模块',
      status: 'completed',
      priority: 5,
      created_at: '2024-01-15 10:30:00',
    },
    {
      key: '2',
      task_id: 'task-002',
      title: '代码审查',
      description: '审查API接口代码',
      status: 'running',
      priority: 8,
      created_at: '2024-01-15 11:00:00',
    },
    {
      key: '3',
      task_id: 'task-003',
      title: '性能优化',
      description: '优化数据库查询性能',
      status: 'pending',
      priority: 6,
      created_at: '2024-01-15 11:30:00',
    },
  ]

  return (
    <div>
      <h1>任务管理</h1>
      <Table columns={columns} dataSource={dataSource} style={{ marginTop: 24 }} />
    </div>
  )
}
