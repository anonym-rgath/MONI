import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { Session } from '../api/typen'

export const BASIS = '/api/v1'

export const problem = (status: number, detail: string) =>
  HttpResponse.json({ type: 'about:blank', title: 'Fehler', status, detail }, { status, headers: { 'Content-Type': 'application/problem+json' } })

export const sitzungAntwort = (s: Session | null) =>
  http.get(`${BASIS}/auth/session`, () => (s ? HttpResponse.json(s) : problem(401, 'Nicht angemeldet')))

// Standard: nicht angemeldet. Tests überschreiben mit server.use(...).
export const server = setupServer(
  sitzungAntwort(null),
  http.get(`${BASIS}/setup`, () => HttpResponse.json({ required: false })),
  http.get(`${BASIS}/auth/oidc`, () => HttpResponse.json({ providers: [] })),
)
