import { Fragment, useId, useState } from 'react'
import type { Snapshot } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { formatWert } from '../../texte/metriken'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { dauer } from '../../ui/Zeit'
import { Diagramm } from './Diagramm'
import { istZahl } from './Kennzahlen'

type Dock = NonNullable<Snapshot['containers']>[number]
const nv = t('server.nv')
const ZUSTAND: Record<string, TextSchluessel> = { running: 'server.zLaeuft', exited: 'server.zBeendet', paused: 'server.zPausiert', restarting: 'server.zNeustart', created: 'server.zErstellt' }
const HEALTH: Record<string, TextSchluessel> = { healthy: 'server.hGesund', unhealthy: 'server.hKrank', starting: 'server.hStartet', none: 'server.hKeiner', unknown: 'server.hUnbekannt' }
const text = (w: Record<string, TextSchluessel>, k: string | null | undefined, ersatz: TextSchluessel | null) => (k && w[k] ? t(w[k]) : (k ?? (ersatz ? t(ersatz) : nv)))
const metrik = (metric: string, w: number | null | undefined) => (istZahl(w) ? formatWert(metric, w) : nv)

function Verlaufszeile({ id, c, serverId, bereichMs }: { id: string; c: Dock; serverId: number; bereichMs: number }) {
  const name = c.name ?? ''
  const label = `name:${name}`
  return (
    <tr id={id} className="border-b border-border bg-bg last:border-0">
      <Zelle colSpan={9}>
        <dl className="m-0 mb-3 flex flex-wrap gap-x-8 gap-y-1 text-sm">
          {[
            [t('server.cImage'), c.image],
            [t('server.cId'), c.id],
            [t('server.cLaeuftSeit'), istZahl(c.uptime_seconds) ? dauer(c.uptime_seconds) : null],
            [t('server.cSpeicherLimit'), istZahl(c.memory_limit_bytes) ? formatWert('moni_container_memory_usage_bytes', c.memory_limit_bytes) : null],
          ].filter((x): x is [string, string] => Boolean(x[1])).map(([k, v]) => (
            <div key={k} className="flex gap-2"><dt className="text-muted">{k}</dt><dd className="num m-0">{v}</dd></div>
          ))}
        </dl>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,320px),1fr))] gap-4">
          <Diagramm serverId={serverId} bereichMs={bereichMs} titel={t('server.diagrammContainerCpu', { name })} reihen={[{ metric: 'moni_container_cpu_usage_percent', label, name: t('server.cCpu'), format: (w) => formatWert('moni_container_cpu_usage_percent', w) }]} />
          <Diagramm serverId={serverId} bereichMs={bereichMs} titel={t('server.diagrammContainerRam', { name })} reihen={[{ metric: 'moni_container_memory_usage_bytes', label, name: t('server.cRam'), format: (w) => formatWert('moni_container_memory_usage_bytes', w) }]} />
        </div>
      </Zelle>
    </tr>
  )
}

export function Container({ serverId, snapshot, bereichMs }: { serverId: number; snapshot: Snapshot | undefined; bereichMs: number }) {
  const basis = useId()
  const [offen, setOffen] = useState<ReadonlySet<string>>(new Set())
  const liste = snapshot?.containers ?? []
  if (liste.length === 0) {
    return <Leerzustand text={snapshot?.docker?.reachable ? t('server.keineContainer') : t('server.keineDocker')} />
  }
  const umschalten = (name: string) => setOffen((o) => { const n = new Set(o); if (!n.delete(name)) n.add(name); return n })
  return (
    <Tabelle id="container-h" titel={t('server.container')} kopf={[t('server.cVerlauf'), t('server.cName'), t('server.cImage'), t('server.cZustand'), t('server.cHealth'), t('server.cCpu'), t('server.cRam'), t('server.cNeustarts'), t('server.cLaeuftSeit')]}>
      {liste.map((c, i) => {
        const name = c.name ?? `${i}`
        const zeile = `${basis}-${i}`
        const auf = offen.has(name)
        return (
          <Fragment key={name}>
            <tr className="border-b border-border last:border-0">
              <Zelle>
                <button type="button" aria-expanded={auf} aria-controls={zeile} aria-label={t('server.verlaufVon', { name })} onClick={() => umschalten(name)}
                  className="min-h-9 min-w-9 cursor-pointer rounded-lg border border-control text-sm"><span aria-hidden="true">{auf ? '−' : '+'}</span></button>
              </Zelle>
              <Zeilenkopf>{name}</Zeilenkopf>
              <Zelle>{c.image ?? nv}</Zelle>
              <Zelle>{text(ZUSTAND, c.status, null)}</Zelle>
              <Zelle>{text(HEALTH, c.health, 'server.hKeiner')}</Zelle>
              <Zelle num>{metrik('moni_container_cpu_usage_percent', c.cpu_percent)}</Zelle>
              <Zelle num>{metrik('moni_container_memory_usage_bytes', c.memory_usage_bytes)}</Zelle>
              <Zelle num>{metrik('moni_container_restarts', c.restart_count)}</Zelle>
              <Zelle num>{istZahl(c.uptime_seconds) ? dauer(c.uptime_seconds) : nv}</Zelle>
            </tr>
            {auf && <Verlaufszeile id={zeile} c={c} bereichMs={bereichMs} serverId={serverId} />}
          </Fragment>
        )
      })}
    </Tabelle>
  )
}
