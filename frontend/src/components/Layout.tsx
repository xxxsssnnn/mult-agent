import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { Layout as AntLayout, Menu } from 'antd'
import {
  DashboardOutlined,
  RobotOutlined,
  TagsOutlined,
  MessageOutlined,
  LogoutOutlined,
} from '@ant-design/icons'
import { useState } from 'react'

const { Header, Sider, Content } = AntLayout

const menuItems = [
  {
    key: '/dashboard',
    icon: <DashboardOutlined />,
    label: '仪表盘',
  },
  {
    key: '/agents',
    icon: <RobotOutlined />,
    label: 'Agent管理',
  },
  {
    key: '/tasks',
    icon: <TagsOutlined />,
    label: '任务管理',
  },
  {
    key: '/conversations',
    icon: <MessageOutlined />,
    label: '对话管理',
  },
]

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)

  const handleLogout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    navigate('/login')
  }

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div style={{ padding: '16px', textAlign: 'center', color: 'white' }}>
          <h2 style={{ margin: 0, color: 'white' }}>
            {collapsed ? 'MA' : 'Multi-Agent'}
          </h2>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <AntLayout>
        <Header style={{ background: '#fff', padding: '0 24px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
          <Menu
            mode="horizontal"
            items={[
              {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '退出登录',
                onClick: handleLogout,
              },
            ]}
          />
        </Header>
        <Content style={{ margin: '24px', padding: 24, background: '#fff', minHeight: 280 }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  )
}
