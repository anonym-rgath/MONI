import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { describe, expect, test, vi } from 'vitest'
import type { Alarm, Regel, Server, Snapshot, Verlaufspunkt } from '../../api/typen'
import { alarmSau, regelCpu, serverEins, serverSau, sitzungAdmin, sitzungBetrachter, snapshotSau } from '../../test/fixtures'
import { zeigeApp } from '../../test/render'
import { BASIS, problem, server, sitzungAntwort } from '../../test/server'
import { morgenAcht } from './StummDialog'
import { rate } from './verlaufsdaten'

// Zählt, wie oft ein Diagramm rendert (echtes Diagramm, nur umhüllt).
const renders = vi.hoisted(() => ({ n: 0 }))
vi.mock('./Diagramm', async (original) => {
  const m = await original<typeof import('./Diagramm')>()
  return { ...m, Diagramm: (p: Parameters<typeof m.Diagramm>[0]) => { renders.n++; return m.Diagramm(p) } }
})

const punkte = (...werte: number[]): Verlaufspunkt[] => werte.map((w, i) => [new Date(Date.UTC(2026, 10, 11, 9, i)).toISOString(), w])

interface Daten { fehler?: (metric: string) => boolean; server?: Server; snapshot?: Snapshot | null; regeln?: Regel[]; alarme?: Alarm[]; verlauf?: (metric: string, label: string | null) => Verlaufspunkt[] }
const anfragen: URL[] = []

function daten(d: Daten = {}) {
  anfragen.length = 0
  const s = d.server ?? serverSau
  server.use(
    http.get(`${BASIS}/servers/${s.id}`, () => HttpResponse.json(s)),
    http.get(`${BASIS}/servers/${s.id}/snapshot`, () => (d.snapshot === null ? problem(404, 'Noch kein Snapshot') : HttpResponse.json(d.snapshot ?? snapshotSau))),
    http.get(`${BASIS}/alert-rules`, () => HttpResponse.json(d.regeln ?? [regelCpu])),
    http.get(`${BASIS}/alerts`, () => HttpResponse.json(d.alarme ?? [])),
    http.get(`${BASIS}/servers/${s.id}/history`, ({ request }) => {
      const u = new URL(request.url)
      anfragen.push(u)
      if (d.fehler?.(u.searchParams.get('metric')!)) return problem(500, 'Verlauf kaputt')
      const pts = d.verlauf ? d.verlauf(u.searchParams.get('metric')!, u.searchParams.get('label')) : punkte(20, 60, 100)
      return HttpResponse.json({ resolution: 'raw', series: pts.length ? [{ labels: {}, points: pts }] : [] })
    }),
  )
  return s
}

const zeige = async (d: Daten = {}, sitzung = sitzungAdmin) => {
  const s = daten(d)
  server.use(sitzungAntwort(sitzung))
  const r = zeigeApp(`/server/${s.id}`)
  await screen.findByRole('heading', { level: 1, name: s.name })
  return r
}

describe('rate', () => {
  test('kumulative Bytes ergeben Bytes/s, Zähler-Reset ergibt keine negative Rate', () => {
    expect(rate([[0, 100], [10, 300], [20, 50]])).toEqual([{ ts: 10, bytes_je_s: 20 }, { ts: 20, bytes_je_s: 5 }])
  })
})

