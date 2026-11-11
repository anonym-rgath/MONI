import { useMutation } from '@tanstack/react-query'
import { toDataURL } from 'qrcode'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Einmalwerte } from '../../ui/Einmalwerte'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { useTitel } from '../../ui/useTitel'
import { AnmeldeRahmen, Ueberschrift } from './AnmeldeRahmen'
import { useNachSchritt } from './weiter'

export function ZweiFaktorEinrichten() {
  useTitel(t('seiten.zweiFaktorEinrichten'))
  const nachSchritt = useNachSchritt()
  const [code, setCode] = useState('')
  const [bild, setBild] = useState('')
  const [qrFehler, setQrFehler] = useState(false)
  // gcTime 0: Secret, URI und Codes bleiben nur im Zustand dieser Seite (D5)
  const setup = useMutation({ gcTime: 0, mutationFn: () => api.post<{ secret: string; otpauth_uri: string }>('/auth/totp/setup', {}) })
  const bestaetigen = useMutation({ gcTime: 0, mutationFn: (c: string) => api.post<{ recovery_codes: string[] }>('/auth/totp/confirm', { code: c }) })
  const gestartet = useRef(false) // StrictMode ruft Effekte doppelt auf; ein zweites Setup würde das erste Secret ungültig machen
  const { mutate } = setup
  const uri = setup.data?.otpauth_uri

  useEffect(() => {
    if (gestartet.current) return
    gestartet.current = true
    mutate()
  }, [mutate])

  useEffect(() => {
    if (!uri) return
    let aktuell = true
    toDataURL(uri, { margin: 2, width: 192 }).then((u) => aktuell && setBild(u), () => aktuell && setQrFehler(true))
    return () => { aktuell = false }
  }, [uri])

  const codes = bestaetigen.data?.recovery_codes
  return (
    <AnmeldeRahmen>
      <Ueberschrift>{t('seiten.zweiFaktorEinrichten')}</Ueberschrift>
      {codes ? (
        <div className="flex flex-col gap-4.5">
          <p className="text-sm text-muted">{t('zweiFaktor.codesHinweis')}</p>
          <Einmalwerte titel={t('zweiFaktor.codesTitel')} werte={codes} onFertig={() => void nachSchritt('full')} />
        </div>
      ) : setup.isError ? (
        <div className="flex flex-col gap-3">
          <Hinweis art="fehler">{setup.error.message}</Hinweis>
          <Knopf onClick={() => mutate()}>{t('waechter.erneutVersuchen')}</Knopf>
        </div>
      ) : !setup.data ? (
        <p role="status">{t('zweiFaktor.wirdVorbereitet')}</p>
      ) : (
        <div className="flex flex-col gap-4.5">
          <p>{t('zweiFaktor.einfuehrung')}</p>
          {bild && <img src={bild} alt={t('zweiFaktor.qrAlt')} width={192} height={192} className="self-center rounded-lg" />}
          {qrFehler && <Hinweis art="fehler">{t('zweiFaktor.qrFehler')}</Hinweis>}
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium">{t('zweiFaktor.geheimnis')}</span>
            <code className="num rounded-lg bg-bg p-3 break-all">{setup.data.secret}</code>
          </div>
          <Formular absenden={t('zweiFaktor.bestaetigen')} onSubmit={async () => { await bestaetigen.mutateAsync(code.trim()) }}>
            <Feld label={t('zweiFaktor.code')} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} required className="num" value={code} onChange={(e) => setCode(e.target.value)} />
          </Formular>
        </div>
      )}
    </AnmeldeRahmen>
  )
}
