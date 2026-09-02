import { List, Avatar, Typography, Empty } from 'antd'
import { MessageOutlined } from '@ant-design/icons'

const { Text } = Typography

export default function Conversations() {
  const conversations = [
    {
      id: '1',
      title: 'Python代码优化讨论',
      lastMessage: '建议使用列表推导式来提高性能...',
      time: '2024-01-15 14:30',
      messageCount: 12,
    },
    {
      id: '2',
      title: 'API设计评审',
      lastMessage: 'RESTful API的设计需要考虑版本控制...',
      time: '2024-01-15 13:20',
      messageCount: 8,
    },
    {
      id: '3',
      title: '数据库架构讨论',
      lastMessage: '对于高并发场景，建议引入读写分离...',
      time: '2024-01-15 12:10',
      messageCount: 15,
    },
  ]

  return (
    <div>
      <h1>对话管理</h1>
      
      {conversations.length > 0 ? (
        <List
          itemLayout="horizontal"
          dataSource={conversations}
          style={{ marginTop: 24 }}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                avatar={<Avatar icon={<MessageOutlined />} style={{ backgroundColor: '#1890ff' }} />}
                title={<a href={`/conversations/${item.id}`}>{item.title}</a>}
                description={
                  <div>
                    <Text ellipsis>{item.lastMessage}</Text>
                    <div style={{ marginTop: 4 }}>
                      <Text type="secondary">{item.time}</Text>
                      <Text type="secondary" style={{ marginLeft: 16 }}>
                        {item.messageCount} 条消息
                      </Text>
                    </div>
                  </div>
                }
              />
            </List.Item>
          )}
        />
      ) : (
        <Empty description="暂无对话记录" style={{ marginTop: 100 }} />
      )}
    </div>
  )
}
