import type { Alarm, Kanal, Regel, Server, Session, Snapshot } from '../api/typen'

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

// Übersicht: vier Server wie im Entwurf (pi ok, sau-index Warnung, nas stumm, vps-backup nicht erreichbar).
const vor = (sekunden: number) => new Date(Date.now() - sekunden * 1000).toISOString()
const summary = (cpu: number, ram: number, fs: number, temp: number, mount: string, laufen: number, gesamt: number) => ({
  cpu_percent: cpu, memory_percent: ram, temperature_max_c: temp, filesystem_max: { mountpoint: mount, percent: fs },
  containers_running: laufen, containers_total: gesamt,
})
export const serverPi: Server = {
  ...serverEins, id: 10, name: 'pi', url: 'https://localhost', is_local: true, security: null, last_seen: vor(2),
  summary: summary(23, 61, 48, 52, '/', 14, 14),
}
export const serverSau: Server = {
  ...serverEins, id: 11, name: 'sau-index', security: 'mtls', last_seen: vor(3), warnings: 1,
  summary: summary(41, 72, 81, 58, '/var', 9, 9),
}
export const serverNas: Server = {
  ...serverEins, id: 12, name: 'nas', security: 'mtls', last_seen: vor(4), muted_until: new Date(Date.now() + 3 * 3600_000).toISOString(),
  summary: summary(8, 34, 66, 44, '/srv', 5, 6),
}
export const serverVps: Server = {
  ...serverEins, id: 13, name: 'vps-backup', security: 'token', status: 'unreachable', failures: 3, last_seen: vor(12 * 60), summary: null,
}
export const alleServer = [serverPi, serverSau, serverNas, serverVps]

export const regelDateisystem: Regel = {
  id: 1, name: 'Dateisystem voll', metric: 'moni_filesystem_usage_percent', func: 'value', window_seconds: null, op: '>=',
  warning: 75, critical: 85, for_seconds: 300, key_labels: ['mountpoint'], label_filter: {}, require_prior_ok: false,
  server_id: null, enabled: true, created_at: null, updated_at: null,
}
export const alarmSau: Alarm = {
  rule: { id: 1, name: 'Dateisystem voll' }, server: { id: 11, name: 'sau-index' }, labels: { mountpoint: '/var' },
  state: 'firing', severity: 'warning', value: 81, since: vor(2 * 3600), muted: false,
}

export const regelCpu: Regel = { ...regelDateisystem, id: 2, name: 'CPU hoch', metric: 'moni_host_cpu_usage_percent', func: 'value', warning: 75, critical: 90, key_labels: [] }

export const snapshotSau: Snapshot = {
  timestamp: '2026-11-11T10:00:00Z',
  host: {
    cpu: { usage_percent: 41, cores: 4 },
    load: { load1: 0.52, load5: 0.4, load15: 0.3 },
    memory: { total_bytes: 8 * 1024 ** 3, used_bytes: 3 * 1024 ** 3, usage_percent: 37.5 },
    uptime_seconds: 3 * 86400,
    temperature: { zones: [{ name: 'cpu', id: 'z0', celsius: 58 }] },
    filesystems: [
      { mountpoint: '/', device: '/dev/sda1', fstype: 'ext4', total_bytes: 100 * 1024 ** 3, used_bytes: 40 * 1024 ** 3, usage_percent: 40 },
      { mountpoint: '/var', device: '/dev/sdb1', fstype: 'xfs', total_bytes: 100 * 1024 ** 3, used_bytes: 81 * 1024 ** 3, usage_percent: 81 },
    ],
    network: [
      { interface: 'lo', rx_bytes: 9e12, tx_bytes: 9e12 },
      { interface: 'eth0', rx_bytes: 5e9, tx_bytes: 1e9 },
      { interface: 'wlan0', rx_bytes: 1e6, tx_bytes: 1e6 },
    ],
  },
  containers: [
    { id: 'abc123', name: 'photoprism', image: 'photoprism/photoprism:latest', status: 'running', health: 'healthy', cpu_percent: 3.5, memory_usage_bytes: 512 * 1024 ** 2, restart_count: 0, uptime_seconds: 7200 },
    { id: 'def456', name: 'redis', image: 'redis:7', status: 'exited', health: 'none', cpu_percent: null, memory_usage_bytes: null, restart_count: 2, uptime_seconds: null },
  ],
  docker: { reachable: true, container_count: 2 },
  warnings: [],
}

export const kanalWebhook: Kanal = {
  id: 5, name: 'ntfy', kind: 'webhook', config: {}, min_severity: 'warning', enabled: true, created_at: null, secret_set: true, url_hint: 'https://ntfy.sh/…',
}
export const kanalEmail: Kanal = {
  id: 6, name: 'E-Mail Bereitschaft', kind: 'email', min_severity: 'critical', enabled: true, created_at: null, secret_set: true, url_hint: null,
  config: { host: 'smtp.example.org', port: 587, security: 'starttls', username: 'moni', from_addr: 'moni@example.org', to: ['a@example.org', 'b@example.org'] },
}
