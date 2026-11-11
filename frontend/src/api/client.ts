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

type Param = string | number | boolean | undefined | readonly string[]

const unerwartet = (status: number) => new ApiFehler(status, t('fehler.unerwartet', { status }))

async function anfrage<T>(methode: string, pfad: string, body?: unknown, params?: Record<string, Param>): Promise<T> {
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
    res = await fetch(url, { method: methode, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) })
  } catch {
    throw new ApiFehler(0, t('fehler.netzwerk'))
  }

  if (!res.ok) {
    if (res.headers.get('content-type')?.includes('application/problem+json')) {
      const p: unknown = await res.json().catch(() => null)
      const detail = (p as { detail?: unknown } | null)?.detail
      if (typeof detail === 'string') throw new ApiFehler(res.status, detail)
    }
    throw unerwartet(res.status)
  }
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw unerwartet(res.status)
  }
}

export const api = {
  get: <T>(pfad: string, params?: Record<string, Param>) => anfrage<T>('GET', pfad, undefined, params),
  post: <T>(pfad: string, body?: unknown) => anfrage<T>('POST', pfad, body),
  put: <T>(pfad: string, body?: unknown) => anfrage<T>('PUT', pfad, body),
  patch: <T>(pfad: string, body?: unknown) => anfrage<T>('PATCH', pfad, body),
  delete: (pfad: string) => anfrage<void>('DELETE', pfad),
}
