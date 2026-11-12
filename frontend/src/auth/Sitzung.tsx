import { QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { erzeugeQueryClient, schluessel } from '../api/abfragen'
import { ApiFehler, api, setzeAblauf, setzeCsrf } from '../api/client'
import type { Session, SitzungBenutzer, Stage } from '../api/typen'

// Warum die Sitzung hier endete: abgelaufen führt mit Hinweis und Rücksprungziel zur Anmeldung, abgemeldet ohne.
type Ende = 'abgelaufen' | 'abgemeldet'

interface SitzungWert {
  stage: Stage
  user: SitzungBenutzer | null
  csrf: string
  laden: boolean
  fehler: ApiFehler | null // erster Abruf der Sitzung scheiterte anders als mit 401
  ende: Ende | null
  erneutVersuchen: () => void
  abmelden: () => Promise<void>
}
const ABMELDE_FRIST_MS = 10_000
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

export function SitzungProvider({ children }: { children: ReactNode }) {
  const [qc] = useState(() => erzeugeQueryClient())
  return (
    <QueryClientProvider client={qc}>
      <Innen>{children}</Innen>
    </QueryClientProvider>
  )
}

function Innen({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
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
        return null // 401 auf /auth/session heißt: keine Sitzung (Stage none); einen Ablauf meldet der Client selbst
      }
    },
  })

  const laden = q.isPending
  const stage: Stage = q.data?.stage ?? 'none'
  const [ende, setEnde] = useState<Ende | null>(null)
  const [vorher, setVorher] = useState(stage)
  if (stage !== vorher) { // Zustand aus dem vorigen Render: der Grund gilt bis zur nächsten Anmeldung
    setVorher(stage)
    if (stage !== 'none') setEnde(null)
  }
  const aktiv = useRef(stage) // für den Ablauf-Rückruf außerhalb des Renderns
  useEffect(() => { aktiv.current = stage })

  // Einzige Navigationsquelle ist der Wächter: erst den Grund festschreiben, dann Stage none; so baut er das Ziel in einem Schritt.
  const beenden = useCallback((grund: Ende) => {
    flushSync(() => setEnde(grund))
    setzeCsrf('')
    // Alles verwerfen. Die Sitzungsabfrage bleibt als Hülle stehen (Wert null = Stage none), weil ihr Beobachter sonst an einer gelöschten Abfrage hängt.
    qc.setQueryData(schluessel.sitzung, null)
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== schluessel.sitzung[0] })
    qc.getMutationCache().clear()
    aktiv.current = 'none'
  }, [qc])

  const abmeldend = useRef(false) // ein 401 während des Abmeldens (auch auf den Logout selbst) ist kein Ablauf
  useEffect(() => {
    setzeAblauf(() => { if (aktiv.current !== 'none' && !abmeldend.current) beenden('abgelaufen') })
    return () => setzeAblauf(() => {})
  }, [beenden])

  const abmelden = useCallback(async () => {
    abmeldend.current = true
    const abbruch = new AbortController()
    const frist = setTimeout(() => abbruch.abort(), ABMELDE_FRIST_MS)
    try {
      await api.post('/auth/logout', undefined, { signal: abbruch.signal })
    } catch {
      /* die Sitzung wird lokal in jedem Fall verworfen */
    } finally {
      clearTimeout(frist)
      abmeldend.current = false
    }
    beenden('abgemeldet')
  }, [beenden])

  const wert = useMemo<SitzungWert>(
    () => ({
      stage,
      user: q.data?.user ?? null,
      csrf: q.data?.csrf_token ?? '',
      laden,
      fehler: q.data === undefined && q.error instanceof ApiFehler ? q.error : null,
      ende,
      erneutVersuchen: () => void q.refetch(),
      abmelden,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- q.refetch ist stabil, q selbst wechselt bei jedem Render
    [stage, q.data, q.error, laden, ende, abmelden],
  )
  return <Kontext value={wert}>{children}</Kontext>
}

export function useSitzung() {
  const k = use(Kontext)
  if (!k) throw new Error('SitzungProvider fehlt')
  return k
}
