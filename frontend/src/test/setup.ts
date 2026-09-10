import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom 未实现 matchMedia，antd 的响应式组件（Grid/useBreakpoint）依赖它
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

// 部分 antd 组件依赖 ResizeObserver，jsdom 同样未实现
if (!('ResizeObserver' in window)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  const roTarget = window as unknown as { ResizeObserver: unknown }
  roTarget.ResizeObserver = ResizeObserverStub
}

// 每个用例后清理 DOM 与 localStorage，避免用例间互相污染
afterEach(() => {
  cleanup()
  localStorage.clear()
})
