import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router'

const H1 = 'h1[tabindex="-1"]'

// D9: Nach einem Routenwechsel (nicht beim ersten Laden) bekommt die H1 der neuen Seite den Fokus.
// Lädt die Seite nach (React.lazy), steht zuerst nur der Ladezustand im DOM; dann wird auf die H1 gewartet.
export function FokusNachWechsel() {
  const { pathname } = useLocation()
  const vorher = useRef(pathname)
  useEffect(() => {
    if (vorher.current === pathname) return
    vorher.current = pathname
    const fokus = () => { const h = document.querySelector<HTMLElement>(H1); h?.focus(); return Boolean(h) }
    if (fokus()) return
    const beobachter = new MutationObserver(() => { if (fokus()) beobachter.disconnect() })
    beobachter.observe(document.body, { childList: true, subtree: true })
    return () => beobachter.disconnect() // nächster Wechsel oder Abbau beendet das Warten
  }, [pathname])
  return null
}
