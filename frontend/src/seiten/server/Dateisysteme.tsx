import type { Snapshot } from '../../api/typen'
import { t } from '../../texte/de'
import { formatWert } from '../../texte/metriken'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { istZahl } from './Kennzahlen'

const ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const nv = t('server.nv')

// Balken ist nur Beiwerk, die Zahl (bei Warnung oder Kritisch mit Wort) trägt die Aussage (D11).
function Auslastung({ prozent }: { prozent: number | null | undefined }) {
  if (!istZahl(prozent)) return <span className="text-muted">{nv}</span>
  const stufe = prozent >= 85 ? 'crit' : prozent >= 75 ? 'warn' : 'ok'
  const wort = stufe === 'crit' ? ` ${t('karte.wortKritisch')}` : stufe === 'warn' ? ` ${t('karte.wortWarnung')}` : ''
  return (
    <span className="flex items-center gap-2.5">
      <span aria-hidden="true" className="h-1.5 w-24 overflow-hidden rounded-[3px] bg-track">
        <span className={`block h-full ${{ ok: 'bg-ok', warn: 'bg-warn', crit: 'bg-crit' }[stufe]}`} style={{ width: `${Math.min(100, Math.max(0, prozent))}%` }} />
      </span>
      <span className={{ ok: '', warn: 'text-warn', crit: 'text-crit' }[stufe]}>{`${ZAHL.format(prozent)} %${wort}`}</span>
    </span>
  )
}

export function Dateisysteme({ snapshot }: { snapshot: Snapshot | undefined }) {
  const liste = snapshot?.host?.filesystems ?? []
  if (liste.length === 0) return <Leerzustand text={t('server.keineDateisysteme')} />
  return (
    <Tabelle id="dateisysteme-h" titel={t('server.dateisysteme')} kopf={[t('server.einhaengepunkt'), t('server.geraet'), t('server.typ'), t('server.belegt'), t('server.auslastung')]}>
      {liste.map((f, i) => (
        <tr key={`${f.mountpoint}-${i}`} className="border-b border-border last:border-0">
          <Zeilenkopf>{f.mountpoint ?? nv}</Zeilenkopf>
          <Zelle>{f.device ?? nv}</Zelle>
          <Zelle>{f.fstype ?? nv}</Zelle>
          <Zelle num>{istZahl(f.used_bytes) && istZahl(f.total_bytes) ? t('server.belegtVon', { genutzt: formatWert('moni_filesystem_used_bytes', f.used_bytes), gesamt: formatWert('moni_filesystem_size_bytes', f.total_bytes) }) : nv}</Zelle>
          <Zelle num><Auslastung prozent={f.usage_percent} /></Zelle>
        </tr>
      ))}
    </Tabelle>
  )
}
