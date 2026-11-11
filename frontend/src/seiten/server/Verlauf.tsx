import type { Snapshot } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { formatWert } from '../../texte/metriken'
import { Zeitraum } from '../../ui/Zeitraum'
import { Diagramm } from './Diagramm'
import { istZahl } from './Kennzahlen'

export const BEREICHE = [
  { id: '1h', ms: 3_600_000 },
  { id: '24h', ms: 86_400_000 },
  { id: '7d', ms: 7 * 86_400_000 },
  { id: '30d', ms: 30 * 86_400_000 },
  { id: '1y', ms: 365 * 86_400_000 },
] as const
export type BereichId = (typeof BEREICHE)[number]['id']

// Das Haupt-Interface ist das mit den meisten empfangenen Bytes; lo zählt nicht.
export function hauptInterface(s: Snapshot | undefined): string | null {
  const netz = (s?.host?.network ?? []).filter((n) => n.interface && n.interface !== 'lo' && istZahl(n.rx_bytes))
  return netz.sort((a, b) => b.rx_bytes! - a.rx_bytes!)[0]?.interface ?? null
}

const je = (metric: string) => (w: number) => `${formatWert(metric, w)}/s`
const fmt = (metric: string) => (w: number) => formatWert(metric, w)

export function Verlauf({ serverId, snapshot, bereich, onBereich }: { serverId: number; snapshot: Snapshot | undefined; bereich: BereichId; onBereich: (b: BereichId) => void }) {
  const ms = BEREICHE.find((b) => b.id === bereich)!.ms
  const netz = hauptInterface(snapshot)
  return (
    <section aria-labelledby="verlauf-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="verlauf-h" className="m-0 text-[1.0625rem] font-semibold">{t('server.verlauf', { zeitraum: t(`server.l${bereich}`) })}</h2>
        <Zeitraum label={t('server.zeitraum')} wert={bereich} onWahl={onBereich}
          optionen={BEREICHE.map((b) => ({ id: b.id, text: t(`server.z${b.id}` as TextSchluessel) }))} />
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,420px),1fr))] gap-4">
        <Diagramm serverId={serverId} bereichMs={ms} titel={t('server.diagrammCpu')} reihen={[{ metric: 'moni_host_cpu_usage_percent', name: t('server.cpu'), format: fmt('moni_host_cpu_usage_percent') }]} />
        <Diagramm serverId={serverId} bereichMs={ms} titel={t('server.diagrammRam')} reihen={[{ metric: 'moni_host_memory_usage_percent', name: t('server.ram'), format: fmt('moni_host_memory_usage_percent') }]} />
        {netz ? (
          <Diagramm serverId={serverId} bereichMs={ms} titel={t('server.diagrammNetz', { name: netz })} reihen={[
            { metric: 'moni_network_receive_bytes', label: `interface:${netz}`, name: t('server.empfangen'), zaehler: true, format: je('moni_network_receive_bytes') },
            { metric: 'moni_network_transmit_bytes', label: `interface:${netz}`, name: t('server.gesendet'), zaehler: true, format: je('moni_network_transmit_bytes') },
          ]} />
        ) : (
          <figure className="m-0 flex flex-col gap-1 rounded-[14px] border border-border bg-surface p-4">
            <figcaption><h3 className="m-0 text-base font-semibold">{t('server.netzwerk')}</h3><p className="m-0 text-sm text-muted">{t('server.keineVerlaufsdaten')}</p></figcaption>
          </figure>
        )}
        <Diagramm serverId={serverId} bereichMs={ms} titel={t('server.diagrammTemp')} reihen={[{ metric: 'moni_host_temperature_celsius', name: t('server.temperatur'), format: fmt('moni_host_temperature_celsius') }]} />
      </div>
    </section>
  )
}
