import { useQueries } from '@tanstack/react-query'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useRegeln, verlaufOptionen } from '../../api/abfragen'
import { ApiFehler } from '../../api/client'
import type { Regel } from '../../api/typen'
import { t } from '../../texte/de'
import { labelText } from '../../texte/metriken'
import { Hinweis } from '../../ui/Hinweis'
import { type Linie, mittel, punkteDerSerie, rateDerSerie, spitze, zusammenfuehren } from './verlaufsdaten'

export interface Reihe {
  metric: string
  label?: string // "key:value" wie in der API
  name: string
  zaehler?: boolean // kumulativ: im Diagramm als Rate je Sekunde
  format: (wert: number) => string
}

// Zweite und weitere Reihen sind gestrichelt (D11); die Farben stammen aus den Tokens.
const FARBEN = ['var(--accent)', 'var(--muted)', 'var(--warn)', 'var(--ok)']
const HOEHE = 220

// Schwelle aus der aktiven Regel (nur func "value", obere Schwelle), die für diesen Server und diese Reihe gilt:
// label_filter muss zu den Labels der Reihe passen. Server-Regel vor globaler; Warnung vor kritisch.
function schwelle(regeln: Regel[] | undefined, metric: string, serverId: number, labels: Record<string, string>): { wert: number; art: 'server.schwelleWarn' | 'server.schwelleKrit' } | null {
  const passend = (regeln ?? []).filter((r) => r.enabled && r.func === 'value' && r.metric === metric && (r.server_id === null || r.server_id === serverId)
    && (r.op === '>' || r.op === '>=') && Object.entries(r.label_filter).every(([k, v]) => labels[k] === v))
  const r = passend.find((x) => x.server_id === serverId) ?? passend[0]
  if (!r) return null
  if (r.warning !== null) return { wert: r.warning, art: 'server.schwelleWarn' }
  return r.critical !== null ? { wert: r.critical, art: 'server.schwelleKrit' } : null
}

const parseLabel = (l: string | undefined): Record<string, string> => { const [k, ...v] = (l ?? '').split(':'); return k ? { [k]: v.join(':') } : {} }

export function Diagramm({ serverId, titel, reihen, bereichMs }: { serverId: number; titel: string; reihen: Reihe[]; bereichMs: number }) {
  const regeln = useRegeln()
  const abfragen = useQueries({ queries: reihen.map((r) => verlaufOptionen(serverId, r.metric, r.label, bereichMs)) })
  const fehler = abfragen.find((q) => q.error)?.error
  const laedt = abfragen.some((q) => q.isPending) && !fehler

  const linien: (Linie & { format: Reihe['format']; labels: Record<string, string> })[] = abfragen.flatMap((q, i) => {
    const r = reihen[i]!
    const serien = q.data?.series ?? []
    return serien.map((s) => {
      const punkte = punkteDerSerie(s.points)
      return { name: serien.length > 1 ? (s.labels.zone ?? labelText(s.labels)) : r.name, punkte: r.zaehler ? rateDerSerie(punkte) : punkte, format: r.format, labels: { ...parseLabel(r.label), ...s.labels } }
    })
  }).filter((l) => l.punkte.length > 0)
  const regel = linien[0] ? schwelle(regeln.data, reihen[0]!.metric, serverId, linien[0].labels) : null
  const grenze = regel?.wert ?? null
  const fmt = reihen[0]!.format

  const teile = linien.map((l) => t(linien.length > 1 ? 'server.dsName' : 'server.ds', { name: l.name, avg: l.format(mittel(l.punkte)), max: l.format(spitze(l.punkte)) }))
  if (grenze !== null && linien.length > 0) {
    teile.push(t(Math.max(...linien.map((l) => spitze(l.punkte))) >= grenze ? 'server.schwelleUeber' : 'server.schwelleUnter', { art: t(regel!.art), wert: fmt(grenze) }))
  }
  const zusammenfassung = teile.join('; ')
  const lang = bereichMs > 86_400_000
  const zeit = new Intl.DateTimeFormat('de-DE', lang ? { dateStyle: 'short' } : { timeStyle: 'short' })

  return (
    <figure className="m-0 flex min-w-0 flex-col gap-3 rounded-[14px] border border-border bg-surface p-4">
      <figcaption className="flex flex-col gap-1">
        <h3 className="m-0 text-base font-semibold">{titel}</h3>
        {fehler && <Hinweis art="fehler">{fehler instanceof ApiFehler ? fehler.detail : t('fehler.allgemein')}</Hinweis>}
        {laedt && <p className="m-0 text-sm text-muted">{t('server.verlaufLaedt')}</p>}
        {!fehler && !laedt && <p className="num m-0 text-sm text-muted">{zusammenfassung || t('server.keineVerlaufsdaten')}</p>}
      </figcaption>
      {linien.length > 0 && (
        <div role="img" aria-label={`${titel}. ${zusammenfassung}`}>
          <ResponsiveContainer width="100%" height={HOEHE} initialDimension={{ width: 600, height: HOEHE }}>
            <LineChart data={zusammenfuehren(linien)} accessibilityLayer={false} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="ts" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(v: number) => zeit.format(v)} stroke="var(--muted)" tick={{ fill: 'var(--muted)', fontSize: 12 }} />
              <YAxis width={64} domain={[0, (m: number) => Math.max(m, grenze ?? 0)]} tickFormatter={(v: number) => fmt(v)} stroke="var(--muted)" tick={{ fill: 'var(--muted)', fontSize: 12 }} />
              <Tooltip formatter={(v) => fmt(Number(v))} labelFormatter={(v) => new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'medium' }).format(Number(v))} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }} />
              {linien.length > 1 && <Legend />}
              {grenze !== null && (
                <ReferenceLine y={grenze} stroke="var(--warn)" strokeDasharray="2 4" strokeWidth={2} label={{ value: t('server.schwelleLinie', { art: t(regel!.art), wert: fmt(grenze) }), position: 'insideTopRight', fill: 'var(--warn)', fontSize: 12 }} />
              )}
              {linien.map((l, i) => (
                <Line key={i} dataKey={`l${i}`} name={l.name} type="monotone" dot={false} connectNulls isAnimationActive={false}
                  stroke={FARBEN[i % FARBEN.length]} strokeWidth={2} strokeDasharray={i > 0 ? '6 4' : undefined} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </figure>
  )
}
