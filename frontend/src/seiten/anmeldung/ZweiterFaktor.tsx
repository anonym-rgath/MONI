import { useState } from 'react'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'
import { useTitel } from '../../ui/useTitel'
import { AnmeldeRahmen, Ueberschrift } from './AnmeldeRahmen'
import { useNachSchritt } from './weiter'

export function ZweiterFaktor() {
  useTitel(t('seiten.zweiterFaktor'))
  const nachSchritt = useNachSchritt()
  const [wiederherstellung, setWiederherstellung] = useState(false)
  const [code, setCode] = useState('')

  return (
    <AnmeldeRahmen>
      <Ueberschrift>{t('seiten.zweiterFaktor')}</Ueberschrift>
      <Formular
        absenden={t('anmeldung.anmelden')}
        onSubmit={async () => {
          await api.post('/auth/login/mfa', { code: code.trim() })
          await nachSchritt('full')
        }}
      >
        {wiederherstellung ? (
          <Feld
            key="rc"
            label={t('anmeldung.codeWiederherstellung')}
            hinweis={t('anmeldung.codeWiederherstellungHinweis')}
            autoComplete="off"
            spellCheck={false}
            required
            className="num"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        ) : (
          <Feld
            key="totp"
            label={t('anmeldung.codeApp')}
            hinweis={t('anmeldung.codeHinweis')}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={6}
            required
            className="num text-[1.375rem] tracking-[0.2em]"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        )}
        <Knopf art="link" onClick={() => { setWiederherstellung(!wiederherstellung); setCode('') }}>
          {t(wiederherstellung ? 'anmeldung.zuApp' : 'anmeldung.zuWiederherstellung')}
        </Knopf>
      </Formular>
    </AnmeldeRahmen>
  )
}
