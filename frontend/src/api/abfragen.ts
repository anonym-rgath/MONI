import { MutationCache, QueryCache, QueryClient, useQuery } from '@tanstack/react-query'
import { ApiFehler, api } from './client'
import type { Alarm, Regel, Server, Snapshot } from './typen'

export const schluessel = {
  sitzung: ['sitzung'] as const,
  setup: ['setup'] as const,
  oidcAnbieter: ['oidc-anbieter'] as const,
  server: ['server'] as const,
  serverEinzeln: (id: number) => ['server', id] as const,
  snapshot: (id: number) => ['server', id, 'snapshot'] as const,
  alarme: ['alarme'] as const,
  regeln: ['regeln'] as const,
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
  useQuery({ queryKey: schluessel.serverEinzeln(id), queryFn: () => api.get<Server>(`/servers/${id}`) })
export const useSnapshot = (id: number) =>
  useQuery({ queryKey: schluessel.snapshot(id), queryFn: () => api.get<Snapshot>(`/servers/${id}/snapshot`), refetchInterval: LIVE_MS })
export const useAlarme = () => useQuery({ queryKey: schluessel.alarme, queryFn: () => api.get<Alarm[]>('/alerts'), refetchInterval: LIVE_MS })
// Schwellen für die Alarmzeilen der Übersicht; ändern sich selten, daher ohne Takt.
export const useRegeln = () => useQuery({ queryKey: schluessel.regeln, queryFn: () => api.get<Regel[]>('/alert-rules'), staleTime: 60_000 })
