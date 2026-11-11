import { useId, useState } from 'react'
import { t } from '../texte/de'
import { Hinweis } from './Hinweis'
import { Knopf } from './Knopf'

// Einmalwerte liegen nur in den Props/im Zustand dieses Baums (D5): kein Cache, kein Speicher, keine Logs.
export function Einmalwerte({ titel, werte, onFertig }: { titel: string; werte: string[]; onFertig: () => void }) {
  const [bestaetigt, setBestaetigt] = useState(false)
  const [meldung, setMeldung] = useState('')
  const box = useId()

  async function kopieren() {
    try {
      await navigator.clipboard.writeText(werte.join('\n'))
      setMeldung(t('einmalwerte.kopiert'))
    } catch {
      setMeldung(t('einmalwerte.kopierenFehlt')) // fehlende API oder abgelehnte Berechtigung
    }
  }

  function speichern() {
    const url = URL.createObjectURL(new Blob([werte.join('\n') + '\n'], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = t('einmalwerte.dateiname')
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section className="flex flex-col gap-4.5">
      <h2 className="text-[1.0625rem] font-semibold">{titel}</h2>
      <ul className="num grid grid-cols-1 gap-1.5 rounded-lg bg-bg p-3 text-lg sm:grid-cols-2">
        {werte.map((w) => <li key={w}>{w}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Knopf onClick={() => void kopieren()}>{t('einmalwerte.kopieren')}</Knopf>
        <Knopf onClick={speichern}>{t('einmalwerte.speichern')}</Knopf>
      </div>
      {meldung && <Hinweis>{meldung}</Hinweis>}
      <div className="flex items-center gap-2.5">
        <input id={box} type="checkbox" checked={bestaetigt} onChange={(e) => setBestaetigt(e.target.checked)} className="size-5 accent-accent" />
        <label htmlFor={box}>{t('einmalwerte.bestaetigung')}</label>
      </div>
      <Knopf art="haupt" disabled={!bestaetigt} onClick={onFertig}>{t('einmalwerte.weiter')}</Knopf>
    </section>
  )
}
