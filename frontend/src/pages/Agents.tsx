import { Table, Button, Space, Tag, Modal, Form, Input, Select, message } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { useState } from 'react'

export default function Agents() {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [form] = Form.useForm()

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
    },
    {
      title: '能力',
      dataIndex: 'capabilities',
      key: 'capabilities',
      render: (caps: string[]) => (
        <>
          {caps.map((cap) => (
            <Tag key={cap} color="blue">
              {cap}
            </Tag>
          ))}
        </>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'active' ? 'green' : 'red'}>
          {status}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any) => (
        <Space size="middle">
          <Button icon={<EditOutlined />} size="small">
            编辑
          </Button>
          <Button icon={<DeleteOutlined />} danger size="small">
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const dataSource = [
    {
      key: '1',
      name: 'Coder Agent',
      type: 'coder',
      description: '代码生成和优化工具',
      capabilities: ['code_generation', 'code_review'],
      status: 'active',
    },
    {
      key: '2',
      name: 'Reviewer Agent',
      type: 'reviewer',
      description: '代码审查和安全审计',
      capabilities: ['code_review', 'security_audit'],
      status: 'active',
    },
  ]

  const handleCreate = async (values: any) => {
    console.log('Creating agent:', values)
    message.success('Agent创建成功')
    setIsModalOpen(false)
    form.resetFields()
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <h1>Agent管理</h1>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setIsModalOpen(true)}
        >
          创建Agent
        </Button>
      </div>

      <Table columns={columns} dataSource={dataSource} />

      <Modal
        title="创建Agent"
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
      >
        <Form form={form} onFinish={handleCreate} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入Agent名称' }]}
          >
            <Input placeholder="Agent名称" />
          </Form.Item>
          <Form.Item
            name="type"
            label="类型"
            rules={[{ required: true, message: '请选择Agent类型' }]}
          >
            <Select placeholder="选择类型">
              <Select.Option value="coder">Coder</Select.Option>
              <Select.Option value="reviewer">Reviewer</Select.Option>
              <Select.Option value="planner">Planner</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="Agent描述" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block>
              创建
            </Button>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
