import { useAudit } from '../../api/abfragen'
import { t, type TextSchluessel } from '../../texte/de'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zelle } from '../../ui/Tabelle'
import { fehlertext, zeitpunkt } from './hilfen'

const SPALTEN: TextSchluessel[] = ['verwaltung.spZeit', 'verwaltung.spAkteur', 'verwaltung.spAktion', 'verwaltung.spZiel', 'verwaltung.spIp', 'verwaltung.spDetails']
const ohne = () => t('verwaltung.ohne')

// Alle Felder sind Serverdaten und erscheinen nur als Text (D5): JSX maskiert, die Details als JSON-Text in <code>.
export function Audit() {
  const audit = useAudit()
  const eintraege = audit.data?.pages.flatMap((p) => p.entries) ?? []
  return (
    <div className="flex flex-col gap-4">
      {audit.isError && <Hinweis art="fehler">{fehlertext(audit.error)}</Hinweis>}
      {audit.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {audit.data && eintraege.length === 0 && <Leerzustand text={t('verwaltung.auditLeer')} />}
      {eintraege.length > 0 && (
        <Tabelle id="audit-h" titel={t('verwaltung.auditListe')} kopf={SPALTEN.map((k) => t(k))}>
          {eintraege.map((e) => (
            <tr key={e.id} className="border-t border-border">
              <Zelle num>{zeitpunkt(e.ts)}</Zelle>
              <Zelle>{e.actor ?? ohne()}</Zelle>
              <Zelle><code className="num">{e.action}</code></Zelle>
              <Zelle><span className="break-all">{e.target ?? ohne()}</span></Zelle>
              <Zelle num>{e.ip ?? ohne()}</Zelle>
              <Zelle>{e.details ? <code className="num text-sm break-all">{JSON.stringify(e.details)}</code> : ohne()}</Zelle>
            </tr>
          ))}
        </Tabelle>
      )}
      {audit.hasNextPage && (
        <div>
          <Knopf disabled={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
            {audit.isFetchingNextPage ? t('verwaltung.laden') : t('verwaltung.aeltere')}
          </Knopf>
        </div>
      )}
    </div>
  )
}
