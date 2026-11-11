import { Link } from 'react-router'
import { useAlarme, useRegeln, useServer } from '../../api/abfragen'
import type { Regel } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { formatWert, istZustand, labelText } from '../../texte/metriken'
import { Hinweis } from '../../ui/Hinweis'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { useTitel } from '../../ui/useTitel'
import { dauer, sekundenSeit, useJetzt } from '../../ui/Zeit'
import { stummBis } from '../uebersicht/Aufmerksamkeit'

const ART: Record<Regel['op'], TextSchluessel> = { '>': 'alarme.schwelleUeber', '>=': 'alarme.schwelleAb', '<': 'alarme.schwelleUnter', '<=': 'alarme.schwelleBis' }
const FARBE = { critical: 'border-l-crit text-crit', warning: 'border-l-warn text-warn' } as const

export function Offen() {
  useTitel(t('rahmen.alarme'))
  const jetzt = useJetzt()
  const alarme = useAlarme()
  const regeln = useRegeln().data ?? []
  const server = useServer().data ?? []
  // Wartende Alarme (noch unter der Mindestdauer) zählen wie in der Übersicht nicht als offen.
  const offen = (alarme.data ?? []).filter((a) => a.state === 'firing').sort((a, b) => Number(b.severity === 'critical') - Number(a.severity === 'critical'))

  return (
    <section aria-labelledby="offen-h" className="flex flex-col gap-3">
      {alarme.isError && <Hinweis art="fehler">{t('alarme.fehler')}</Hinweis>}
      {alarme.isPending && <Ladezustand text={t('alarme.laden')} />}
      {alarme.data && (
        <>
          <h2 id="offen-h" className="m-0 text-[1.0625rem] font-semibold">{offen.length === 1 ? t('alarme.offenEins') : t('alarme.offenMehr', { n: offen.length })}</h2>
          {offen.length === 0 && <Leerzustand text={t('alarme.keine')} />}
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {offen.map((a) => {
              const regel = regeln.find((r) => r.id === a.rule.id)
              const schwelle = regel?.[a.severity]
              const metric = regel?.metric ?? ''
              // Ja/Nein-Metriken nur als Zustand (P6-R8), alle anderen als Wert mit Schwelle.
              const wert = istZustand(metric) || !regel || schwelle == null
                ? formatWert(metric, a.value)
                : t('alarme.wertSchwelle', { wert: formatWert(metric, a.value), art: t(ART[regel.op]), schwelle: formatWert(metric, schwelle) })
              const srv = server.find((s) => s.id === a.server.id)
              const stumm = a.muted ? ((srv && stummBis(srv, jetzt)) || t('status.stumm').toLowerCase()) : null
              return (
                <li key={`${a.rule.id}-${a.server.id}-${JSON.stringify(a.labels)}`} className={`flex flex-wrap items-center gap-x-5 gap-y-2 rounded-[14px] border border-border border-l-4 bg-surface px-4.5 py-4 ${FARBE[a.severity]}`}>
                  <span className="min-w-19 font-semibold">{t(a.severity === 'critical' ? 'alarme.kritisch' : 'alarme.warnung')}</span>
                  <div className="flex min-w-60 flex-1 flex-col gap-0.5 text-text">
                    <span className="font-semibold">
                      {a.rule.name}{t('alarme.trenner')}
                      <Link to={`/server/${a.server.id}`} className="underline underline-offset-3">{a.server.name}</Link>
                    </span>
                    {Object.keys(a.labels).length > 0 && <span className="text-sm text-muted">{labelText(a.labels)}</span>}
                  </div>
                  <span className="num text-text">{wert}</span>
                  {a.since && <span className="min-w-27 text-muted">{t('uebersicht.seit', { dauer: dauer(sekundenSeit(Date.parse(a.since), jetzt)) })}</span>}
                  {stumm && <span className="rounded-full bg-surface-2 px-2.5 py-0.5 text-[0.8125rem] text-muted">{stumm}</span>}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
