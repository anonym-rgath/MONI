import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { defaultScheduler, notifyManager } from '@tanstack/react-query'
import { delay, http, HttpResponse } from 'msw'
import { useEffect } from 'react'
import { useLocation } from 'react-router'
import { describe, expect, test, vi } from 'vitest'
import { sitzungAdmin, sitzungEnroll, sitzungMfa } from '../test/fixtures'
import { Sonde, zeigeApp } from '../test/render'
import { problem, server, sitzungAntwort } from '../test/server'
import { sicheresZiel } from './Sitzung'

const ort = () => screen.getByTestId('ort').textContent
const h1 = (name: string) => screen.findByRole('heading', { level: 1, name })

// Merkt jeden Ort, den der Router annimmt (auch kurze Zwischenstationen).
const orte: string[] = []
function Spur() {
  const { pathname, search } = useLocation()
  useEffect(() => { orte.push(pathname + search) }, [pathname, search])
  return null
}

describe('Sitzung', () => {
  test('ohne Sitzung (401) gilt Stage none und /login', async () => {
    zeigeApp('/')
    await waitFor(() => expect(ort()).toBe('/login?weiter=%2F'))
    expect(await h1('Anmelden')).toBeInTheDocument()
  })

  test('schreibende Anfrage sendet den CSRF-Header aus der Sitzung', async () => {
    let csrf: string | null = null
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/sonde', ({ request }) => {
      csrf = request.headers.get('X-CSRF-Token')
      return new HttpResponse(null, { status: 204 })
    }))
    zeigeApp('/', <Sonde />)
    await h1('Übersicht')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Schreiben' }))
    await waitFor(() => expect(csrf).toBe('csrf-admin'))
  })

  test('401 mit Sitzung führt zu /login mit abgelaufen und Rücksprungziel', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.get('/api/v1/alerts', () => problem(401, 'Sitzung abgelaufen')))
    zeigeApp('/alarme?x=1', <Sonde />)
    await h1('Alarme')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Anfrage auslösen' }))
    await waitFor(() => expect(ort()).toBe('/login?abgelaufen=1&weiter=%2Falarme%3Fx%3D1'))
    expect(await h1('Anmelden')).toBeInTheDocument()
  })

  // Früher: zwei Navigationen (Handler und Wächter), je nach Reihenfolge gewann /login?weiter=… ohne abgelaufen.
  // Ein synchroner Scheduler der Abfragen erzwingt die frühere Verliererreihenfolge; beide Reihenfolgen müssen gleich enden.
  test('Ablauf navigiert genau einmal: direkt zu /login mit abgelaufen, ohne Zwischenstation', async () => {
    for (const takt of [(cb: () => void) => cb(), defaultScheduler]) {
      notifyManager.setScheduler(takt)
      try {
        server.use(sitzungAntwort(sitzungAdmin), http.get('/api/v1/alerts', () => problem(401, 'Nicht angemeldet')))
        orte.length = 0
        const { unmount } = zeigeApp('/alarme?x=1', <><Sonde /><Spur /></>)
        await h1('Alarme')
        await userEvent.setup().click(screen.getByRole('button', { name: 'Anfrage auslösen' }))
        await h1('Anmelden')
        await new Promise((r) => setTimeout(r, 50)) // späte Umleitungen hätten jetzt stattgefunden
        expect(orte).toEqual(['/alarme?x=1', '/login?abgelaufen=1&weiter=%2Falarme%3Fx%3D1'])
        unmount()
      } finally {
        notifyManager.setScheduler(defaultScheduler)
      }
    }
  })

  test('direktes api.post mit 401 "Nicht angemeldet" meldet ab (abgelaufen)', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/sonde', () => problem(401, 'Nicht angemeldet')))
    zeigeApp('/alarme', <Sonde />)
    await h1('Alarme')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Schreiben' }))
    await waitFor(() => expect(ort()).toBe('/login?abgelaufen=1&weiter=%2Falarme'))
  })

  test('direktes api.post mit 401 "Anmeldung fehlgeschlagen" meldet nicht ab', async () => {
    let gesendet = false
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/sonde', () => { gesendet = true; return problem(401, 'Anmeldung fehlgeschlagen') }))
    zeigeApp('/alarme', <Sonde />)
    await h1('Alarme')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Schreiben' }))
    await waitFor(() => expect(gesendet).toBe(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(ort()).toBe('/alarme')
    expect(screen.getByRole('heading', { level: 1, name: 'Alarme' })).toBeInTheDocument()
  })

  test('Ablauf in Stage mfa meldet ab (abgelaufen)', async () => {
    server.use(sitzungAntwort(sitzungMfa), http.post('/api/v1/auth/login/mfa', () => problem(401, 'Nicht angemeldet')))
    const user = userEvent.setup()
    zeigeApp('/login/zweiter-faktor')
    await h1('Zweiter Faktor')
    await user.type(screen.getByLabelText('Code aus der Authenticator-App'), '123456')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))
    await waitFor(() => expect(ort()).toBe('/login?abgelaufen=1'))
    expect(await h1('Anmelden')).toBeInTheDocument()
  })

  test('falscher Code in Stage mfa (401 "Anmeldung fehlgeschlagen") bleibt am Formular', async () => {
    server.use(sitzungAntwort(sitzungMfa), http.post('/api/v1/auth/login/mfa', () => problem(401, 'Anmeldung fehlgeschlagen')))
    const user = userEvent.setup()
    zeigeApp('/login/zweiter-faktor')
    await h1('Zweiter Faktor')
    await user.type(screen.getByLabelText('Code aus der Authenticator-App'), '123456')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))
    expect(await screen.findByText('Anmeldung fehlgeschlagen')).toBeInTheDocument()
    expect(ort()).toBe('/login/zweiter-faktor')
  })

  test('Ablauf in Stage enroll meldet ab (abgelaufen)', async () => {
    server.use(sitzungAntwort(sitzungEnroll), http.post('/api/v1/auth/totp/setup', () => problem(401, 'Nicht angemeldet')))
    zeigeApp('/einrichtung/zwei-faktor')
    await waitFor(() => expect(ort()).toBe('/login?abgelaufen=1'))
    expect(await h1('Anmelden')).toBeInTheDocument()
  })

  test('401 mit "Kopplungs-Token ungültig" meldet nicht ab', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.get('/api/v1/alerts', () => problem(401, 'Kopplungs-Token ungültig')))
    zeigeApp('/alarme', <Sonde />)
    await h1('Alarme')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Anfrage auslösen' }))
    await waitFor(() => expect(screen.getByTestId('fehler')).toHaveTextContent('Kopplungs-Token ungültig'))
    expect(ort()).toBe('/alarme')
  })

  test('läuft die Sitzung beim erneuten Abruf ab, geht es zu /login mit abgelaufen', async () => {
    server.use(sitzungAntwort(sitzungAdmin))
    zeigeApp('/konto')
    await h1('Konto')
    server.use(sitzungAntwort(null))
    window.dispatchEvent(new Event('visibilitychange')) // Rückkehr zum Tab löst den Neuabruf aus
    await waitFor(() => expect(ort()).toBe('/login?abgelaufen=1&weiter=%2Fkonto'))
  })

  test('abmelden sendet CSRF, leert den Query-Cache und springt nach /login', async () => {
    let csrf: string | null = null
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/auth/logout', ({ request }) => {
      csrf = request.headers.get('X-CSRF-Token')
      return new HttpResponse(null, { status: 204 })
    }))
    const user = userEvent.setup()
    zeigeApp('/', <Sonde />)
    await h1('Übersicht')
    await user.click(screen.getByRole('button', { name: 'Wert setzen' }))
    await user.click(screen.getByRole('button', { name: 'Wert lesen' }))
    expect(screen.getByTestId('wert')).toHaveTextContent('da')
    await user.click(screen.getByRole('button', { name: 'Abmelden' }))
    await waitFor(() => expect(ort()).toBe('/login'))
    expect(csrf).toBe('csrf-admin')
    await user.click(screen.getByRole('button', { name: 'Wert lesen' }))
    expect(screen.getByTestId('wert')).toHaveTextContent('undefined')
  })

  test('abmelden meldet ab, auch wenn der Manager nicht antwortet', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/auth/logout', () => HttpResponse.error()))
    zeigeApp('/')
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Abmelden' }))
    await waitFor(() => expect(ort()).toBe('/login?abmeldung=offen'))
    expect(await h1('Anmelden')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Der Manager hat das Abmelden nicht bestätigt')
  })

  test('abmelden während laufender Abfrage zeigt keine „Sitzung abgelaufen“-Meldung', async () => {
    let abgelaufen = false // die Übersicht selbst fragt /alerts ab; der 401 soll erst während des Logouts kommen
    server.use(sitzungAntwort(sitzungAdmin),
      http.get('/api/v1/alerts', () => (abgelaufen ? problem(401, 'Nicht angemeldet') : HttpResponse.json([]))),
      http.post('/api/v1/auth/logout', async () => { await delay(150); return new HttpResponse(null, { status: 204 }) }))
    const user = userEvent.setup()
    zeigeApp('/', <Sonde />)
    await h1('Übersicht')
    await user.click(screen.getByRole('button', { name: 'Abmelden' }))
    abgelaufen = true
    await user.click(screen.getByRole('button', { name: 'Anfrage auslösen' })) // 401 trifft ein, während der Logout noch läuft
    await h1('Anmelden')
    await new Promise((r) => setTimeout(r, 300))
    expect(ort()).toBe('/login')
    expect(screen.queryByText(/Sitzung ist abgelaufen/)).not.toBeInTheDocument()
  })

  test('401 auf den Logout-Aufruf selbst endet abgemeldet ohne Fehlerseite', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/auth/logout', () => problem(401, 'Nicht angemeldet')))
    zeigeApp('/')
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Abmelden' }))
    expect(await h1('Anmelden')).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(ort()).toBe('/login')
    expect(screen.queryByText(/Sitzung ist abgelaufen/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  test('abmelden hängt → nach 10 s lokal abgemeldet', async () => {
    server.use(sitzungAntwort(sitzungAdmin), http.post('/api/v1/auth/logout', async () => { await delay('infinite') }))
    zeigeApp('/')
    const knopf = await screen.findByRole('button', { name: 'Abmelden' })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      fireEvent.click(knopf)
      await vi.advanceTimersByTimeAsync(9_000)
      expect(ort()).toBe('/')
      await vi.advanceTimersByTimeAsync(1_500)
    } finally {
      vi.useRealTimers()
    }
    await waitFor(() => expect(ort()).toBe('/login?abmeldung=offen'))
    expect(await h1('Anmelden')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Der Manager hat das Abmelden nicht bestätigt')
  })

  test('sicheresZiel verwirft Open-Redirect-Ziele', () => {
    for (const böse of ['//evil.example', '/\\evil.example', '/\t/evil.example', '/\n/evil.example', '/\r/evil', '/.//evil', '/a/..//evil', '/%2e//evil', 'https://evil.example', 'javascript:alert(1)', 'alarme', '', null]) {
      expect(sicheresZiel(böse), String(böse)).toBeNull()
    }
    expect(sicheresZiel('/alarme?x=1')).toBe('/alarme?x=1')
    expect(sicheresZiel('/server/3?x=1#a')).toBe('/server/3?x=1#a')
    expect(sicheresZiel('/%2F%2Fevil')).toBe('/%2F%2Fevil')
  })

  test('Fehler beim ersten Laden der Sitzung: Hinweis statt Weiterleitung, Wiederholen funktioniert', async () => {
    const user = userEvent.setup()
    for (const antwort of [() => HttpResponse.error(), () => problem(500, 'Interner Fehler')]) {
      server.use(http.get('/api/v1/auth/session', antwort))
      const { unmount } = zeigeApp('/konto')
      const hinweis = await screen.findByRole('alert')
      expect(hinweis).toHaveTextContent(/Manager nicht erreichbar\.|Interner Fehler/)
      expect(ort()).toBe('/konto')
      server.use(sitzungAntwort(sitzungAdmin))
      await user.click(screen.getByRole('button', { name: 'Erneut versuchen' }))
      await h1('Konto')
      expect(ort()).toBe('/konto')
      unmount()
    }
  })
})
