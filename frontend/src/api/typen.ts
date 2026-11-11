// Handgeschrieben nach den Antworten der Manager-API (backend/moni/**). Unbekannte Felder sind optional.
export type Stage = 'none' | 'mfa' | 'enroll' | 'full'
export type ApiRolle = 'admin' | 'viewer'

export interface SitzungBenutzer {
  id: number
  email: string
  name: string
  role: ApiRolle
  kind: 'local' | 'oidc'
  totp_enabled: boolean
}
export interface Session {
  stage: Exclude<Stage, 'none'>
  csrf_token: string
  user: SitzungBenutzer
}

export interface ServerSummary {
  cpu_percent: number | null
  memory_percent: number | null
  temperature_max_c: number | null
  filesystem_max: { mountpoint: string; percent: number } | null
  containers_running: number | null
  containers_total: number | null
}
export interface Server {
  id: number
  name: string
  url: string
  is_local: boolean
  enabled: boolean
  status: 'unknown' | 'ok' | 'unreachable' | 'error'
  failures: number
  last_error: string | null
  last_seen: string | null
  header_names: string[]
  warnings: number
  agent_version: string | null
  security: string | null
  cert_not_after: string | null
  muted_until: string | null
  summary?: ServerSummary | null // Task 4
  agent_info?: Record<string, unknown> | null // nur im Einzelabruf
}

export interface Snapshot {
  timestamp: string
  host?: {
    cpu?: { usage_percent?: number | null }
    load?: { load1?: number | null; load5?: number | null; load15?: number | null }
    memory?: { total_bytes?: number | null; used_bytes?: number | null; usage_percent?: number | null; swap_used_bytes?: number | null }
    uptime_seconds?: number | null
    process_count?: number | null
    temperature?: { zones?: { name?: string; id?: string; celsius?: number | null }[] }
    filesystems?: { mountpoint?: string; device?: string; fstype?: string; total_bytes?: number | null; used_bytes?: number | null; usage_percent?: number | null }[]
    network?: { interface?: string; rx_bytes?: number | null; tx_bytes?: number | null }[]
  }
  containers?: {
    name?: string
    image?: string
    status?: string
    health?: string | null
    cpu_percent?: number | null
    memory_usage_bytes?: number | null
    restart_count?: number | null
  }[]
  docker?: { reachable?: boolean; container_count?: number }
  warnings?: unknown[]
}

export interface Serie { metric: string; labels: Record<string, string> }
// raw: [ts, wert]; sonst [ts, min, avg, max]
export type Verlaufspunkt = [string, ...(number | null)[]]
export interface Verlauf {
  resolution: 'raw' | '1m' | '1h'
  series: { labels: Record<string, string>; points: Verlaufspunkt[] }[]
}

export type Schweregrad = 'warning' | 'critical'
export interface Alarm {
  rule: { id: number; name: string }
  server: { id: number; name: string }
  labels: Record<string, string>
  state: 'pending' | 'firing'
  severity: Schweregrad
  value: number | null
  since: string | null
  muted: boolean
}
export interface Verlaufseintrag {
  id: number
  ts: string | null
  rule_id: number
  rule_name: string
  server_id: number
  server_name: string
  labels: Record<string, string>
  event: string
  severity: Schweregrad | null
  value: number | null
  threshold: number | null
  reason: string | null
  muted: boolean
}
export interface Seite<T> { entries: T[]; next_before: number | null }

export interface Regel {
  id: number
  name: string
  metric: string
  func: string
  window_seconds: number | null
  op: '>' | '>=' | '<' | '<='
  warning: number | null
  critical: number | null
  for_seconds: number
  key_labels: string[]
  label_filter: Record<string, string>
  require_prior_ok: boolean
  server_id: number | null
  enabled: boolean
  created_at: string | null
  updated_at: string | null
}
export interface Kanal {
  id: number
  name: string
  kind: 'webhook' | 'email' | string
  config: Record<string, unknown>
  min_severity: Schweregrad
  enabled: boolean
  created_at: string | null
  secret_set: boolean
  url_hint: string | null
}

export interface Benutzer {
  id: number
  email: string
  name: string
  role: ApiRolle
  kind: 'local' | 'oidc'
  totp_enabled: boolean
  disabled: boolean
  locked: boolean
  oidc_bound: boolean
  created_at: string
  last_login: string | null
}
export interface Token {
  id: string
  name: string
  scope: 'read' | 'admin'
  created_at: string
  expires_at: string | null
  token?: string // nur in der POST-Antwort (D5: nie in den Cache)
}
export interface AgentToken {
  id: string
  name: string | null
  scope: string | null
  created_at: string | null
  expires_at: string | null
  token?: string
}
export interface AuditEintrag {
  id: number
  ts: string
  actor: string | null
  action: string
  target: string | null
  ip: string | null
  details: Record<string, unknown> | null
}
export interface OidcAnbieter {
  id: string
  label: string
  issuer?: string
  client_id?: string
  client_secret_set?: boolean
}
