import { useEffect, useState } from 'react'
import { t } from '../texte/de'

// Dauer in der größten passenden Einheit ("12 min"); negative Werte (Uhrenabweichung) zählen als 0.
export function dauer(sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden))
  if (s < 60) return t('zeit.sekunden', { n: s })
  if (s < 3600) return t('zeit.minuten', { n: Math.floor(s / 60) })
  if (s < 86400) return t('zeit.stunden', { n: Math.floor(s / 3600) })
  const tage = Math.floor(s / 86400)
  return tage === 1 ? t('zeit.tag') : t('zeit.tage', { n: tage })
}
export const vor = (sekunden: number) => t('zeit.vor', { dauer: dauer(sekunden) })

// Sekundentakt nur für die Anzeige; löst keine Abfrage aus.
export function useJetzt(): number {
  const [jetzt, setJetzt] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setJetzt(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return jetzt
}

export const sekundenSeit = (ms: number, jetzt: number) => (jetzt - ms) / 1000

// Lokale Zeit im Format de-DE; nur die Uhrzeit, wenn es heute ist.
export function lokal(ms: number, jetzt: number): string {
  const heute = new Date(jetzt).toDateString() === new Date(ms).toDateString()
  return new Intl.DateTimeFormat('de-DE', heute ? { timeStyle: 'short' } : { dateStyle: 'short', timeStyle: 'short' }).format(ms)
}
