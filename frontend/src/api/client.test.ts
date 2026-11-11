import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, test } from 'vitest'
import { server, problem } from '../test/server'
import { ApiFehler, api, setzeCsrf } from './client'

afterEach(() => setzeCsrf(''))

describe('API-Client', () => {
  test('schreibende Anfrage sendet den CSRF-Header, lesende nicht', async () => {
    setzeCsrf('geheim-1')
    const gesehen: Record<string, string | null> = {}
    server.use(
      http.get('/api/v1/servers', ({ request }) => {
        gesehen.get = request.headers.get('X-CSRF-Token')
        return HttpResponse.json([])
      }),
      http.patch('/api/v1/servers/3', async ({ request }) => {
        gesehen.patch = request.headers.get('X-CSRF-Token')
        gesehen.body = JSON.stringify(await request.json())
        return HttpResponse.json({ ok: true })
      }),
      http.delete('/api/v1/servers/3', ({ request }) => {
        gesehen.delete = request.headers.get('X-CSRF-Token')
        return new HttpResponse(null, { status: 204 })
      }),
    )
    await api.get('/servers')
    await api.patch('/servers/3', { enabled: false })
    expect(await api.delete('/servers/3')).toBeUndefined()
    expect(gesehen).toEqual({ get: null, patch: 'geheim-1', body: '{"enabled":false}', delete: 'geheim-1' })
  })

  test('Parameter werden angehängt, Listen wiederholt', async () => {
    let url = ''
    server.use(http.get('/api/v1/servers/1/history', ({ request }) => { url = new URL(request.url).search; return HttpResponse.json({}) }))
    await api.get('/servers/1/history', { metric: 'm', label: ['a:1', 'b:2'], leer: undefined })
    expect(url).toBe('?metric=m&label=a%3A1&label=b%3A2')
  })

  test('problem+json wird zu ApiFehler mit detail', async () => {
    server.use(http.post('/api/v1/servers', () => problem(409, 'Name bereits vergeben')))
    const e = await api.post('/servers', {}).catch((x: unknown) => x)
    expect(e).toBeInstanceOf(ApiFehler)
    expect(e).toMatchObject({ status: 409, detail: 'Name bereits vergeben', message: 'Name bereits vergeben' })
  })

  test('andere Fehlerantwort ergibt den festen Text mit Status', async () => {
    server.use(http.get('/api/v1/servers', () => new HttpResponse('<html>kaputt</html>', { status: 502, headers: { 'Content-Type': 'text/html' } })))
    await expect(api.get('/servers')).rejects.toMatchObject({ status: 502, detail: 'Der Manager hat unerwartet geantwortet (502).' })
  })

  test('Netzwerkfehler ergibt "Manager nicht erreichbar."', async () => {
    server.use(http.get('/api/v1/servers', () => HttpResponse.error()))
    await expect(api.get('/servers')).rejects.toMatchObject({ status: 0, detail: 'Manager nicht erreichbar.' })
  })
})
