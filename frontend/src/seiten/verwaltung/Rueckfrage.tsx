import { useRef, useState } from 'react'
import { Dialog } from '../../ui/Dialog'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { t } from '../../texte/de'
import { fehlertext } from './hilfen'

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
