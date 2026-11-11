import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import type { OidcAnbieter } from '../../api/typen'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'

// Die Kennung kommt aus der URL (PUT legt an oder ändert), darum nur beim Anlegen eine Eingabe. Ein leeres Secret wird nicht gesendet (D5): die API behält das alte.
export function AnbieterFormular({ anbieter, onFertig, onAbbrechen }: { anbieter: OidcAnbieter | null; onFertig: () => void; onAbbrechen: () => void }) {
  const qc = useQueryClient()
  const [kennung, setKennung] = useState('')
  const [issuer, setIssuer] = useState(anbieter?.issuer ?? '')
  const [clientId, setClientId] = useState(anbieter?.client_id ?? '')
  const [secret, setSecret] = useState('')
  const [bezeichnung, setBezeichnung] = useState(anbieter?.label ?? '')

  async function senden() {
    const id = anbieter?.id ?? kennung.trim()
    await api.put(`/auth/oidc/providers/${id}`, { issuer: issuer.trim(), client_id: clientId.trim(), ...(secret && { client_secret: secret }), ...(bezeichnung.trim() && { label: bezeichnung.trim() }) })
    await qc.invalidateQueries({ queryKey: schluessel.oidcKonfig })
    onFertig()
  }

  return (
    <>
      <Formular onSubmit={senden} absenden={t('verwaltung.speichern')}>
        {!anbieter && <Feld label={t('verwaltung.fKennung')} hinweis={t('verwaltung.fKennungHinweis')} value={kennung} onChange={(e) => setKennung(e.target.value)} required maxLength={32} pattern="[a-z0-9\-]{1,32}" autoComplete="off" spellCheck={false} />}
        <Feld label={t('verwaltung.fIssuer')} value={issuer} onChange={(e) => setIssuer(e.target.value)} required inputMode="url" autoComplete="off" spellCheck={false} />
        <Feld label={t('verwaltung.fClientId')} value={clientId} onChange={(e) => setClientId(e.target.value)} required autoComplete="off" spellCheck={false} />
        <Feld label={t('verwaltung.fSecret')} hinweis={t(anbieter ? 'verwaltung.fSecretBearbeiten' : 'verwaltung.fSecretNeu')} type="password" value={secret} onChange={(e) => setSecret(e.target.value)} required={!anbieter} autoComplete="new-password" />
        <Feld label={t('verwaltung.fBezeichnung')} value={bezeichnung} onChange={(e) => setBezeichnung(e.target.value)} maxLength={64} />
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}
