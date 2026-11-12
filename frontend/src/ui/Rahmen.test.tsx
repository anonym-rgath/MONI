import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { sitzungAdmin, sitzungBetrachter } from '../test/fixtures'
import { mockMatchMedia, wechsleSystem } from '../test/matchMedia'
import { zeigeApp } from '../test/render'
import { server, sitzungAntwort } from '../test/server'

afterEach(() => vi.restoreAllMocks())

const zeigeRahmen = async (s: typeof sitzungAdmin) => {
  server.use(sitzungAntwort(s))
  const r = zeigeApp('/')
  await screen.findByRole('heading', { level: 1, name: 'Übersicht' })
  return r
}

describe('Darstellung', () => {
  test('folgt dem System und lässt sich überschreiben', async () => {
    const user = userEvent.setup()
    mockMatchMedia({ light: true })
    await zeigeRahmen(sitzungAdmin)
    expect(document.documentElement.dataset.theme).toBe('light')
    await user.click(screen.getByRole('button', { name: 'Dunkel' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: 'Dunkel' })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('moni.darstellung')).toBe('dunkel')
  })

  test('Auto folgt einem Wechsel der Systemeinstellung', async () => {
    mockMatchMedia({ light: false })
    await zeigeRahmen(sitzungAdmin)
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.documentElement.dataset.theme).toBe('dark')
    act(() => wechsleSystem(true))
    expect(document.documentElement.dataset.theme).toBe('light')
    act(() => wechsleSystem(false))
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  test('Klick auf Auto speichert auto', async () => {
    const user = userEvent.setup()
    localStorage.setItem('moni.darstellung', 'hell')
    await zeigeRahmen(sitzungAdmin)
    expect(screen.getByRole('button', { name: 'Hell' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Auto' }))
    expect(localStorage.getItem('moni.darstellung')).toBe('auto')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('ohne lesbaren Speicher gilt Auto', async () => {
    const user = userEvent.setup()
    mockMatchMedia({ light: true })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('gesperrt') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('gesperrt') })
    await zeigeRahmen(sitzungAdmin)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Dunkel' }))  // Schreiben schlägt fehl, die Wahl gilt trotzdem
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  test('gespeicherte Wahl hat Vorrang vor dem System', async () => {
    mockMatchMedia({ light: true })
    localStorage.setItem('moni.darstellung', 'dunkel')
    await zeigeRahmen(sitzungAdmin)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})

describe('Rahmen', () => {
  test('Sprunglink führt zu main und ist der erste Tab-Stopp', async () => {
    const user = userEvent.setup()
    await zeigeRahmen(sitzungAdmin)
    await user.tab()
    const link = screen.getByRole('link', { name: 'Zum Inhalt springen' })
    expect(link).toHaveFocus()
    expect(link).toHaveAttribute('href', '#inhalt')
    const main = screen.getByRole('main')
    expect(main).toHaveAttribute('id', 'inhalt')
    expect(main).toHaveAttribute('tabindex', '-1')
  })

  test('hat keine axe-Verstöße', async () => {
    const { container } = await zeigeRahmen(sitzungAdmin)
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Benutzer und Rolle kommen aus der Sitzung, Verwaltung nur für Admins', async () => {
    const { unmount } = await zeigeRahmen(sitzungBetrachter)
    expect(screen.getByRole('navigation', { name: 'Hauptnavigation' })).not.toHaveTextContent('Verwaltung')
    expect(screen.getByText('Bea · Betrachter')).toBeInTheDocument()
    unmount()
    await zeigeRahmen(sitzungAdmin)
    expect(screen.getByRole('link', { name: 'Verwaltung' })).toBeInTheDocument()
    expect(screen.getByText('Robin · Admin')).toBeInTheDocument()
  })

  test('Alarmzähler steht auch als Text da', async () => {
    await zeigeRahmen(sitzungAdmin)
    expect(screen.getByRole('link', { name: 'Alarme 0 offen' })).toBeInTheDocument()
  })
})
