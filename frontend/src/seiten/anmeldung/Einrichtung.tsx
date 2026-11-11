import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Navigate } from 'react-router'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { useTitel } from '../../ui/useTitel'
import { AnmeldeRahmen, Ueberschrift } from './AnmeldeRahmen'
import { useNachSchritt } from './weiter'

export function Einrichtung() {
  useTitel(t('seiten.einrichtung'))
  const qc = useQueryClient()
  const nachSchritt = useNachSchritt()
  const [f, setF] = useState({ setup_token: '', name: '', email: '', password: '' })
  const setup = useQuery({ queryKey: schluessel.setup, retry: false, queryFn: () => api.get<{ required: boolean }>('/setup') })
  const feld = (name: keyof typeof f) => ({ value: f[name], onChange: (e: { target: { value: string } }) => setF({ ...f, [name]: e.target.value }), required: true })

  if (setup.data && !setup.data.required) return <Navigate to="/login" replace />
  return (
    <AnmeldeRahmen>
      <Ueberschrift>{t('seiten.einrichtung')}</Ueberschrift>
      {setup.isPending ? (
        <p role="status">{t('waechter.laden')}</p>
      ) : (
        <Formular
          absenden={t('einrichtung.anlegen')}
          onSubmit={async () => {
            await api.post('/setup', f)
            qc.removeQueries({ queryKey: schluessel.setup }) // sonst schickt der Login mit altem Wert zurück
            await nachSchritt('enroll')
          }}
        >
          <Feld label={t('einrichtung.setupToken')} hinweis={t('einrichtung.setupTokenHinweis')} autoComplete="off" spellCheck={false} {...feld('setup_token')} />
          <Feld label={t('einrichtung.name')} autoComplete="name" {...feld('name')} />
          <Feld label={t('einrichtung.email')} type="email" autoComplete="username" spellCheck={false} {...feld('email')} />
          <Feld label={t('einrichtung.passwort')} type="password" autoComplete="new-password" {...feld('password')} />
        </Formular>
      )}
    </AnmeldeRahmen>
  )
}
