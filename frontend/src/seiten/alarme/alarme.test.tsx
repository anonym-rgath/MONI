import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { axe } from 'vitest-axe'
import { describe, expect, test } from 'vitest'
import type { Alarm, Kanal, Regel, Verlaufseintrag } from '../../api/typen'
import { alarmSau, alleServer, kanalEmail, kanalWebhook, regelCpu, regelDateisystem, sitzungAdmin, sitzungBetrachter } from '../../test/fixtures'
import { zeigeApp } from '../../test/render'
import { BASIS, problem, server, sitzungAntwort } from '../../test/server'

const eintrag = (id: number, extra: Partial<Verlaufseintrag> = {}): Verlaufseintrag => ({
  id, ts: '2026-11-11T09:14:00Z', rule_id: 1, rule_name: 'Dateisystem voll', server_id: 11, server_name: 'sau-index', labels: { mountpoint: '/var' },
  event: 'firing', severity: 'warning', value: 81, threshold: 75, reason: null, muted: false, ...extra,
})

interface Daten { alarme?: Alarm[]; regeln?: Regel[]; kanaele?: Kanal[]; verlauf?: (url: URL) => { entries: Verlaufseintrag[]; next_before: number | null } }
const anfragen: { methode: string; url: URL; body: unknown }[] = []

function daten(d: Daten = {}) {
  anfragen.length = 0
  server.use(
    http.get(`${BASIS}/servers`, () => HttpResponse.json(alleServer)),
    http.get(`${BASIS}/alerts`, () => HttpResponse.json(d.alarme ?? [alarmSau])),
    http.get(`${BASIS}/alert-rules`, () => HttpResponse.json(d.regeln ?? [regelDateisystem, regelCpu])),
    http.get(`${BASIS}/alert-channels`, () => HttpResponse.json(d.kanaele ?? [kanalWebhook, kanalEmail])),
    http.get(`${BASIS}/alert-settings`, () => HttpResponse.json({ remind_minutes: 240 })),
    http.get(`${BASIS}/alerts/history`, ({ request }) => {
      const url = new URL(request.url)
      anfragen.push({ methode: 'GET', url, body: null })
      return HttpResponse.json(d.verlauf ? d.verlauf(url) : { entries: [eintrag(3), eintrag(2, { event: 'resolved', severity: 'critical', reason: 'Container entfernt' })], next_before: null })
    }),
  )
}
const zeige = async (pfad: string, s = sitzungAdmin, h1 = 'Alarme') => {
  server.use(sitzungAntwort(s))
  const r = zeigeApp(pfad)
  await screen.findByRole('heading', { level: 1, name: h1 })
  return r
}
const aufzeichnen = (methode: 'post' | 'patch' | 'put' | 'delete', pfad: string, antwort: () => Response | Promise<Response>) =>
  server.use(http[methode](`${BASIS}${pfad}`, async ({ request }) => {
    anfragen.push({ methode: request.method, url: new URL(request.url), body: request.method === 'DELETE' ? null : await request.json().catch(() => null) })
    return antwort()
  }))
const letzte = () => anfragen[anfragen.length - 1]!

describe('Bereichsnavigation', () => {
  test('Bereichsnavigation sind Links mit aria-current', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme')
    const nav = screen.getByRole('navigation', { name: 'Bereiche der Alarme' })
    const offen = within(nav).getByRole('link', { name: /Offen/ })
    expect(offen).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Verlauf' })).not.toHaveAttribute('aria-current')
    expect(within(nav).getAllByRole('link')).toHaveLength(4)
    expect(within(nav).queryByRole('tab')).not.toBeInTheDocument()
    await user.click(within(nav).getByRole('link', { name: 'Regeln' }))
    await screen.findByRole('heading', { level: 2, name: 'Regeln' })
    expect(within(nav).getByRole('link', { name: 'Regeln' })).toHaveAttribute('aria-current', 'page')
    expect(document.title).toBe('Alarmregeln – Moni')
  })
})

