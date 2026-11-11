import { useEffect } from 'react'
import { t, type TextSchluessel } from '../texte/de'

// Platzhalter bis zur jeweiligen Seite (spätere Tasks ersetzen sie); Titel nach dem Schema „Bereich – Moni“ (D9).
export function Platzhalter({ titel }: { titel: TextSchluessel }) {
  const text = t(titel)
  useEffect(() => { document.title = `${text} – ${t('app.titel')}` }, [text])
  return <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{text}</h1>
}
