import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Server } from '../../api/typen'
import { t } from '../../texte/de'
import { Hinweis } from '../../ui/Hinweis'
import { Knopf } from '../../ui/Knopf'
import { fehlertext, Rueckfrage } from '../../ui/Meldungen'
import { lokal } from '../../ui/Zeit'
import { StummDialog } from './StummDialog'

// Nur für Admins (der Aufrufer prüft die Rolle). Ergebnisse gehen als Text an onMeldung (role="status" auf der Seite).
export function Aktionen({ server, stumm, onMeldung }: { server: Server; stumm: boolean; onMeldung: (text: string) => void }) {
  const qc = useQueryClient()
  const [stummOffen, setStummOffen] = useState(false)
  const [ausOffen, setAusOffen] = useState(false)
  const neu = () => Promise.all([
    qc.invalidateQueries({ queryKey: schluessel.serverEinzeln(server.id), exact: true }),
    qc.invalidateQueries({ queryKey: schluessel.server, exact: true }),
  ])

  const aufheben = useMutation({
    mutationFn: () => api.delete(`/servers/${server.id}/mute`),
    onSuccess: async () => { await neu(); onMeldung(t('server.stummAufgehoben')) },
  })
  const schalten = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/servers/${server.id}`, { enabled }),
    onSuccess: async (_, enabled) => { await neu(); onMeldung(t(enabled ? 'server.aktiviertMeldung' : 'server.deaktiviertMeldung')) },
  })
  const fehler = ausOffen ? aufheben.error : (aufheben.error ?? schalten.error)

  return (
    <div className="flex flex-col items-start gap-2 sm:items-end">
      <div className="flex flex-wrap gap-2">
        {server.enabled && (stumm
          ? <Knopf disabled={aufheben.isPending} onClick={() => aufheben.mutate()}>{t('server.stummAufheben')}</Knopf>
          : <Knopf onClick={() => setStummOffen(true)}>{t('server.stummschalten')}</Knopf>)}
        {server.enabled
          ? <Knopf onClick={() => setAusOffen(true)}>{t('server.deaktivieren')}</Knopf>
          : <Knopf disabled={schalten.isPending} onClick={() => schalten.mutate(true)}>{t('server.aktivieren')}</Knopf>}
      </div>
      {fehler && <Hinweis art="fehler">{fehlertext(fehler)}</Hinweis>}

      <StummDialog serverId={server.id} offen={stummOffen} onClose={() => setStummOffen(false)}
        onErfolg={(bis) => { setStummOffen(false); onMeldung(t('server.stummGesetzt', { zeit: lokal(bis, Date.now()) })) }} />
      <Rueckfrage titel={t('server.deaktivierenTitel')} text={t('server.deaktivierenText', { name: server.name })} aktion={t('server.deaktivieren')}
        offen={ausOffen} onClose={() => setAusOffen(false)} onBestaetigen={async () => { await schalten.mutateAsync(false) }} />
    </div>
  )
}
