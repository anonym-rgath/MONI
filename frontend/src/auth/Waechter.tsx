import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import type { Stage } from '../api/typen'
import { t } from '../texte/de'
import { sicheresZiel, useSitzung } from './Sitzung'

const START: Record<Stage, string> = {
  none: '/login',
  mfa: '/login/zweiter-faktor',
  enroll: '/einrichtung/zwei-faktor',
  full: '/',
}

// Lässt nur durch, wenn die Sitzung genau in der verlangten Stage ist; sonst geht es zur passenden Seite der echten Stage.
export function Geschuetzt({ stage, rolle, children }: { stage: Stage; rolle?: 'admin'; children: ReactNode }) {
  const s = useSitzung()
  const { pathname, search } = useLocation()
  if (s.laden) return <p role="status">{t('waechter.laden')}</p>
  if (s.fehler) {
    return (
      <div role="alert">
        <p>{s.fehler.detail}</p>
        <button type="button" onClick={s.erneutVersuchen} className="min-h-10 cursor-pointer rounded-lg border border-control px-3 text-sm">{t('waechter.erneutVersuchen')}</button>
      </div>
    )
  }
  if (s.stage !== stage) {
    const weiter = sicheresZiel(new URLSearchParams(search).get('weiter')) // Rücksprungziel der Anmeldeseiten überlebt jeden Stage-Wechsel
    if (s.stage === 'full' && weiter) return <Navigate to={weiter} replace />
    const tiefenlink = s.stage === 'none' ? sicheresZiel(pathname + search) : null // Tiefenlink merken
    const behalten = tiefenlink ?? (s.stage === 'none' || s.stage === 'full' ? null : weiter)
    return <Navigate to={behalten ? `${s.stage === 'none' ? START.none : START[s.stage]}?weiter=${encodeURIComponent(behalten)}` : START[s.stage]} replace />
  }
  if (rolle && s.user?.role !== rolle) return <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{t('waechter.keineBerechtigung')}</h1>
  return children
}
