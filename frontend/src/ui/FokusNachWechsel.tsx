import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router'

// D9: Nach einem Routenwechsel (nicht beim ersten Laden) bekommt die H1 der neuen Seite den Fokus.
export function FokusNachWechsel() {
  const { pathname } = useLocation()
  const vorher = useRef(pathname)
  useEffect(() => {
    if (vorher.current === pathname) return
    vorher.current = pathname
    document.querySelector<HTMLElement>('h1[tabindex="-1"]')?.focus()
  }, [pathname])
  return null
}
