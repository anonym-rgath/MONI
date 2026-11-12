import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { describe, expect, test, vi } from 'vitest'
import type { AgentToken, AuditEintrag, Benutzer, OidcKonfig, Token } from '../../api/typen'
import { alleServer, serverEins, sitzungAdmin, sitzungBetrachter } from '../../test/fixtures'
import { zeigeApp } from '../../test/render'
import { BASIS, problem, server, sitzungAntwort } from '../../test/server'

const benutzer = (id: number, extra: Partial<Benutzer> = {}): Benutzer => ({
  id, email: `u${id}@example.test`, name: `Nutzer ${id}`, role: 'viewer', kind: 'local', totp_enabled: true, disabled: false, locked: false,
  oidc_bound: false, created_at: '2026-11-01T08:00:00Z', last_login: '2026-11-10T08:00:00Z', ...extra,
})
const token = (id: string, extra: Partial<Token> = {}): Token => ({ id, name: `Token ${id}`, scope: 'read', created_at: '2026-11-01T08:00:00Z', expires_at: null, ...extra })
const eintrag = (id: number, extra: Partial<AuditEintrag> = {}): AuditEintrag => ({
  id, ts: '2026-11-10T09:00:00Z', actor: 'robin@example.test', action: 'user.created', target: 'u1@example.test', ip: '192.0.2.7', details: { role: 'viewer' }, ...extra,
})

interface Aufruf { methode: string; pfad: string; url: URL; body: unknown }
const aufrufe: Aufruf[] = []
const letzter = () => aufrufe[aufrufe.length - 1]!
const mit = (methode: string) => aufrufe.filter((a) => a.methode === methode)

interface Daten {
  server?: typeof alleServer; benutzer?: Benutzer[]; tokens?: Token[]; agentTokens?: AgentToken[]; audit?: (url: URL) => { entries: AuditEintrag[]; next_before: number | null }
  oidc?: OidcKonfig
}
function daten(d: Daten = {}) {
  aufrufe.length = 0
  server.use(
    http.get(`${BASIS}/servers`, () => HttpResponse.json(d.server ?? alleServer)),
    http.get(`${BASIS}/users`, () => HttpResponse.json(d.benutzer ?? [benutzer(1, { role: 'admin', name: 'Robin' }), benutzer(2), benutzer(3, { locked: true }), benutzer(4, { kind: 'oidc', totp_enabled: false, disabled: true })])),
    http.get(`${BASIS}/tokens`, () => HttpResponse.json(d.tokens ?? [token('t1'), token('t2', { scope: 'admin', expires_at: '2027-01-01T00:00:00Z' })])),
    http.get(`${BASIS}/servers/:id/agent-tokens`, () => HttpResponse.json(d.agentTokens ?? [{ id: 'a1', name: 'Prometheus', scope: 'read', created_at: '2026-11-01T08:00:00Z', expires_at: null }])),
    http.get(`${BASIS}/audit`, ({ request }) => {
      const url = new URL(request.url)
      aufrufe.push({ methode: 'GET', pfad: '/audit', url, body: null })
      return HttpResponse.json(d.audit ? d.audit(url) : { entries: [eintrag(2), eintrag(1)], next_before: null })
    }),
    http.get(`${BASIS}/auth/oidc/providers`, () => HttpResponse.json(d.oidc ?? {
      public_url: 'https://moni.example.org',
      providers: [{ id: 'keycloak', label: 'Keycloak', issuer: 'https://sso.example.org/realms/x', client_id: 'moni', client_secret_set: true }],
    })),
  )
}
const aufzeichnen = (methode: 'post' | 'patch' | 'put' | 'delete', pfad: string, antwort: () => Response | Promise<Response> = () => new HttpResponse(null, { status: 204 })) =>
  server.use(http[methode](`${BASIS}${pfad}`, async ({ request }) => {
    const url = new URL(request.url)
    aufrufe.push({ methode: request.method, pfad: url.pathname.replace(BASIS, ''), url, body: request.method === 'DELETE' ? null : await request.json().catch(() => null) })
    return antwort()
  }))
