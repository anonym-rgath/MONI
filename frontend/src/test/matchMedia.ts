import { vi } from 'vitest'

type Hoerer = (e: { matches: boolean }) => void

// Hörer bleiben erhalten; wechsleSystem() löst sie aus wie ein Wechsel der Systemeinstellung.
let hell = false
const hoerer = new Set<Hoerer>()

export function mockMatchMedia({ light }: { light: boolean }) {
  hell = light
  hoerer.clear()
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() { return hell && query.includes('light') },
    media: query,
    addEventListener: (_: string, h: Hoerer) => hoerer.add(h),
    removeEventListener: (_: string, h: Hoerer) => hoerer.delete(h),
  }))
}

export function wechsleSystem(light: boolean) {
  hell = light
  hoerer.forEach((h) => h({ matches: light }))
}
