import type { Alarm, Server } from '../../api/typen'

export type Zustand = 'fehler' | 'kritisch' | 'warnung' | 'ok' | 'unbekannt' | 'stumm' | 'deaktiviert'
const RANG: Record<Zustand, number> = { fehler: 0, kritisch: 1, warnung: 2, ok: 3, unbekannt: 3, stumm: 4, deaktiviert: 5 }

export const offeneAlarme = (server: Server, alarme: Alarm[]) => alarme.filter((a) => a.state === 'firing' && a.server.id === server.id)

export function zustand(server: Server, alarme: Alarm[], jetzt = Date.now()): Zustand {
  if (!server.enabled) return 'deaktiviert'
  if (server.status === 'unreachable' || server.status === 'error') return 'fehler'
  const offen = offeneAlarme(server, alarme)
  if (offen.some((a) => a.severity === 'critical')) return 'kritisch'
  if (offen.length > 0) return 'warnung'
  if (server.muted_until && Date.parse(server.muted_until) > jetzt) return 'stumm'
  return server.status === 'unknown' ? 'unbekannt' : 'ok'
}

// Kleinere Zahl = dringender: Fehler/nicht erreichbar < kritischer Alarm < Warnung < OK < stumm < deaktiviert.
export const dringlichkeit = (server: Server, alarme: Alarm[], jetzt = Date.now()) => RANG[zustand(server, alarme, jetzt)]

export const sortiere = (servers: Server[], alarme: Alarm[], jetzt = Date.now()) =>
  [...servers].sort((a, b) => dringlichkeit(a, alarme, jetzt) - dringlichkeit(b, alarme, jetzt) || a.name.localeCompare(b.name, 'de'))

// Ein Server braucht Aufmerksamkeit bei Fehler/nicht erreichbar oder offenem firing-Alarm (auch wenn stumm).
export const brauchtAufmerksamkeit = (server: Server, alarme: Alarm[]) =>
  server.enabled && (server.status === 'unreachable' || server.status === 'error' || offeneAlarme(server, alarme).length > 0)
