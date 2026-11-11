import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Einmalwerte } from '../../ui/Einmalwerte'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { QrSecret } from '../../ui/QrSecret'
import { useTitel } from '../../ui/useTitel'
import { AnmeldeRahmen, Ueberschrift } from './AnmeldeRahmen'
import { Geschuetzt } from '../../auth/Waechter'
import { useNachSchritt } from './weiter'

// Sobald Codes angezeigt werden, hält die Route die Seite offen: confirm setzt die Sitzung auf full, ein Refetch (Fokuswechsel zur Passwort-App) würde sonst zu / umleiten.
export function ZweiFaktorRoute() {
  const [codesOffen, setCodesOffen] = useState(false)
  return (
    <Geschuetzt stage="enroll" offen={codesOffen}>
      <ZweiFaktorEinrichten onCodes={() => setCodesOffen(true)} />
    </Geschuetzt>
  )
}

function ZweiFaktorEinrichten({ onCodes }: { onCodes: () => void }) {
  useTitel(t('seiten.zweiFaktorEinrichten'))
  const nachSchritt = useNachSchritt()
  const [code, setCode] = useState('')
  // gcTime 0: Secret, URI und Codes bleiben nur im Zustand dieser Seite (D5)
  const setup = useMutation({ gcTime: 0, mutationFn: () => api.post<{ secret: string; otpauth_uri: string }>('/auth/totp/setup', {}) })
  const bestaetigen = useMutation({ gcTime: 0, onSuccess: onCodes, mutationFn: (c: string) => api.post<{ recovery_codes: string[] }>('/auth/totp/confirm', { code: c }) })
  const gestartet = useRef(false) // StrictMode ruft Effekte doppelt auf; ein zweites Setup würde das erste Secret ungültig machen
  const { mutate } = setup

  useEffect(() => {
    if (gestartet.current) return
    gestartet.current = true
    mutate()
  }, [mutate])

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
          <QrSecret secret={setup.data.secret} uri={setup.data.otpauth_uri} />
          <Formular absenden={t('zweiFaktor.bestaetigen')} onSubmit={async () => { await bestaetigen.mutateAsync(code.trim()) }}>
            <Feld label={t('zweiFaktor.code')} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" required className="num" value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, '').slice(0, 6))} />
          </Formular>
        </div>
      )}
    </AnmeldeRahmen>
  )
}
