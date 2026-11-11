import { vi } from 'vitest'

export function mockMatchMedia({ light }: { light: boolean }) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: light && query.includes('light'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
}
