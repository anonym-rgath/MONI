import { MutationCache, QueryCache, QueryClient, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ApiFehler, api } from './client'
import type { AgentToken, Alarm, AuditEintrag, Benutzer, Kanal, OidcKonfig, Regel, Seite, Server, Snapshot, Token, Verlauf, Verlaufseintrag } from './typen'

export const schluessel = {
  sitzung: ['sitzung'] as const,
  setup: ['setup'] as const,
  oidcAnbieter: ['oidc-anbieter'] as const,
  oidcKonfig: ['oidc-konfig'] as const, // Verwaltung; oidcAnbieter gehört der Anmeldeseite (andere Antwort)
  benutzer: ['benutzer'] as const,
  tokens: ['tokens'] as const,
  audit: ['audit'] as const,
  agentTokens: (id: number) => ['agent-tokens', id] as const,
  server: ['server'] as const,
  serverEinzeln: (id: number) => ['server', id] as const,
  snapshot: (id: number) => ['server', id, 'snapshot'] as const,
  alarme: ['alarme'] as const,
  regeln: ['regeln'] as const,
  kanaele: ['kanaele'] as const,
  erinnerung: ['erinnerung'] as const,
  alarmVerlauf: (serverId: number | null) => ['alarme', 'verlauf', serverId] as const, // unter ['alarme']: Regeländerungen laden ihn mit neu
  verlauf: (id: number, metric: string, label: string, bereichMs: number) => ['server', id, 'verlauf', metric, label, bereichMs] as const,
}

// D7: Live-Daten alle 15 s, nie bei verborgenem Tab; nur Netzwerkfehler werden (höchstens 2-mal) wiederholt.
export const LIVE_MS = 15_000

export function erzeugeQueryClient(bei: (fehler: unknown) => void) {
  return new QueryClient({
    queryCache: new QueryCache({ onError: bei }),
    mutationCache: new MutationCache({ onError: bei }),
    defaultOptions: {
      queries: {
        retry: (n, e) => e instanceof ApiFehler && e.status === 0 && n < 2,
        refetchOnWindowFocus: true,
        refetchIntervalInBackground: false,
      },
    },
  })
}

export const useServer = () => useQuery({ queryKey: schluessel.server, queryFn: () => api.get<Server[]>('/servers'), refetchInterval: LIVE_MS })
export const useServerEinzeln = (id: number) =>
  useQuery({ queryKey: schluessel.serverEinzeln(id), queryFn: () => api.get<Server>(`/servers/${id}`), refetchInterval: LIVE_MS })
export const useSnapshot = (id: number) =>
  useQuery({ queryKey: schluessel.snapshot(id), queryFn: () => api.get<Snapshot>(`/servers/${id}/snapshot`), refetchInterval: LIVE_MS })
export const useAlarme = () => useQuery({ queryKey: schluessel.alarme, queryFn: () => api.get<Alarm[]>('/alerts'), refetchInterval: LIVE_MS })
// Schwellen für die Alarmzeilen der Übersicht; ändern sich selten, daher ohne Takt.
export const useRegeln = () => useQuery({ queryKey: schluessel.regeln, queryFn: () => api.get<Regel[]>('/alert-rules'), staleTime: 60_000 })

// D7: Verlauf alle 60 s. Das Fenster wird erst beim Abruf berechnet, damit es mitwandert und der Schlüssel stabil bleibt.
export const VERLAUF_MS = 60_000
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')
export const verlaufOptionen = (id: number, metric: string, label: string | undefined, bereichMs: number) => ({
  queryKey: schluessel.verlauf(id, metric, label ?? '', bereichMs),
  queryFn: () => {
    const bis = Date.now()
    return api.get<Verlauf>(`/servers/${id}/history`, { metric, from: iso(bis - bereichMs), to: iso(bis), resolution: 'auto', label: label ? [label] : undefined })
  },
  refetchInterval: VERLAUF_MS,
})

export const HISTORIE_SEITE = 50
// Blättern mit before=next_before; der Serverfilter steckt im Schlüssel, ein Wechsel beginnt also wieder bei der ersten Seite.
export const useAlarmVerlauf = (serverId: number | null) =>
  useInfiniteQuery({
    queryKey: schluessel.alarmVerlauf(serverId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => api.get<Seite<Verlaufseintrag>>('/alerts/history', { limit: HISTORIE_SEITE, before: pageParam, server_id: serverId ?? undefined }),
    getNextPageParam: (letzte) => letzte.next_before ?? undefined,
  })
// Nur Admins; die Kanäle-Seite ist geschützt, GET liefert nie Geheimnisse (nur secret_set und url_hint).
export const useKanaele = () => useQuery({ queryKey: schluessel.kanaele, queryFn: () => api.get<Kanal[]>('/alert-channels') })
export const useErinnerung = () => useQuery({ queryKey: schluessel.erinnerung, queryFn: () => api.get<{ remind_minutes: number }>('/alert-settings') })

// Verwaltung (nur Admins; die Routen sind geschützt). Keine der Listen enthält Geheimnisse.
export const useBenutzer = () => useQuery({ queryKey: schluessel.benutzer, queryFn: () => api.get<Benutzer[]>('/users') })
export const useTokens = () => useQuery({ queryKey: schluessel.tokens, queryFn: () => api.get<Token[]>('/tokens') })
export const useAgentTokens = (id: number) => useQuery({ queryKey: schluessel.agentTokens(id), queryFn: () => api.get<AgentToken[]>(`/servers/${id}/agent-tokens`) })
export const useOidcKonfig = () => useQuery({ queryKey: schluessel.oidcKonfig, queryFn: () => api.get<OidcKonfig>('/auth/oidc/providers') })
export const AUDIT_SEITE = 50
export const useAudit = () =>
  useInfiniteQuery({
    queryKey: schluessel.audit,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => api.get<Seite<AuditEintrag>>('/audit', { limit: AUDIT_SEITE, before: pageParam }),
    getNextPageParam: (letzte) => letzte.next_before ?? undefined,
  })
