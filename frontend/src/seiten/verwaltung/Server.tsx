import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { schluessel, useServer } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Server as ServerEintrag } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Statusmarke, type Ton } from '../../ui/Statusmarke'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { AgentTokens } from './AgentTokens'
import { fehlertext, tag } from './hilfen'
import { Koppeln } from './Koppeln'
import { Meldung } from './Meldung'
import { Rueckfrage } from './Rueckfrage'

const STATUS: Record<ServerEintrag['status'], { text: TextSchluessel; ton: Ton }> = {
  ok: { text: 'status.ok', ton: 'ok' }, unknown: { text: 'status.unbekannt', ton: 'neutral' },
  unreachable: { text: 'status.nichtErreichbar', ton: 'crit' }, error: { text: 'status.fehler', ton: 'crit' },
}
const art = (s: ServerEintrag): TextSchluessel => (s.is_local ? 'karte.managerLokal' : s.security === 'mtls' ? 'karte.agentMtls' : 'karte.agentToken')
const SPALTEN: TextSchluessel[] = ['verwaltung.spName', 'verwaltung.spArt', 'verwaltung.spStatus', 'verwaltung.spUrl', 'verwaltung.spZertifikat', 'verwaltung.spAktionen']

type Aktion = { art: 'entfernen' | 'aus'; server: ServerEintrag }

export function Server() {
  const qc = useQueryClient()
  const liste = useServer()
  const [params, setParams] = useSearchParams()
  const [koppeln, setKoppeln] = useState(false)
  const [aktion, setAktion] = useState<Aktion | null>(null)
  const [tokensVon, setTokensVon] = useState<ServerEintrag | null>(null)
  const [meldung, setMeldung] = useState('')
  const neuLaden = () => qc.invalidateQueries({ queryKey: schluessel.server, exact: true })

  // ?koppeln=1 (Link aus der Übersicht) öffnet den Dialog einmal; danach verschwindet der Parameter.
  const verlangt = params.has('koppeln')
  if (verlangt && !koppeln) setKoppeln(true) // Zustand beim Rendern anpassen, kein Effekt nötig
  useEffect(() => { if (verlangt) setParams({}, { replace: true }) }, [verlangt, setParams])

  const einschalten = useMutation({
    mutationFn: (s: ServerEintrag) => api.patch(`/servers/${s.id}`, { enabled: true }),
    onSuccess: async () => { await neuLaden(); setMeldung(t('server.aktiviertMeldung')) },
    gcTime: 0,
  })

  const kopf = (
    <Knopf art="haupt" onClick={() => { setMeldung(''); setKoppeln(true) }}>{t('verwaltung.koppeln')}</Knopf>
  )

  return (
    <div className="flex flex-col gap-4">
      <Meldung text={meldung} />
      {liste.isError && <Hinweis art="fehler">{fehlertext(liste.error)}</Hinweis>}
      {einschalten.error && <Hinweis art="fehler">{fehlertext(einschalten.error)}</Hinweis>}
      {liste.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {liste.data?.length === 0 && <Leerzustand text={t('verwaltung.serverLeer')}>{kopf}</Leerzustand>}
      {liste.data && liste.data.length > 0 && (
        <Tabelle id="server-h" titel={t('verwaltung.serverListe')} kopf={SPALTEN.map((k) => t(k))} aktion={kopf}>
          {liste.data.map((s) => {
            const st = s.enabled ? STATUS[s.status] : { text: 'status.deaktiviert' as const, ton: 'neutral' as const }
            const name = <>{' '}<span className="sr-only">{s.name}</span></>
            return (
              <tr key={s.id} className="border-t border-border">
                <Zeilenkopf>{s.name}</Zeilenkopf>
                <Zelle>{t(art(s))}</Zelle>
                <Zelle><Statusmarke ton={st.ton}>{t(st.text)}</Statusmarke></Zelle>
                <Zelle><span className="num break-all">{s.url}</span></Zelle>
                <Zelle num>{tag(s.cert_not_after)}</Zelle>
                <Zelle>
                  <div className="flex flex-wrap gap-2">
                    {s.enabled
                      ? <Knopf onClick={() => { setMeldung(''); setAktion({ art: 'aus', server: s }) }}>{t('server.deaktivieren')}{name}</Knopf>
                      : <Knopf disabled={einschalten.isPending} onClick={() => { setMeldung(''); einschalten.mutate(s) }}>{t('server.aktivieren')}{name}</Knopf>}
                    {s.security === 'mtls' && <Knopf onClick={() => { setMeldung(''); setTokensVon(s) }}>{t('verwaltung.agentTokens')}{name}</Knopf>}
                    {!s.is_local && <Knopf onClick={() => { setMeldung(''); setAktion({ art: 'entfernen', server: s }) }}>{t('verwaltung.entfernen')}{name}</Knopf>}
                  </div>
                </Zelle>
              </tr>
            )
          })}
        </Tabelle>
      )}

      <Dialog titel={t('verwaltung.koppeln')} offen={koppeln} onClose={() => setKoppeln(false)}>
        <Koppeln onAbbrechen={() => setKoppeln(false)} onFertig={() => { setKoppeln(false); setMeldung(t('verwaltung.gekoppelt')) }} />
      </Dialog>
      <AgentTokens server={tokensVon} onClose={() => setTokensVon(null)} />
      <Rueckfrage titel={t('verwaltung.entfernenTitel')} text={aktion?.art === 'entfernen' ? t('verwaltung.entfernenText', { name: aktion.server.name }) : ''} aktion={t('verwaltung.entfernen')}
        offen={aktion?.art === 'entfernen'} onClose={() => setAktion(null)}
        onBestaetigen={async () => { await api.delete(`/servers/${aktion!.server.id}`); await neuLaden(); setMeldung(t('verwaltung.entfernt')) }} />
      <Rueckfrage titel={t('server.deaktivierenTitel')} text={aktion?.art === 'aus' ? t('server.deaktivierenText', { name: aktion.server.name }) : ''} aktion={t('server.deaktivieren')}
        offen={aktion?.art === 'aus'} onClose={() => setAktion(null)}
        onBestaetigen={async () => { await api.patch(`/servers/${aktion!.server.id}`, { enabled: false }); await neuLaden(); setMeldung(t('server.deaktiviertMeldung')) }} />
    </div>
  )
}
