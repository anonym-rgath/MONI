import { Link } from 'react-router'
import type { Alarm, Regel, Server } from '../../api/typen'
import { t } from '../../texte/de'
import { formatWert, istZustand, labelText, metrikName } from '../../texte/metriken'
import { dauer, lokal, sekundenSeit } from '../../ui/Zeit'
import { offeneAlarme, zustand } from './dringlichkeit'

interface Zeile { server: Server; schwere: 'kritisch' | 'warnung'; text: string; seit: number | null }

export const stummBis = (s: Server, jetzt: number) => (s.muted_until && Date.parse(s.muted_until) > jetzt ? t('karte.stummBis', { zeit: lokal(Date.parse(s.muted_until), jetzt) }) : null)

function zeile(server: Server, alarme: Alarm[], regeln: Regel[], jetzt: number): Zeile {
  const z = zustand(server, alarme, jetzt)
  if (z === 'fehler') {
    const text = server.status === 'error'
      ? (server.last_error ? t('uebersicht.fehlerMit', { fehler: server.last_error }) : t('uebersicht.fehler'))
      : (server.failures === 1 ? t('uebersicht.nichtErreichbarAbruf') : server.failures > 1 ? t('uebersicht.nichtErreichbarAbrufe', { n: server.failures }) : t('uebersicht.nichtErreichbar'))
    return { server, schwere: 'kritisch', text, seit: null }
  }
  const offen = offeneAlarme(server, alarme).sort((a, b) => Number(b.severity === 'critical') - Number(a.severity === 'critical'))
  const a = offen[0]!
  const regel = regeln.find((r) => r.id === a.rule.id)
  const metric = regel?.metric ?? ''
  const ort = labelText(a.labels)
  const schwelle = regel?.[a.severity]
  // Ja/Nein-Metriken als Zustand ("Container photoprism: gestoppt"), nie als Zahl mit Schwelle.
  const text = istZustand(metric)
    ? t('uebersicht.zustandZeile', { wer: ort || metrikName(metric), zustand: formatWert(metric, a.value) }) + t('uebersicht.regelZusatz', { regel: a.rule.name })
    : t('uebersicht.alarmZeile', {
        regel: a.rule.name,
        ort: ort ? t('uebersicht.alarmOrt', { ort }) : '',
        wert: formatWert(metric, a.value),
        schwelle: schwelle == null ? '' : t('uebersicht.alarmSchwelle', { schwelle: formatWert(metric, schwelle) }),
      })
  return { server, schwere: z === 'kritisch' ? 'kritisch' : 'warnung', text: offen.length > 1 ? `${text} · ${t('uebersicht.weitere', { n: offen.length - 1 })}` : text, seit: a.since ? Date.parse(a.since) : null }
}

const BOX = 'rounded-[14px] border border-border bg-surface px-5 py-4.5'
const FARBE = { kritisch: 'text-crit', warnung: 'text-warn' } as const

// probleme und uebrige sind schon nach Dringlichkeit sortiert.
export function Aufmerksamkeit({ probleme, uebrige, alarme, regeln, jetzt }: { probleme: Server[]; uebrige: Server[]; alarme: Alarm[]; regeln: Regel[]; jetzt: number }) {
  if (probleme.length === 0) return <p role="status" className={`m-0 ${BOX}`}>{t('uebersicht.alleNormal')}</p>
  const stumm = uebrige.filter((s) => zustand(s, alarme, jetzt) === 'stumm').length
  return (
    <section aria-labelledby="att-h" className={`flex flex-col gap-3 ${BOX}`}>
      <h2 id="att-h" className="m-0 text-[1.0625rem] font-semibold">
        {probleme.length === 1 ? t('uebersicht.attentionEins') : t('uebersicht.attentionMehr', { n: probleme.length })}
      </h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {probleme.map((s) => {
          const z = zeile(s, alarme, regeln, jetzt)
          return (
            <li key={s.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className={`min-w-24 font-semibold ${FARBE[z.schwere]}`}>{t(z.schwere === 'kritisch' ? 'uebersicht.schwereKritisch' : 'uebersicht.schwereWarnung')}</span>
              <Link to={`/server/${s.id}`} className="font-semibold underline underline-offset-3">{s.name}</Link>
              <span>{z.text}</span>
              {stummBis(s, jetzt) && <span className="text-muted">{stummBis(s, jetzt)}</span>}
              {z.seit != null && <span className="text-muted">{t('uebersicht.seit', { dauer: dauer(sekundenSeit(z.seit, jetzt)) })}</span>}
            </li>
          )
        })}
      </ul>
      <p className="m-0 text-muted">
        {t('uebersicht.uebrige', { n: uebrige.length, m: stumm })}{' '}
        <Link to="/alarme" className="text-accent underline underline-offset-3">{t('uebersicht.alleAlarme')}</Link>
      </p>
    </section>
  )
}
