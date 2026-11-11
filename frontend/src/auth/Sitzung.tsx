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
  fehler: ApiFehler | null // erster Abruf der Sitzung scheiterte anders als mit 401
  erneutVersuchen: () => void
  abmelden: () => Promise<void>
}
const Kontext = createContext<SitzungWert | null>(null)

// Open-Redirect-Schutz: nur Pfade innerhalb dieser Anwendung.
// Steuerzeichen (Tab, CR, LF) entfernt der URL-Parser still; so würde aus "/\t/evil" ein fremdes Ziel.
// eslint-disable-next-line no-control-regex
const UNZULAESSIG = /[\u0000-\u001F\u007F\\]/
export function sicheresZiel(weiter: string | null): string | null {
  if (!weiter || !weiter.startsWith('/') || UNZULAESSIG.test(weiter)) return null
  try {
    const u = new URL(weiter, location.origin)
    const ziel = u.pathname + u.search + u.hash
    // "/.//evil" wird zu "//evil": nach dem Normalisieren darf kein protokollrelatives Ziel übrig sein.
    return u.origin === location.origin && ziel.startsWith('/') && !/^\/[/\\]/.test(ziel) ? ziel : null
  } catch {
    return null
  }
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
    retry: false, // Fehler sofort zeigen, Wiederholen übernimmt der Knopf
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
    () => ({
      stage,
      user: q.data?.user ?? null,
      csrf: q.data?.csrf_token ?? '',
      laden,
      fehler: q.data === undefined && q.error instanceof ApiFehler ? q.error : null,
      erneutVersuchen: () => void q.refetch(),
      abmelden,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- q.refetch ist stabil, q selbst wechselt bei jedem Render
    [stage, q.data, q.error, laden, abmelden],
  )
  return <Kontext value={wert}>{children}</Kontext>
}

export function useSitzung() {
  const k = use(Kontext)
  if (!k) throw new Error('SitzungProvider fehlt')
  return k
}
