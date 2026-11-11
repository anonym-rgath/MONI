import { Link } from 'react-router'
import type { Server } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { Balken } from '../../ui/Balken'
import { Statusmarke, type Ton } from '../../ui/Statusmarke'
import { lokal, sekundenSeit, vor } from '../../ui/Zeit'
import type { Zustand } from './dringlichkeit'

const MARKE: Record<Zustand, { text: TextSchluessel; ton: Ton; rand: string }> = {
  fehler: { text: 'status.nichtErreichbar', ton: 'crit', rand: 'border-t-crit' },
  kritisch: { text: 'status.kritisch', ton: 'crit', rand: 'border-t-crit' },
  warnung: { text: 'status.warnung', ton: 'warn', rand: 'border-t-warn' },
  ok: { text: 'status.ok', ton: 'ok', rand: 'border-t-ok' },
  unbekannt: { text: 'status.unbekannt', ton: 'neutral', rand: 'border-t-muted' },
  stumm: { text: 'status.stumm', ton: 'neutral', rand: 'border-t-muted' },
  deaktiviert: { text: 'status.deaktiviert', ton: 'neutral', rand: 'border-t-muted' },
}
// D12 Paket 5: Standardregeln für CPU, RAM, Speicher und Temperatur. Temperaturbalken ist bei 90 °C voll.
const PROZENT = { warn: 75, crit: 85, max: 100 }
const TEMPERATUR = { warn: 65, crit: 75, max: 90 }

const art = (s: Server): TextSchluessel => (s.is_local ? 'karte.managerLokal' : s.security === 'mtls' ? 'karte.agentMtls' : 'karte.agentToken')

export function Serverkarte({ server, zustand, jetzt }: { server: Server; zustand: Zustand; jetzt: number }) {
  const m = MARKE[zustand]
  const sm = server.summary
  const fs = sm?.filesystem_max
  const laeuft = sm?.containers_running != null && sm.containers_total != null
  const rechts =
    zustand === 'deaktiviert' ? t('karte.aus')
    : zustand === 'stumm' && server.muted_until ? t('karte.stummBis', { zeit: lokal(Date.parse(server.muted_until), jetzt) })
    : server.last_seen ? t('karte.zuletzt', { vor: vor(sekundenSeit(Date.parse(server.last_seen), jetzt)) })
    : t('karte.nieGesehen')
  return (
    <Link to={`/server/${server.id}`} className={`flex flex-1 flex-col gap-3.5 rounded-[14px] border border-t-4 border-border bg-surface p-4.5 ${m.rand}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="m-0 text-[1.0625rem] font-semibold">{server.name}</h3>
          <div className="mt-0.5 text-[0.8125rem] text-muted">{t(art(server))}</div>
        </div>
        <Statusmarke ton={m.ton}>{t(m.text)}</Statusmarke>
      </div>
      <dl className="m-0 flex flex-col gap-2.5">
        <Balken name={t('karte.cpu')} wert={sm?.cpu_percent} einheit="%" grenzen={PROZENT} />
        <Balken name={t('karte.ram')} wert={sm?.memory_percent} einheit="%" grenzen={PROZENT} />
        <Balken name={fs?.mountpoint ? t('karte.speicherMit', { ort: fs.mountpoint }) : t('karte.speicher')} wert={fs?.percent} einheit="%" grenzen={PROZENT} />
        <Balken name={t('karte.temperatur')} wert={sm?.temperature_max_c} einheit="°C" grenzen={TEMPERATUR} />
      </dl>
      <div className="mt-auto flex flex-wrap justify-between gap-x-3 gap-y-1.5 border-t border-border pt-3 text-[0.8125rem] text-muted">
        <span>{laeuft ? t('karte.container', { laufen: sm.containers_running!, gesamt: sm.containers_total! }) : t('karte.keineDaten')}</span>
        <span>{rechts}</span>
      </div>
    </Link>
  )
}
