import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router'
import { schluessel } from '../../api/abfragen'
import { sicheresZiel } from '../../auth/Sitzung'
import type { Stage } from '../../api/typen'

const START = { mfa: '/login/zweiter-faktor', enroll: '/einrichtung/zwei-faktor' } as const

// Nach jedem Schritt: Sitzung neu laden, dann zur Seite der neuen Stage; `weiter` bleibt über die Schritte erhalten.
export function useNachSchritt() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const weiter = sicheresZiel(params.get('weiter'))
  return async (stage: Exclude<Stage, 'none'>) => {
    await qc.invalidateQueries({ queryKey: schluessel.sitzung })
    const ziel = stage === 'full' ? (weiter ?? '/') : `${START[stage]}${weiter ? `?weiter=${encodeURIComponent(weiter)}` : ''}`
    void navigate(ziel, { replace: true })
  }
}