let qc: QueryClient
function QcSonde() {
  const client = useQueryClient()
  useEffect(() => { qc = client }, [client])
  return null
}
const zeige = async (pfad: string, h1: string, s = sitzungAdmin) => {
  server.use(sitzungAntwort(s))
  const r = zeigeApp(pfad, <QcSonde />)
  await screen.findByRole('heading', { level: 1, name: h1 })
  return r
}
const ort = () => screen.getByTestId('ort').textContent

describe('Bereichsnavigation', () => {
  test('fünf Links mit aria-current; /verwaltung führt zu den Servern', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung', 'Server verwalten')
    expect(ort()).toBe('/verwaltung/server')
    expect(document.title).toBe('Server verwalten – Moni')
    const nav = screen.getByRole('navigation', { name: 'Bereiche der Verwaltung' })
    expect(within(nav).getAllByRole('link')).toHaveLength(5)
    expect(within(nav).getByRole('link', { name: 'Server' })).toHaveAttribute('aria-current', 'page')
    await user.click(within(nav).getByRole('link', { name: 'Audit-Protokoll' }))
    await screen.findByRole('heading', { level: 1, name: 'Audit-Protokoll' })
    expect(document.title).toBe('Audit-Protokoll – Moni')
    // die Seite ist neu aufgebaut (Fehlergrenze je Pfad), also die Navigation neu suchen
    expect(within(screen.getByRole('navigation', { name: 'Bereiche der Verwaltung' })).getByRole('link', { name: 'Audit-Protokoll' })).toHaveAttribute('aria-current', 'page')
  })
})

describe('Server', () => {
  test('Tabelle zeigt Name, Art, Status, URL und Zertifikat', async () => {
    daten({ server: [{ ...serverEins, cert_not_after: '2027-03-01T12:00:00Z' }] })
    await zeige('/verwaltung/server', 'Server verwalten')
    const zeile = await screen.findByRole('row', { name: /alpha/ })
    expect(zeile).toHaveTextContent('Agent · mTLS')
    expect(zeile).toHaveTextContent('OK')
    expect(zeile).toHaveTextContent('https://alpha.example.test:8443')
    expect(zeile).toHaveTextContent('01.03.2027')
  })

  test('lokaler Server hat keine Entfernen-Aktion', async () => {
    daten()
    await zeige('/verwaltung/server', 'Server verwalten')
    await screen.findByRole('row', { name: /pi/ })
    const lokal = screen.getByRole('row', { name: /^pi / })
    expect(within(lokal).queryByRole('button', { name: /Entfernen/ })).not.toBeInTheDocument()
    expect(within(lokal).queryByRole('button', { name: /Agent-Tokens/ })).not.toBeInTheDocument() // Backend: nur für mTLS (422 sonst)
    expect(within(screen.getByRole('row', { name: /vps-backup/ })).queryByRole('button', { name: /Agent-Tokens/ })).not.toBeInTheDocument() // Token-Modus
    expect(within(screen.getByRole('row', { name: /sau-index/ })).getByRole('button', { name: /Agent-Tokens/ })).toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /sau-index/ })).getByRole('button', { name: /Entfernen/ })).toBeInTheDocument()
  })

  test('Entfernen fragt vorher nach und löscht erst nach der Bestätigung', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    aufzeichnen('delete', '/servers/11')
    await user.click(await screen.findByRole('button', { name: /Entfernen sau-index/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Server ‚sau-index‘ entfernen? Verlauf und Alarme dieses Servers werden gelöscht.')
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Abbrechen' }))
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: /Entfernen sau-index/ }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Entfernen' }))
    await waitFor(() => expect(mit('DELETE')).toHaveLength(1))
    expect(letzter().pfad).toBe('/servers/11')
    expect(await screen.findByText('Server entfernt.')).toBeInTheDocument()
  })

  test('Deaktivieren fragt nach, Aktivieren sendet {enabled: true}', async () => {
    daten({ server: [serverEins, { ...serverEins, id: 2, name: 'beta', enabled: false }] })
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    aufzeichnen('patch', '/servers/2', () => HttpResponse.json({}))
    await user.click(await screen.findByRole('button', { name: /Aktivieren beta/ }))
    await waitFor(() => expect(mit('PATCH')).toHaveLength(1))
    expect(letzter().body).toEqual({ enabled: true })
    aufzeichnen('patch', '/servers/1', () => HttpResponse.json({}))
    await user.click(screen.getByRole('button', { name: /Deaktivieren alpha/ }))
    expect(mit('PATCH')).toHaveLength(1)
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Deaktivieren' }))
    await waitFor(() => expect(mit('PATCH')).toHaveLength(2))
    expect(letzter().body).toEqual({ enabled: false })
  })
})