describe('Verlauf', () => {
  test('Zeitraumwechsel lädt history mit from/to und setzt aria-pressed', async () => {
    const user = userEvent.setup()
    await zeige()
    const knopf = (n: string) => screen.getByRole('button', { name: n })
    await waitFor(() => expect(anfragen.length).toBeGreaterThan(0))
    expect(knopf('24 Std.')).toHaveAttribute('aria-pressed', 'true')
    anfragen.length = 0
    await user.click(knopf('7 Tage'))
    expect(knopf('7 Tage')).toHaveAttribute('aria-pressed', 'true')
    expect(knopf('24 Std.')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('heading', { level: 2, name: 'Verlauf der letzten 7 Tage' })).toBeInTheDocument()
    await waitFor(() => expect(anfragen.length).toBeGreaterThan(0))
    const u = anfragen[0]!
    expect(u.searchParams.get('resolution')).toBe('auto')
    const [von, bis] = [u.searchParams.get('from')!, u.searchParams.get('to')!]
    expect(von).toMatch(/Z$/)
    expect(bis).toMatch(/Z$/)
    expect((Date.parse(bis) - Date.parse(von)) / 86_400_000).toBeCloseTo(7, 3)
  })

  test('Diagramm-Zusammenfassung nennt Durchschnitt, Spitze und Schwelle', async () => {
    await zeige()
    const text = 'Ø 60 %, Spitze 100 %; Warnschwelle 75 %, überschritten'
    const figur = (await screen.findByText(text)).closest('figure')!
    expect(within(figur).getByRole('img')).toHaveAttribute('aria-label', expect.stringContaining(text))
    expect(within(figur).getByRole('heading', { level: 3, name: 'CPU' })).toBeInTheDocument()
  })

  test('ohne Regel keine Schwelle; ohne Daten der Hinweis', async () => {
    await zeige({ regeln: [], verlauf: (m) => (m === 'moni_host_cpu_usage_percent' ? punkte(10, 30) : []) })
    expect(await screen.findByText('Ø 20 %, Spitze 30 %')).toBeInTheDocument()
    expect((await screen.findAllByText('Für diesen Zeitraum liegen keine Daten vor.')).length).toBe(3)
  })

  test('Netzwerk-Diagramm nutzt das Interface mit den meisten empfangenen Bytes, nie lo; gesendet ist eine zweite Reihe', async () => {
    await zeige()
    await waitFor(() => expect(anfragen.some((u) => u.searchParams.get('metric') === 'moni_network_receive_bytes' && u.searchParams.get('label') === 'interface:eth0')).toBe(true))
    expect(anfragen.some((u) => u.searchParams.get('label') === 'interface:lo')).toBe(false)
    expect(anfragen.some((u) => u.searchParams.get('metric') === 'moni_network_transmit_bytes' && u.searchParams.get('label') === 'interface:eth0')).toBe(true)
  })
})

describe('Diagramm-Details', () => {
  test('Spitze stammt aus dem Maximum, Ø aus dem Mittelwert', async () => {
    const stufen: Verlaufspunkt[] = [
      [new Date(Date.UTC(2026, 10, 11, 9, 0)).toISOString(), 10, 40, 70],
      [new Date(Date.UTC(2026, 10, 11, 9, 1)).toISOString(), 20, 60, 90],
    ]
    await zeige({ regeln: [], verlauf: (m) => (m === 'moni_host_cpu_usage_percent' ? stufen : []) })
    expect(await screen.findByText('Ø 50 %, Spitze 90 %')).toBeInTheDocument()
  })

  test('Zusammenfassung bleibt, wenn eine Reihe ausfällt', async () => {
    await zeige({ fehler: (m) => m === 'moni_network_transmit_bytes' })
    const figur = (await screen.findByRole('heading', { level: 3, name: /Netzwerk/ })).closest('figure')!
    expect(await within(figur).findByRole('alert')).toHaveTextContent('Verlauf kaputt')
    expect(figur.textContent).toMatch(/empfangen: Ø/)
  })

  test('Legende zeigt das Strichmuster der zweiten Reihe', async () => {
    await zeige()
    const figur = (await screen.findByRole('heading', { level: 3, name: /Netzwerk/ })).closest('figure')!
    const eintraege = await within(figur).findAllByRole('listitem')
    expect(eintraege.map((e) => e.textContent)).toEqual(['empfangen', 'gesendet'])
    expect(eintraege.map((e) => e.querySelector('line')!.getAttribute('stroke-dasharray'))).toEqual([null, '6 4'])
  })
})

describe('Schwellenlinie (P6-R10)', () => {
  const regel = (extra: Partial<Regel>): Regel => ({ ...regelCpu, ...extra })
  const zusammenfassung = () => screen.findByText(/^Ø 60 %/)

  test('label_filter, der nicht zur Reihe passt, überspringt die Regel', async () => {
    await zeige({ regeln: [regel({ label_filter: { mountpoint: '/' } })] })
    expect((await zusammenfassung()).textContent).toBe('Ø 60 %, Spitze 100 %')
  })

  test('nur func "value": increase-Regeln ergeben keine Linie', async () => {
    await zeige({ regeln: [regel({ func: 'increase', window_seconds: 60 })] })
    expect((await zusammenfassung()).textContent).toBe('Ø 60 %, Spitze 100 %')
  })

  test('ohne Warnschwelle heißt die Linie "Kritische Schwelle"', async () => {
    await zeige({ regeln: [regel({ warning: null, critical: 90 })] })
    expect((await zusammenfassung()).textContent).toBe('Ø 60 %, Spitze 100 %; Kritische Schwelle 90 %, überschritten')
  })
})

