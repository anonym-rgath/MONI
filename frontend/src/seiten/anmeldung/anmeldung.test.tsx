import { focusManager, useQueryClient } from '@tanstack/react-query'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import type { Session } from '../../api/typen'
import { sitzungAdmin, sitzungEnroll, sitzungMfa } from '../../test/fixtures'
import { zeigeApp } from '../../test/render'
import { problem, server } from '../../test/server'

const B = '/api/v1'
const ort = () => screen.getByTestId('ort').textContent
const h1 = (name: string) => screen.findByRole('heading', { level: 1, name })

// Die "Serversitzung": POST-Handler setzen sie, GET /auth/session liefert sie.
let aktuelle: Session | null = null
beforeEach(() => {
  aktuelle = null
  server.use(http.get(`${B}/auth/session`, () => (aktuelle ? HttpResponse.json(aktuelle) : problem(401, 'Nicht angemeldet'))))
})

const anmelden = async (user: ReturnType<typeof userEvent.setup>, mail = 'robin@example.test', pw = 'geheim-1234') => {
  await user.type(await screen.findByLabelText('E-Mail-Adresse'), mail)
  await user.type(screen.getByLabelText('Passwort'), pw)
  await user.click(screen.getByRole('button', { name: 'Weiter' }))
}

describe('Login', () => {
  test('Felder sind leer und tragen die richtigen autocomplete-Werte', async () => {
    zeigeApp('/login')
    const mail = await screen.findByLabelText('E-Mail-Adresse')
    expect(mail).toHaveValue('')
    expect(mail).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Passwort')).toHaveValue('')
    expect(screen.getByLabelText('Passwort')).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Darstellung' })).toBeInTheDocument()
  })

  test('stage mfa führt zur Codeeingabe, enroll zur Einrichtung', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/auth/login`, async ({ request }) => {
      expect(await request.json()).toEqual({ email: 'robin@example.test', password: 'geheim-1234' })
      aktuelle = sitzungMfa
      return HttpResponse.json({ stage: 'mfa' })
    }))
    const { unmount } = zeigeApp('/login')
    await anmelden(user)
    await waitFor(() => expect(ort()).toBe('/login/zweiter-faktor'))
    expect(await h1('Zweiter Faktor')).toBeInTheDocument()
    unmount()

    aktuelle = null
    server.use(http.post(`${B}/auth/login`, () => {
      aktuelle = sitzungEnroll
      return HttpResponse.json({ stage: 'enroll' })
    }))
    zeigeApp('/login')
    await anmelden(user)
    await waitFor(() => expect(ort()).toBe('/einrichtung/zwei-faktor'))
  })

  test('das Rücksprungziel bleibt über den zweiten Faktor erhalten', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/auth/login`, () => {
      aktuelle = sitzungMfa
      return HttpResponse.json({ stage: 'mfa' })
    }))
    zeigeApp('/login?weiter=%2Fkonto')
    await anmelden(user)
    await waitFor(() => expect(ort()).toBe('/login/zweiter-faktor?weiter=%2Fkonto'))
  })

  test('falsches Passwort zeigt detail der API und behält die E-Mail', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/auth/login`, () => problem(401, 'E-Mail oder Passwort falsch.')))
    zeigeApp('/login')
    await anmelden(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('E-Mail oder Passwort falsch.')
    expect(screen.getByLabelText('E-Mail-Adresse')).toHaveValue('robin@example.test')
    expect(ort()).toBe('/login')
  })

  test('OIDC-Anbieter erscheinen als Links mit richtiger start-URL; ohne Anbieter keine Trennlinie', async () => {
    server.use(http.get(`${B}/auth/oidc`, () => HttpResponse.json({ providers: [{ id: 'google', label: 'Google' }, { id: 'ms/ü', label: 'Microsoft' }] })))
    const { unmount } = zeigeApp('/login')
    expect(await screen.findByRole('link', { name: 'Mit Google anmelden' })).toHaveAttribute('href', '/api/v1/auth/oidc/start?provider=google')
    expect(screen.getByRole('link', { name: 'Mit Microsoft anmelden' })).toHaveAttribute('href', '/api/v1/auth/oidc/start?provider=ms%2F%C3%BC')
    expect(screen.getByText('oder')).toBeInTheDocument()
    unmount()

    server.use(http.get(`${B}/auth/oidc`, () => HttpResponse.json({ providers: [] })))
    zeigeApp('/login')
    await screen.findByLabelText('E-Mail-Adresse')
    expect(screen.queryByText('oder')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /anmelden/ })).not.toBeInTheDocument()
  })

  test('fehler=oidc und abgelaufen=1 zeigen ihre Hinweise', async () => {
    const { unmount } = zeigeApp('/login?fehler=oidc')
    expect(await screen.findByRole('alert')).toHaveTextContent('Die Anmeldung über den Anbieter ist fehlgeschlagen. Versuche es erneut oder melde dich mit Passwort an.')
    unmount()
    zeigeApp('/login?abgelaufen=1')
    expect(await screen.findByText('Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.')).toBeInTheDocument()
  })

  test('wenn das Setup nötig ist, geht es zur Einrichtung', async () => {
    server.use(http.get(`${B}/setup`, () => HttpResponse.json({ required: true })))
    zeigeApp('/login')
    await waitFor(() => expect(ort()).toBe('/einrichtung'))
    expect(await h1('Einrichtung')).toBeInTheDocument()
  })

  test('Absenden ist während der Anfrage gesperrt', async () => {
    const user = userEvent.setup()
    let frei = () => {}
    const sperre = new Promise<void>((r) => { frei = r })
    let anzahl = 0
    server.use(http.post(`${B}/auth/login`, async () => {
      anzahl++
      await sperre
      return problem(401, 'E-Mail oder Passwort falsch.')
    }))
    zeigeApp('/login')
    await anmelden(user)
    const knopf = screen.getByRole('button', { name: /Weiter|Wird gesendet/ })
    await waitFor(() => expect(knopf).toBeDisabled())
    await user.click(knopf)
    frei()
    await screen.findByRole('alert')
    expect(anzahl).toBe(1)
  })

  test('Anmeldeseite hat keine axe-Verstöße', async () => {
    server.use(http.get(`${B}/auth/oidc`, () => HttpResponse.json({ providers: [{ id: 'google', label: 'Google' }] })))
    const { container } = zeigeApp('/login?fehler=oidc')
    await screen.findByRole('link', { name: 'Mit Google anmelden' })
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('Zweiter Faktor', () => {
  beforeEach(() => { aktuelle = sitzungMfa })

  test('TOTP-Feld: numerisch, one-time-code; Erfolg führt zu weiter bzw. /', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/auth/login/mfa`, async ({ request }) => {
      expect(await request.json()).toEqual({ code: '123456' })
      aktuelle = sitzungAdmin
      return HttpResponse.json({ stage: 'full' })
    }))
    const { unmount } = zeigeApp('/login/zweiter-faktor?weiter=%2Fkonto')
    const feld = await screen.findByLabelText('Code aus der Authenticator-App')
    expect(feld).toHaveAttribute('inputmode', 'numeric')
    expect(feld).toHaveAttribute('autocomplete', 'one-time-code')
    await user.type(feld, '123456')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))
    await waitFor(() => expect(ort()).toBe('/konto'))
    unmount()

    aktuelle = sitzungMfa
    zeigeApp('/login/zweiter-faktor?weiter=%2F%2Fevil.test')
    await user.type(await screen.findByLabelText('Code aus der Authenticator-App'), '123456')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))
    await waitFor(() => expect(ort()).toBe('/'))
  })

  test('eingefügter Code mit Leerzeichen wird bereinigt', async () => {
    const user = userEvent.setup()
    zeigeApp('/login/zweiter-faktor')
    const feld = await screen.findByLabelText('Code aus der Authenticator-App')
    await user.click(feld)
    await user.paste('123 456')
    expect(feld).toHaveValue('123456')
  })

  test('Umschalter wechselt auf den Wiederherstellungscode und zurück', async () => {
    const user = userEvent.setup()
    let gesendet: unknown
    server.use(http.post(`${B}/auth/login/mfa`, async ({ request }) => {
      gesendet = await request.json()
      return problem(401, 'Anmeldung fehlgeschlagen') // fester Backend-Text für falsche Faktoren; meldet nicht ab (P6-R13)
    }))
    zeigeApp('/login/zweiter-faktor')
    await user.click(await screen.findByRole('button', { name: 'Stattdessen Wiederherstellungscode verwenden' }))
    expect(screen.queryByLabelText('Code aus der Authenticator-App')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Wiederherstellungscode'), 'abcd-1234')
    await user.click(screen.getByRole('button', { name: 'Anmelden' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Anmeldung fehlgeschlagen')
    expect(gesendet).toEqual({ code: 'abcd-1234' })
    expect(screen.getByLabelText('Wiederherstellungscode')).toHaveValue('abcd-1234')
    await user.click(screen.getByRole('button', { name: 'Stattdessen Code aus der App verwenden' }))
    expect(screen.getByLabelText('Code aus der Authenticator-App')).toHaveValue('')
  })

  test('Seite hat keine axe-Verstöße', async () => {
    const { container } = zeigeApp('/login/zweiter-faktor')
    await h1('Zweiter Faktor')
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('Ersteinrichtung', () => {
  beforeEach(() => { server.use(http.get(`${B}/setup`, () => HttpResponse.json({ required: true }))) })

  test('legt das Konto an und führt zur Zwei-Faktor-Einrichtung; Hinweis zum Setup-Token', async () => {
    const user = userEvent.setup()
    let gesendet: unknown
    server.use(http.post(`${B}/setup`, async ({ request }) => {
      gesendet = await request.json()
      aktuelle = sitzungEnroll
      return HttpResponse.json({ stage: 'enroll' })
    }))
    zeigeApp('/einrichtung')
    await user.type(await screen.findByLabelText('Setup-Token'), 'tok-123')
    expect(screen.getByLabelText('Setup-Token')).toHaveAccessibleDescription('Das Setup-Token steht im Log des Managers.')
    await user.type(screen.getByLabelText('Name'), 'Robin')
    await user.type(screen.getByLabelText('E-Mail-Adresse'), 'robin@example.test')
    await user.type(screen.getByLabelText('Passwort'), 'geheim-1234-xyz')
    await user.click(screen.getByRole('button', { name: 'Konto anlegen' }))
    await waitFor(() => expect(ort()).toBe('/einrichtung/zwei-faktor'))
    expect(gesendet).toEqual({ setup_token: 'tok-123', email: 'robin@example.test', name: 'Robin', password: 'geheim-1234-xyz' })
  })

  test('Fehler der API erscheint oben, Eingaben bleiben', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/setup`, () => problem(403, 'Setup-Token ungültig.')))
    zeigeApp('/einrichtung')
    await user.type(await screen.findByLabelText('Setup-Token'), 'falsch')
    await user.type(screen.getByLabelText('Name'), 'Robin')
    await user.type(screen.getByLabelText('E-Mail-Adresse'), 'robin@example.test')
    await user.type(screen.getByLabelText('Passwort'), 'geheim-1234-xyz')
    await user.click(screen.getByRole('button', { name: 'Konto anlegen' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Setup-Token ungültig.')
    expect(screen.getByLabelText('Setup-Token')).toHaveValue('falsch')
  })

  test('ohne nötiges Setup geht es zum Login', async () => {
    server.use(http.get(`${B}/setup`, () => HttpResponse.json({ required: false })))
    zeigeApp('/einrichtung')
    await waitFor(() => expect(ort()).toBe('/login'))
  })

  test('Seite hat keine axe-Verstöße', async () => {
    const { container } = zeigeApp('/einrichtung')
    await h1('Einrichtung')
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('Zwei-Faktor einrichten', () => {
  const SECRET = 'JBSWY3DPEHPK3PXQ'
  const CODES = ['aaaa-1111-zzzz', 'bbbb-2222-yyyy']
  let leseCache = () => ''
  function Sonde() {
    const qc = useQueryClient()
    leseCache = () =>
      JSON.stringify({
        q: qc.getQueryCache().getAll().map((q) => q.state.data),
        m: qc.getMutationCache().getAll().map((m) => [m.state.data, m.state.variables]),
      })
    return null
  }

  beforeEach(() => {
    aktuelle = sitzungEnroll
    server.use(
      http.post(`${B}/auth/totp/setup`, () => HttpResponse.json({ secret: SECRET, otpauth_uri: `otpauth://totp/Moni:robin?secret=${SECRET}&issuer=Moni` })),
      http.post(`${B}/auth/totp/confirm`, async ({ request }) => {
        expect(await request.json()).toEqual({ code: '654321' })
        return HttpResponse.json({ recovery_codes: CODES })
      }),
    )
  })

  test('QR-Code ist ein img mit Alt-Text, Secret steht als Text daneben', async () => {
    zeigeApp('/einrichtung/zwei-faktor')
    const bild = await screen.findByRole('img', { name: 'QR-Code für die Authenticator-App' })
    await waitFor(() => expect(bild).toHaveAttribute('src', expect.stringMatching(/^data:image\//)))
    expect(screen.getByText(SECRET)).toBeInTheDocument()
    expect(screen.getByText('Geheimnis zum Abtippen')).toBeInTheDocument()
  })

  test('Wiederherstellungscodes: Weiter erst nach Bestätigung; nach Verlassen nicht mehr im DOM oder Cache', async () => {
    const user = userEvent.setup()
    const logs = [vi.spyOn(console, 'log'), vi.spyOn(console, 'info'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error')]
    zeigeApp('/einrichtung/zwei-faktor', <Sonde />)
    await user.type(await screen.findByLabelText('Code aus der App'), '654321')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))
    expect(await screen.findByText(CODES[0]!)).toBeInTheDocument()
    expect(screen.getByText(CODES[1]!)).toBeInTheDocument()
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument()
    const weiter = screen.getByRole('button', { name: 'Weiter' })
    expect(weiter).toBeDisabled()
    expect(ort()).toBe('/einrichtung/zwei-faktor')

    await user.click(screen.getByRole('checkbox', { name: 'Ich habe die Codes sicher gespeichert' }))
    aktuelle = sitzungAdmin
    await user.click(weiter)
    await waitFor(() => expect(ort()).toBe('/'))

    expect(document.body.textContent).not.toContain(CODES[0])
    expect(document.body.textContent).not.toContain(SECRET)
    await waitFor(() => {
      const cache = leseCache()
      expect(cache).not.toContain(CODES[0])
      expect(cache).not.toContain(SECRET)
      expect(cache).not.toContain('otpauth')
      expect(cache).not.toContain('654321')
    })
    for (const speicher of [localStorage, sessionStorage]) expect(JSON.stringify({ ...speicher })).not.toContain(CODES[0])
    for (const l of logs) expect(JSON.stringify(l.mock.calls)).not.toContain(CODES[0])
  })

  test('Codes bleiben nach Fokuswechsel sichtbar, auch wenn die Sitzung full meldet; fokussiert die Überschrift', async () => {
    const user = userEvent.setup()
    zeigeApp('/einrichtung/zwei-faktor')
    await user.click(await screen.findByLabelText('Code aus der App'))
    await user.paste('654 321')
    expect(screen.getByLabelText('Code aus der App')).toHaveValue('654321')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))
    expect(await screen.findByText(CODES[0]!)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Wiederherstellungscodes' })).toHaveFocus()
    aktuelle = sitzungAdmin
    act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
    await new Promise((r) => setTimeout(r, 150)) // Refetch der Sitzung abwarten
    expect(ort()).toBe('/einrichtung/zwei-faktor')
    expect(screen.getByText(CODES[0]!)).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Ich habe die Codes sicher gespeichert' }))
    await user.click(screen.getByRole('button', { name: 'Weiter' }))
    await waitFor(() => expect(ort()).toBe('/'))
  })

  test('ein Benutzer mit Stage full ohne offene Codes wird weggeleitet', async () => {
    aktuelle = sitzungAdmin
    zeigeApp('/einrichtung/zwei-faktor')
    await waitFor(() => expect(ort()).toBe('/'))
  })

  test('falscher Code zeigt detail der API; die Einrichtung bleibt stehen', async () => {
    const user = userEvent.setup()
    server.use(http.post(`${B}/auth/totp/confirm`, () => problem(400, 'Code ungültig.')))
    zeigeApp('/einrichtung/zwei-faktor')
    await user.type(await screen.findByLabelText('Code aus der App'), '000000')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Code ungültig.')
    expect(screen.getByLabelText('Code aus der App')).toHaveValue('000000')
    expect(screen.getByText(SECRET)).toBeInTheDocument()
  })

  test('Seite hat keine axe-Verstöße', async () => {
    const user = userEvent.setup()
    const { container } = zeigeApp('/einrichtung/zwei-faktor')
    await screen.findByText(SECRET)
    expect(await axe(container)).toHaveNoViolations()
    await user.type(screen.getByLabelText('Code aus der App'), '654321')
    await user.click(screen.getByRole('button', { name: 'Bestätigen' }))
    await screen.findByText(CODES[0]!)
    expect(await axe(container)).toHaveNoViolations()
  })
})
