import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel } from '../../api/abfragen'
import { api } from '../../api/client'
import { t } from '../../texte/de'
import { Dialog } from '../../ui/Dialog'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Knopf } from '../../ui/Knopf'

type Wahl = '1' | '4' | 'morgen' | 'eigen'
const MAX_MS = 30 * 86_400_000
const OPTIONEN: { id: Wahl; text: 'server.stumm1' | 'server.stumm4' | 'server.stummMorgen' | 'server.stummEigen' }[] = [
  { id: '1', text: 'server.stumm1' }, { id: '4', text: 'server.stumm4' }, { id: 'morgen', text: 'server.stummMorgen' }, { id: 'eigen', text: 'server.stummEigen' },
]

// Ortszeit des Browsers; datetime-local liefert "2026-11-12T08:00" ohne Zone, new Date liest das als Ortszeit.
function ziel(wahl: Wahl, eigen: string, jetzt: number): number {
  if (wahl === '1') return jetzt + 3_600_000
  if (wahl === '4') return jetzt + 4 * 3_600_000
  if (wahl === 'eigen') return eigen ? new Date(eigen).getTime() : NaN
  return morgenAcht(jetzt)
}

// Nächster 8-Uhr-Zeitpunkt in Ortszeit: vor 8 Uhr heute, sonst morgen.
export function morgenAcht(jetzt: number): number {
  const d = new Date(jetzt)
  d.setHours(8, 0, 0, 0)
  if (d.getTime() <= jetzt) d.setDate(d.getDate() + 1)
  return d.getTime()
}

function Inhalt({ serverId, onClose, onErfolg }: { serverId: number; onClose: () => void; onErfolg: (bis: number) => void }) {
  const qc = useQueryClient()
  const [wahl, setWahl] = useState<Wahl>('1')
  const [eigen, setEigen] = useState('')
  const [fehler, setFehler] = useState<string>()

  async function senden() {
    const jetzt = Date.now()
    const bis = ziel(wahl, eigen, jetzt)
    const meldung = Number.isNaN(bis) ? t('server.stummFehlt') : bis <= jetzt ? t('server.stummVergangen') : bis > jetzt + MAX_MS ? t('server.stummZuWeit') : undefined
    setFehler(meldung)
    if (meldung) return
    // Die API-Fehler (422) zeigt das Formular unverändert an.
    await api.put(`/servers/${serverId}/mute`, { until: new Date(bis).toISOString() })
    await qc.invalidateQueries({ queryKey: schluessel.serverEinzeln(serverId), exact: true })
    void qc.invalidateQueries({ queryKey: schluessel.server, exact: true })
    onErfolg(bis)
  }

  return (
    <>
      <p className="m-0 text-muted">{t('server.stummText')}</p>
      <Formular onSubmit={senden} absenden={t('server.stummAbsenden')}>
        <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
          <legend className="mb-1 text-sm font-medium">{t('server.stummDauer')}</legend>
          {OPTIONEN.map((o) => (
            <label key={o.id} className="flex min-h-11 cursor-pointer items-center gap-3">
              <input type="radio" name="dauer" value={o.id} checked={wahl === o.id} onChange={() => { setWahl(o.id); setFehler(undefined) }} className="size-5 accent-[var(--accent)]" />
              {t(o.text)}
            </label>
          ))}
        </fieldset>
        {wahl === 'eigen' && (
          <Feld label={t('server.stummZeitpunkt')} hinweis={t('server.stummZeitpunktHinweis')} type="datetime-local" value={eigen} onChange={(e) => setEigen(e.target.value)} fehler={fehler} />
        )}
      </Formular>
      <Knopf onClick={onClose}>{t('server.abbrechen')}</Knopf>
    </>
  )
}

export function StummDialog({ serverId, offen, onClose, onErfolg }: { serverId: number; offen: boolean; onClose: () => void; onErfolg: (bis: number) => void }) {
  return (
    <Dialog titel={t('server.stummTitel')} offen={offen} onClose={onClose}>
      <Inhalt serverId={serverId} onClose={onClose} onErfolg={onErfolg} />
    </Dialog>
  )
}
