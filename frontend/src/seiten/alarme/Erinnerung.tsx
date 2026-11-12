import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useErinnerung } from '../../api/abfragen'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { fehlertext, Meldung } from '../../ui/Meldungen'

// Nur auf der Kanäle-Seite (Admins): GET und PUT /alert-settings sind Admin-Endpunkte.
export function Erinnerung() {
  const qc = useQueryClient()
  const einstellung = useErinnerung()
  const [eingabe, setEingabe] = useState<string | null>(null)
  const [meldung, setMeldung] = useState('')
  const wert = eingabe ?? (einstellung.data ? String(einstellung.data.remind_minutes) : '')

  async function senden() {
    setMeldung('')
    await api.put('/alert-settings', { remind_minutes: Number(wert) })
    await qc.invalidateQueries({ queryKey: schluessel.erinnerung })
    setEingabe(null)
    setMeldung(t('alarme.erinnerungGespeichert'))
  }

  return (
    <section className="flex max-w-lg flex-col gap-3">
      {einstellung.isError && <Hinweis art="fehler">{fehlertext(einstellung.error)}</Hinweis>}
      <Formular onSubmit={senden} absenden={t('alarme.erinnerungSpeichern')}>
        <Feld label={t('alarme.erinnerung')} hinweis={t('alarme.erinnerungHinweis')} type="number" inputMode="numeric" min={0} max={10080} step={1}
          value={wert} onChange={(e) => { setEingabe(e.target.value); setMeldung('') }} disabled={einstellung.isPending} required />
      </Formular>
      <Meldung text={meldung} />
    </section>
  )
}
