import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useOidcKonfig } from '../../api/abfragen'
import { api } from '../../api/client'
import type { OidcAnbieter } from '../../api/typen'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { fehlertext, Meldung, Rueckfrage } from '../../ui/Meldungen'
import { Ladezustand } from '../../ui/Ladezustand'
import { Leerzustand } from '../../ui/Leerzustand'
import { AnbieterFormular } from './AnbieterFormular'

const redirectUri = (basis: string, id: string) => `${basis}/api/v1/auth/oidc/callback/${id}`

function PublicUrl({ wert, onGespeichert }: { wert: string; onGespeichert: () => void }) {
  const qc = useQueryClient()
  const [url, setUrl] = useState(wert)
  async function senden() {
    await api.put('/auth/oidc/public-url', { public_url: url.trim() })
    await qc.invalidateQueries({ queryKey: schluessel.oidcKonfig })
    onGespeichert()
  }
  return (
    <section aria-labelledby="public-url-h" className="flex max-w-xl flex-col gap-3">
      <h2 id="public-url-h" className="m-0 text-[1.0625rem] font-semibold">{t('verwaltung.publicUrlTitel')}</h2>
      <Formular onSubmit={senden} absenden={t('verwaltung.publicUrlSpeichern')}>
        <Feld label={t('verwaltung.fPublicUrl')} hinweis={t('verwaltung.fPublicUrlHinweis')} value={url} onChange={(e) => setUrl(e.target.value)} required inputMode="url" autoComplete="off" spellCheck={false} />
      </Formular>
    </section>
  )
}

function Karte({ a, basis, onBearbeiten, onLoeschen, onMeldung }: { a: OidcAnbieter; basis: string | null; onBearbeiten: () => void; onLoeschen: () => void; onMeldung: (text: string) => void }) {
  const uri = basis ? redirectUri(basis, a.id) : null
  async function kopieren() {
    try {
      await navigator.clipboard.writeText(uri ?? '')
      onMeldung(t('verwaltung.kopiert'))
    } catch {
      onMeldung(t('verwaltung.kopierenFehlt')) // fehlende API oder abgelehnte Berechtigung
    }
  }
  return (
    <li className="flex flex-col gap-3 rounded-[14px] border border-border bg-surface px-5 py-4.5">
      <h3 className="m-0 text-base font-semibold">{a.label}</h3>
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">{t('verwaltung.fKennung')}</dt><dd className="num m-0">{a.id}</dd>
        <dt className="text-muted">{t('verwaltung.fIssuer')}</dt><dd className="num m-0 break-all">{a.issuer}</dd>
        <dt className="text-muted">{t('verwaltung.fClientId')}</dt><dd className="num m-0 break-all">{a.client_id}</dd>
        <dt className="text-muted">{t('verwaltung.redirectUri')}</dt>
        <dd className="num m-0 break-all">{uri ?? t('verwaltung.redirectFehlt')}</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        {uri && <Knopf onClick={() => void kopieren()}>{t('verwaltung.kopieren')}{' '}<span className="sr-only">{a.label}</span></Knopf>}
        <Knopf onClick={onBearbeiten}>{t('verwaltung.anbieterBearbeiten')}{' '}<span className="sr-only">{a.label}</span></Knopf>
        <Knopf onClick={onLoeschen}>{t('verwaltung.loeschen')}{' '}<span className="sr-only">{a.label}</span></Knopf>
      </div>
    </li>
  )
}

export function Anmeldeanbieter() {
  const qc = useQueryClient()
  const konfig = useOidcKonfig()
  const [bearbeite, setBearbeite] = useState<OidcAnbieter | 'neu' | null>(null)
  const [loesche, setLoesche] = useState<OidcAnbieter | null>(null)
  const [meldung, setMeldung] = useState('')
  const basis = konfig.data?.public_url ?? null

  return (
    <div className="flex flex-col gap-6">
      <Meldung text={meldung} />
      {konfig.isError && <Hinweis art="fehler">{fehlertext(konfig.error)}</Hinweis>}
      {konfig.isPending && <Ladezustand text={t('verwaltung.laden')} />}
      {konfig.data && (
        <>
          <PublicUrl wert={konfig.data.public_url ?? ''} onGespeichert={() => setMeldung(t('verwaltung.publicUrlGespeichert'))} />
          <section aria-labelledby="anbieter-h" className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="anbieter-h" className="m-0 text-[1.0625rem] font-semibold">{t('verwaltung.anbieterTitel')}</h2>
              <Knopf art="haupt" onClick={() => { setMeldung(''); setBearbeite('neu') }}>{t('verwaltung.anbieterAnlegen')}</Knopf>
            </div>
            {konfig.data.providers.length === 0 && <Leerzustand text={t('verwaltung.anbieterLeer')} />}
            <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(min(100%,24rem),1fr))] gap-4 p-0">
              {konfig.data.providers.map((a) => (
                <Karte key={a.id} a={a} basis={basis} onMeldung={setMeldung} onBearbeiten={() => { setMeldung(''); setBearbeite(a) }} onLoeschen={() => { setMeldung(''); setLoesche(a) }} />
              ))}
            </ul>
          </section>
        </>
      )}

      <Dialog titel={t(bearbeite === 'neu' ? 'verwaltung.anbieterAnlegen' : 'verwaltung.anbieterBearbeitenTitel')} offen={bearbeite !== null} onClose={() => setBearbeite(null)}>
        {bearbeite && <AnbieterFormular anbieter={bearbeite === 'neu' ? null : bearbeite} onAbbrechen={() => setBearbeite(null)} onFertig={() => { setBearbeite(null); setMeldung(t('verwaltung.gespeichert')) }} />}
      </Dialog>
      <Rueckfrage titel={t('verwaltung.anbieterLoeschenTitel')} text={loesche ? t('verwaltung.anbieterLoeschenText', { name: loesche.label }) : ''} aktion={t('verwaltung.loeschen')}
        offen={loesche !== null} onClose={() => setLoesche(null)}
        onBestaetigen={async () => { await api.delete(`/auth/oidc/providers/${loesche!.id}`); await qc.invalidateQueries({ queryKey: schluessel.oidcKonfig }); setMeldung(t('verwaltung.anbieterGeloescht')) }} />
    </div>
  )
}