describe('morgenAcht', () => {
  const lokalZeit = (h: number, m: number) => new Date(2026, 10, 11, h, m).getTime()
  test.each([
    ['kurz nach Mitternacht: heute 08:00', lokalZeit(0, 30), new Date(2026, 10, 11, 8).getTime()],
    ['07:59: heute 08:00', lokalZeit(7, 59), new Date(2026, 10, 11, 8).getTime()],
    ['genau 08:00: morgen 08:00', lokalZeit(8, 0), new Date(2026, 10, 12, 8).getTime()],
    ['23:59: morgen 08:00', lokalZeit(23, 59), new Date(2026, 10, 12, 8).getTime()],
  ])('%s', (_n, jetzt, erwartet) => { expect(morgenAcht(jetzt)).toBe(erwartet) })
})

describe('ungültige id', () => {
  test('zeigt "Server nicht gefunden" ohne Anfrage an /servers/…', async () => {
    const urls: string[] = []
    const merke = ({ request }: { request: Request }) => { urls.push(new URL(request.url).pathname) }
    server.events.on('request:start', merke)
    server.use(sitzungAntwort(sitzungAdmin))
    zeigeApp('/server/abc')
    expect(await screen.findByText('Server nicht gefunden.')).toBeInTheDocument()
    server.events.removeListener('request:start', merke)
    expect(urls.filter((u) => u.includes('/servers/'))).toEqual([])
  })
})

