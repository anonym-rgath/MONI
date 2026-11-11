import { t } from './texte/de'
import { DarstellungProvider } from './ui/Darstellung'
import { Rahmen } from './ui/Rahmen'

// Platzhalter bis Task 2 (Sitzung, Router): Benutzer und Alarmzähler kommen dann vom Server.
export function App() {
  return (
    <DarstellungProvider>
      <Rahmen benutzer={{ name: t('rahmen.platzhalterName'), rolle: 'admin' }} offeneAlarme={0}>
        <h1 className="text-[1.625rem] leading-tight font-semibold">{t('rahmen.uebersicht')}</h1>
      </Rahmen>
    </DarstellungProvider>
  )
}
