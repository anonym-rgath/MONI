import { Link } from 'react-router'
import { useAlarme, useRegeln, useServer } from '../../api/abfragen'
import { useSitzung } from '../../auth/Sitzung'
import { t } from '../../texte/de'
import { Hinweis } from '../../ui/Hinweis'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { useTitel } from '../../ui/useTitel'
import { sekundenSeit, useJetzt, vor } from '../../ui/Zeit'
import { Aufmerksamkeit } from './Aufmerksamkeit'
import { brauchtAufmerksamkeit, sortiere, zustand } from './dringlichkeit'
import { Serverkarte } from './Serverkarte'

const KOPPELN = 'inline-flex min-h-11.5 items-center rounded-lg bg-accent px-4 font-semibold text-on-accent'
const Koppeln = () => <Link to="/verwaltung/server?koppeln=1" className={KOPPELN}>{t('uebersicht.koppeln')}</Link>

export function Uebersicht() {
  useTitel(t('rahmen.uebersicht'))
  const { user } = useSitzung()
  const jetzt = useJetzt()
  const servers = useServer()
  const alarme = useAlarme()
  const regeln = useRegeln()
  const admin = user?.role === 'admin'
  const liste = servers.data
  const fehler = servers.isError || alarme.isError
  const a = alarme.data ?? []
  const sortiert = liste ? sortiere(liste, a, jetzt) : []
  const probleme = sortiert.filter((s) => brauchtAufmerksamkeit(s, a))
  const uebrige = sortiert.filter((s) => !brauchtAufmerksamkeit(s, a))

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 tabIndex={-1} className="m-0 text-[1.625rem] leading-tight font-semibold">{t('rahmen.uebersicht')}</h1>
          {liste && servers.dataUpdatedAt > 0 && (
            <p className="mt-1.5 mb-0 text-muted">{t('uebersicht.kopf', { n: liste.length, vor: vor(sekundenSeit(servers.dataUpdatedAt, jetzt)) })}</p>
          )}
        </div>
        {admin && <Koppeln />}
      </div>

      {fehler && <Hinweis art="fehler">{t('uebersicht.managerFehler')}</Hinweis>}
      {servers.isPending && <Ladezustand />}
      {liste?.length === 0 && <Leerzustand text={t('uebersicht.leer')}>{admin && <Koppeln />}</Leerzustand>}
      {liste && liste.length > 0 && (
        <div className={`flex flex-col gap-6 ${fehler ? 'opacity-60' : ''}`}>
          <Aufmerksamkeit probleme={probleme} uebrige={uebrige} alarme={a} regeln={regeln.data ?? []} jetzt={jetzt} />
          <section aria-labelledby="server-h" className="flex flex-col gap-3">
            <h2 id="server-h" className="m-0 text-[1.0625rem] font-semibold">{t('uebersicht.serverListe')}</h2>
            <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(min(100%,280px),1fr))] gap-4 p-0">
              {sortiert.map((s) => (
                <li key={s.id} className="flex"><Serverkarte server={s} zustand={zustand(s, a, jetzt)} jetzt={jetzt} /></li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </>
  )
}
