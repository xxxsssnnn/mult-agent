import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Login from '../Login'
import { authAPI } from '../../services/auth'

const navigateMock = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom'
  )
  return { ...actual, useNavigate: () => navigateMock }
})

function renderLogin() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Login 页面', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    navigateMock.mockReset()
    localStorage.clear()
  })

  it('渲染用户名、密码输入框与登录按钮', () => {
    renderLogin()
    expect(screen.getByPlaceholderText('用户名')).toBeTruthy()
    expect(screen.getByPlaceholderText('密码')).toBeTruthy()
    // antd 会在两个中文字符间自动插入空格，实际文案为 "登 录"
    expect(screen.getByRole('button', { name: /登\s*录/ })).toBeTruthy()
  })

  it('提交成功后写入令牌并跳转 dashboard', async () => {
    vi.spyOn(authAPI, 'login').mockResolvedValue({
      access_token: 'access-1',
      refresh_token: 'refresh-1',
    } as any)

    renderLogin()
    await userEvent.type(screen.getByPlaceholderText('用户名'), 'alice')
    await userEvent.type(screen.getByPlaceholderText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))

    await waitFor(() => expect(authAPI.login).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith('/dashboard')
    )
    expect(localStorage.getItem('access_token')).toBe('access-1')
    expect(localStorage.getItem('refresh_token')).toBe('refresh-1')
  })
})
