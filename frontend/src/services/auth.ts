import api from './api'

export interface LoginParams {
  username: string
  password: string
}

export interface RegisterParams {
  username: string
  email: string
  password: string
}

export const authAPI = {
  login: async (params: LoginParams) => {
    const formData = new FormData()
    formData.append('username', params.username)
    formData.append('password', params.password)
    
    const response = await api.post('/auth/login', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  register: async (params: RegisterParams) => {
    const response = await api.post('/auth/register', params)
    return response.data
  },

  getCurrentUser: async () => {
    const response = await api.get('/auth/me')
    return response.data
  },
}