describe('Offen', () => {
  test('Liste mit Schwere, Regel · Server, Klartext-Labels, Wert mit Schwelle und stummem Server', async () => {
    daten({ alarme: [{ ...alarmSau, muted: true }, { ...alarmSau, rule: { id: 2, name: 'CPU hoch' }, server: { id: 10, name: 'pi' }, labels: {}, severity: 'critical', value: 93 }] })
    await zeige('/alarme')
    const zeilen = await screen.findAllByRole('listitem')
    const kritisch = zeilen.find((z) => z.textContent?.includes('CPU hoch'))!
    expect(kritisch).toHaveTextContent('Kritisch')
    expect(kritisch).toHaveTextContent('CPU hoch · pi')
    const warnung = zeilen.find((z) => z.textContent?.includes('Dateisystem voll'))!
    expect(warnung).toHaveTextContent('Warnung')
    expect(warnung).toHaveTextContent('Dateisystem voll · sau-index')
    expect(warnung).toHaveTextContent('Einhängepunkt /var')
    await waitFor(() => expect(warnung).toHaveTextContent('81 % (ab 75 %)'))
    expect(warnung).toHaveTextContent('seit 2 Std.')
    expect(kritisch).not.toHaveTextContent('stummgeschaltet')
    expect(screen.getByRole('heading', { level: 2, name: '2 offene Alarme' })).toBeInTheDocument()
  })

  test('ohne Alarme: Leerzustand', async () => {
    daten({ alarme: [] })
    await zeige('/alarme')
    expect(await screen.findByText('Keine offenen Alarme.')).toBeInTheDocument()
  })
})

