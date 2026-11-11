import type { Server, Session } from '../api/typen'

const benutzer = { id: 1, email: 'robin@example.test', name: 'Robin', kind: 'local', totp_enabled: true } as const

export const sitzungAdmin: Session = { stage: 'full', csrf_token: 'csrf-admin', user: { ...benutzer, role: 'admin' } }
export const sitzungBetrachter: Session = {
  stage: 'full',
  csrf_token: 'csrf-bea',
  user: { ...benutzer, id: 2, email: 'bea@example.test', name: 'Bea', role: 'viewer' },
}
export const sitzungMfa: Session = { ...sitzungAdmin, stage: 'mfa' }
export const sitzungEnroll: Session = { ...sitzungAdmin, stage: 'enroll' }

export const serverEins: Server = {
  id: 1, name: 'alpha', url: 'https://alpha.example.test:8443', is_local: false, enabled: true, status: 'ok', failures: 0,
  last_error: null, last_seen: '2026-11-11T10:00:00Z', header_names: [], warnings: 0, agent_version: '2.0.0',
  security: 'mtls', cert_not_after: null, muted_until: null, summary: null,
}
