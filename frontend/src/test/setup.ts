import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, vi } from 'vitest'
import * as axeMatchers from 'vitest-axe/matchers'
import { mockMatchMedia } from './matchMedia'
import { server } from './server'

expect.extend(axeMatchers)

// axe prüft Symbole über canvas; jsdom meldet das als "Not implemented" (Rauschen), null ist die ehrliche Antwort.
HTMLCanvasElement.prototype.getContext = () => null

// jsdom kennt weder <dialog>.showModal noch ResizeObserver (Recharts). Der Esc-Handler bildet das Browserverhalten nach: cancel, dann close.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close = function () {
  if (!this.open) return
  this.removeAttribute('open')
  this.dispatchEvent(new Event('close'))
}
document.addEventListener('keydown', (e) => {
  const d = e.key === 'Escape' ? document.querySelector('dialog[open]') : null
  if (d && d.dispatchEvent(new Event('cancel', { cancelable: true }))) (d as HTMLDialogElement).close()
})
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } // nicht stubGlobal: afterEach hebt Stubs auf

// jsdom kennt matchMedia nicht; Standard: System ist dunkel.
mockMatchMedia({ light: false })

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterAll(() => server.close())

afterEach(() => {
  cleanup()
  server.resetHandlers()
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.unstubAllGlobals()
  mockMatchMedia({ light: false })
})