describe('Serverdetail', () => {
  test('Kopf mit Brotkrumen, Status, dringendstem Problem und Metadaten', async () => {
    await zeige({ server: { ...serverSau, cert_not_after: '2027-02-08T00:00:00Z' }, alarme: [alarmSau], regeln: [{ ...regelCpu, id: 1, metric: 'moni_filesystem_usage_percent', name: 'Dateisystem voll' }] })
    expect(screen.getByRole('navigation', { name: 'Brotkrumen' })).toHaveTextContent('Übersicht / sau-index')
    expect(screen.getByText('sau-index', { selector: '[aria-current="page"]' })).toBeInTheDocument()
    expect(await screen.findByText(/Dateisystem voll \(Einhängepunkt \/var\)/)).toBeInTheDocument()
    expect(screen.getByText('Warnung')).toBeInTheDocument()
    expect(screen.getByText(/Agent 2\.0\.0/)).toBeInTheDocument()
    expect(screen.getByText(/Zertifikat gültig bis/)).toBeInTheDocument()
  })

  test('Sekundentakt aktualisiert die Altersangabe, Diagramme rendern dabei nicht neu', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    try {
      await zeige()
      await screen.findAllByText('Ø 60 %, Spitze 100 %; Warnschwelle 75 %, überschritten')
      const alter = () => screen.getByText(/zuletzt gesehen vor/).textContent
      const vorher = alter()
      const n = renders.n
      for (let i = 0; i < 3; i++) act(() => vi.advanceTimersByTime(1000))
      expect(alter()).not.toBe(vorher)
      expect(renders.n).toBe(n)
    } finally {
      vi.useRealTimers()
    }
  })

  test('Fehler des Managers bei vorhandenen Daten zeigt den Hinweis der Übersicht und behält die Seite', async () => {
    await zeige()
    const s = serverSau
    expect(screen.queryByText('Manager nicht erreichbar, neuer Versuch in 15 s')).not.toBeInTheDocument()
    server.use(http.get(`${BASIS}/servers/${s.id}`, () => problem(502, 'weg')))
    act(() => { window.dispatchEvent(new Event('visibilitychange')) }) // Rückkehr zum Tab löst den Abruf aus
    expect(await screen.findByText('Manager nicht erreichbar, neuer Versuch in 15 s')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: s.name })).toBeInTheDocument()
  })

  test('Kennzahlen als dl, Tabellen mit Zeilenkopf und scrollbarem Bereich', async () => {
    await zeige()
    const dl = (await screen.findByText('CPU', { selector: 'dt' })).closest('dl')!
    expect(within(dl).getByText('41 %')).toBeInTheDocument()
    expect(within(dl).getByText('/var')).toBeInTheDocument()
    const region = screen.getByRole('region', { name: 'Dateisysteme' })
    expect(region).toHaveAttribute('tabindex', '0')
    expect(within(region).getByRole('rowheader', { name: '/var' })).toBeInTheDocument()
    expect(within(region).getAllByRole('columnheader').length).toBe(5)
    const netz = screen.getByRole('region', { name: 'Netzwerk' })
    expect(within(netz).getByRole('rowheader', { name: 'eth0' })).toBeInTheDocument()
    const cont = screen.getByRole('region', { name: 'Container' })
    const zeile = within(cont).getByRole('rowheader', { name: 'photoprism' }).closest('tr')!
    expect(zeile).toHaveTextContent('gesund')
    expect(zeile).toHaveTextContent('512,0 MB')
  })

  test('Snapshot ohne Temperatur und Docker zeigt "nicht verfügbar", keine leere Tabelle ohne Hinweis', async () => {
    await zeige({ snapshot: { timestamp: snapshotSau.timestamp, host: { cpu: { usage_percent: 5 } } } })
    expect(await screen.findAllByText('nicht verfügbar')).not.toHaveLength(0)
    expect(screen.getByText('Temperatur', { selector: 'dt' }).nextElementSibling).toHaveTextContent('nicht verfügbar')
    expect(screen.getByText('Docker ist auf diesem Server nicht verfügbar.')).toBeInTheDocument()
    expect(screen.getByText('Keine Dateisysteme gemeldet.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Container' })).not.toBeInTheDocument()
  })

  test('ohne Snapshot (404) bleibt die Seite benutzbar', async () => {
    await zeige({ snapshot: null })
    expect((await screen.findAllByText('nicht verfügbar')).length).toBeGreaterThan(0)
  })

  test('Kopplungsfehler zeigt den Hinweis auf pair reset', async () => {
    await zeige({ server: { ...serverSau, status: 'error', security: 'mtls', last_error: 'Zertifikat abgelehnt' } })
    expect(await screen.findByText(/Zertifikat abgelehnt/)).toBeInTheDocument()
    expect(screen.getByText('python -m moni.cli pair reset')).toBeInTheDocument()
    expect(screen.getByText(/Agent zurücksetzen mit/)).toBeInTheDocument()
  })

  test('last_error erscheint einmal', async () => {
    await zeige({ server: { ...serverSau, status: 'error', security: 'mtls', last_error: 'Zertifikat abgelehnt' } })
    await screen.findByText(/Zertifikat abgelehnt/)
    expect(screen.getAllByText(/Zertifikat abgelehnt/)).toHaveLength(1)
  })

  test('Token-Server mit Fehler bekommt den Hinweis nicht', async () => {
    await zeige({ server: { ...serverSau, status: 'error', security: 'token', last_error: 'Timeout' } })
    expect(await screen.findByText(/Timeout/)).toBeInTheDocument()
    expect(screen.queryByText(/pair reset/)).not.toBeInTheDocument()
  })

  test('Betrachter sieht keine Aktionen', async () => {
    await zeige({}, sitzungBetrachter)
    expect(screen.queryByRole('button', { name: /Stummschalten|Stummschaltung|Deaktivieren|Aktivieren/ })).not.toBeInTheDocument()
  })

  test('Containerverlauf klappt per Knopf auf, aria-expanded wechselt, Abfrage mit label=name:…', async () => {
    const user = userEvent.setup()
    await zeige()
    const knopf = await screen.findByRole('button', { name: 'Verlauf von photoprism' })
    expect(knopf).toHaveAttribute('aria-expanded', 'false')
    expect(anfragen.some((u) => u.searchParams.get('label') === 'name:photoprism')).toBe(false)
    await user.click(knopf)
    expect(knopf).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(knopf.getAttribute('aria-controls')!)).toHaveTextContent('photoprism/photoprism:latest')
    await waitFor(() => expect(anfragen.some((u) => u.searchParams.get('metric') === 'moni_container_cpu_usage_percent' && u.searchParams.get('label') === 'name:photoprism')).toBe(true))
    expect(anfragen.some((u) => u.searchParams.get('metric') === 'moni_container_memory_usage_bytes' && u.searchParams.get('label') === 'name:photoprism')).toBe(true)
    await user.click(knopf)
    expect(knopf).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(knopf.getAttribute('aria-controls')!)).toBeNull()
  })

  test('Serverdetail ohne axe-Verstöße', async () => {
    const user = userEvent.setup()
    const { container } = await zeige({ alarme: [alarmSau] })
    await user.click(await screen.findByRole('button', { name: 'Verlauf von photoprism' }))
    await screen.findAllByText(/Ø/)
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('Stummschalten und Deaktivieren', () => {
  const stumm = (antwort?: () => Response | Promise<Response>) => {
    const body: { until: string }[] = []
    server.use(http.put(`${BASIS}/servers/${serverSau.id}/mute`, async ({ request }) => {
      body.push((await request.json()) as { until: string })
      return antwort ? antwort() : HttpResponse.json(serverSau)
    }))
    return body
  }

  test('Stummschalten sendet ISO mit Z, höchstens 30 Tage; Fehler 422 erscheint im Dialog', async () => {
    const user = userEvent.setup()
    const body = stumm()
    await zeige()
    await user.click(screen.getByRole('button', { name: 'Stummschalten …' }))
    const dialog = await screen.findByRole('dialog', { name: 'Server stummschalten' })

    // zu weit in der Zukunft: Meldung am Feld, keine Anfrage
    await user.click(within(dialog).getByRole('radio', { name: 'Eigener Zeitpunkt' }))
    const feld = within(dialog).getByLabelText('Zeitpunkt')
    await user.type(feld, '2099-01-01T08:00')
    await user.click(within(dialog).getByRole('button', { name: 'Stummschalten' }))
    expect(await within(dialog).findByText('Wähle höchstens 30 Tage in der Zukunft.')).toBeInTheDocument()
    expect(feld).toHaveAttribute('aria-invalid', 'true')
    expect(body).toHaveLength(0)

    // 422 der API erscheint unverändert im Dialog
    server.use(http.put(`${BASIS}/servers/${serverSau.id}/mute`, () => problem(422, 'Stummschaltung muss in der Zukunft liegen und darf höchstens 30 Tage dauern')))
    await user.click(within(dialog).getByRole('radio', { name: '4 Std.' }))
    await user.click(within(dialog).getByRole('button', { name: 'Stummschalten' }))
    expect(await within(dialog).findByText(/darf höchstens 30 Tage dauern/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  test('1 Std. schickt UTC mit Z und schließt den Dialog', async () => {
    const user = userEvent.setup()
    const body = stumm()
    await zeige()
    const vor = Date.now()
    await user.click(screen.getByRole('button', { name: 'Stummschalten …' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('radio', { name: '1 Std.' }))
    await user.click(within(dialog).getByRole('button', { name: 'Stummschalten' }))
    await waitFor(() => expect(body).toHaveLength(1))
    expect(body[0]!.until).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$/)
    expect(Date.parse(body[0]!.until) - vor).toBeGreaterThan(3_590_000)
    expect(Date.parse(body[0]!.until) - vor).toBeLessThan(3_620_000)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText(/stummgeschaltet bis/)).toBeInTheDocument()
  })

  test('Dialog: Esc schließt und Fokus kehrt zum Knopf zurück', async () => {
    const user = userEvent.setup()
    await zeige()
    const knopf = screen.getByRole('button', { name: 'Stummschalten …' })
    await user.click(knopf)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(knopf).toHaveFocus()
    await user.click(knopf)
    await user.click(await screen.findByRole('button', { name: 'Abbrechen' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(knopf).toHaveFocus()
  })

  test('stummer Server: Stummschaltung aufheben per DELETE', async () => {
    const user = userEvent.setup()
    let gerufen = false
    server.use(http.delete(`${BASIS}/servers/${serverSau.id}/mute`, () => { gerufen = true; return new HttpResponse(null, { status: 204 }) }))
    await zeige({ server: { ...serverSau, muted_until: new Date(Date.now() + 3600_000).toISOString() } })
    expect(screen.queryByRole('button', { name: 'Stummschalten …' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stummschaltung aufheben' }))
    await waitFor(() => expect(gerufen).toBe(true))
    expect(await screen.findByText('Stummschaltung aufgehoben.')).toBeInTheDocument()
  })

  test('Deaktivieren fragt nach und sendet PATCH {enabled:false}; Aktivieren sofort', async () => {
    const user = userEvent.setup()
    const patches: unknown[] = []
    server.use(http.patch(`${BASIS}/servers/${serverEins.id}`, async ({ request }) => { patches.push(await request.json()); return HttpResponse.json(serverEins) }))
    await zeige({ server: serverEins })
    await user.click(screen.getByRole('button', { name: 'Deaktivieren' }))
    const dialog = await screen.findByRole('dialog', { name: 'Server deaktivieren' })
    expect(dialog).toHaveTextContent('wird nicht mehr abgefragt')
    expect(patches).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Deaktivieren' }))
    await waitFor(() => expect(patches).toEqual([{ enabled: false }]))
  })

  test('deaktivierter Server bietet Aktivieren an', async () => {
    const user = userEvent.setup()
    const patches: unknown[] = []
    server.use(http.patch(`${BASIS}/servers/${serverEins.id}`, async ({ request }) => { patches.push(await request.json()); return HttpResponse.json(serverEins) }))
    await zeige({ server: { ...serverEins, enabled: false } })
    await user.click(screen.getByRole('button', { name: 'Aktivieren' }))
    await waitFor(() => expect(patches).toEqual([{ enabled: true }]))
  })
})