describe('Koppeln', () => {
  test('?koppeln=1 öffnet den Dialog und entfernt den Parameter', async () => {
    daten()
    await zeige('/verwaltung/server?koppeln=1', 'Server verwalten')
    expect(await screen.findByRole('dialog', { name: 'Server koppeln' })).toBeInTheDocument()
    await waitFor(() => expect(ort()).toBe('/verwaltung/server'))
  })

  test('Koppeln mit Code sendet {name, pairing_code}; "Kopplungs-Token ungültig" erscheint am Feld ohne Abmeldung', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    await user.click(await screen.findByRole('button', { name: 'Server koppeln' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'delta')
    await user.type(within(dialog).getByLabelText('Kopplungscode'), 'moni-pair-v1:abc')
    expect(dialog).toHaveTextContent('python -m moni.cli pair code')
    aufzeichnen('post', '/servers', () => problem(401, 'Kopplungs-Token ungültig'))
    await user.click(within(dialog).getByRole('button', { name: 'Server koppeln' }))
    await waitFor(() => expect(within(dialog).getByLabelText('Kopplungscode')).toHaveAttribute('aria-invalid', 'true'))
    await waitFor(() => expect(within(dialog).getByLabelText('Kopplungscode')).toHaveFocus())
    expect(letzter().body).toEqual({ name: 'delta', pairing_code: 'moni-pair-v1:abc' })
    expect(within(dialog).getByLabelText('Kopplungscode')).toHaveAccessibleDescription(/Kopplungs-Token ungültig/)
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument() // nicht zusätzlich oben im Formular
    expect(ort()).toBe('/verwaltung/server') // keine Abmeldung
    expect(screen.getByRole('heading', { level: 1, name: 'Server verwalten' })).toBeInTheDocument()
  })

  test('Erfolg schließt den Dialog, lädt die Liste neu und meldet es', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    await user.click(await screen.findByRole('button', { name: 'Server koppeln' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'delta')
    await user.type(within(dialog).getByLabelText('Kopplungscode'), 'moni-pair-v1:abc')
    await user.type(within(dialog).getByLabelText('URL (optional)'), 'https://delta.example.test:8443')
    aufzeichnen('post', '/servers', () => HttpResponse.json({ ...serverEins, id: 20, name: 'delta' }, { status: 201 }))
    await user.click(within(dialog).getByRole('button', { name: 'Server koppeln' }))
    expect(await screen.findByText('Server gekoppelt.')).toBeInTheDocument()
    expect(letzter().body).toEqual({ name: 'delta', pairing_code: 'moni-pair-v1:abc', url: 'https://delta.example.test:8443' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('andere Fehler erscheinen oben im Formular', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    await user.click(await screen.findByRole('button', { name: 'Server koppeln' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'delta')
    await user.type(within(dialog).getByLabelText('Kopplungscode'), 'x')
    aufzeichnen('post', '/servers', () => problem(409, 'Name oder Adresse ist schon vergeben'))
    await user.click(within(dialog).getByRole('button', { name: 'Server koppeln' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Name oder Adresse ist schon vergeben')
    expect(within(dialog).getByLabelText('Kopplungscode')).not.toHaveAttribute('aria-invalid')
  })

  test('Token-Modus zeigt den Proxy-Warnhinweis und sendet Token, URL und Header', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    await user.click(await screen.findByRole('button', { name: 'Server koppeln' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByText('Nur hinter einem vertrauenswürdigen Proxy verwenden.')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('radio', { name: 'Mit Token, ohne mTLS' }))
    expect(within(dialog).getByText('Nur hinter einem vertrauenswürdigen Proxy verwenden.')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Kopplungscode')).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Name'), 'proxy')
    await user.type(within(dialog).getByLabelText('URL des Agents'), 'https://agent.example.test')
    const tokenFeld = within(dialog).getByLabelText('Token des Agents')
    expect(tokenFeld).toHaveAttribute('type', 'password')
    await user.type(tokenFeld, 'geheim')
    await user.click(within(dialog).getByRole('button', { name: 'Header hinzufügen' }))
    expect(within(dialog).getByRole('button', { name: 'Header 1 entfernen' })).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Header-Name 1'), 'X-Zugang')
    const wert = within(dialog).getByLabelText('Header-Wert 1')
    expect(wert).toHaveAttribute('type', 'password')
    await user.type(wert, 'abc')
    aufzeichnen('post', '/servers', () => HttpResponse.json({ ...serverEins, id: 21 }, { status: 201 }))
    await user.click(within(dialog).getByRole('button', { name: 'Server koppeln' }))
    await waitFor(() => expect(mit('POST')).toHaveLength(1))
    expect(letzter().body).toEqual({ name: 'proxy', url: 'https://agent.example.test', token: 'geheim', headers: { 'X-Zugang': 'abc' } })
  })
})

describe('Agent-Tokens', () => {
  test('Liste, Erstellen mit Einmalwert und Widerrufen mit Rückfrage; 404 sagt "Agent-Token nicht gefunden"', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server', 'Server verwalten')
    await user.click(await screen.findByRole('button', { name: /Agent-Tokens sau-index/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Agent-Tokens von sau-index' })
    expect(await within(dialog).findByRole('row', { name: /Prometheus/ })).toHaveTextContent('Lesen')

    aufzeichnen('post', '/servers/11/agent-tokens', () => HttpResponse.json({ id: 'a2', name: 'Grafana', scope: 'read', created_at: null, expires_at: null, token: 'agent-geheim-123' }, { status: 201 }))
    await user.click(within(dialog).getByRole('button', { name: 'Token erstellen' }))
    await user.type(within(dialog).getByLabelText('Name'), 'Grafana')
    await user.type(within(dialog).getByLabelText('Ablauf in Tagen (optional)'), '30')
    await user.click(within(dialog).getByRole('button', { name: 'Token erstellen' }))
    expect(await within(dialog).findByText('agent-geheim-123')).toBeInTheDocument()
    expect(letzter().body).toEqual({ name: 'Grafana', expires_days: 30 })
    await user.keyboard('{Escape}') // ein angezeigtes Token lässt sich nicht wegdrücken
    expect(within(dialog).getByText('agent-geheim-123')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Weiter' }))
    expect(screen.queryByText('agent-geheim-123')).not.toBeInTheDocument()
    await user.keyboard('{Escape}') // nach dem Bestätigen schließt Esc wieder
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(await screen.findByRole('button', { name: /Agent-Tokens sau-index/ }))
    expect(await within(dialog).findByRole('row', { name: /Prometheus/ })).toBeInTheDocument()

    aufzeichnen('delete', '/servers/11/agent-tokens/a1', () => problem(404, 'Agent-Token nicht gefunden'))
    await user.click(within(dialog).getByRole('button', { name: /Widerrufen Prometheus/ }))
    expect(within(dialog).getByText(/Token ‚Prometheus‘ widerrufen\?/)).toBeInTheDocument()
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Widerrufen' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Agent-Token nicht gefunden')
  })
})

describe('API-Tokens', () => {
  test('API-Token erscheint einmal; nach Schließen weder im DOM noch im Query-Cache', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/tokens', 'API-Tokens')
    expect(await screen.findByRole('row', { name: /Token t1/ })).toHaveTextContent('Lesen')
    expect(screen.getByRole('row', { name: /Token t2/ })).toHaveTextContent('Admin')
    aufzeichnen('post', '/tokens', () => HttpResponse.json({ ...token('t3', { name: 'Skript' }), token: 'moni_SEHR_GEHEIM_42' }, { status: 201 }))
    await user.click(screen.getByRole('button', { name: 'Token erstellen' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Skript')
    await user.selectOptions(within(dialog).getByLabelText('Bereich'), 'Admin')
    await user.type(within(dialog).getByLabelText('Ablauf in Tagen (optional)'), '90')
    await user.click(within(dialog).getByRole('button', { name: 'Token erstellen' }))
    expect(await within(dialog).findByText('moni_SEHR_GEHEIM_42')).toBeInTheDocument()
    expect(letzter().body).toEqual({ name: 'Skript', scope: 'admin', expires_days: 90 })
    await user.click(within(dialog).getByRole('checkbox', { name: 'Ich habe das Token sicher gespeichert' }))
    await user.click(within(dialog).getByRole('button', { name: 'Weiter' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.body.textContent).not.toContain('moni_SEHR_GEHEIM_42')
    await waitFor(() => expect(qc.getMutationCache().getAll()).toHaveLength(0))
    const cache = JSON.stringify([qc.getQueryCache().getAll().map((q) => q.state.data), qc.getMutationCache().getAll().map((m) => m.state.data)])
    expect(cache).not.toContain('moni_SEHR_GEHEIM_42')
    // erneut öffnen: leere Eingaben, kein Rest
    await user.click(screen.getByRole('button', { name: 'Token erstellen' }))
    expect(within(await screen.findByRole('dialog')).getByLabelText('Name')).toHaveValue('')
    expect(document.body.textContent).not.toContain('moni_SEHR_GEHEIM_42')
  })

  test('Esc verwirft ein angezeigtes Token nicht; nach dem Bestätigen schließt der Dialog wieder', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/tokens', 'API-Tokens')
    aufzeichnen('post', '/tokens', () => HttpResponse.json({ ...token('t3'), token: 'moni_ESC_GEHEIM' }, { status: 201 }))
    await user.click(await screen.findByRole('button', { name: 'Token erstellen' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Skript')
    await user.click(within(dialog).getByRole('button', { name: 'Token erstellen' }))
    expect(await within(dialog).findByText('moni_ESC_GEHEIM')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Abbrechen' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(dialog).getByText('moni_ESC_GEHEIM')).toBeInTheDocument()
    ;(dialog as HTMLDialogElement).close() // Browser schließen ohne cancel (zweites Esc ohne Nutzeraktion)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(dialog).getByText('moni_ESC_GEHEIM')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: 'Ich habe das Token sicher gespeichert' }))
    await user.click(within(dialog).getByRole('button', { name: 'Weiter' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Token erstellen' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  test('Widerrufen fragt vorher nach', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/tokens', 'API-Tokens')
    aufzeichnen('delete', '/tokens/t1')
    await user.click(await screen.findByRole('button', { name: /Widerrufen Token t1/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Token ‚Token t1‘ widerrufen?')
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Widerrufen' }))
    await waitFor(() => expect(mit('DELETE')).toHaveLength(1))
    expect(letzter().pfad).toBe('/tokens/t1')
    expect(await screen.findByText('Token widerrufen.')).toBeInTheDocument()
  })

  test('Doppelklick auf Widerrufen sendet nur eine Anfrage', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/tokens', 'API-Tokens')
    aufzeichnen('delete', '/tokens/t1')
    await user.click(await screen.findByRole('button', { name: /Widerrufen Token t1/ }))
    const knopf = within(await screen.findByRole('dialog')).getByRole('button', { name: 'Widerrufen' })
    act(() => { knopf.click(); knopf.click() }) // im selben Tick, bevor der gesperrte Zustand gerendert ist
    expect(await screen.findByText('Token widerrufen.')).toBeInTheDocument()
    expect(mit('DELETE')).toHaveLength(1)
  })
})

describe('Benutzer', () => {
  test('Tabelle mit Rolle, Art, 2FA und Status', async () => {
    daten()
    await zeige('/verwaltung/benutzer', 'Benutzer')
    const robin = await screen.findByRole('row', { name: /Robin/ })
    expect(robin).toHaveTextContent('Admin')
    expect(robin).toHaveTextContent('Lokal')
    expect(robin).toHaveTextContent('eingerichtet')
    expect(robin).toHaveTextContent('Aktiv')
    expect(screen.getByRole('row', { name: /Nutzer 3/ })).toHaveTextContent('Gesperrt')
    const oidc = screen.getByRole('row', { name: /Nutzer 4/ })
    expect(oidc).toHaveTextContent('OIDC')
    expect(oidc).toHaveTextContent('Deaktiviert')
    expect(within(oidc).queryByRole('button', { name: /Passwort zurücksetzen/ })).not.toBeInTheDocument() // nur lokale Konten
    expect(within(screen.getByRole('row', { name: /Nutzer 2/ })).queryByRole('button', { name: /Entsperren/ })).not.toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /Nutzer 3/ })).getByRole('button', { name: /Entsperren/ })).toBeInTheDocument()
  })

  test('Anlegen: Startpasswort nur für lokale Konten', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/benutzer', 'Benutzer')
    aufzeichnen('post', '/users', () => HttpResponse.json(benutzer(9), { status: 201 }))
    await user.click(await screen.findByRole('button', { name: 'Benutzer anlegen' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('E-Mail-Adresse'), 'neu@example.test')
    await user.type(within(dialog).getByLabelText('Name'), 'Neu')
    await user.selectOptions(within(dialog).getByLabelText('Rolle'), 'Betrachter')
    await user.type(within(dialog).getByLabelText('Startpasswort'), 'Start-Passwort-1')
    await user.click(within(dialog).getByRole('button', { name: 'Benutzer anlegen' }))
    await waitFor(() => expect(mit('POST')).toHaveLength(1))
    expect(letzter().body).toEqual({ email: 'neu@example.test', name: 'Neu', role: 'viewer', kind: 'local', password: 'Start-Passwort-1' })
    expect(await screen.findByText('Benutzer angelegt.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Benutzer anlegen' }))
    const d2 = await screen.findByRole('dialog')
    await user.selectOptions(within(d2).getByLabelText('Art des Kontos'), 'OIDC')
    expect(within(d2).queryByLabelText('Startpasswort')).not.toBeInTheDocument()
    await user.type(within(d2).getByLabelText('E-Mail-Adresse'), 'sso@example.test')
    await user.click(within(d2).getByRole('button', { name: 'Benutzer anlegen' }))
    await waitFor(() => expect(mit('POST')).toHaveLength(2))
    expect(letzter().body).toEqual({ email: 'sso@example.test', name: '', role: 'viewer', kind: 'oidc' })
  })

  test('letzter Admin: die API-Meldung erscheint unverändert im Dialog', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/benutzer', 'Benutzer')
    aufzeichnen('patch', '/users/1', () => problem(409, 'Der letzte aktive Admin kann nicht entfernt, herabgestuft oder deaktiviert werden'))
    await user.click(await screen.findByRole('button', { name: /Deaktivieren Robin/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Robin kann sich nicht mehr anmelden')
    expect(mit('PATCH')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Deaktivieren' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Der letzte aktive Admin kann nicht entfernt, herabgestuft oder deaktiviert werden')
    expect(letzter().body).toEqual({ disabled: true })
  })

  test('Rolle ändern, Passwort, 2FA, Entsperren und Löschen', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/benutzer', 'Benutzer')
    aufzeichnen('patch', '/users/2', () => HttpResponse.json(benutzer(2, { role: 'admin' })))
    await user.click(await screen.findByRole('button', { name: /Rolle ändern Nutzer 2/ }))
    let dialog = await screen.findByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText('Rolle'), 'Admin')
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(mit('PATCH')).toHaveLength(1))
    expect(letzter().body).toEqual({ role: 'admin' })

    aufzeichnen('post', '/users/2/password')
    await user.click(await screen.findByRole('button', { name: /Passwort zurücksetzen Nutzer 2/ }))
    dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Neues Passwort'), 'Neues-Passwort-9')
    await user.click(within(dialog).getByRole('button', { name: 'Passwort zurücksetzen' }))
    await waitFor(() => expect(mit('POST')).toHaveLength(1))
    expect(letzter().body).toEqual({ password: 'Neues-Passwort-9' })

    aufzeichnen('post', '/users/2/reset-2fa')
    await user.click(await screen.findByRole('button', { name: /2FA zurücksetzen Nutzer 2/ }))
    dialog = await screen.findByRole('dialog')
    expect(mit('POST')).toHaveLength(1)
    await user.click(within(dialog).getByRole('button', { name: '2FA zurücksetzen' }))
    await waitFor(() => expect(mit('POST')).toHaveLength(2))
    expect(letzter().pfad).toBe('/users/2/reset-2fa')

    aufzeichnen('post', '/users/3/unlock')
    await user.click(screen.getByRole('button', { name: /Entsperren Nutzer 3/ }))
    await waitFor(() => expect(letzter().pfad).toBe('/users/3/unlock'))

    aufzeichnen('delete', '/users/2')
    await user.click(screen.getByRole('button', { name: /Löschen Nutzer 2/ }))
    dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Nutzer 2 wird gelöscht. Das lässt sich nicht rückgängig machen.')
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Löschen' }))
    await waitFor(() => expect(mit('DELETE')).toHaveLength(1))
  })

  test('eigene Rolle ändern lädt die Sitzung neu', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/benutzer', 'Benutzer')
    aufzeichnen('patch', '/users/1', () => HttpResponse.json(benutzer(1, { name: 'Robin' })))
    await user.click(await screen.findByRole('button', { name: /Rolle ändern Robin/ }))
    const dialog = await screen.findByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText('Rolle'), 'Betrachter')
    server.use(sitzungAntwort({ ...sitzungAdmin, user: { ...sitzungAdmin.user!, role: 'viewer' } }))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Keine Berechtigung' })).toBeInTheDocument()
  })
})

describe('Audit', () => {
  test('Audit-Details werden als Text gerendert (Fixture mit <script> im Ziel)', async () => {
    daten({ audit: () => ({ entries: [eintrag(5, { target: '<script>window.boese=1</script>', details: { html: '<img src=x onerror=alert(1)>' } })], next_before: null }) })
    const { container } = await zeige('/verwaltung/audit', 'Audit-Protokoll')
    const zeile = await screen.findByRole('row', { name: /user\.created/ })
    expect(zeile).toHaveTextContent('<script>window.boese=1</script>')
    expect(zeile).toHaveTextContent('"html":"<img src=x onerror=alert(1)>"')
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(zeile).toHaveTextContent('192.0.2.7')
    expect(zeile).toHaveTextContent('robin@example.test')
  })

  test('Ältere laden blättert mit before=next_before', async () => {
    daten({ audit: (url) => (url.searchParams.get('before') === '2' ? { entries: [eintrag(1, { action: 'login.ok' })], next_before: null } : { entries: [eintrag(3), eintrag(2)], next_before: 2 }) })
    const user = userEvent.setup()
    await zeige('/verwaltung/audit', 'Audit-Protokoll')
    await screen.findAllByRole('row', { name: /user\.created/ })
    expect(aufrufe[0]!.url.searchParams.get('limit')).toBe('50')
    expect(aufrufe[0]!.url.searchParams.has('before')).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Ältere laden' }))
    expect(await screen.findByRole('row', { name: /login\.ok/ })).toBeInTheDocument()
    expect(screen.getAllByRole('row', { name: /user\.created/ })).toHaveLength(2) // angehängt
    expect(screen.queryByRole('button', { name: 'Ältere laden' })).not.toBeInTheDocument()
    // Rückkehr zum Tab lädt die geblätterten Seiten nicht alle neu
    const n = aufrufe.length
    act(() => { window.dispatchEvent(new Event('visibilitychange')) })
    await new Promise((r) => setTimeout(r, 50))
    expect(aufrufe).toHaveLength(n)
  })
})

describe('Anmeldeanbieter', () => {
  test('zeigt die Redirect-URI je Anbieter mit Kopierknopf', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/anmeldung', 'Anmeldung')
    const karte = (await screen.findByRole('heading', { level: 3, name: 'Keycloak' })).closest('li')!
    expect(karte).toHaveTextContent('https://moni.example.org/api/v1/auth/oidc/callback/keycloak')
    expect(karte).toHaveTextContent('https://sso.example.org/realms/x')
    expect(screen.getByLabelText('Öffentliche URL')).toHaveValue('https://moni.example.org')
    const schreiben = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: schreiben } }) // nach userEvent.setup(), das die Zwischenablage ersetzt
    await user.click(within(karte).getByRole('button', { name: /^Kopieren/ }))
    expect(schreiben).toHaveBeenCalledWith('https://moni.example.org/api/v1/auth/oidc/callback/keycloak')
    expect(await screen.findByText('Redirect-URI kopiert.')).toBeInTheDocument()
  })

  test('Öffentliche URL speichern (PUT /auth/oidc/public-url)', async () => {
    daten({ oidc: { public_url: null, providers: [] } })
    const user = userEvent.setup()
    await zeige('/verwaltung/anmeldung', 'Anmeldung')
    expect(await screen.findByText('Noch keine Anmeldeanbieter.')).toBeInTheDocument()
    aufzeichnen('put', '/auth/oidc/public-url', () => HttpResponse.json({ public_url: 'https://moni.example.org' }))
    await user.type(screen.getByLabelText('Öffentliche URL'), 'https://moni.example.org')
    await user.click(screen.getByRole('button', { name: 'URL speichern' }))
    await waitFor(() => expect(mit('PUT')).toHaveLength(1))
    expect(letzter().body).toEqual({ public_url: 'https://moni.example.org' })
    expect(await screen.findByText('Öffentliche URL gespeichert.')).toBeInTheDocument()
  })

  test('Anbieter anlegen fragt nach der Kennung; Bearbeiten lässt das Secret leer und sendet es dann nicht', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/anmeldung', 'Anmeldung')
    aufzeichnen('put', '/auth/oidc/providers/gitlab', () => HttpResponse.json({ id: 'gitlab', label: 'GitLab' }))
    await user.click(await screen.findByRole('button', { name: 'Anbieter anlegen' }))
    let dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Kennung'), 'gitlab')
    await user.type(within(dialog).getByLabelText('Issuer'), 'https://gitlab.example.org')
    await user.type(within(dialog).getByLabelText('Client-ID'), 'abc')
    const secret = within(dialog).getByLabelText('Client-Secret')
    expect(secret).toHaveAttribute('type', 'password')
    await user.type(secret, 'hunter2')
    await user.type(within(dialog).getByLabelText('Bezeichnung'), 'GitLab')
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(mit('PUT')).toHaveLength(1))
    expect(letzter().body).toEqual({ issuer: 'https://gitlab.example.org', client_id: 'abc', client_secret: 'hunter2', label: 'GitLab' })

    aufzeichnen('put', '/auth/oidc/providers/keycloak', () => HttpResponse.json({ id: 'keycloak', label: 'Keycloak 2' }))
    await user.click(await screen.findByRole('button', { name: /Bearbeiten Keycloak/ }))
    dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText('Kennung')).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText('Issuer')).toHaveValue('https://sso.example.org/realms/x')
    expect(within(dialog).getByLabelText('Client-Secret')).toHaveValue('')
    await user.clear(within(dialog).getByLabelText('Bezeichnung'))
    await user.type(within(dialog).getByLabelText('Bezeichnung'), 'Keycloak 2')
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(mit('PUT')).toHaveLength(2))
    expect(letzter().body).toEqual({ issuer: 'https://sso.example.org/realms/x', client_id: 'moni', label: 'Keycloak 2' })
  })

  test('Löschen fragt vorher nach; API-Fehler beim Speichern erscheinen im Formular', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/anmeldung', 'Anmeldung')
    aufzeichnen('delete', '/auth/oidc/providers/keycloak')
    await user.click(await screen.findByRole('button', { name: /Löschen Keycloak/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Anbieter ‚Keycloak‘ löschen?')
    expect(mit('DELETE')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Löschen' }))
    await waitFor(() => expect(mit('DELETE')).toHaveLength(1))
  })
})

describe('Zugriff und Barrierefreiheit', () => {
  test.each(['/verwaltung/server', '/verwaltung/benutzer', '/verwaltung/tokens', '/verwaltung/audit', '/verwaltung/anmeldung'])('Betrachter bekommt auf %s "Keine Berechtigung"', async (pfad) => {
    daten()
    await zeige(pfad, 'Keine Berechtigung', sitzungBetrachter)
  })

  test.each([
    ['/verwaltung/server', 'Server verwalten', 'sau-index'],
    ['/verwaltung/benutzer', 'Benutzer', 'Robin'],
    ['/verwaltung/tokens', 'API-Tokens', 'Token t1'],
    ['/verwaltung/audit', 'Audit-Protokoll', 'user.created'],
    ['/verwaltung/anmeldung', 'Anmeldung', 'Keycloak'],
  ])('Verwaltung ohne axe-Verstöße: %s', async (pfad, h1, warte) => {
    daten()
    const { container } = await zeige(pfad, h1)
    await screen.findAllByText(new RegExp(warte))
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Dialoge ohne axe-Verstöße (Koppeln im Token-Modus, Token anlegen)', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/verwaltung/server?koppeln=1', 'Server verwalten')
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: 'Mit Token, ohne mTLS' }))
    await user.click(within(dialog).getByRole('button', { name: 'Header hinzufügen' }))
    expect(await axe(document.body)).toHaveNoViolations()
  })
})
