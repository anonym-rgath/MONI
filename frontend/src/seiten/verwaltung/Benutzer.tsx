import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useBenutzer } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Benutzer as BenutzerEintrag } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { fehlertext, Meldung, Rueckfrage } from '../../ui/Meldungen'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { Statusmarke } from '../../ui/Statusmarke'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { BenutzerAnlegen, PasswortSetzen, RolleAendern } from './BenutzerFormulare'
import { zeitpunkt } from './hilfen'

type Art = 'neu' | 'rolle' | 'passwort' | 'aus' | 'zweiFaktor' | 'loeschen'
const SPALTEN: TextSchluessel[] = ['verwaltung.spName', 'verwaltung.spEmail', 'verwaltung.spRolle', 'verwaltung.spArt', 'verwaltung.spZweiFaktor', 'verwaltung.spStatus', 'verwaltung.spLetzte', 'verwaltung.spAktionen']

export function Benutzer() {
  const qc = useQueryClient()
  const liste = useBenutzer()
  const [dialog, setDialog] = useState<{ art: Art; u?: BenutzerEintrag } | null>(null)
  const [meldung, setMeldung] = useState('')
  const neuLaden = () => qc.invalidateQueries({ queryKey: schluessel.benutzer })
  const u = dialog?.u
  const anzeige = u ? u.name || u.email : ''
  const schliessen = () => setDialog(null)
  const fertig = (text: string) => () => { schliessen(); setMeldung(text) }
  const oeffnen = (art: Art, ziel?: BenutzerEintrag) => { setMeldung(''); setDialog({ art, u: ziel }) }

  // Ohne Folgen, daher ohne Rückfrage: Aktivieren und Entsperren.
  const direkt = useMutation({
    mutationFn: async (a: { u: BenutzerEintrag; weg: 'aktivieren' | 'entsperren' }) =>
      a.weg === 'aktivieren' ? api.patch(`/users/${a.u.id}`, { disabled: false }) : api.post(`/users/${a.u.id}/unlock`),
    onSuccess: async (_, a) => { await neuLaden(); setMeldung(a.weg === 'aktivieren' ? t('verwaltung.gespeichert') : t('verwaltung.benutzerEntsperrt')) },
    gcTime: 0,
  })
  const bestaetigen = (aufruf: () => Promise<unknown>, text: string) => async () => { await aufruf(); await neuLaden(); setMeldung(text) }

  return (
    <div className="flex flex-col gap-4">
      <Meldung text={meldung} />
      {liste.isError && <Hinweis art="fehler">{fehlertext(liste.error)}</Hinweis>}
      {direkt.error && <Hinweis art="fehler">{fehlertext(direkt.error)}</Hinweis>}
      {liste.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {liste.data?.length === 0 && <Leerzustand text={t('verwaltung.benutzerLeer')} />}
      {liste.data && liste.data.length > 0 && (
        <Tabelle id="benutzer-h" titel={t('verwaltung.benutzerListe')} kopf={SPALTEN.map((k) => t(k))}
          aktion={<Knopf art="haupt" onClick={() => oeffnen('neu')}>{t('verwaltung.benutzerAnlegen')}</Knopf>}>
          {liste.data.map((b) => {
            const wer = <>{' '}<span className="sr-only">{b.name || b.email}</span></>
            const status = b.disabled ? 'verwaltung.stDeaktiviert' : b.locked ? 'verwaltung.stGesperrt' : 'verwaltung.stAktiv'
            return (
              <tr key={b.id} className="border-t border-border">
                <Zeilenkopf>{b.name || b.email}</Zeilenkopf>
                <Zelle>{b.email}</Zelle>
                <Zelle>{t(b.role === 'admin' ? 'verwaltung.rolleAdmin' : 'verwaltung.rolleBetrachter')}</Zelle>
                <Zelle>{t(b.kind === 'local' ? 'verwaltung.artLokal' : 'verwaltung.artOidc')}</Zelle>
                <Zelle>{t(b.totp_enabled ? 'verwaltung.zfAn' : 'verwaltung.zfAus')}</Zelle>
                <Zelle><Statusmarke ton={b.disabled || b.locked ? 'warn' : 'ok'}>{t(status)}</Statusmarke></Zelle>
                <Zelle num>{b.last_login ? zeitpunkt(b.last_login) : t('verwaltung.nieAngemeldet')}</Zelle>
                <Zelle>
                  <div className="flex flex-wrap gap-2">
                    <Knopf onClick={() => oeffnen('rolle', b)}>{t('verwaltung.rolleAendern')}{wer}</Knopf>
                    {b.disabled
                      ? <Knopf disabled={direkt.isPending} onClick={() => { setMeldung(''); direkt.mutate({ u: b, weg: 'aktivieren' }) }}>{t('verwaltung.aktivieren')}{wer}</Knopf>
                      : <Knopf onClick={() => oeffnen('aus', b)}>{t('verwaltung.deaktivieren')}{wer}</Knopf>}
                    {b.locked && <Knopf disabled={direkt.isPending} onClick={() => { setMeldung(''); direkt.mutate({ u: b, weg: 'entsperren' }) }}>{t('verwaltung.entsperren')}{wer}</Knopf>}
                    {b.kind === 'local' && <Knopf onClick={() => oeffnen('passwort', b)}>{t('verwaltung.passwort')}{wer}</Knopf>}
                    {b.kind === 'local' && <Knopf onClick={() => oeffnen('zweiFaktor', b)}>{t('verwaltung.zweiFaktor')}{wer}</Knopf>}
                    <Knopf onClick={() => oeffnen('loeschen', b)}>{t('verwaltung.loeschen')}{wer}</Knopf>
                  </div>
                </Zelle>
              </tr>
            )
          })}
        </Tabelle>
      )}

      <Dialog titel={t('verwaltung.benutzerAnlegen')} offen={dialog?.art === 'neu'} onClose={schliessen}>
        <BenutzerAnlegen onAbbrechen={schliessen} onFertig={fertig(t('verwaltung.benutzerAngelegt'))} />
      </Dialog>
      <Dialog titel={t('verwaltung.rolleTitel', { name: anzeige })} offen={dialog?.art === 'rolle'} onClose={schliessen}>
        {u && <RolleAendern benutzer={u} onAbbrechen={schliessen} onFertig={fertig(t('verwaltung.gespeichert'))} />}
      </Dialog>
      <Dialog titel={t('verwaltung.passwort')} offen={dialog?.art === 'passwort'} onClose={schliessen}>
        {u && <PasswortSetzen benutzer={u} onAbbrechen={schliessen} onFertig={fertig(t('verwaltung.passwortGesetzt'))} />}
      </Dialog>
      <Rueckfrage titel={t('verwaltung.deaktivierenTitel')} text={t('verwaltung.deaktivierenText', { name: anzeige })} aktion={t('verwaltung.deaktivieren')}
        offen={dialog?.art === 'aus'} onClose={schliessen}
        onBestaetigen={bestaetigen(() => api.patch(`/users/${u!.id}`, { disabled: true }), t('verwaltung.gespeichert'))} />
      <Rueckfrage titel={t('verwaltung.zweiFaktorTitel')} text={t('verwaltung.zweiFaktorText', { name: anzeige })} aktion={t('verwaltung.zweiFaktor')}
        offen={dialog?.art === 'zweiFaktor'} onClose={schliessen}
        onBestaetigen={bestaetigen(() => api.post(`/users/${u!.id}/reset-2fa`), t('verwaltung.zweiFaktorZurueck'))} />
      <Rueckfrage titel={t('verwaltung.loeschenTitel')} text={t('verwaltung.loeschenText', { name: anzeige })} aktion={t('verwaltung.loeschen')}
        offen={dialog?.art === 'loeschen'} onClose={schliessen}
        onBestaetigen={bestaetigen(() => api.delete(`/users/${u!.id}`), t('verwaltung.benutzerGeloescht'))} />
    </div>
  )
}
