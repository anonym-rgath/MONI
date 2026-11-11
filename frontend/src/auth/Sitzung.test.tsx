import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { sitzungAdmin } from '../test/fixtures'
import { Sonde, zeigeApp } from '../test/render'
import { problem, server, sitzungAntwort } from '../test/server'
import { sicheresZiel } from './Sitzung'

const ort = () => screen.getByTestId('ort').textContent
const h1 = (name: string) => screen.findByRole('heading', { level: 1, name })

describe('Sitzung', () => {
  test('ohne Sitzung (401) gilt Stage none und /login', async () => {
    zeigeApp('/')
    await waitFor(() => expect(ort()).toBe('/login'))
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
    await waitFor(() => expect(ort()).toBe('/login'))
    expect(await h1('Anmelden')).toBeInTheDocument()
  })

  test('sicheresZiel verwirft Open-Redirect-Ziele', () => {
    expect(sicheresZiel('/alarme?x=1')).toBe('/alarme?x=1')
    expect(sicheresZiel('//evil.example')).toBeNull()
    expect(sicheresZiel('/\\evil.example')).toBeNull()
    expect(sicheresZiel('https://evil.example')).toBeNull()
    expect(sicheresZiel('alarme')).toBeNull()
    expect(sicheresZiel(null)).toBeNull()
  })
})
