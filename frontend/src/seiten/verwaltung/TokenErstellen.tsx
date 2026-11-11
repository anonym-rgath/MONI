import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Einmalwerte } from '../../ui/Einmalwerte'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'
import { Auswahl } from '../alarme/felder'

// Formular für API-Token (mit Bereich) und Agent-Token (ohne). Das Klartext-Token lebt nur im Zustand dieser Komponente (D5):
// gcTime 0, kein setQueryData; die Liste wird nur ungültig gemacht. Beim Schließen verschwindet die Komponente samt Wert.
export function TokenErstellen({ pfad, mitBereich, titelNeu, onErstellt, onFertig, onAbbrechen }:
  { pfad: string; mitBereich: boolean; titelNeu: string; onErstellt: () => Promise<unknown>; onFertig: () => void; onAbbrechen: () => void }) {
  const [name, setName] = useState('')
  const [bereich, setBereich] = useState('read')
  const [tage, setTage] = useState('')
  const [wert, setWert] = useState<string | null>(null)
  const erstellen = useMutation({ mutationFn: (body: object) => api.post<{ token: string }>(pfad, body), gcTime: 0 })

  async function senden() {
    const res = await erstellen.mutateAsync({ name: name.trim(), ...(mitBereich && { scope: bereich }), ...(tage && { expires_days: Number(tage) }) })
    setWert(res.token)
    await onErstellt()
  }

  if (wert !== null) {
    return <Einmalwerte titel={titelNeu} werte={[wert]} onFertig={onFertig} bestaetigung={t('verwaltung.tokenBestaetigung')} dateiname={t('verwaltung.tokenDatei')} />
  }
  return (
    <>
    <Formular onSubmit={senden} absenden={t('verwaltung.tokenErstellen')}>
      <Feld label={t('verwaltung.fName')} value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} autoComplete="off" />
      {mitBereich && (
        <Auswahl label={t('verwaltung.fBereich')} value={bereich} onChange={(e) => setBereich(e.target.value)}>
          <option value="read">{t('verwaltung.bereichLesen')}</option>
          <option value="admin">{t('verwaltung.bereichAdmin')}</option>
        </Auswahl>
      )}
      <Feld label={t('verwaltung.fAblauf')} hinweis={t('verwaltung.fAblaufHinweis')} type="number" min={1} max={3650} value={tage} onChange={(e) => setTage(e.target.value)} />
      <p className="m-0 text-sm text-muted">{t('verwaltung.tokenHinweis')}</p>
    </Formular>
    <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}
