import { useRef, useState } from 'react'
import { ApiFehler } from '../api/client'
import { t } from '../texte/de'
import { Dialog } from './Dialog'
import { Hinweis } from './Hinweis'
import { Knopf } from './Knopf'

// Gemeinsame Helfer für Ergebnisse von Aktionen: Fehlertext, Statusmeldung und Rückfrage.

// API-Texte (detail) erscheinen unverändert (D12); alles andere bekommt den allgemeinen Text.
export const fehlertext = (e: unknown) => (e instanceof ApiFehler ? e.detail : t('fehler.allgemein'))

// Ergebnis einer Aktion; die Region steht immer im DOM, damit Screenreader den neuen Text ansagen (D11).
export function Meldung({ text }: { text: string }) {
  return <div role="status">{text && <p className="m-0 rounded-lg bg-surface-2 px-3 py-2.5 text-sm">{text}</p>}</div>
}


// Der Inhalt allein, wenn der Dialog schon da ist (Agent-Tokens wechseln Ansichten in einem Dialog).
export function RueckfrageInhalt({ text, aktion, onClose, onBestaetigen }: { text: string; aktion: string; onClose: () => void; onBestaetigen: () => Promise<void> }) {
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const sperre = useRef(false) // wie im Formular: fängt auch zwei Klicks im selben Tick
  async function los() {
    if (sperre.current) return
    sperre.current = true
    setLaeuft(true)
    setFehler(null)
    try {
      await onBestaetigen()
      onClose()
    } catch (e) {
      setFehler(fehlertext(e))
      setLaeuft(false)
      sperre.current = false
    }
  }
  return (
    <>
      <p className="m-0">{text}</p>
      {fehler && <Hinweis art="fehler">{fehler}</Hinweis>}
      <div className="flex flex-wrap gap-2">
        <Knopf art="haupt" disabled={laeuft} onClick={() => void los()}>{aktion}</Knopf>
        <Knopf onClick={onClose}>{t('verwaltung.abbrechen')}</Knopf>
      </div>
    </>
  )
}

// Rückfrage vor Aktionen mit Folgen. Fehler der API (etwa der geschützte letzte Admin) stehen im Dialog; er schließt erst bei Erfolg.
export function Rueckfrage({ titel, text, aktion, offen, onClose, onBestaetigen }:
  { titel: string; text: string; aktion: string; offen: boolean; onClose: () => void; onBestaetigen: () => Promise<void> }) {
  return (
    <Dialog titel={titel} offen={offen} onClose={onClose}>
      <RueckfrageInhalt text={text} aktion={aktion} onClose={onClose} onBestaetigen={onBestaetigen} />
    </Dialog>
  )
}
