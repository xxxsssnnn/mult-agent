import { Card, Row, Col, Statistic } from 'antd'
import {
  RobotOutlined,
  TagsOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'

export default function Dashboard() {
  return (
    <div>
      <h1>仪表盘</h1>
      <Row gutter={16} style={{ marginTop: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Agent总数"
              value={12}
              prefix={<RobotOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="活跃任务"
              value={45}
              prefix={<TagsOutlined />}
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="已完成"
              value={128}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="待处理"
              value={8}
              prefix={<ClockCircleOutlined />}
              valueStyle={{ color: '#faad14' }}
            />
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 24 }}>
        <h2>系统状态</h2>
        <p>所有服务运行正常 ✓</p>
      </Card>
    </div>
  )
}
