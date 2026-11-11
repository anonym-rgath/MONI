import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useKanaele } from '../../api/abfragen'
import { ApiFehler, api } from '../../api/client'
import type { Kanal } from '../../api/typen'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { useTitel } from '../../ui/useTitel'
import { Erinnerung } from './Erinnerung'
import { KanalFormular } from './KanalFormular'

const fehlertext = (e: unknown) => (e instanceof ApiFehler ? e.detail : t('fehler.allgemein'))

function ziel(k: Kanal): string {
  if (k.kind === 'webhook') return k.url_hint ?? ''
  return t('alarme.zielEmail', { host: String(k.config.host ?? ''), n: Array.isArray(k.config.to) ? k.config.to.length : 0 })
}
function art(k: Kanal): string {
  if (k.kind === 'webhook') return t(k.secret_set ? 'alarme.kanalWebhookHmac' : 'alarme.kanalWebhook')
  return t('alarme.kanalEmail', { sicherheit: k.config.security === 'tls' ? t('alarme.sTls') : t('alarme.sStarttls'), port: String(k.config.port ?? '') })
}

function Karte({ kanal, onBearbeiten, onLoeschen }: { kanal: Kanal; onBearbeiten: () => void; onLoeschen: () => void }) {
  const test = useMutation({ mutationFn: () => api.post<{ ok: boolean; error: string | null }>(`/alert-channels/${kanal.id}/test`), gcTime: 0 })
  // Das Ergebnis steht in einer dauerhaft vorhandenen role="status"-Region, damit Screenreader es ansagen.
  const ergebnis = test.isPending ? t('alarme.testLaeuft')
    : test.error ? (test.error instanceof ApiFehler && test.error.status === 429 ? t('alarme.testZuViele') : fehlertext(test.error))
    : test.data?.ok ? t('alarme.testOk')
    : test.data ? <><span>{t('alarme.testFehler')}</span> <span>{test.data.error}</span></> : null
  return (
    <li className="flex flex-col gap-3 rounded-[14px] border border-border bg-surface px-5 py-4.5">
      <div>
        <h3 className="m-0 text-base font-semibold">{kanal.name}{!kanal.enabled && <span className="ml-2 text-sm font-normal text-muted">({t('alarme.kanalAus')})</span>}</h3>
        <div className="text-sm text-muted">{art(kanal)}</div>
      </div>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">{t('alarme.ziel')}</dt><dd className="num m-0 break-all">{ziel(kanal)}</dd>
        <dt className="text-muted">{t('alarme.meldet')}</dt><dd className="m-0">{t(kanal.min_severity === 'critical' ? 'alarme.meldetKritisch' : 'alarme.meldetAlle')}</dd>
        <dt className="text-muted">{t('alarme.geheimnis')}</dt><dd className="m-0">{t(kanal.secret_set ? 'alarme.geheimnisJa' : 'alarme.geheimnisNein')}</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <Knopf disabled={test.isPending} onClick={() => test.mutate()}>{t('alarme.test')}</Knopf>
        <Knopf onClick={onBearbeiten}>{t('alarme.kanalBearbeiten')}<span className="sr-only"> {kanal.name}</span></Knopf>
        <Knopf onClick={onLoeschen}>{t('alarme.loeschen')}<span className="sr-only"> {kanal.name}</span></Knopf>
      </div>
      <div role="status" className="text-sm">{ergebnis}</div>
    </li>
  )
}

// Nur für Admins (Route mit Wächter); GET /alert-channels liefert nie Geheimnisse.
export function Kanaele() {
  useTitel(t('seiten.alarmKanaele'))
  const qc = useQueryClient()
  const kanaele = useKanaele()
  const [bearbeite, setBearbeite] = useState<Kanal | 'neu' | null>(null)
  const [loesche, setLoesche] = useState<Kanal | null>(null)
  const [meldung, setMeldung] = useState('')
  const entfernen = useMutation({
    mutationFn: (k: Kanal) => api.delete(`/alert-channels/${k.id}`),
    onSuccess: async () => { setLoesche(null); await qc.invalidateQueries({ queryKey: schluessel.kanaele }); setMeldung(t('alarme.kanalGeloescht')) },
  })

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="kanaele-h" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="kanaele-h" className="m-0 text-[1.0625rem] font-semibold">{t('alarme.kanaele')}</h2>
          <Knopf art="haupt" onClick={() => { setMeldung(''); setBearbeite('neu') }}>{t('alarme.kanalAnlegen')}</Knopf>
        </div>
        <div role="status">{meldung && <p className="m-0 rounded-lg bg-surface-2 px-3 py-2.5 text-sm">{meldung}</p>}</div>
        {kanaele.isError && <Hinweis art="fehler">{fehlertext(kanaele.error)}</Hinweis>}
        {kanaele.isPending && <Ladezustand text={t('alarme.laden')} />}
        {kanaele.data?.length === 0 && <Leerzustand text={t('alarme.kanalLeer')} />}
        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] gap-4 p-0">
          {kanaele.data?.map((k) => (
            <Karte key={k.id} kanal={k} onBearbeiten={() => { setMeldung(''); setBearbeite(k) }} onLoeschen={() => { setMeldung(''); entfernen.reset(); setLoesche(k) }} />
          ))}
        </ul>
      </section>
      <Erinnerung />

      <Dialog titel={t(bearbeite === 'neu' ? 'alarme.kanalAnlegen' : 'alarme.kanalBearbeiten')} offen={bearbeite !== null} onClose={() => setBearbeite(null)}>
        {bearbeite && <KanalFormular kanal={bearbeite === 'neu' ? null : bearbeite} onFertig={() => { setBearbeite(null); setMeldung(t('alarme.gespeichert')) }} />}
        <Knopf onClick={() => setBearbeite(null)}>{t('alarme.abbrechen')}</Knopf>
      </Dialog>
      <Dialog titel={t('alarme.kanalLoeschenTitel')} offen={loesche !== null} onClose={() => setLoesche(null)}>
        {loesche && (
          <>
            <p className="m-0">{t('alarme.kanalLoeschenText', { name: loesche.name })}</p>
            {entfernen.error && <Hinweis art="fehler">{fehlertext(entfernen.error)}</Hinweis>}
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
