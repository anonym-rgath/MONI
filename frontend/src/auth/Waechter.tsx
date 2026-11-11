import type { ReactNode } from 'react'
import { Navigate } from 'react-router'
import type { Stage } from '../api/typen'
import { t } from '../texte/de'
import { useSitzung } from './Sitzung'

const START: Record<Stage, string> = {
  none: '/login',
  mfa: '/login/zweiter-faktor',
  enroll: '/einrichtung/zwei-faktor',
  full: '/',
}

// Lässt nur durch, wenn die Sitzung genau in der verlangten Stage ist; sonst geht es zur passenden Seite der echten Stage.
export function Geschuetzt({ stage, rolle, children }: { stage: Stage; rolle?: 'admin'; children: ReactNode }) {
  const s = useSitzung()
  if (s.laden) return <p role="status">{t('waechter.laden')}</p>
  if (s.stage !== stage) return <Navigate to={START[s.stage]} replace />
  if (rolle && s.user?.role !== rolle) return <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{t('waechter.keineBerechtigung')}</h1>
  return children
}
