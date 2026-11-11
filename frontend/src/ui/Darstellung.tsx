import { createContext, use, useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'

export type Wahl = 'auto' | 'hell' | 'dunkel'
const SCHLUESSEL = 'moni.darstellung' // einziger Eintrag im localStorage (D5)
const WAHLEN: readonly Wahl[] = ['auto', 'hell', 'dunkel']
const HELL = '(prefers-color-scheme: light)'

function lesen(): Wahl {
  try {
    const w = localStorage.getItem(SCHLUESSEL)
    return WAHLEN.find((x) => x === w) ?? 'auto'
  } catch {
    return 'auto'
  }
}

const Kontext = createContext<{ wahl: Wahl; setzen: (w: Wahl) => void } | null>(null)

export function DarstellungProvider({ children }: { children: ReactNode }) {
  const [wahl, setWahl] = useState<Wahl>(lesen)
  const [systemHell, setSystemHell] = useState(() => matchMedia(HELL).matches)

  useLayoutEffect(() => {
    const mq = matchMedia(HELL)
    const bei = () => setSystemHell(mq.matches)
    bei()
    mq.addEventListener('change', bei)
    return () => mq.removeEventListener('change', bei)
  }, [])

  const hell = wahl === 'auto' ? systemHell : wahl === 'hell'
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = hell ? 'light' : 'dark'
  }, [hell])

  const setzen = useCallback((w: Wahl) => {
    setWahl(w)
    try {
      localStorage.setItem(SCHLUESSEL, w)
    } catch {
      /* Komfortwert, ohne Speicher gilt die Wahl nur bis zum Neuladen */
    }
  }, [])

  const wert = useMemo(() => ({ wahl, setzen }), [wahl, setzen])
  return <Kontext value={wert}>{children}</Kontext>
}

export function useDarstellung() {
  const k = use(Kontext)
  if (!k) throw new Error('DarstellungProvider fehlt')
  return k
}
