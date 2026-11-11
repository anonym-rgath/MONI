import { Link, useParams } from 'react-router'
import { useAlarme, useRegeln, useServerEinzeln, useSnapshot } from '../../api/abfragen'
import { ApiFehler } from '../../api/client'
import { useSitzung } from '../../auth/Sitzung'
import { t } from '../../texte/de'
import { Hinweis } from '../../ui/Hinweis'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Statusmarke } from '../../ui/Statusmarke'
import { useTitel } from '../../ui/useTitel'
import { lokal, sekundenSeit, useJetzt, vor } from '../../ui/Zeit'
import { useState } from 'react'
import { stummBis, zeile } from '../uebersicht/Aufmerksamkeit'
import { brauchtAufmerksamkeit, zustand } from '../uebersicht/dringlichkeit'
import { MARKE } from '../uebersicht/Serverkarte'
import { Aktionen } from './Aktionen'
import { Container } from './Container'
import { Dateisysteme } from './Dateisysteme'
import { Kennzahlen } from './Kennzahlen'
import { Netzwerk } from './Netzwerk'
import { BEREICHE, Verlauf, type BereichId } from './Verlauf'

const FARBE = { kritisch: 'text-crit', warnung: 'text-warn' } as const

function Unbekannt() {
  useTitel(t('server.nichtGefunden'))
  return (
    <>
      <h1 tabIndex={-1} className="m-0 text-[1.625rem] leading-tight font-semibold">{t('seiten.server')}</h1>
      <Leerzustand text={t('server.nichtGefunden')}><Link to="/" className="text-accent underline underline-offset-3">{t('server.zurUebersicht')}</Link></Leerzustand>
    </>
  )
}

// Ungültige ids (nicht rein numerisch) lösen keine Anfrage aus.
export function Serverdetail() {
  const param = useParams().id ?? ''
  return /^\d{1,9}$/.test(param) ? <Detail id={Number(param)} /> : <Unbekannt />
}

function Detail({ id }: { id: number }) {
  const { user } = useSitzung()
  const jetzt = useJetzt()
  const server = useServerEinzeln(id)
  const snapshot = useSnapshot(id)
  const alarme = useAlarme()
  const regeln = useRegeln()
  const [bereich, setBereich] = useState<BereichId>('24h')
  const [meldung, setMeldung] = useState('')
  const s = server.data
  useTitel(s?.name ?? t('seiten.server'))

  const h1 = <h1 tabIndex={-1} className="m-0 text-[1.625rem] leading-tight font-semibold">{s?.name ?? t('seiten.server')}</h1>
  if (!s) {
    const fehlt = server.error instanceof ApiFehler && server.error.status === 404
    return (
      <>
        {h1}
        {server.isPending && <Ladezustand text={t('server.laden')} />}
        {server.isError && (
          <Leerzustand text={fehlt ? t('server.nichtGefunden') : server.error instanceof ApiFehler ? server.error.detail : t('fehler.allgemein')}>
            <Link to="/" className="text-accent underline underline-offset-3">{t('server.zurUebersicht')}</Link>
          </Leerzustand>
        )}
      </>
    )
  }

  const a = alarme.data ?? []
  const z = zustand(s, a, jetzt)
  const marke = MARKE[z]
  const problem = s.enabled && brauchtAufmerksamkeit(s, a) ? zeile(s, a, regeln.data ?? [], jetzt) : null
  const sicherheit = s.is_local ? t('karte.managerLokal')
    : s.security === 'mtls' ? (s.cert_not_after ? t('server.mtlsBis', { datum: lokal(Date.parse(s.cert_not_after), jetzt) }) : t('server.mtls'))
    : s.security === 'token' ? t('server.token') : null
  const meta = [
    s.agent_version ? t('server.agent', { version: s.agent_version }) : null,
    sicherheit,
    s.url,
    s.last_seen ? t('karte.zuletzt', { vor: vor(sekundenSeit(Date.parse(s.last_seen), jetzt)) }) : t('karte.nieGesehen'),
    stummBis(s, jetzt),
  ].filter(Boolean).join(' · ')
  const snap = snapshot.data
  const ms = BEREICHE.find((b) => b.id === bereich)!.ms

  return (
    <>
      <div className="flex flex-col gap-3">
        <nav aria-label={t('server.brotkrumen')} className="text-sm text-muted">
          <Link to="/" className="text-accent underline underline-offset-3">{t('rahmen.uebersicht')}</Link> <span aria-hidden="true">/</span> <span aria-current="page">{s.name}</span>
        </nav>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">{h1}<Statusmarke ton={marke.ton}>{t(marke.text)}</Statusmarke></div>
          {user?.role === 'admin' && <Aktionen server={s} stumm={Boolean(stummBis(s, jetzt))} onMeldung={setMeldung} />}
        </div>
        <p className="m-0">
          {problem ? <><span className={`font-semibold ${FARBE[problem.schwere]}`}>{t(problem.schwere === 'kritisch' ? 'uebersicht.schwereKritisch' : 'uebersicht.schwereWarnung')}:</span> {problem.text}</>
            : s.enabled ? t('server.keineProbleme') : t('server.ausgeschaltet')}
        </p>
        <p className="m-0 text-sm text-muted">{meta}</p>
        <div role="status">{meldung && <p className="m-0 rounded-lg bg-surface-2 px-3 py-2.5 text-sm">{meldung}</p>}</div>
        {s.last_error && <Hinweis art="fehler">{s.last_error}</Hinweis>}
        {s.last_error && s.security === 'mtls' && (
          <Hinweis>{t('server.pairVor')}<code className="num">{t('server.pairBefehl')}</code>{t('server.pairNach')}</Hinweis>
        )}
      </div>
      <Kennzahlen snapshot={snap} />
      <Verlauf serverId={id} snapshot={snap} bereich={bereich} onBereich={setBereich} />
      <Dateisysteme snapshot={snap} />
      <Netzwerk serverId={id} snapshot={snap} />
      <Container serverId={id} snapshot={snap} bereichMs={ms} />
    </>
  )
}
