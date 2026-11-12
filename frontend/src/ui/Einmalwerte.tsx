import { useEffect, useId, useRef, useState } from 'react'
import { t } from '../texte/de'
import { Knopf } from './Knopf'

// Einmalwerte liegen nur in den Props/im Zustand dieses Baums (D5): kein Cache, kein Speicher, keine Logs.
// bestaetigung und dateiname passen den Text an andere Werte als Wiederherstellungscodes an (etwa Tokens).
export function Einmalwerte({ titel, werte, onFertig, bestaetigung = t('einmalwerte.bestaetigung'), dateiname = t('einmalwerte.dateiname') }:
  { titel: string; werte: string[]; onFertig: () => void; bestaetigung?: string; dateiname?: string }) {
  const [bestaetigt, setBestaetigt] = useState(false)
  const [meldung, setMeldung] = useState('')
  const box = useId()
  const titelRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => titelRef.current?.focus(), []) // Wechsel der Ansicht ansagen

  // Erst leeren, dann setzen: so sagt der Screenreader auch ein zweites Kopieren an.
  function melden(text: string) {
    setMeldung('')
    setTimeout(() => setMeldung(text), 50)
  }

  async function kopieren() {
    try {
      await navigator.clipboard.writeText(werte.join('\n'))
      melden(t('einmalwerte.kopiert'))
    } catch {
      melden(t('einmalwerte.kopierenFehlt')) // fehlende API oder abgelehnte Berechtigung
    }
  }

  function speichern() {
    const url = URL.createObjectURL(new Blob([werte.join('\n') + '\n'], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = dateiname
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 0) // nach dem Klick, sonst kann der Download abbrechen
  }

  return (
    <section className="flex flex-col gap-4.5">
      <h2 ref={titelRef} tabIndex={-1} className="text-[1.0625rem] font-semibold">{titel}</h2>
      <ul className="num grid grid-cols-1 gap-1.5 rounded-lg bg-bg p-3 text-lg sm:grid-cols-2">
        {werte.map((w) => <li key={w}>{w}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Knopf onClick={() => void kopieren()}>{t('einmalwerte.kopieren')}</Knopf>
        <Knopf onClick={speichern}>{t('einmalwerte.speichern')}</Knopf>
      </div>
      <p role="status" className="min-h-5 text-sm">{meldung}</p>
      <div className="flex items-center gap-2.5">
        <input id={box} type="checkbox" checked={bestaetigt} onChange={(e) => setBestaetigt(e.target.checked)} className="size-5 accent-accent" />
        <label htmlFor={box}>{bestaetigung}</label>
      </div>
      <Knopf art="haupt" disabled={!bestaetigt} onClick={onFertig}>{t('einmalwerte.weiter')}</Knopf>
    </section>
  )
}
