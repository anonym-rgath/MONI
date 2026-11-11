import { act, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { describe, expect, test } from 'vitest'
import type { Alarm, Server } from '../../api/typen'
import { alarmSau, alleServer, regelDateisystem, serverPi, sitzungAdmin, sitzungBetrachter } from '../../test/fixtures'
import { mockMatchMedia } from '../../test/matchMedia'
import { zeigeApp } from '../../test/render'
import { BASIS, problem, server, sitzungAntwort } from '../../test/server'
import { dringlichkeit, sortiere } from './dringlichkeit'

const daten = (servers: Server[] = alleServer, alarme: Alarm[] = [alarmSau]) => {
  server.use(
    http.get(`${BASIS}/servers`, () => HttpResponse.json(servers)),
    http.get(`${BASIS}/alerts`, () => HttpResponse.json(alarme)),
    http.get(`${BASIS}/alert-rules`, () => HttpResponse.json([regelDateisystem])),
  )
}
const zeige = async (s = sitzungAdmin) => {
  server.use(sitzungAntwort(s))
  const r = zeigeApp('/')
  await screen.findByRole('heading', { level: 1, name: 'Übersicht' })
  return r
}

describe('Dringlichkeit', () => {
  test('Sortierung nach Dringlichkeit', () => {
    expect(sortiere([...alleServer].reverse(), [alarmSau]).map((s) => s.name)).toEqual(['vps-backup', 'sau-index', 'pi', 'nas'])
  })

  test('Reihenfolge: Fehler < kritisch < Warnung < OK < stumm < deaktiviert', () => {
    const kritisch: Alarm = { ...alarmSau, severity: 'critical' }
    const fehler = { ...serverPi, status: 'error' as const }
    const stumm = { ...serverPi, muted_until: new Date(Date.now() + 60_000).toISOString() }
    const aus = { ...serverPi, enabled: false }
    const wert = [dringlichkeit(fehler, []), dringlichkeit(serverPi, [kritisch, alarmSau].map((a) => ({ ...a, server: { id: serverPi.id, name: 'pi' } }))),
      dringlichkeit(serverPi, [{ ...alarmSau, server: { id: serverPi.id, name: 'pi' } }]), dringlichkeit(serverPi, []), dringlichkeit(stumm, []), dringlichkeit(aus, [])]
    expect(wert).toEqual([...wert].sort((a, b) => a - b))
    expect(new Set(wert).size).toBe(6)
  })

  test('ein wartender Alarm (pending) zählt nicht', () => {
    expect(dringlichkeit(serverPi, [{ ...alarmSau, server: { id: serverPi.id, name: 'pi' }, state: 'pending' }])).toBe(dringlichkeit(serverPi, []))
  })
})

describe('Übersicht', () => {
  test('Karte ohne summary zeigt "keine Daten" statt NaN', async () => {
    daten()
    await zeige()
    const karte = (await screen.findByRole('heading', { level: 3, name: 'vps-backup' })).closest('a')!
    expect(karte).toHaveAttribute('href', '/server/13')
    expect(within(karte).getAllByText('keine Daten')).toHaveLength(5) // vier Kennzahlen und Container
    expect(karte).not.toHaveTextContent('NaN')
    expect(karte).toHaveTextContent('Nicht erreichbar')
    expect(karte).toHaveTextContent('Agent · Token')
    expect(karte).toHaveTextContent('zuletzt gesehen vor 12 min')
  })

  test('Karte zeigt Zahl, bei Warnung mit Wort, dazu Art, Container und Stummschaltung', async () => {
    daten()
    await zeige()
    const sau = (await screen.findByRole('heading', { level: 3, name: 'sau-index' })).closest('a')!
    expect(sau).toHaveTextContent('Agent · mTLS')
    expect(within(sau).getByText('81 % Warnung')).toBeInTheDocument()
    expect(sau).toHaveTextContent('Speicher /var')
    expect(within(sau).getByText('41 %')).toBeInTheDocument()
    expect(within(sau).getByText('58 °C')).toBeInTheDocument()
    expect(sau).toHaveTextContent('9 von 9 Containern laufen')
    const nas = screen.getByRole('heading', { level: 3, name: 'nas' }).closest('a')!
    expect(nas).toHaveTextContent('Stummgeschaltet')
    expect(nas).toHaveTextContent('5 von 6 Containern laufen')
    expect(nas).toHaveTextContent(/stumm bis /)
    const pi = screen.getByRole('heading', { level: 3, name: 'pi' }).closest('a')!
    expect(pi).toHaveTextContent('Manager · lokal')
  })

  test('Schwellen: Temperatur ab 75 kritisch, CPU ab 85 kritisch', async () => {
    daten([{ ...serverPi, summary: { ...serverPi.summary!, temperature_max_c: 76, cpu_percent: 85 } }], [])
    await zeige()
    expect(await screen.findByText('76 °C kritisch')).toBeInTheDocument()
    expect(screen.getByText('85 % kritisch')).toBeInTheDocument()
  })

  test('Aufmerksamkeit nennt Regel, Wert und Schwelle; die übrigen werden gezählt', async () => {
    daten()
    await zeige()
    const abschnitt = (await screen.findByRole('heading', { level: 2, name: '2 Server brauchen Aufmerksamkeit' })).closest('section')!
    const zeilen = within(abschnitt).getAllByRole('listitem')
    expect(zeilen).toHaveLength(2)
    expect(zeilen[0]).toHaveTextContent('Kritisch')
    expect(within(zeilen[0]!).getByRole('link', { name: 'vps-backup' })).toHaveAttribute('href', '/server/13')
    expect(zeilen[0]).toHaveTextContent('ist nicht erreichbar (3 Abrufe in Folge fehlgeschlagen)')
    expect(zeilen[1]).toHaveTextContent('Warnung')
    expect(within(zeilen[1]!).getByRole('link', { name: 'sau-index' })).toBeInTheDocument()
    expect(zeilen[1]).toHaveTextContent('Dateisystem voll (/var): 81 % (Schwelle 75 %)')
    expect(zeilen[1]).toHaveTextContent('seit 2 Std.')
    expect(abschnitt).toHaveTextContent('Die übrigen 2 laufen normal, davon 1 stummgeschaltet.')
  })

  test('ohne Probleme steht "Alle Server laufen normal."', async () => {
    daten([serverPi], [])
    await zeige()
    expect(await screen.findByText('Alle Server laufen normal.')).toBeInTheDocument()
  })

  test('Kopf zeigt Anzahl und Alter; Admin sieht "Server koppeln"', async () => {
    daten()
    await zeige()
    expect(await screen.findByText(/^4 Server · aktualisiert vor \d+ s$/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Server koppeln' })).toHaveAttribute('href', '/verwaltung/server?koppeln=1')
  })

  test('Betrachter sieht keinen Knopf "Server koppeln"', async () => {
    daten()
    await zeige(sitzungBetrachter)
    await screen.findByRole('heading', { level: 3, name: 'pi' })
    expect(screen.queryByRole('link', { name: 'Server koppeln' })).not.toBeInTheDocument()
  })

  test('leer: Admin bekommt den nächsten Schritt, Betrachter nur den Satz', async () => {
    daten([], [])
    const { unmount } = await zeige()
    expect(await screen.findByText('Noch keine Server.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Server koppeln' })).toHaveLength(2) // Kopf und Leerzustand
    unmount()
    await zeige(sitzungBetrachter)
    expect(await screen.findByText('Noch keine Server.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Server koppeln' })).not.toBeInTheDocument()
  })

  test('Laden zeigt ein Skelett mit aria-busy', async () => {
    server.use(http.get(`${BASIS}/servers`, () => new Promise(() => {})))
    await zeige()
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull()
  })

  test('Fehler ohne alte Daten zeigt nur den Hinweis', async () => {
    server.use(http.get(`${BASIS}/servers`, () => problem(500, 'kaputt')))
    await zeige()
    expect(await screen.findByRole('alert')).toHaveTextContent('Manager nicht erreichbar, neuer Versuch in 15 s')
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
  })

  test('Fehler des Managers zeigt Hinweis und behält letzte Daten', async () => {
    daten()
    await zeige()
    await screen.findByRole('heading', { level: 3, name: 'pi' })
    server.use(http.get(`${BASIS}/servers`, () => problem(502, 'weg')))
    act(() => { window.dispatchEvent(new Event('visibilitychange')) }) // Rückkehr zum Tab löst den Abruf aus
    expect(await screen.findByRole('alert')).toHaveTextContent('Manager nicht erreichbar, neuer Versuch in 15 s')
    expect(screen.getByRole('heading', { level: 3, name: 'pi' })).toBeInTheDocument()
  })

  test('Alarmzähler im Rahmen zählt offene firing-Alarme', async () => {
    daten(alleServer, [alarmSau, { ...alarmSau, state: 'pending' }])
    await zeige()
    await waitFor(() => expect(screen.getByRole('link', { name: 'Alarme 1 offen' })).toBeInTheDocument())
  })

  test.each([[true, 'hell'], [false, 'dunkel']])('ohne axe-Verstöße (%s)', async (hell) => {
    mockMatchMedia({ light: hell })
    daten()
    const { container } = await zeige()
    await screen.findByRole('heading', { level: 3, name: 'pi' })
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Seitentitel und H1 sind fokussierbar', async () => {
    daten()
    await zeige()
    expect(document.title).toBe('Übersicht – Moni')
    expect(screen.getByRole('heading', { level: 1 })).toHaveAttribute('tabindex', '-1')
  })
})
