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
// offen: die Seite hält ungespeicherte Einmalwerte und bleibt trotz Stage-Wechsel stehen (verlassen nur über ihren eigenen Weg).
export function Geschuetzt({ stage, rolle, offen = false, children }: { stage: Stage; rolle?: 'admin'; offen?: boolean; children: ReactNode }) {
  const s = useSitzung()
  const { pathname, search } = useLocation()
  if (offen) return children
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
    if (s.stage === 'none' && s.ende === 'abgemeldet') return <Navigate to={START.none} replace />
    // Tiefenlink merken; auf einer Anmeldeseite ist das ihr eigenes weiter, nicht die Seite selbst
    const tiefenlink = s.stage === 'none' ? (stage === 'full' ? sicheresZiel(pathname + search) : weiter) : null
    const behalten = tiefenlink ?? (s.stage === 'none' || s.stage === 'full' ? null : weiter)
    const abfrage = [s.stage === 'none' && s.ende === 'abgelaufen' ? 'abgelaufen=1' : '', behalten ? `weiter=${encodeURIComponent(behalten)}` : ''].filter(Boolean).join('&')
    return <Navigate to={abfrage ? `${START[s.stage]}?${abfrage}` : START[s.stage]} replace />
  }
  if (rolle && s.user?.role !== rolle) return <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{t('waechter.keineBerechtigung')}</h1>
  return children
}
