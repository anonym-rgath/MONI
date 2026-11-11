import { useState } from 'react'
import { api } from '../../api/client'
import { useSitzung } from '../../auth/Sitzung'
import { t } from '../../texte/de'
import { useDarstellung, type Wahl } from '../../ui/Darstellung'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { useTitel } from '../../ui/useTitel'
import { ZweiFaktorKonto } from './ZweiFaktorKonto'

const karte = 'flex flex-col gap-4 rounded-xl border border-border bg-surface p-5'
const WAHLEN: { wahl: Wahl; text: 'rahmen.auto' | 'rahmen.hell' | 'rahmen.dunkel' }[] = [
  { wahl: 'auto', text: 'rahmen.auto' },
  { wahl: 'hell', text: 'rahmen.hell' },
  { wahl: 'dunkel', text: 'rahmen.dunkel' },
]

export function Konto() {
  useTitel(t('seiten.konto'))
  const { user } = useSitzung()
  if (!user) return null
  const oidc = user.kind === 'oidc'
  return (
    <>
      <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{t('seiten.konto')}</h1>
      <section aria-labelledby="konto-profil" className={karte}>
        <h2 id="konto-profil" className="text-[1.0625rem] font-semibold">{t('konto.profil')}</h2>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2">
          <dt className="text-muted">{t('konto.name')}</dt><dd>{user.name}</dd>
          <dt className="text-muted">{t('konto.email')}</dt><dd>{user.email}</dd>
          <dt className="text-muted">{t('konto.rolle')}</dt><dd>{t(user.role === 'admin' ? 'rolle.admin' : 'rolle.betrachter')}</dd>
          <dt className="text-muted">{t('konto.art')}</dt><dd>{t(oidc ? 'konto.artOidc' : 'konto.artLokal')}</dd>
        </dl>
      </section>
      {oidc ? (
        <Hinweis>{t('konto.oidcHinweis', { anbieter: t('konto.anbieterAllgemein') })}</Hinweis>
      ) : (
        <>
          <Passwort />
          <ZweiFaktorKonto aktiv={user.totp_enabled} />
        </>
      )}
      <Darstellung />
    </>
  )
}

function Passwort() {
  const [aktuell, setAktuell] = useState('')
  const [neu, setNeu] = useState('')
  const [fertig, setFertig] = useState(false)
  return (
    <section aria-labelledby="konto-passwort" className={karte}>
      <h2 id="konto-passwort" className="text-[1.0625rem] font-semibold">{t('konto.passwortTitel')}</h2>
      {fertig && <Hinweis>{t('konto.passwortGeaendert')}</Hinweis>}
      <Formular
        absenden={t('konto.passwortTitel')}
        onSubmit={async () => {
          setFertig(false)
          // kein useMutation: ein falsches aktuelles Passwort antwortet mit 401, der globale Handler würde abmelden
          await api.post('/account/password', { current_password: aktuell, new_password: neu })
          setAktuell('')
          setNeu('')
          setFertig(true)
        }}
      >
        <Feld label={t('konto.passwortAktuell')} type="password" autoComplete="current-password" required value={aktuell} onChange={(e) => setAktuell(e.target.value)} />
        <Feld label={t('konto.passwortNeu')} type="password" autoComplete="new-password" required hinweis={t('konto.passwortHinweis')} value={neu} onChange={(e) => setNeu(e.target.value)} />
      </Formular>
    </section>
  )
}

function Darstellung() {
  const { wahl, setzen } = useDarstellung()
  return (
    <fieldset className={karte}>
      <legend className="px-1 text-[1.0625rem] font-semibold">{t('konto.darstellungTitel')}</legend>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {WAHLEN.map((o) => (
          <label key={o.wahl} className="flex min-h-11 cursor-pointer items-center gap-2">
            <input type="radio" name="darstellung" checked={wahl === o.wahl} onChange={() => setzen(o.wahl)} className="size-5 accent-accent" />
            {t(o.text)}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
