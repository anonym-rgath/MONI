import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Kanal, Schweregrad } from '../../api/typen'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Auswahl, Bereich, Haken, zeilen } from './felder'

const text = (v: unknown, standard = '') => (typeof v === 'string' ? v : standard)

// Geheimnisse (URL, HMAC-Secret, Passwort) werden nie vorbelegt und nur gesendet, wenn das Feld etwas enthält (D5).
export function KanalFormular({ kanal, onFertig }: { kanal: Kanal | null; onFertig: () => void }) {
  const qc = useQueryClient()
  const c = kanal?.config ?? {}
  const [art, setArt] = useState<'webhook' | 'email'>(kanal?.kind === 'email' ? 'email' : 'webhook')
  const [name, setName] = useState(kanal?.name ?? '')
  const [url, setUrl] = useState('')
  const [hmac, setHmac] = useState('')
  const [host, setHost] = useState(text(c.host))
  const [port, setPort] = useState(String(typeof c.port === 'number' ? c.port : 587))
  const [sicherheit, setSicherheit] = useState(c.security === 'tls' ? 'tls' : 'starttls')
  const [benutzer, setBenutzer] = useState(text(c.username))
  const [passwort, setPasswort] = useState('')
  const [absender, setAbsender] = useState(text(c.from_addr))
  const [an, setAn] = useState(Array.isArray(c.to) ? c.to.join('\n') : '')
  const [ab, setAb] = useState<Schweregrad>(kanal?.min_severity ?? 'warning')
  const [aktiv, setAktiv] = useState(kanal?.enabled ?? true)
  const bearbeiten = kanal !== null
  const hinweis = bearbeiten ? t('alarme.fGeheimLeer') : undefined

  async function senden() {
    const gemeinsam = { name: name.trim(), min_severity: ab, enabled: aktiv }
    const nutzlast = art === 'webhook'
      ? { ...gemeinsam, ...(url && { url }), ...(hmac && { hmac_secret: hmac }) }
      : { ...gemeinsam, host: host.trim(), port: Number(port), security: sicherheit, from_addr: absender.trim(), to: zeilen(an),
          username: benutzer ? benutzer : bearbeiten ? null : undefined, ...(passwort && { password: passwort }) }
    if (kanal) await api.patch(`/alert-channels/${kanal.id}`, nutzlast)
    else await api.post('/alert-channels', { ...nutzlast, kind: art })
    await qc.invalidateQueries({ queryKey: schluessel.kanaele })
    onFertig()
  }

  return (
    <Formular onSubmit={senden} absenden={t('alarme.speichern')}>
      {!bearbeiten && (
        <Auswahl label={t('alarme.fArt')} value={art} onChange={(e) => setArt(e.target.value as 'webhook' | 'email')}>
          <option value="webhook">{t('alarme.kanalWebhook')}</option>
          <option value="email">{t('alarme.fArtEmail')}</option>
        </Auswahl>
      )}
      <Feld label={t('alarme.fName')} value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} />
      {art === 'webhook' ? (
        <>
          <Feld label={t('alarme.fUrl')} hinweis={hinweis} value={url} onChange={(e) => setUrl(e.target.value)} required={!bearbeiten} inputMode="url" autoComplete="off" spellCheck={false} />
          <Feld label={t('alarme.fHmac')} hinweis={hinweis} type="password" value={hmac} onChange={(e) => setHmac(e.target.value)} autoComplete="new-password" />
        </>
      ) : (
        <>
          <Feld label={t('alarme.fHost')} value={host} onChange={(e) => setHost(e.target.value)} required autoComplete="off" spellCheck={false} />
          <Feld label={t('alarme.fPort')} type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} required />
          <Auswahl label={t('alarme.fSicherheit')} value={sicherheit} onChange={(e) => {
            const neu = e.target.value
            if (port === (neu === 'tls' ? '587' : '465')) setPort(neu === 'tls' ? '465' : '587') // Standardport mitführen
            setSicherheit(neu)
          }}>
            <option value="starttls">{t('alarme.sStarttls')}</option>
            <option value="tls">{t('alarme.sTls')}</option>
          </Auswahl>
          <Feld label={t('alarme.fBenutzer')} value={benutzer} onChange={(e) => setBenutzer(e.target.value)} autoComplete="off" spellCheck={false} />
          <Feld label={t('alarme.fPasswort')} hinweis={hinweis} type="password" value={passwort} onChange={(e) => setPasswort(e.target.value)} autoComplete="new-password" />
          <Feld label={t('alarme.fAbsender')} type="email" value={absender} onChange={(e) => setAbsender(e.target.value)} required />
          <Bereich label={t('alarme.fEmpfaenger')} hinweis={t('alarme.fEmpfaengerHinweis')} value={an} onChange={(e) => setAn(e.target.value)} required spellCheck={false} />
        </>
      )}
      <Auswahl label={t('alarme.fMeldetAb')} value={ab} onChange={(e) => setAb(e.target.value as Schweregrad)}>
        <option value="warning">{t('alarme.warnung')}</option>
        <option value="critical">{t('alarme.kritisch')}</option>
      </Auswahl>
      <Haken label={t('alarme.fKanalAktiv')} checked={aktiv} onChange={setAktiv} />
    </Formular>
  )
}
