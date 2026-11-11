import { useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ApiFehler } from '../api/client'
import { t } from '../texte/de'
import { Hinweis } from './Hinweis'
import { Knopf } from './Knopf'

// Sperrt den Absende-Knopf während der Anfrage, zeigt ApiFehler.detail oben; die Eingaben gehören den Kindern und bleiben stehen.
export function Formular({ onSubmit, absenden, children }: { onSubmit: () => Promise<void> | void; absenden: string; children: ReactNode }) {
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const sperre = useRef(false) // schneller als State: fängt auch zwei Absendungen im selben Tick

  async function senden(e: FormEvent) {
    e.preventDefault()
    if (sperre.current) return
    sperre.current = true
    setLaeuft(true)
    setFehler(null)
    try {
      await onSubmit()
    } catch (err) {
      setFehler(err instanceof ApiFehler ? err.detail : t('fehler.allgemein'))
    } finally {
      sperre.current = false
      setLaeuft(false)
    }
  }

  return (
    <form onSubmit={(e) => void senden(e)} className="flex flex-col gap-4.5">
      {fehler && <Hinweis art="fehler">{fehler}</Hinweis>}
      {children}
      <Knopf art="haupt" type="submit" disabled={laeuft}>{laeuft ? t('formular.wirdGesendet') : absenden}</Knopf>
    </form>
  )
}