describe('Verlauf', () => {
  test('Verlauf blättert mit before=next_before und der Serverfilter setzt zurück', async () => {
    daten({ verlauf: (url) => (url.searchParams.get('before') === '3' ? { entries: [eintrag(2, { event: 'changed', reason: 'Warnung → Kritisch' })], next_before: null } : { entries: [eintrag(4), eintrag(3, { event: 'reminder' })], next_before: 3 }) })
    const user = userEvent.setup()
    await zeige('/alarme/verlauf')
    const tabelle = await screen.findByRole('table')
    await within(tabelle).findAllByRole('row')
    expect(anfragen[0]!.url.searchParams.get('limit')).toBe('50')
    expect(anfragen[0]!.url.searchParams.has('before')).toBe(false)
    expect(within(tabelle).getByRole('columnheader', { name: 'Schwere' })).toBeInTheDocument()
    expect(within(tabelle).getByText('ausgelöst')).toBeInTheDocument()
    expect(within(tabelle).getByText('Erinnerung')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Ältere laden' }))
    expect(await within(tabelle).findByText('Schwere geändert')).toBeInTheDocument()
    expect(within(tabelle).getByText('ausgelöst')).toBeInTheDocument() // angehängt, nicht ersetzt
    expect(anfragen.some((a) => a.url.searchParams.get('before') === '3')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Ältere laden' })).not.toBeInTheDocument()

    anfragen.length = 0
    await user.selectOptions(screen.getByRole('combobox', { name: 'Server' }), 'sau-index')
    await waitFor(() => expect(anfragen.length).toBeGreaterThan(0))
    expect(anfragen[0]!.url.searchParams.get('server_id')).toBe('11')
    expect(anfragen[0]!.url.searchParams.has('before')).toBe(false)
  })
})

describe('Regeln', () => {
  test('Tabelle in Klartext', async () => {
    daten()
    await zeige('/alarme/regeln')
    const zeile = (await screen.findByRole('row', { name: /Dateisystem voll/ }))
    expect(zeile).toHaveTextContent('bei Dateisystem-Auslastung der Wert die Schwelle erreicht oder überschreitet (je Einhängepunkt)')
    expect(zeile).toHaveTextContent('75 %')
    expect(zeile).toHaveTextContent('85 %')
    expect(zeile).toHaveTextContent('5 min')
    expect(zeile).toHaveTextContent('alle Server')
    expect(zeile).toHaveTextContent('aktiv')
  })

  test('Regel-Formular sendet Minuten als Sekunden und nur geänderte Felder; 422 erscheint am Formular', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/regeln')
    await user.click(await screen.findByRole('button', { name: /Bearbeiten.*Dateisystem voll/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Mindestdauer in Minuten')).toHaveValue(5)
    await user.clear(within(dialog).getByLabelText('Mindestdauer in Minuten'))
    await user.type(within(dialog).getByLabelText('Mindestdauer in Minuten'), '10')
    aufzeichnen('patch', '/alert-rules/1', () => problem(422, 'Warnschwelle muss vor der kritischen Schwelle liegen'))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Warnschwelle muss vor der kritischen Schwelle liegen')
    expect(letzte().body).toEqual({ for_seconds: 600 })
    expect(dialog).toHaveAttribute('open')

    aufzeichnen('patch', '/alert-rules/1', () => HttpResponse.json({ ...regelDateisystem, for_seconds: 600 }))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    expect((await screen.findAllByRole('status')).some((s) => s.textContent === 'Gespeichert.')).toBe(true)
    expect(dialog).not.toHaveAttribute('open')
  })

  test('Eine Antwort ohne Änderung zählt als Gespeichert; Filter und Gruppierung werden zu Liste und Objekt', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/regeln')
    await user.click(await screen.findByRole('button', { name: /Bearbeiten.*Dateisystem voll/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Filter'), 'mountpoint=/var')
    await user.type(within(dialog).getByLabelText('Gruppierung nach Labels'), ', device')
    aufzeichnen('patch', '/alert-rules/1', () => HttpResponse.json(regelDateisystem))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(letzte().body).toEqual({ key_labels: ['mountpoint', 'device'], label_filter: { mountpoint: '/var' } }))
    await waitFor(() => expect(screen.getAllByRole('status').some((s) => s.textContent === 'Gespeichert.')).toBe(true))
  })

  test('Anlegen: Zunahme mit Fenster in Minuten wird zu window_seconds, Server und Aktiv', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/regeln')
    await user.click(await screen.findByRole('button', { name: 'Regel anlegen' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText('Fenster in Minuten')).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Name'), 'Neustart-Schleife')
    await user.selectOptions(within(dialog).getByLabelText('Metrik'), 'moni_container_restarts')
    await user.selectOptions(within(dialog).getByLabelText('Funktion'), 'increase')
    await user.type(within(dialog).getByLabelText('Fenster in Minuten'), '10')
    await user.type(within(dialog).getByLabelText('Kritisch ab'), '3')
    await user.selectOptions(within(dialog).getByLabelText('Gilt für'), 'sau-index')
    aufzeichnen('post', '/alert-rules', () => HttpResponse.json({ ...regelDateisystem, id: 9 }, { status: 201 }))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(letzte().body).toMatchObject({
      name: 'Neustart-Schleife', metric: 'moni_container_restarts', func: 'increase', window_seconds: 600, op: '>=', warning: null, critical: 3,
      for_seconds: 0, key_labels: [], label_filter: {}, require_prior_ok: false, server_id: 11, enabled: true,
    }))
  })

  test('Regel löschen fragt vorher und nennt die stille Auflösung', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/regeln')
    await user.click(await screen.findByRole('button', { name: /Löschen.*Dateisystem voll/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Regel ‚Dateisystem voll‘ löschen? Offene Alarme dieser Regel werden ohne Benachrichtigung aufgelöst.')
    expect(anfragen.filter((a) => a.methode === 'DELETE')).toHaveLength(0)
    aufzeichnen('delete', '/alert-rules/1', () => new HttpResponse(null, { status: 204 }))
    await user.click(within(dialog).getByRole('button', { name: 'Löschen' }))
    await waitFor(() => expect(letzte().methode).toBe('DELETE'))
    await waitFor(() => expect(screen.getAllByRole('status').some((s) => s.textContent === 'Regel gelöscht.')).toBe(true))
  })
})

describe('Kanäle', () => {
  test('Karten zeigen Ziel, Meldet und Geheimnis', async () => {
    daten({ kanaele: [kanalWebhook, kanalEmail, { ...kanalWebhook, id: 7, name: 'offen', secret_set: false, min_severity: 'critical' }] })
    await zeige('/alarme/kanaele')
    const web = (await screen.findByRole('heading', { level: 3, name: 'ntfy' })).closest('li')!
    expect(web).toHaveTextContent('https://ntfy.sh/…')
    expect(web).toHaveTextContent('Warnungen und Kritisches')
    expect(web).toHaveTextContent('Geheimnis gesetzt')
    const mail = screen.getByRole('heading', { level: 3, name: 'E-Mail Bereitschaft' }).closest('li')!
    expect(mail).toHaveTextContent('smtp.example.org, 2 Empfänger')
    expect(mail).toHaveTextContent('Nur Kritisches')
    expect(screen.getByRole('heading', { level: 3, name: 'offen' }).closest('li')).toHaveTextContent('ohne Geheimnis')
  })

  test('Kanal bearbeiten: leeres Passwortfeld wird nicht gesendet', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/kanaele')
    const karte = (await screen.findByRole('heading', { level: 3, name: 'E-Mail Bereitschaft' })).closest('li')!
    await user.click(within(karte).getByRole('button', { name: /bearbeiten/i }))
    const dialog = await screen.findByRole('dialog')
    const passwort = within(dialog).getByLabelText('Passwort')
    expect(passwort).toHaveValue('')
    expect(dialog).toHaveTextContent('Leer lassen, um das gespeicherte zu behalten')
    expect(within(dialog).getByLabelText('Host')).toHaveValue('smtp.example.org')
    expect(within(dialog).getByLabelText('Empfänger')).toHaveValue('a@example.org\nb@example.org')
    await user.clear(within(dialog).getByLabelText('Port'))
    await user.type(within(dialog).getByLabelText('Port'), '2525')
    aufzeichnen('patch', '/alert-channels/6', () => HttpResponse.json(kanalEmail))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(letzte().body).toBeTruthy())
    expect(letzte().body).toMatchObject({ host: 'smtp.example.org', port: 2525, security: 'starttls', to: ['a@example.org', 'b@example.org'], min_severity: 'critical', enabled: true })
    expect(letzte().body).not.toHaveProperty('password')

    await user.click(within(karte).getByRole('button', { name: /bearbeiten/i }))
    const zweiter = await screen.findByRole('dialog')
    await user.type(within(zweiter).getByLabelText('Passwort'), 'neues-passwort')
    await user.click(within(zweiter).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(letzte().body).toHaveProperty('password', 'neues-passwort'))
  })

  test('Webhook anlegen: URL und optionales Secret; leeres Secret entfällt', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/kanaele')
    await user.click(await screen.findByRole('button', { name: 'Kanal anlegen' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Hook')
    await user.type(within(dialog).getByLabelText('URL'), 'https://hook.example.test/x')
    aufzeichnen('post', '/alert-channels', () => HttpResponse.json(kanalWebhook, { status: 201 }))
    await user.click(within(dialog).getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(letzte().body).toEqual({ name: 'Hook', kind: 'webhook', url: 'https://hook.example.test/x', min_severity: 'warning', enabled: true }))
  })

  test('Testnachricht: ok und Fehlertext werden angesagt; 429 zeigt Wartehinweis', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/kanaele')
    const karte = (await screen.findByRole('heading', { level: 3, name: 'ntfy' })).closest('li')!
    const status = within(karte).getByRole('status')
    expect(status).toBeEmptyDOMElement()
    const knopf = within(karte).getByRole('button', { name: 'Testnachricht senden' })
    aufzeichnen('post', '/alert-channels/5/test', () => HttpResponse.json({ ok: true, error: null }))
    await user.click(knopf)
    await waitFor(() => expect(status).toHaveTextContent('Testnachricht gesendet. Der Empfänger hat sie angenommen.'))
    aufzeichnen('post', '/alert-channels/5/test', () => HttpResponse.json({ ok: false, error: 'Webhook antwortet mit HTTP 502' }))
    await user.click(knopf)
    await waitFor(() => expect(status).toHaveTextContent('Webhook antwortet mit HTTP 502'))
    expect(status).not.toHaveTextContent('Der Empfänger hat sie angenommen')
    aufzeichnen('post', '/alert-channels/5/test', () => problem(429, 'Zu viele Testversuche, bitte eine Minute warten'))
    await user.click(knopf)
    await waitFor(() => expect(status).toHaveTextContent('Zu viele Tests, bitte eine Minute warten.'))
    expect(status).not.toHaveTextContent('Webhook antwortet')
  })

  test('Erinnerung lädt, speichert Minuten und meldet das Ergebnis', async () => {
    daten()
    const user = userEvent.setup()
    await zeige('/alarme/kanaele')
    const feld = await screen.findByLabelText('Erinnern, solange ein Alarm offen ist, alle … Minuten')
    await waitFor(() => expect(feld).toHaveValue(240))
    expect(screen.getByText('0 schaltet Erinnerungen ab, höchstens 10080 (7 Tage).')).toBeInTheDocument()
    await user.clear(feld)
    await user.type(feld, '60')
    aufzeichnen('put', '/alert-settings', () => HttpResponse.json({ remind_minutes: 60 }))
    await user.click(screen.getByRole('button', { name: 'Erinnerung speichern' }))
    await waitFor(() => expect(letzte().body).toEqual({ remind_minutes: 60 }))
    await waitFor(() => expect(screen.getAllByRole('status').some((s) => s.textContent === 'Erinnerung gespeichert.')).toBe(true))
  })
})

describe('Betrachter', () => {
  test('Betrachter sieht keine Kanäle und keine Bearbeiten-Knöpfe', async () => {
    daten()
    server.use(http.get(`${BASIS}/alert-channels`, () => problem(403, 'Keine Berechtigung')))
    await zeige('/alarme/regeln', sitzungBetrachter)
    await screen.findByRole('row', { name: /Dateisystem voll/ })
    const nav = screen.getByRole('navigation', { name: 'Bereiche der Alarme' })
    expect(within(nav).queryByRole('link', { name: 'Kanäle' })).not.toBeInTheDocument()
    expect(within(nav).getAllByRole('link')).toHaveLength(3)
    expect(screen.queryByRole('button', { name: /Bearbeiten|Löschen|Regel anlegen/ })).not.toBeInTheDocument()
  })

  test('direkter Aufruf der Kanäle zeigt Keine Berechtigung ohne Anfrage', async () => {
    daten()
    let abgefragt = false
    server.use(http.get(`${BASIS}/alert-channels`, () => { abgefragt = true; return problem(403, 'Keine Berechtigung') }))
    await zeige('/alarme/kanaele', sitzungBetrachter, 'Keine Berechtigung')
    expect(abgefragt).toBe(false)
  })
})

describe('Barrierefreiheit', () => {
  test.each([['/alarme'], ['/alarme/verlauf'], ['/alarme/regeln'], ['/alarme/kanaele']])('Alarmseite %s ohne axe-Verstöße', async (pfad) => {
    daten()
    const { container } = await zeige(pfad)
    await waitFor(() => expect(document.querySelectorAll('[aria-busy="true"]').length).toBe(0))
    await screen.findAllByText(/./, { selector: 'main *' })
    if (pfad.endsWith('regeln')) await screen.findByRole('row', { name: /CPU hoch/ })
    if (pfad.endsWith('kanaele')) await screen.findByRole('heading', { level: 3, name: 'ntfy' })
    if (pfad.endsWith('verlauf')) await screen.findAllByRole('row', { name: /sau-index/ })
    expect(await axe(container)).toHaveNoViolations()
  })

  test('Dialog mit Regel-Formular ohne axe-Verstöße', async () => {
    daten()
    const user = userEvent.setup()
    const { container } = await zeige('/alarme/regeln')
    await user.click(await screen.findByRole('button', { name: 'Regel anlegen' }))
    await screen.findByRole('dialog')
    expect(await axe(container)).toHaveNoViolations()
  })
})
