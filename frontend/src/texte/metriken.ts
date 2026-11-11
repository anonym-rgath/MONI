import { dauer } from '../ui/Zeit'

// Klartext zu den Metriken des Managers (backend/moni/metrics_model.py HELP plus moni_server_up); von Alarmen und Verlauf geteilt.
export type Einheit = 'prozent' | 'celsius' | 'bytes' | 'bytes_je_s' | 'anzahl' | 'dauer' | 'zustand'
interface Metrik { name: string; einheit: Einheit; schlecht?: string; gut?: string } // schlecht/gut: Zustandswort für Wert 0 bzw. 1

export const METRIKEN: Record<string, Metrik> = {
  moni_server_up: { name: 'Server', einheit: 'zustand', schlecht: 'nicht erreichbar', gut: 'erreichbar' },
  moni_host_cpu_usage_percent: { name: 'CPU-Auslastung', einheit: 'prozent' },
  moni_host_load1: { name: 'Last (1 Minute)', einheit: 'anzahl' },
  moni_host_load5: { name: 'Last (5 Minuten)', einheit: 'anzahl' },
  moni_host_load15: { name: 'Last (15 Minuten)', einheit: 'anzahl' },
  moni_host_memory_total_bytes: { name: 'Arbeitsspeicher gesamt', einheit: 'bytes' },
  moni_host_memory_used_bytes: { name: 'Arbeitsspeicher belegt', einheit: 'bytes' },
  moni_host_memory_usage_percent: { name: 'Arbeitsspeicher-Auslastung', einheit: 'prozent' },
  moni_host_swap_used_bytes: { name: 'Swap belegt', einheit: 'bytes' },
  moni_host_uptime_seconds: { name: 'Laufzeit', einheit: 'dauer' },
  moni_host_processes: { name: 'Prozesse', einheit: 'anzahl' },
  moni_host_temperature_celsius: { name: 'Temperatur', einheit: 'celsius' },
  moni_filesystem_size_bytes: { name: 'Dateisystem gesamt', einheit: 'bytes' },
  moni_filesystem_used_bytes: { name: 'Dateisystem belegt', einheit: 'bytes' },
  moni_filesystem_usage_percent: { name: 'Dateisystem-Auslastung', einheit: 'prozent' },
  moni_network_receive_bytes: { name: 'Netzwerk empfangen', einheit: 'bytes' },
  moni_network_transmit_bytes: { name: 'Netzwerk gesendet', einheit: 'bytes' },
  moni_container_up: { name: 'Container', einheit: 'zustand', schlecht: 'gestoppt', gut: 'läuft' },
  moni_container_cpu_usage_percent: { name: 'Container-CPU', einheit: 'prozent' },
  moni_container_memory_usage_bytes: { name: 'Container-Speicher', einheit: 'bytes' },
  moni_container_restarts: { name: 'Container-Neustarts', einheit: 'anzahl' },
  moni_container_healthy: { name: 'Container-Healthcheck', einheit: 'zustand', schlecht: 'Healthcheck schlägt fehl', gut: 'gesund' },
  moni_docker_up: { name: 'Docker-API', einheit: 'zustand', schlecht: 'nicht erreichbar', gut: 'erreichbar' },
  moni_snapshot_timestamp_seconds: { name: 'Zeitpunkt des Snapshots', einheit: 'anzahl' },
}

const ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const EINS = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const GROESSEN = ['KB', 'MB', 'GB', 'TB']

function bytes(wert: number): string {
  if (Math.abs(wert) < 1024) return `${ZAHL.format(wert)} B`
  let w = wert
  let i = -1
  do { w /= 1024; i++ } while (Math.abs(w) >= 1024 && i < GROESSEN.length - 1)
  return `${EINS.format(w)} ${GROESSEN[i]}`
}

export const metrikName = (metric: string) => METRIKEN[metric]?.name ?? metric
export const istZustand = (metric: string) => METRIKEN[metric]?.einheit === 'zustand'

export function formatWert(metric: string, wert: number | null | undefined): string {
  if (typeof wert !== 'number' || !Number.isFinite(wert)) return 'keine Daten'
  const m = METRIKEN[metric]
  switch (m?.einheit) {
    case 'prozent': return `${ZAHL.format(wert)} %`
    case 'celsius': return `${ZAHL.format(wert)} °C`
    case 'bytes': return bytes(wert)
    case 'bytes_je_s': return `${bytes(wert)}/s`
    case 'dauer': return dauer(wert)
    case 'zustand': return (wert === 0 ? m.schlecht : m.gut) ?? ZAHL.format(wert)
    default: return ZAHL.format(wert)
  }
}

const LABEL: Record<string, string> = { mountpoint: 'Einhängepunkt', name: 'Container', zone: 'Zone', interface: 'Schnittstelle' }
export const labelText = (labels: Record<string, string>) =>
  Object.entries(labels).map(([k, v]) => `${LABEL[k] ?? k} ${v}`).join(', ')
