import { useState } from 'react'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'

// Ein Code aus der Authenticator-App; Fehler (auch ein 401 bei falschem Code) zeigt das Formular, ohne die Sitzung anzufassen.
export function CodeFormular({ label, hinweis, absenden, onCode, onAbbrechen }:
  { label: string; hinweis?: string; absenden: string; onCode: (code: string) => Promise<void>; onAbbrechen: () => void }) {
  const [code, setCode] = useState('')
  return (
    <div className="flex flex-col gap-3">
      <Formular absenden={absenden} onSubmit={() => onCode(code.trim())}>
        <Feld label={label} hinweis={hinweis} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" required className="num" value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, '').slice(0, 6))} />
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('konto.abbrechen')}</Knopf>
    </div>
  )
}
