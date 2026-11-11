import { QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { erzeugeQueryClient, schluessel } from '../api/abfragen'
import { ApiFehler, api, setzeCsrf } from '../api/client'
import type { Session, SitzungBenutzer, Stage } from '../api/typen'

const KOPPLUNG = 'Kopplungs-Token ungültig' // Kopplungsfehler, keine abgelaufene Sitzung (D8)

interface SitzungWert {
  stage: Stage
  user: SitzungBenutzer | null
  csrf: string
  laden: boolean
  abmelden: () => Promise<void>
}
const Kontext = createContext<SitzungWert | null>(null)

// Open-Redirect-Schutz: nur Pfade innerhalb dieser Anwendung.
export function sicheresZiel(weiter: string | null): string | null {
  return weiter && weiter.startsWith('/') && !weiter.startsWith('//') && !weiter.includes('\\') ? weiter : null
}

type Behandler = (fehler: unknown) => void

// Veränderlicher Stand außerhalb des Renderns: QueryCache-Handler und queryFn brauchen immer den aktuellen Wert.
class Zustand {
  stage: Stage = 'none'
  ort = ''
  private behandler: Behandler = () => {}
  merke(stage: Stage, ort: string) { this.stage = stage; this.ort = ort }
  setzeBehandler(b: Behandler) { this.behandler = b }
  behandeln = (e: unknown) => this.behandler(e)
}

export function SitzungProvider({ children }: { children: ReactNode }) {
  const [zustand] = useState(() => new Zustand())
  const [qc] = useState(() => erzeugeQueryClient(zustand.behandeln))
  return (
    <QueryClientProvider client={qc}>
      <Innen zustand={zustand}>{children}</Innen>
    </QueryClientProvider>
  )
}

function Innen({ zustand, children }: { zustand: Zustand; children: ReactNode }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { pathname, search } = useLocation()

  const q = useQuery({
    queryKey: schluessel.sitzung,
    queryFn: async (): Promise<Session | null> => {
      try {
        const s = await api.get<Session>('/auth/session')
        setzeCsrf(s.csrf_token)
        return s
      } catch (e) {
        if (!(e instanceof ApiFehler) || e.status !== 401) throw e
        setzeCsrf('')
        if (zustand.stage === 'full') zustand.behandeln(e) // Sitzung ist serverseitig abgelaufen
        return null // 401 auf /auth/session heißt: keine Sitzung (Stage none)
      }
    },
  })

  const laden = q.isPending
  const stage: Stage = q.data?.stage ?? 'none'
  useEffect(() => { zustand.merke(stage, pathname + search) })

  const leeren = useCallback(() => {
    setzeCsrf('')
    // Alles verwerfen. Die Sitzungsabfrage bleibt als Hülle stehen (Wert null = Stage none), weil ihr Beobachter sonst an einer gelöschten Abfrage hängt.
    qc.setQueryData(schluessel.sitzung, null)
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== schluessel.sitzung[0] })
    qc.getMutationCache().clear()
    zustand.merke('none', zustand.ort)
  }, [qc, zustand])

  useEffect(() => {
    zustand.setzeBehandler((e) => {
      if (!(e instanceof ApiFehler) || e.status !== 401 || e.detail === KOPPLUNG || zustand.stage !== 'full') return
      const weiter = sicheresZiel(zustand.ort)
      leeren()
      void navigate(`/login?abgelaufen=1${weiter ? `&weiter=${encodeURIComponent(weiter)}` : ''}`, { replace: true })
    })
  }, [zustand, leeren, navigate])

  const abmelden = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } catch {
      /* die Sitzung wird lokal in jedem Fall verworfen */
    }
    leeren()
    void navigate('/login')
  }, [leeren, navigate])

  const wert = useMemo<SitzungWert>(
    () => ({ stage, user: q.data?.user ?? null, csrf: q.data?.csrf_token ?? '', laden, abmelden }),
    [stage, q.data, laden, abmelden],
  )
  return <Kontext value={wert}>{children}</Kontext>
}

export function useSitzung() {
  const k = use(Kontext)
  if (!k) throw new Error('SitzungProvider fehlt')
  return k
}
