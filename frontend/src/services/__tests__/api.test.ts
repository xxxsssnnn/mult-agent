import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import api from '../api'

// axios 拦截器以 handlers 数组暴露，取第一个拦截器的 fulfilled/rejected 直接验证
function requestFulfilled() {
  const handlers = (
    api.interceptors.request as unknown as {
      handlers: Array<{ fulfilled: (c: any) => any }>
    }
  ).handlers
  return handlers[0].fulfilled
}

function responseRejected() {
  const handlers = (
    api.interceptors.response as unknown as {
      handlers: Array<{ rejected: (e: any) => Promise<never> }>
    }
  ).handlers
  return handlers[0].rejected
}

describe('api 服务', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('存在 token 时注入 Bearer 认证头', () => {
    localStorage.setItem('access_token', 'tok-123')
    const config = requestFulfilled()({ headers: {} })
    expect(config.headers.Authorization).toBe('Bearer tok-123')
  })

  it('无 token 时不注入 Authorization', () => {
    const config = requestFulfilled()({ headers: {} })
    expect(config.headers.Authorization).toBeUndefined()
  })

  it('响应 401 时清除令牌并跳转登录页', async () => {
    localStorage.setItem('access_token', 'a')
    localStorage.setItem('refresh_token', 'b')
    const fakeLocation = { href: '' }
    vi.stubGlobal('location', fakeLocation)

    await expect(
      responseRejected()({ response: { status: 401 } })
    ).rejects.toBeDefined()

    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(fakeLocation.href).toBe('/login')
  })
})
