import { t, type TextSchluessel } from '../texte/de'
import { useTitel } from '../ui/useTitel'

// Platzhalter bis zur jeweiligen Seite (spätere Tasks ersetzen sie); Titel nach dem Schema „Bereich – Moni“ (D9).
export function Platzhalter({ titel }: { titel: TextSchluessel }) {
  const text = t(titel)
  useTitel(text)
  return <h1 tabIndex={-1} className="text-[1.625rem] leading-tight font-semibold">{text}</h1>
}
