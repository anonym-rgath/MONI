import { useState } from 'react'
import { useAlarmVerlauf, useRegeln, useServer } from '../../api/abfragen'
import type { Verlaufseintrag } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { formatWert, labelText } from '../../texte/metriken'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { useTitel } from '../../ui/useTitel'
import { lokal, useJetzt } from '../../ui/Zeit'
import { Auswahl } from './felder'

const EREIGNIS: Record<string, TextSchluessel> = {
  firing: 'alarme.ereignisFiring', resolved: 'alarme.ereignisResolved', changed: 'alarme.ereignisChanged', reminder: 'alarme.ereignisReminder',
}
const KOPF: TextSchluessel[] = ['alarme.spZeit', 'alarme.spEreignis', 'alarme.spSchwere', 'alarme.spRegel', 'alarme.spServer', 'alarme.spWert', 'alarme.spGrund']

export function Verlauf() {
  useTitel(t('seiten.alarmVerlauf'))
  const jetzt = useJetzt()
  const [filter, setFilter] = useState<number | null>(null)
  const verlauf = useAlarmVerlauf(filter)
  const server = useServer().data ?? []
  const regeln = useRegeln().data ?? []
  const eintraege: Verlaufseintrag[] = verlauf.data?.pages.flatMap((p) => p.entries) ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="max-w-72">
        <Auswahl label={t('alarme.serverFilter')} value={filter ?? ''} onChange={(e) => setFilter(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{t('alarme.alleServer')}</option>
          {server.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </Auswahl>
      </div>
      {verlauf.isError && <Hinweis art="fehler">{t('alarme.fehler')}</Hinweis>}
      {verlauf.isPending && <Ladezustand text={t('alarme.verlaufLaden')} />}
      {verlauf.data && eintraege.length === 0 && <Leerzustand text={t('alarme.verlaufLeer')} />}
      {eintraege.length > 0 && (
        <Tabelle id="verlauf-h" titel={t('alarme.verlauf')} kopf={KOPF.map((k) => t(k))}>
          {eintraege.map((e) => {
            const metric = regeln.find((r) => r.id === e.rule_id)?.metric ?? ''
            const grund = [labelText(e.labels), e.reason].filter(Boolean).join(' · ')
            return (
              <tr key={e.id} className="border-t border-border">
                <Zelle num>{e.ts ? lokal(Date.parse(e.ts), jetzt) : t('alarme.ohneSchwere')}</Zelle>
                <Zeilenkopf>{EREIGNIS[e.event] ? t(EREIGNIS[e.event]!) : e.event}</Zeilenkopf>
                <td className={`px-4 py-2.5 font-semibold ${e.severity === 'critical' ? 'text-crit' : e.severity === 'warning' ? 'text-warn' : 'text-muted'}`}>
                  {e.severity ? t(e.severity === 'critical' ? 'alarme.kritisch' : 'alarme.warnung') : t('alarme.ohneSchwere')}
                </td>
                <Zelle>{e.rule_name}</Zelle>
                <Zelle>{e.server_name}</Zelle>
                <Zelle num>{e.value == null ? t('alarme.ohneSchwere') : formatWert(metric, e.value)}</Zelle>
                <Zelle>{grund}</Zelle>
              </tr>
            )
          })}
        </Tabelle>
      )}
      {verlauf.hasNextPage && (
        <div>
          <Knopf disabled={verlauf.isFetchingNextPage} onClick={() => void verlauf.fetchNextPage()}>
            {verlauf.isFetchingNextPage ? t('alarme.aelterLaden') : t('alarme.aeltere')}
          </Knopf>
        </div>
      )}
    </div>
  )
}
