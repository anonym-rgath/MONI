import { useState } from 'react'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Einmalwerte } from '../../ui/Einmalwerte'
import { Formular } from '../../ui/Formular'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { QrSecret } from '../../ui/QrSecret'
import { Feld } from '../../ui/Feld'
import { CodeFormular } from './CodeFormular'

// Einmalwerte (Secret, URI, Codes) liegen nur in diesem Zustand (D5).
type Schritt =
  | { art: 'uebersicht' }
  | { art: 'wechselnCode' }
  | { art: 'wechselnQr'; secret: string; uri: string }
  | { art: 'neueCodesCode' }
  | { art: 'codes'; titel: string; codes: string[] }

const UEBERSICHT: Schritt = { art: 'uebersicht' }

export function ZweiFaktorKonto({ aktiv }: { aktiv: boolean }) {
  const [schritt, setSchritt] = useState<Schritt>(UEBERSICHT)
  const [code, setCode] = useState('')
  const zurueck = () => { setSchritt(UEBERSICHT); setCode('') }

  return (
    <section aria-labelledby="konto-2fa" className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <h2 id="konto-2fa" className="text-[1.0625rem] font-semibold">{t('konto.zweiFaktorTitel')}</h2>
      {schritt.art === 'uebersicht' && (
        <>
          <p><span className="text-muted">{t('konto.status')}: </span>{aktiv ? t('konto.eingerichtet') : t('konto.nichtEingerichtet')}</p>
          {aktiv && (
            <div className="flex flex-wrap gap-2">
              <Knopf onClick={() => setSchritt({ art: 'wechselnCode' })}>{t('konto.wechseln')}</Knopf>
              <Knopf onClick={() => setSchritt({ art: 'neueCodesCode' })}>{t('konto.neueCodes')}</Knopf>
            </div>
          )}
        </>
      )}
      {schritt.art === 'wechselnCode' && (
        <>
          <p className="text-sm text-muted">{t('konto.wechselnHinweis')}</p>
          <CodeFormular
            label={t('konto.codeAktuell')} hinweis={t('konto.codeAktuellHinweis')} absenden={t('konto.weiter')} onAbbrechen={zurueck}
            onCode={async (c) => {
              const r = await api.post<{ secret: string; otpauth_uri: string }>('/auth/totp/setup', { current_code: c })
              setSchritt({ art: 'wechselnQr', secret: r.secret, uri: r.otpauth_uri })
            }}
          />
        </>
      )}
      {schritt.art === 'wechselnQr' && (
        <div className="flex flex-col gap-4.5">
          <p>{t('zweiFaktor.einfuehrung')}</p>
          <QrSecret secret={schritt.secret} uri={schritt.uri} />
          <Formular
            absenden={t('zweiFaktor.bestaetigen')}
            onSubmit={async () => {
              const r = await api.post<{ recovery_codes: string[] }>('/auth/totp/confirm', { code: code.trim() })
              setCode('')
              setSchritt({ art: 'codes', titel: t('zweiFaktor.codesTitel'), codes: r.recovery_codes })
            }}
          >
            <Feld label={t('zweiFaktor.code')} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" required className="num" value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, '').slice(0, 6))} />
          </Formular>
          <Knopf onClick={zurueck}>{t('konto.abbrechen')}</Knopf>
        </div>
      )}
      {schritt.art === 'neueCodesCode' && (
        <>
          <Hinweis>{t('konto.neueCodesWarnung')}</Hinweis>
          <CodeFormular
            label={t('konto.codeAktuell')} hinweis={t('konto.codeAktuellHinweis')} absenden={t('konto.erzeugen')} onAbbrechen={zurueck}
            onCode={async (c) => {
              const r = await api.post<{ recovery_codes: string[] }>('/account/recovery-codes', { code: c })
              setSchritt({ art: 'codes', titel: t('konto.neueCodesTitel'), codes: r.recovery_codes })
            }}
          />
        </>
      )}
      {schritt.art === 'codes' && (
        <>
          <p className="text-sm text-muted">{t('zweiFaktor.codesHinweis')}</p>
          <Einmalwerte titel={schritt.titel} werte={schritt.codes} onFertig={zurueck} />
        </>
      )}
    </section>
  )
}
