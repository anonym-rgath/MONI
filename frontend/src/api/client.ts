import { t } from '../texte/de'

const BASIS = '/api/v1'

export class ApiFehler extends Error {
  readonly status: number
  readonly detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiFehler'
    this.status = status
    this.detail = detail
  }
}

// Nur im Speicher (D5): die SitzungProvider setzt den Wert, sobald /auth/session geantwortet hat.
let csrf = ''
export const setzeCsrf = (wert: string) => { csrf = wert }

// P6-R13: eine Regel für alle Anfragen. Jede 401 heißt „Sitzung abgelaufen“, außer falschem Faktor/Passwort und Kopplungsfehler (feste Backend-Texte).
const KEIN_ABLAUF = new Set(['Kopplungs-Token ungültig', 'Anmeldung fehlgeschlagen'])
let beiAblauf = () => {}
export const setzeAblauf = (f: () => void) => { beiAblauf = f }

type Param = string | number | boolean | undefined | readonly string[]

const unerwartet = (status: number) => new ApiFehler(status, t('fehler.unerwartet', { status }))

interface Optionen { signal?: AbortSignal }

async function anfrage<T>(methode: string, pfad: string, body?: unknown, params?: Record<string, Param>, opt?: Optionen): Promise<T> {
  // jsdom/Node-fetch brauchen eine absolute URL; im Browser ist sie gleich dem relativen Pfad (gleicher Ursprung).
  const url = new URL(BASIS + pfad, location.origin)
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined) continue
    for (const x of Array.isArray(v) ? v : [v]) url.searchParams.append(k, String(x))
  }
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (methode !== 'GET') headers['X-CSRF-Token'] = csrf

  let res: Response
  try {
    res = await fetch(url, { method: methode, headers, credentials: 'same-origin', signal: opt?.signal, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiFehler(0, t('fehler.netzwerk'))
  }

  if (!res.ok) {
    let fehler = unerwartet(res.status)
    if (res.headers.get('content-type')?.includes('application/problem+json')) {
      const p: unknown = await res.json().catch(() => null)
      const detail = (p as { detail?: unknown } | null)?.detail
      if (typeof detail === 'string') fehler = new ApiFehler(res.status, detail)
    }
    if (fehler.status === 401 && !KEIN_ABLAUF.has(fehler.detail)) beiAblauf()
    throw fehler
  }
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw unerwartet(res.status)
  }
}

export const api = {
  get: <T>(pfad: string, params?: Record<string, Param>, opt?: Optionen) => anfrage<T>('GET', pfad, undefined, params, opt),
  post: <T>(pfad: string, body?: unknown, opt?: Optionen) => anfrage<T>('POST', pfad, body, undefined, opt),
  put: <T>(pfad: string, body?: unknown, opt?: Optionen) => anfrage<T>('PUT', pfad, body, undefined, opt),
  patch: <T>(pfad: string, body?: unknown, opt?: Optionen) => anfrage<T>('PATCH', pfad, body, undefined, opt),
  delete: (pfad: string, opt?: Optionen) => anfrage<void>('DELETE', pfad, undefined, undefined, opt),
}
