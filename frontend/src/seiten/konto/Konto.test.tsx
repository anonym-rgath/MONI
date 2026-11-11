import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { describe, expect, test } from 'vitest'
import type { Session } from '../../api/typen'
import { sitzungAdmin } from '../../test/fixtures'
import { zeigeApp } from '../../test/render'
import { BASIS, problem, server, sitzungAntwort } from '../../test/server'

const SECRET = 'JBSWY3DPEHPK3PXP'
const URI = `otpauth://totp/Moni:robin@example.test?secret=${SECRET}&issuer=Moni`
const CODES = ['aaaa-1111', 'bbbb-2222', 'cccc-3333']
const NEU = ['neu1-1111', 'neu2-2222']

interface Aufruf { pfad: string; body: unknown }
const aufrufe: Aufruf[] = []
const an = (pfad: string) => aufrufe.filter((a) => a.pfad === pfad)
function post(pfad: string, antwort: (body: unknown) => Response) {
  server.use(http.post(`${BASIS}${pfad}`, async ({ request }) => {
    const body: unknown = await request.json().catch(() => null)
    aufrufe.push({ pfad, body })
    return antwort(body)
  }))
}
let qc: QueryClient
function QcSonde() {
  const client = useQueryClient()
  useEffect(() => { qc = client }, [client])
  return null
}
async function zeige(s: Session = sitzungAdmin) {
  aufrufe.length = 0
  server.use(sitzungAntwort(s))
  const r = zeigeApp('/konto', <QcSonde />)
  await screen.findByRole('heading', { level: 1, name: 'Konto' })
  return r
}
const ort = () => screen.getByTestId('ort').textContent
const zweiFaktor = () => screen.getByRole('region', { name: 'Zwei-Faktor-Anmeldung' })

async function codeEingeben(nutzer: ReturnType<typeof userEvent.setup>, label: RegExp | string, wert: string, knopf: string) {
  await nutzer.type(await screen.findByLabelText(label), wert)
  await nutzer.click(screen.getByRole('button', { name: knopf }))
}

