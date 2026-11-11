import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useTokens } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Token } from '../../api/typen'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { fehlertext, zeitpunkt } from './hilfen'
import { Meldung } from './Meldung'
import { Rueckfrage } from './Rueckfrage'
import { TokenErstellen } from './TokenErstellen'

export function Tokens() {
  const qc = useQueryClient()
  const tokens = useTokens()
  const [neu, setNeu] = useState(false)
  const [gesperrt, setGesperrt] = useState(false)
  const [widerruf, setWiderruf] = useState<Token | null>(null)
  const [meldung, setMeldung] = useState('')
  const neuLaden = () => qc.invalidateQueries({ queryKey: schluessel.tokens })

  return (
    <div className="flex flex-col gap-4">
      <Meldung text={meldung} />
      {tokens.isError && <Hinweis art="fehler">{fehlertext(tokens.error)}</Hinweis>}
      {tokens.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {tokens.data?.length === 0 && (
        <Leerzustand text={t('verwaltung.tokenLeer')}><Knopf art="haupt" onClick={() => setNeu(true)}>{t('verwaltung.tokenErstellen')}</Knopf></Leerzustand>
      )}
      {tokens.data && tokens.data.length > 0 && (
        <Tabelle id="tokens-h" titel={t('verwaltung.tokenListe')} kopf={[t('verwaltung.spName'), t('verwaltung.spBereich'), t('verwaltung.erstellt'), t('verwaltung.spAblauf'), t('verwaltung.spAktionen')]}
          aktion={<Knopf art="haupt" onClick={() => { setMeldung(''); setNeu(true) }}>{t('verwaltung.tokenErstellen')}</Knopf>}>
          {tokens.data.map((k) => (
            <tr key={k.id} className="border-t border-border">
              <Zeilenkopf>{k.name}</Zeilenkopf>
              <Zelle>{t(k.scope === 'admin' ? 'verwaltung.bereichAdmin' : 'verwaltung.bereichLesen')}</Zelle>
              <Zelle num>{zeitpunkt(k.created_at)}</Zelle>
              <Zelle num>{k.expires_at ? zeitpunkt(k.expires_at) : t('verwaltung.nie')}</Zelle>
              <Zelle><Knopf onClick={() => { setMeldung(''); setWiderruf(k) }}>{t('verwaltung.widerrufen')}{' '}<span className="sr-only">{k.name}</span></Knopf></Zelle>
            </tr>
          ))}
        </Tabelle>
      )}

      <Dialog titel={t('verwaltung.tokenErstellen')} offen={neu} gesperrt={gesperrt} onClose={() => setNeu(false)}>
        <TokenErstellen pfad="/tokens" mitBereich titelNeu={t('verwaltung.tokenNeuTitel')} onErstellt={neuLaden} onAngezeigt={() => setGesperrt(true)}
          onFertig={() => { setGesperrt(false); setNeu(false) }} onAbbrechen={() => setNeu(false)} />
      </Dialog>
      <Rueckfrage titel={t('verwaltung.widerrufenTitel')} text={widerruf ? t('verwaltung.widerrufenText', { name: widerruf.name }) : ''} aktion={t('verwaltung.widerrufen')}
        offen={widerruf !== null} onClose={() => setWiderruf(null)}
        onBestaetigen={async () => { await api.delete(`/tokens/${widerruf!.id}`); await neuLaden(); setMeldung(t('verwaltung.widerrufen2')) }} />
    </div>
  )
}
