import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { App } from '../App'
import { mockMatchMedia } from '../test/matchMedia'
import { DarstellungProvider } from './Darstellung'
import { Rahmen } from './Rahmen'

const admin = { name: 'Robin', rolle: 'admin' } as const
const betrachter = { name: 'Bea', rolle: 'betrachter' } as const

const zeigeRahmen = (benutzer: typeof admin | typeof betrachter, offeneAlarme: number) =>
  render(<DarstellungProvider><Rahmen benutzer={benutzer} offeneAlarme={offeneAlarme}><h1>Test</h1></Rahmen></DarstellungProvider>)

afterEach(() => vi.restoreAllMocks())

describe('Darstellung', () => {
  test('folgt dem System und lässt sich überschreiben', async () => {
    const user = userEvent.setup()
    mockMatchMedia({ light: true })
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
    await user.click(screen.getByRole('button', { name: 'Dunkel' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: 'Dunkel' })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('moni.darstellung')).toBe('dunkel')
  })

  test('ohne lesbaren Speicher gilt Auto', async () => {
    const user = userEvent.setup()
    mockMatchMedia({ light: true })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('gesperrt') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('gesperrt') })
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Dunkel' }))  // Schreiben schlägt fehl, die Wahl gilt trotzdem
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  test('gespeicherte Wahl hat Vorrang vor dem System', () => {
    mockMatchMedia({ light: true })
    localStorage.setItem('moni.darstellung', 'dunkel')
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})

describe('Rahmen', () => {
  test('Sprunglink führt zu main und ist der erste Tab-Stopp', async () => {
    const user = userEvent.setup()
    zeigeRahmen(admin, 2)
    await user.tab()
    const link = screen.getByRole('link', { name: 'Zum Inhalt springen' })
    expect(link).toHaveFocus()
    expect(link).toHaveAttribute('href', '#inhalt')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'inhalt')
  })

  test('hat keine axe-Verstöße', async () => {
    const { container } = zeigeRahmen(admin, 2)
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Verwaltung ist für Betrachter nicht in der Navigation', () => {
    const { unmount } = zeigeRahmen(betrachter, 0)
    const nav = screen.getByRole('navigation', { name: 'Hauptnavigation' })
    expect(nav).not.toHaveTextContent('Verwaltung')
    expect(screen.getByText('Bea · Betrachter')).toBeInTheDocument()
    unmount()
    zeigeRahmen(admin, 0)
    expect(screen.getByRole('link', { name: 'Verwaltung' })).toBeInTheDocument()
  })

  test('Alarmzähler steht auch als Text da', () => {
    zeigeRahmen(admin, 2)
    expect(screen.getByRole('link', { name: 'Alarme 2 offen' })).toBeInTheDocument()
  })
})
