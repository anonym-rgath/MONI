import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'
import { describe, expect, test } from 'vitest'
import { sitzungAdmin, sitzungBetrachter, sitzungEnroll, sitzungMfa } from '../test/fixtures'
import { zeigeApp } from '../test/render'
import { server, sitzungAntwort } from '../test/server'

const ort = () => screen.getByTestId('ort').textContent
const h1 = (name: string) => screen.findByRole('heading', { level: 1, name })

describe('Routenwächter', () => {
  test('Stage mfa wird auf /login/zweiter-faktor geleitet, enroll auf /einrichtung/zwei-faktor', async () => {
    server.use(sitzungAntwort(sitzungMfa))
    const { unmount } = zeigeApp('/konto')
    await waitFor(() => expect(ort()).toBe('/login/zweiter-faktor'))
    unmount()
    server.use(sitzungAntwort(sitzungEnroll))
    zeigeApp('/')
    await waitFor(() => expect(ort()).toBe('/einrichtung/zwei-faktor'))
  })

  test('mit Stage full führen Anmeldeseiten zur Übersicht', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    zeigeApp('/login')
    await waitFor(() => expect(ort()).toBe('/'))
  })

  test('Betrachter bekommt auf /verwaltung/benutzer "Keine Berechtigung"', async () => {
    server.use(sitzungAntwort(sitzungBetrachter))
    zeigeApp('/verwaltung/benutzer')
    expect(await h1('Keine Berechtigung')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Benutzer' })).not.toBeInTheDocument()
  })

  test('Admin sieht die Verwaltung', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    zeigeApp('/verwaltung/benutzer')
    expect(await h1('Benutzer')).toBeInTheDocument()
  })

  test('jede Route aus D9 hat eine Seite mit H1', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    const pfade = ['/', '/server/1', '/alarme', '/alarme/verlauf', '/alarme/regeln', '/alarme/kanaele', '/verwaltung/server',
      '/verwaltung/benutzer', '/verwaltung/tokens', '/verwaltung/audit', '/verwaltung/anmeldung', '/konto']
    for (const p of pfade) {
      const { unmount } = zeigeApp(p)
      expect(await screen.findByRole('heading', { level: 1 })).toHaveAttribute('tabindex', '-1')
      expect(ort()).toBe(p)
      unmount()
    }
  })

  test('Navigation markiert die aktive Seite und setzt den Fokus auf die H1 (nicht beim ersten Laden)', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    const user = userEvent.setup()
    zeigeApp('/')
    const h1Start = await h1('Übersicht')
    expect(h1Start).not.toHaveFocus()
    expect(screen.getByRole('link', { name: 'Übersicht' })).toHaveAttribute('aria-current', 'page')
    await user.click(screen.getByRole('link', { name: /^Alarme/ }))
    const h1Alarme = await h1('Alarme')
    await waitFor(() => expect(h1Alarme).toHaveFocus())
    expect(screen.getByRole('link', { name: /^Alarme/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Übersicht' })).not.toHaveAttribute('aria-current')
    expect(document.title).toBe('Alarme – Moni')
  })

  test('Seitenrahmen mit Platzhalter hat keine axe-Verstöße', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    const { container } = zeigeApp('/')
    await h1('Übersicht')
    expect(await axe(container)).toHaveNoViolations()
  })
})
