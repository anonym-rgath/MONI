import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, expect, vi } from 'vitest'
import * as axeMatchers from 'vitest-axe/matchers'
import { mockMatchMedia } from './matchMedia'

expect.extend(axeMatchers)

// jsdom kennt matchMedia nicht; Standard: System ist dunkel.
mockMatchMedia({ light: false })

afterEach(() => {
  cleanup()
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.unstubAllGlobals()
  mockMatchMedia({ light: false })
})
