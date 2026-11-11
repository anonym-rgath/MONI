import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Navigate, useSearchParams } from 'react-router'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import type { OidcAnbieter, Stage } from '../../api/typen'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { useTitel } from '../../ui/useTitel'
import { AnmeldeRahmen, Ueberschrift } from './AnmeldeRahmen'
import { useNachSchritt } from './weiter'

const linkKlasse = 'flex min-h-11 items-center justify-center rounded-lg border border-control font-medium'

export function Login() {
  useTitel(t('seiten.login'))
  const [params] = useSearchParams()
  const nachSchritt = useNachSchritt()
  const [email, setEmail] = useState('')
  const [passwort, setPasswort] = useState('')
  const setup = useQuery({ queryKey: schluessel.setup, retry: false, queryFn: () => api.get<{ required: boolean }>('/setup') })
  const oidc = useQuery({ queryKey: schluessel.oidcAnbieter, retry: false, queryFn: () => api.get<{ providers: OidcAnbieter[] }>('/auth/oidc') })

  if (setup.data?.required) return <Navigate to="/einrichtung" replace />
  const anbieter = oidc.data?.providers ?? []

  return (
    <AnmeldeRahmen>
      <Ueberschrift>{t('seiten.login')}</Ueberschrift>
      <div className="mb-4.5 flex flex-col gap-3">
        {params.get('fehler') === 'oidc' && <Hinweis art="fehler">{t('anmeldung.fehlerOidc')}</Hinweis>}
        {params.get('abgelaufen') === '1' && <Hinweis>{t('anmeldung.abgelaufen')}</Hinweis>}
      </div>
      {setup.isPending ? (
        <p role="status">{t('waechter.laden')}</p>
      ) : (
        <div className="flex flex-col gap-4.5">
          <Formular
            absenden={t('anmeldung.weiter')}
            onSubmit={async () => {
              // Zugangsdaten gehen direkt über den Client, nie durch Mutations-Cache (D5)
              const { stage } = await api.post<{ stage: Exclude<Stage, 'none'> }>('/auth/login', { email, password: passwort })
              await nachSchritt(stage)
            }}
          >
            <Feld label={t('anmeldung.email')} type="email" autoComplete="username" spellCheck={false} required value={email} onChange={(e) => setEmail(e.target.value)} />
            <Feld label={t('anmeldung.passwort')} type="password" autoComplete="current-password" required value={passwort} onChange={(e) => setPasswort(e.target.value)} />
          </Formular>
          {anbieter.length > 0 && (
            <>
              <div aria-hidden="true" className="flex items-center gap-3 text-[0.8125rem] text-muted">
                <span className="h-px flex-1 bg-border" />{t('anmeldung.oder')}<span className="h-px flex-1 bg-border" />
              </div>
              {anbieter.map((a) => (
                <a key={a.id} href={`/api/v1/auth/oidc/start?provider=${encodeURIComponent(a.id)}`} className={linkKlasse}>
                  {t('anmeldung.mitAnbieter', { label: a.label })}
                </a>
              ))}
            </>
          )}
        </div>
      )}
    </AnmeldeRahmen>
  )
}