describe('Konto', () => {
  test('Profil, Titel, Link in der Kopfzeile', async () => {
    await zeige()
    expect(document.title).toBe('Konto – Moni')
    expect(screen.getByRole('heading', { level: 1 })).toHaveAttribute('tabindex', '-1')
    const profil = screen.getByRole('region', { name: 'Profil' })
    expect(within(profil).getByText('Robin')).toBeInTheDocument()
    expect(within(profil).getByText('robin@example.test')).toBeInTheDocument()
    expect(within(profil).getByText('Admin')).toBeInTheDocument()
    expect(within(profil).getByText('Lokales Konto')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Robin · Admin' })).toHaveAttribute('href', '/konto')
  })

  test('Authenticator wechseln fragt zuerst den aktuellen Code', async () => {
    const nutzer = userEvent.setup()
    post('/auth/totp/setup', () => HttpResponse.json({ secret: SECRET, otpauth_uri: URI }))
    post('/auth/totp/confirm', () => HttpResponse.json({ recovery_codes: NEU }))
    await zeige()
    await nutzer.click(screen.getByRole('button', { name: 'Authenticator wechseln' }))
    // Schritt 1: nur der aktuelle Code, noch kein Secret, kein Setup-Aufruf
    expect(await screen.findByLabelText('Aktueller Code aus der App')).toBeInTheDocument()
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument()
    expect(an('/auth/totp/setup')).toHaveLength(0)

    await nutzer.type(screen.getByLabelText('Aktueller Code aus der App'), '123456')
    await nutzer.click(screen.getByRole('button', { name: 'Weiter' }))
    // Schritt 2 und 3: QR, Secret, neuer Code
    expect(await screen.findByText(SECRET)).toBeInTheDocument()
    expect(an('/auth/totp/setup')[0]?.body).toEqual({ current_code: '123456' })
    expect(await screen.findByRole('img', { name: /QR-Code/ })).toBeInTheDocument()
    await nutzer.type(screen.getByLabelText('Code aus der App'), '654321')
    await nutzer.click(screen.getByRole('button', { name: 'Bestätigen' }))
    // Schritt 4: neue Wiederherstellungscodes
    expect(await screen.findByText(NEU[0]!)).toBeInTheDocument()
    expect(an('/auth/totp/confirm')[0]?.body).toEqual({ code: '654321' })
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument()
    await nutzer.click(screen.getByLabelText('Ich habe die Codes sicher gespeichert'))
    await nutzer.click(screen.getByRole('button', { name: 'Weiter' }))
    // zurück in der Übersicht: nichts Einmaliges mehr im DOM oder Cache
    expect(await screen.findByRole('button', { name: 'Authenticator wechseln' })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET)
    expect(document.body.textContent).not.toContain(NEU[0])
    expect(document.querySelector('img[src^="data:"]')).toBeNull()
    expect(qc.getMutationCache().getAll()).toHaveLength(0)
    expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(SECRET)
    expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(NEU[0])
  })

  test('falscher aktueller Code beim Wechseln zeigt den Fehlertext und meldet nicht ab', async () => {
    const nutzer = userEvent.setup()
    post('/auth/totp/setup', () => problem(401, 'Anmeldung fehlgeschlagen'))
    await zeige()
    await nutzer.click(screen.getByRole('button', { name: 'Authenticator wechseln' }))
    await codeEingeben(nutzer, 'Aktueller Code aus der App', '000000', 'Weiter')
    expect(await screen.findByText('Anmeldung fehlgeschlagen')).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(ort()).toBe('/konto')
    expect(screen.getByLabelText('Aktueller Code aus der App')).toBeInTheDocument()
  })

  test('Abbrechen in jedem Schritt führt zur Übersicht und verwirft das Secret', async () => {
    const nutzer = userEvent.setup()
    post('/auth/totp/setup', () => HttpResponse.json({ secret: SECRET, otpauth_uri: URI }))
    await zeige()
    await nutzer.click(screen.getByRole('button', { name: 'Authenticator wechseln' }))
    await nutzer.click(screen.getByRole('button', { name: 'Abbrechen' }))
    expect(screen.getByRole('button', { name: 'Authenticator wechseln' })).toBeInTheDocument()
    await nutzer.click(screen.getByRole('button', { name: 'Authenticator wechseln' }))
    await codeEingeben(nutzer, 'Aktueller Code aus der App', '123456', 'Weiter')
    await screen.findByText(SECRET)
    await nutzer.click(screen.getByRole('button', { name: 'Abbrechen' }))
    expect(screen.getByRole('button', { name: 'Authenticator wechseln' })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(SECRET)
  })

  test('neue Wiederherstellungscodes warnen vor Ungültigkeit der alten und erscheinen einmal', async () => {
    const nutzer = userEvent.setup()
    post('/account/recovery-codes', () => HttpResponse.json({ recovery_codes: CODES }))
    await zeige()
    await nutzer.click(screen.getByRole('button', { name: 'Neue Wiederherstellungscodes erzeugen' }))
    expect(screen.getByText('Die alten Codes werden ungültig.')).toBeInTheDocument()
    expect(screen.queryByText(CODES[0]!)).not.toBeInTheDocument()
    await codeEingeben(nutzer, 'Aktueller Code aus der App', '123456', 'Codes erzeugen')
    for (const c of CODES) expect(await screen.findByText(c)).toBeInTheDocument()
    expect(an('/account/recovery-codes')[0]?.body).toEqual({ code: '123456' })
    await nutzer.click(screen.getByLabelText('Ich habe die Codes sicher gespeichert'))
    await nutzer.click(screen.getByRole('button', { name: 'Weiter' }))
    await screen.findByRole('button', { name: 'Neue Wiederherstellungscodes erzeugen' })
    expect(document.body.textContent).not.toContain(CODES[0])
    expect(qc.getMutationCache().getAll()).toHaveLength(0)
    expect(JSON.stringify(qc.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(CODES[0])
  })

  test('falscher Code bei den Wiederherstellungscodes bleibt angemeldet', async () => {
    const nutzer = userEvent.setup()
    post('/account/recovery-codes', () => problem(401, 'Anmeldung fehlgeschlagen'))
    await zeige()
    await nutzer.click(screen.getByRole('button', { name: 'Neue Wiederherstellungscodes erzeugen' }))
    await codeEingeben(nutzer, 'Aktueller Code aus der App', '000000', 'Codes erzeugen')
    expect(await screen.findByText('Anmeldung fehlgeschlagen')).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(ort()).toBe('/konto')
  })

  test('Passwort ändern: Erfolg leert die Felder und nennt die beendeten Sitzungen', async () => {
    const nutzer = userEvent.setup()
    post('/account/password', () => new HttpResponse(null, { status: 204 }))
    await zeige()
    await nutzer.type(screen.getByLabelText('Aktuelles Passwort'), 'alt-passwort')
    await nutzer.type(screen.getByLabelText('Neues Passwort'), 'neues-passwort-123')
    await nutzer.click(screen.getByRole('button', { name: 'Passwort ändern' }))
    expect(await screen.findByText(/Passwort geändert/)).toBeInTheDocument()
    expect(an('/account/password')[0]?.body).toEqual({ current_password: 'alt-passwort', new_password: 'neues-passwort-123' })
    expect(screen.getByLabelText('Aktuelles Passwort')).toHaveValue('')
    expect(screen.getByLabelText('Neues Passwort')).toHaveValue('')
    expect(screen.getByLabelText('Neues Passwort')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByLabelText('Aktuelles Passwort')).toHaveAttribute('autocomplete', 'current-password')
  })

  test('falsches aktuelles Passwort und zu kurzes neues zeigen den Fehlertext, keine Abmeldung', async () => {
    const nutzer = userEvent.setup()
    let antwort = () => problem(401, 'Anmeldung fehlgeschlagen')
    post('/account/password', () => antwort())
    await zeige()
    await nutzer.type(screen.getByLabelText('Aktuelles Passwort'), 'falsch')
    await nutzer.type(screen.getByLabelText('Neues Passwort'), 'neues-passwort-123')
    await nutzer.click(screen.getByRole('button', { name: 'Passwort ändern' }))
    expect(await screen.findByText('Anmeldung fehlgeschlagen')).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(ort()).toBe('/konto')
    expect(screen.getByLabelText('Neues Passwort')).toHaveValue('neues-passwort-123')

    antwort = () => problem(422, 'Das Passwort muss mindestens 12 Zeichen lang sein')
    await nutzer.click(screen.getByRole('button', { name: 'Passwort ändern' }))
    expect(await screen.findByText('Das Passwort muss mindestens 12 Zeichen lang sein')).toBeInTheDocument()
    expect(ort()).toBe('/konto')
  })

  test('OIDC-Konto sieht keinen Passwort-Abschnitt', async () => {
    await zeige({ ...sitzungAdmin, user: { ...sitzungAdmin.user, kind: 'oidc', totp_enabled: false } })
    expect(screen.getByText('Anmeldung über deinen Anbieter; Passwort und zweiter Faktor verwaltet der Anbieter.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Aktuelles Passwort')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Passwort ändern' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Authenticator wechseln' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('group', { name: 'Darstellung' })).toHaveLength(2) // Kopfzeile und Konto
  })

  test('Darstellung im Konto und in der Kopfzeile sind synchron', async () => {
    const nutzer = userEvent.setup()
    await zeige()
    const konto = screen.getAllByRole('group', { name: 'Darstellung' }).find((g) => g.tagName === 'FIELDSET')!
    expect(konto.tagName).toBe('FIELDSET')
    expect(within(konto).getByRole('radio', { name: 'Auto' })).toBeChecked()
    await nutzer.click(within(konto).getByRole('radio', { name: 'Dunkel' }))
    const kopf = screen.getAllByRole('group', { name: 'Darstellung' }).find((g) => g.tagName !== 'FIELDSET')!
    expect(within(kopf).getByRole('button', { name: 'Dunkel' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.documentElement.dataset.theme).toBe('dark')
    await nutzer.click(within(kopf).getByRole('button', { name: 'Hell' }))
    await waitFor(() => expect(within(konto).getByRole('radio', { name: 'Hell' })).toBeChecked())
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  test('Konto ohne axe-Verstöße', async () => {
    const nutzer = userEvent.setup()
    const { container } = await zeige()
    expect(await axe(container)).toHaveNoViolations()
    await nutzer.click(screen.getByRole('button', { name: 'Neue Wiederherstellungscodes erzeugen' }))
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Konto ist für Betrachter erreichbar', async () => {
    await zeige({ ...sitzungAdmin, user: { ...sitzungAdmin.user, role: 'viewer' } })
    expect(within(zweiFaktor()).getByText('Eingerichtet')).toBeInTheDocument()
  })
})
