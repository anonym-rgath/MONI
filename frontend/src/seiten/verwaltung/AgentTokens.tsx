import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useAgentTokens } from '../../api/abfragen'
import { ApiFehler, api } from '../../api/client'
import type { AgentToken, Server } from '../../api/typen'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { fehlertext, Meldung, RueckfrageInhalt } from '../../ui/Meldungen'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { zeitpunkt } from './hilfen'
import { TokenErstellen } from './TokenErstellen'

function Liste({ server, onNeu, onZuruecknehmen }: { server: Server; onNeu: () => void; onZuruecknehmen: (t: AgentToken) => void }) {
  const tokens = useAgentTokens(server.id)
  return (
    <>
      {tokens.isError && <Hinweis art="fehler">{fehlertext(tokens.error)}</Hinweis>}
      {tokens.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {tokens.data?.length === 0 && <Leerzustand text={t('verwaltung.agentLeer')} />}
      {tokens.data && tokens.data.length > 0 && (
        <Tabelle id="agent-tokens-h" titel={t('verwaltung.agentTokens')} kopf={[t('verwaltung.spName'), t('verwaltung.spBereich'), t('verwaltung.erstellt'), t('verwaltung.spAblauf'), t('verwaltung.spAktionen')]}>
          {tokens.data.map((k) => (
            <tr key={k.id} className="border-t border-border">
              <Zeilenkopf>{k.name ?? k.id}</Zeilenkopf>
              <Zelle>{t(k.scope === 'admin' ? 'verwaltung.bereichAdmin' : 'verwaltung.bereichLesen')}</Zelle>
              <Zelle num>{zeitpunkt(k.created_at)}</Zelle>
              <Zelle num>{k.expires_at ? zeitpunkt(k.expires_at) : t('verwaltung.nie')}</Zelle>
              <Zelle><Knopf onClick={() => onZuruecknehmen(k)}>{t('verwaltung.widerrufen')}{' '}<span className="sr-only">{k.name ?? k.id}</span></Knopf></Zelle>
            </tr>
          ))}
        </Tabelle>
      )}
      <div className="flex flex-wrap gap-2">
        <Knopf art="haupt" onClick={onNeu}>{t('verwaltung.tokenErstellen')}</Knopf>
      </div>
    </>
  )
}

// Eine Ansicht im Dialog des Servers: Liste, Erstellen (mit Einmalwert) oder Rückfrage zum Widerrufen.
export function AgentTokens({ server, onClose }: { server: Server | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [ansicht, setAnsicht] = useState<'liste' | 'neu'>('liste')
  const [widerruf, setWiderruf] = useState<AgentToken | null>(null)
  const [meldung, setMeldung] = useState('')
  const [gesperrt, setGesperrt] = useState(false)
  const schliessen = () => { setAnsicht('liste'); setWiderruf(null); setMeldung(''); onClose() }
  const neuLaden = () => (server ? qc.invalidateQueries({ queryKey: schluessel.agentTokens(server.id) }) : Promise.resolve())

  async function zuruecknehmen() {
    if (!server || !widerruf) return
    try {
      await api.delete(`/servers/${server.id}/agent-tokens/${widerruf.id}`)
    } catch (e) {
      if (e instanceof ApiFehler && e.status === 404) { await neuLaden(); throw new ApiFehler(404, t('verwaltung.agentNichtGefunden')) }
      throw e
    }
    await neuLaden()
    setMeldung(t('verwaltung.widerrufen2'))
  }

  return (
    <Dialog breit titel={t('verwaltung.agentTitel', { name: server?.name ?? '' })} offen={server !== null} gesperrt={gesperrt} onClose={schliessen}>
      {server && widerruf === null && (ansicht === 'liste'
        ? (
          <>
            <Meldung text={meldung} />
            <Liste server={server} onNeu={() => { setMeldung(''); setAnsicht('neu') }} onZuruecknehmen={(k) => { setMeldung(''); setWiderruf(k) }} />
            <Knopf onClick={schliessen}>{t('verwaltung.schliessen')}</Knopf>
          </>
        )
        : <TokenErstellen pfad={`/servers/${server.id}/agent-tokens`} mitBereich={false} titelNeu={t('verwaltung.agentNeuTitel')} onErstellt={neuLaden}
            onAngezeigt={() => setGesperrt(true)} onFertig={() => { setGesperrt(false); setAnsicht('liste') }} onAbbrechen={() => setAnsicht('liste')} />)}
      {server && widerruf !== null && (
        <RueckfrageInhalt text={t('verwaltung.widerrufenText', { name: widerruf.name ?? widerruf.id })} aktion={t('verwaltung.widerrufen')}
          onClose={() => setWiderruf(null)} onBestaetigen={zuruecknehmen} />
      )}
    </Dialog>
  )
}
