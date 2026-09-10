import { describe, it, expect, afterEach, vi } from 'vitest'
import { authAPI } from '../auth'
import api from '../api'

describe('authAPI', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('login 以 multipart/form-data 提交用户名密码', async () => {
    const postSpy = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { access_token: 't' } } as any)

    const data = await authAPI.login({ username: 'alice', password: 'secret' })

    expect(data).toEqual({ access_token: 't' })
    const [url, body, config] = postSpy.mock.calls[0]
    expect(url).toBe('/auth/login')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('username')).toBe('alice')
    expect((body as FormData).get('password')).toBe('secret')
    expect((config as any).headers['Content-Type']).toBe('multipart/form-data')
  })

  it('register 以 JSON 提交注册信息', async () => {
    const postSpy = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { id: 1 } } as any)

    await authAPI.register({
      username: 'alice',
      email: 'alice@example.com',
      password: 'secret',
    })

    expect(postSpy).toHaveBeenCalledWith('/auth/register', {
      username: 'alice',
      email: 'alice@example.com',
      password: 'secret',
    })
  })

  it('getCurrentUser 请求 /auth/me', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValue({ data: { username: 'alice' } } as any)

    const data = await authAPI.getCurrentUser()

    expect(getSpy).toHaveBeenCalledWith('/auth/me')
    expect(data).toEqual({ username: 'alice' })
  })
})
