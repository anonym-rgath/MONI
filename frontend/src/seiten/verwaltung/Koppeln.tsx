import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { ApiFehler, api } from '../../api/client'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'

const KOPPLUNG_UNGUELTIG = 'Kopplungs-Token ungültig' // D8: Fehler am Feld, keine Abmeldung (der 401-Handler überspringt diesen Text)

interface Zeile { id: number; name: string; wert: string }

// Inhalt des Koppeln-Dialogs. Zwei Wege: Kopplungscode (empfohlen, mTLS) oder Token mit URL (ohne mTLS).
export function Koppeln({ onFertig, onAbbrechen }: { onFertig: () => void; onAbbrechen: () => void }) {
  const qc = useQueryClient()
  const [art, setArt] = useState<'code' | 'token'>('code')
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [header, setHeader] = useState<Zeile[]>([])
  const [naechste, setNaechste] = useState(1)
  const [codeFehler, setCodeFehler] = useState<string | undefined>()
  const koppeln = useMutation({ mutationFn: (body: object) => api.post('/servers', body), gcTime: 0 }) // Token und Code gehören nicht in den Cache (D5)

  async function senden() {
    setCodeFehler(undefined)
    const body = art === 'code'
      ? { name: name.trim(), pairing_code: code.trim(), ...(url.trim() && { url: url.trim() }) }
      : { name: name.trim(), url: url.trim(), token, ...(header.some((h) => h.name.trim()) && { headers: Object.fromEntries(header.filter((h) => h.name.trim()).map((h) => [h.name.trim(), h.wert])) }) }
    try {
      await koppeln.mutateAsync(body)
    } catch (e) {
      if (art === 'code' && e instanceof ApiFehler && e.detail === KOPPLUNG_UNGUELTIG) { setCodeFehler(e.detail); return }
      throw e
    }
    await qc.invalidateQueries({ queryKey: schluessel.server, exact: true })
    onFertig()
  }
  const aendern = (id: number, feld: 'name' | 'wert', v: string) => setHeader((h) => h.map((z) => (z.id === id ? { ...z, [feld]: v } : z)))

  return (
    <>
      <Formular onSubmit={senden} absenden={t('verwaltung.koppeln')}>
        <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
          <legend className="mb-1 p-0 text-sm font-medium">{t('verwaltung.koppelnArt')}</legend>
          {(['code', 'token'] as const).map((a) => (
            <label key={a} className="flex min-h-11 cursor-pointer items-center gap-3">
              <input type="radio" name="koppelart" checked={art === a} onChange={() => { setArt(a); setCodeFehler(undefined) }} className="size-5 accent-[var(--accent)]" />
              {t(a === 'code' ? 'verwaltung.koppelnCode' : 'verwaltung.koppelnToken')}
            </label>
          ))}
        </fieldset>
        <Feld label={t('verwaltung.fName')} value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off" />
        {art === 'code' ? (
          <>
            <Feld label={t('verwaltung.fCode')} hinweis={t('verwaltung.fCodeHinweis')} fehler={codeFehler} value={code} onChange={(e) => setCode(e.target.value)} required autoComplete="off" spellCheck={false} />
            <Feld label={t('verwaltung.fUrlOptional')} hinweis={t('verwaltung.fUrlOptionalHinweis')} value={url} onChange={(e) => setUrl(e.target.value)} inputMode="url" autoComplete="off" spellCheck={false} />
          </>
        ) : (
          <>
            <Hinweis>{t('verwaltung.proxyWarnung')}</Hinweis>
            <Feld label={t('verwaltung.fUrl')} value={url} onChange={(e) => setUrl(e.target.value)} required inputMode="url" autoComplete="off" spellCheck={false} />
            <Feld label={t('verwaltung.fToken')} type="password" value={token} onChange={(e) => setToken(e.target.value)} required autoComplete="off" />
            <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
              <legend className="mb-1 p-0 text-sm font-medium">{t('verwaltung.headerTitel')}</legend>
              {header.map((h) => (
                <div key={h.id} className="flex flex-wrap items-end gap-2">
                  <Feld label={t('verwaltung.headerName')} value={h.name} onChange={(e) => aendern(h.id, 'name', e.target.value)} autoComplete="off" spellCheck={false} />
                  <Feld label={t('verwaltung.headerWert')} type="password" value={h.wert} onChange={(e) => aendern(h.id, 'wert', e.target.value)} autoComplete="off" />
                  <Knopf onClick={() => setHeader((r) => r.filter((z) => z.id !== h.id))}>{t('verwaltung.headerWeg')}</Knopf>
                </div>
              ))}
              <div><Knopf onClick={() => { setHeader((r) => [...r, { id: naechste, name: '', wert: '' }]); setNaechste(naechste + 1) }}>{t('verwaltung.headerPlus')}</Knopf></div>
            </fieldset>
          </>
        )}
      </Formular>
      <Knopf onClick={onAbbrechen}>{t('verwaltung.abbrechen')}</Knopf>
    </>
  )
}
