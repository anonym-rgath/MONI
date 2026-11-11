import type { Snapshot } from '../../api/typen'
import { t } from '../../texte/de'
import { formatWert } from '../../texte/metriken'

export const istZahl = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

interface Kachel { label: string; wert: string | null; unter?: string }

function kacheln(s: Snapshot | undefined): Kachel[] {
  const h = s?.host
  const cpu = h?.cpu?.usage_percent
  const last = h?.load?.load1
  const m = h?.memory
  const ram = m?.usage_percent ?? (istZahl(m?.used_bytes) && istZahl(m?.total_bytes) && m.total_bytes > 0 ? (m.used_bytes / m.total_bytes) * 100 : undefined)
  const heissest = (h?.temperature?.zones ?? []).filter((z) => istZahl(z.celsius)).sort((a, b) => b.celsius! - a.celsius!)[0]
  const voll = (h?.filesystems ?? []).filter((f) => istZahl(f.usage_percent)).sort((a, b) => b.usage_percent! - a.usage_percent!)[0]
  return [
    {
      label: t('server.cpu'),
      wert: istZahl(cpu) ? formatWert('moni_host_cpu_usage_percent', cpu) : null,
      unter: istZahl(h?.cpu?.cores) && istZahl(last) ? t('server.cpuUnter', { kerne: h.cpu.cores, last: ZAHL.format(last) }) : undefined,
    },
    {
      label: t('server.ram'),
      wert: istZahl(ram) ? formatWert('moni_host_memory_usage_percent', ram) : null,
      unter: istZahl(m?.used_bytes) && istZahl(m?.total_bytes) ? t('server.ramUnter', { genutzt: formatWert('moni_host_memory_used_bytes', m.used_bytes), gesamt: formatWert('moni_host_memory_total_bytes', m.total_bytes) }) : undefined,
    },
    { label: t('server.temperatur'), wert: heissest ? formatWert('moni_host_temperature_celsius', heissest.celsius) : null, unter: heissest?.name ? t('server.temperaturUnter', { zone: heissest.name }) : undefined },
    { label: t('server.speicher'), wert: voll ? formatWert('moni_filesystem_usage_percent', voll.usage_percent) : null, unter: voll?.mountpoint },
    { label: t('server.laufzeit'), wert: istZahl(h?.uptime_seconds) ? formatWert('moni_host_uptime_seconds', h.uptime_seconds) : null },
  ]
}

// Fehlende Werte heißen „nicht verfügbar“ (nie 0 oder leer).
export function Kennzahlen({ snapshot }: { snapshot: Snapshot | undefined }) {
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(min(100%,170px),1fr))] gap-3">
      {kacheln(snapshot).map((k) => (
        <div key={k.label} className="flex flex-col gap-0.5 rounded-xl border border-border bg-surface px-4 py-3.5">
          <dt className="text-[0.8125rem] text-muted">{k.label}</dt>
          <dd className={`m-0 text-[1.375rem] font-medium ${k.wert ? 'num' : 'text-muted'}`}>
            {k.wert ?? t('server.nv')}
            {k.unter && <span className="block font-sans text-[0.8125rem] font-normal text-muted">{k.unter}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}
