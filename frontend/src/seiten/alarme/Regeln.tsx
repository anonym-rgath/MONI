import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useRegeln, useServer } from '../../api/abfragen'
import { ApiFehler, api } from '../../api/client'
import type { Regel } from '../../api/typen'
import { useSitzung } from '../../auth/Sitzung'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { useTitel } from '../../ui/useTitel'
import { dauer } from '../../ui/Zeit'
import { RegelFormular } from './RegelFormular'
import { bedingung, schwelleText } from './regeltext'

const KOPF = ['alarme.regeln', 'alarme.spLoestAus', 'alarme.spWarnung', 'alarme.spKritisch', 'alarme.spDauer', 'alarme.spGiltFuer', 'alarme.spAktiv'] as const

export function Regeln() {
  useTitel(t('seiten.alarmRegeln'))
  const admin = useSitzung().user?.role === 'admin'
  const qc = useQueryClient()
  const regeln = useRegeln()
  const server = useServer().data ?? []
  const [bearbeite, setBearbeite] = useState<Regel | 'neu' | null>(null)
  const [loesche, setLoesche] = useState<Regel | null>(null)
  const [meldung, setMeldung] = useState('')
  const entfernen = useMutation({
    mutationFn: (r: Regel) => api.delete(`/alert-rules/${r.id}`),
    onSuccess: async () => {
      setLoesche(null)
      await Promise.all([qc.invalidateQueries({ queryKey: schluessel.regeln }), qc.invalidateQueries({ queryKey: schluessel.alarme })])
      setMeldung(t('alarme.regelGeloescht'))
    },
  })

  return (
    <div className="flex flex-col gap-3">
      <div role="status">{meldung && <p className="m-0 rounded-lg bg-surface-2 px-3 py-2.5 text-sm">{meldung}</p>}</div>
      {regeln.isError && <Hinweis art="fehler">{regeln.error instanceof ApiFehler ? regeln.error.detail : t('fehler.allgemein')}</Hinweis>}
      <Tabelle id="regeln-h" titel={t('alarme.regeln')} kopf={[...KOPF.map((k) => t(k)), ...(admin ? [t('alarme.spAktionen')] : [])]}
        aktion={admin && <Knopf art="haupt" onClick={() => { setMeldung(''); setBearbeite('neu') }}>{t('alarme.regelAnlegen')}</Knopf>}>
        {(regeln.isPending || regeln.data?.length === 0) && (
          <tr className="border-t border-border"><Zelle colSpan={KOPF.length + (admin ? 1 : 0)}>{t(regeln.isPending ? 'alarme.laden' : 'alarme.regelLeer')}</Zelle></tr>
        )}
        {regeln.data?.map((r) => (
          <tr key={r.id} className="border-t border-border">
            <Zeilenkopf>{r.name}</Zeilenkopf>
            <td className="px-4 py-2.5 text-muted">{bedingung(r)} <code className="num text-[0.8125rem]">{r.metric}</code></td>
            <Zelle num>{schwelleText(r, r.warning)}</Zelle>
            <Zelle num>{schwelleText(r, r.critical)}</Zelle>
            <Zelle>{r.for_seconds === 0 ? t('alarme.sofort') : dauer(r.for_seconds)}</Zelle>
            <Zelle>{r.server_id == null ? t('alarme.alleServerKlein') : (server.find((s) => s.id === r.server_id)?.name ?? t('alarme.serverNr', { id: r.server_id }))}</Zelle>
            <Zelle>{t(r.enabled ? 'alarme.aktivJa' : 'alarme.aktivNein')}</Zelle>
            {admin && (
              <td className="px-4 py-1.5">
                <div className="flex gap-2">
                  <Knopf onClick={() => { setMeldung(''); setBearbeite(r) }}>{t('alarme.bearbeiten')}<span className="sr-only"> {r.name}</span></Knopf>
                  <Knopf onClick={() => { setMeldung(''); entfernen.reset(); setLoesche(r) }}>{t('alarme.loeschen')}<span className="sr-only"> {r.name}</span></Knopf>
                </div>
              </td>
            )}
          </tr>
        ))}
      </Tabelle>

      <Dialog titel={t(bearbeite === 'neu' ? 'alarme.regelAnlegen' : 'alarme.regelBearbeiten')} offen={bearbeite !== null} onClose={() => setBearbeite(null)}>
        {bearbeite && <RegelFormular regel={bearbeite === 'neu' ? null : bearbeite} onFertig={() => { setBearbeite(null); setMeldung(t('alarme.gespeichert')) }} />}
        <Knopf onClick={() => setBearbeite(null)}>{t('alarme.abbrechen')}</Knopf>
      </Dialog>
      <Dialog titel={t('alarme.loeschenTitel')} offen={loesche !== null} onClose={() => setLoesche(null)}>
        {loesche && (
          <>
            <p className="m-0">{t('alarme.loeschenText', { name: loesche.name })}</p>
            {entfernen.error && <Hinweis art="fehler">{entfernen.error instanceof ApiFehler ? entfernen.error.detail : t('fehler.allgemein')}</Hinweis>}
            <div className="flex flex-wrap gap-2">
              <Knopf art="haupt" disabled={entfernen.isPending} onClick={() => entfernen.mutate(loesche)}>{t('alarme.loeschen')}</Knopf>
              <Knopf onClick={() => setLoesche(null)}>{t('alarme.abbrechen')}</Knopf>
            </div>
          </>
        )}
      </Dialog>
    </div>
  )
}
