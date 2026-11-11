import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import { useSitzung } from '../../auth/Sitzung'
import type { ApiRolle, Benutzer } from '../../api/typen'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'
import { Auswahl } from '../alarme/felder'

function Rollen({ wert, onChange }: { wert: ApiRolle; onChange: (r: ApiRolle) => void }) {
  return (
    <Auswahl label={t('verwaltung.fRolle')} value={wert} onChange={(e) => onChange(e.target.value as ApiRolle)}>
      <option value="viewer">{t('verwaltung.rolleBetrachter')}</option>
      <option value="admin">{t('verwaltung.rolleAdmin')}</option>
    </Auswahl>
  )
}

// Startpasswort nur für lokale Konten; die Rolle beginnt beim geringeren Recht.
export function BenutzerAnlegen({ onFertig, onAbbrechen }: { onFertig: () => void; onAbbrechen: () => void }) {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [rolle, setRolle] = useState<ApiRolle>('viewer')
  const [konto, setKonto] = useState<'local' | 'oidc'>('local')
  const [passwort, setPasswort] = useState('')
  async function senden() {
    await api.post('/users', { email: email.trim(), name: name.trim(), role: rolle, kind: konto, ...(konto === 'local' && { password: passwort }) })
    await qc.invalidateQueries({ queryKey: schluessel.benutzer })
    onFertig()
  }
  return (
    <>
      <Formular onSubmit={senden} absenden={t('verwaltung.benutzerAnlegen')}>
        <Feld label={t('verwaltung.fEmail')} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
        <Feld label={t('verwaltung.fName')} value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        <Rollen wert={rolle} onChange={setRolle} />
        <Auswahl label={t('verwaltung.fKonto')} value={konto} onChange={(e) => setKonto(e.target.value as 'local' | 'oidc')}>
          <option value="local">{t('verwaltung.artLokal')}</option>
          <option value="oidc">{t('verwaltung.artOidc')}</option>
        </Auswahl>
        {konto === 'local' && <Feld label={t('verwaltung.fPasswort')} hinweis={t('verwaltung.fPasswortHinweis')} type="password" value={passwort} onChange={(e) => setPasswort(e.target.value)} required autoComplete="new-password" />}
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}

export function RolleAendern({ benutzer, onFertig, onAbbrechen }: { benutzer: Benutzer; onFertig: () => void; onAbbrechen: () => void }) {
  const qc = useQueryClient()
  const { user } = useSitzung()
  const [rolle, setRolle] = useState<ApiRolle>(benutzer.role)
  async function senden() {
    if (rolle !== benutzer.role) {
      await api.patch(`/users/${benutzer.id}`, { role: rolle })
      await qc.invalidateQueries({ queryKey: schluessel.benutzer })
      if (benutzer.id === user?.id) await qc.invalidateQueries({ queryKey: schluessel.sitzung }) // eigene Rolle: Navigation und Rechte sofort anpassen
    }
    onFertig()
  }
  return (
    <>
      <Formular onSubmit={senden} absenden={t('verwaltung.speichern')}>
        <Rollen wert={rolle} onChange={setRolle} />
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}

export function PasswortSetzen({ benutzer, onFertig, onAbbrechen }: { benutzer: Benutzer; onFertig: () => void; onAbbrechen: () => void }) {
  const [passwort, setPasswort] = useState('')
  async function senden() {
    await api.post(`/users/${benutzer.id}/password`, { password: passwort })
    onFertig()
  }
  return (
    <>
      <p className="m-0">{t('verwaltung.passwortText', { name: benutzer.name || benutzer.email })}</p>
      <Formular onSubmit={senden} absenden={t('verwaltung.passwort')}>
        <Feld label={t('verwaltung.fPasswortNeu')} hinweis={t('verwaltung.fPasswortHinweis')} type="password" value={passwort} onChange={(e) => setPasswort(e.target.value)} required autoComplete="new-password" />
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}
