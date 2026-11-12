import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { schluessel, useServer } from '../../api/abfragen'
import { api } from '../../api/client'
import type { Regel } from '../../api/typen'
import { t } from '../../texte/de'
import { METRIKEN } from '../../texte/metriken'
import { Feld } from '../../ui/Feld'
import { Formular } from '../../ui/Formular'
import { Auswahl, Bereich, Haken, kommaListe, zeilen } from './felder'

type Nutzlast = Omit<Regel, 'id' | 'created_at' | 'updated_at'>
const OPERATOREN = [['>', 'alarme.opGroesser'], ['>=', 'alarme.opGroesserGleich'], ['<', 'alarme.opKleiner'], ['<=', 'alarme.opKleinerGleich']] as const

const zahl = (s: string) => (s.trim() === '' ? null : Number(s))
// Schlüsselreihenfolge ist egal (label_filter kommt aus der API unsortiert).
const norm = (v: unknown) => JSON.stringify(v, (_, x: unknown) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort()) : x))

// Zeilen "schluessel=wert" zum Objekt; null bei einer Zeile ohne Gleichheitszeichen.
function filterObjekt(text: string): { objekt: Record<string, string> } | { zeile: string } {
  const objekt: Record<string, string> = {}
  for (const z of zeilen(text)) {
    const i = z.indexOf('=')
    if (i < 1) return { zeile: z }
    objekt[z.slice(0, i).trim()] = z.slice(i + 1).trim()
  }
  return { objekt }
}

export function RegelFormular({ regel, onFertig }: { regel: Regel | null; onFertig: () => void }) {
  const qc = useQueryClient()
  const server = useServer().data ?? []
  const [name, setName] = useState(regel?.name ?? '')
  const [metric, setMetric] = useState(regel?.metric ?? 'moni_host_cpu_usage_percent')
  const [func, setFunc] = useState(regel?.func === 'increase' ? 'increase' : 'value')
  const [fenster, setFenster] = useState(regel?.window_seconds ? String(regel.window_seconds / 60) : '')
  const [op, setOp] = useState<Regel['op']>(regel?.op ?? '>=')
  const [warnung, setWarnung] = useState(regel?.warning == null ? '' : String(regel.warning))
  const [kritisch, setKritisch] = useState(regel?.critical == null ? '' : String(regel.critical))
  const [dauer, setDauer] = useState(regel ? String(regel.for_seconds / 60) : '0')
  const [labels, setLabels] = useState(regel?.key_labels.join(', ') ?? '')
  const [filter, setFilter] = useState(Object.entries(regel?.label_filter ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'))
  const [vorherOk, setVorherOk] = useState(regel?.require_prior_ok ?? false)
  const [serverId, setServerId] = useState(regel?.server_id == null ? '' : String(regel.server_id))
  const [aktiv, setAktiv] = useState(regel?.enabled ?? true)
  const [filterFehler, setFilterFehler] = useState<string>()

  async function senden() {
    const f = filterObjekt(filter)
    setFilterFehler('zeile' in f ? t('alarme.fFilterFehler', { zeile: f.zeile }) : undefined)
    if ('zeile' in f) return
    const neu: Nutzlast = {
      name: name.trim(), metric, func, window_seconds: func === 'increase' && fenster.trim() !== '' ? Math.round(Number(fenster) * 60) : null, op,
      warning: zahl(warnung), critical: zahl(kritisch), for_seconds: Math.round(Number(dauer || 0) * 60), key_labels: kommaListe(labels),
      label_filter: f.objekt, require_prior_ok: vorherOk, server_id: serverId ? Number(serverId) : null, enabled: aktiv,
    }
    if (!regel) await api.post('/alert-rules', neu)
    else {
      // PATCH nur mit geänderten Feldern; ohne Änderung entfällt die Anfrage (die API verlangt mindestens ein Feld).
      const geaendert = Object.fromEntries(Object.entries(neu).filter(([k, v]) => norm(v) !== norm(regel[k as keyof Nutzlast])))
      if (Object.keys(geaendert).length > 0) await api.patch(`/alert-rules/${regel.id}`, geaendert)
    }
    await Promise.all([qc.invalidateQueries({ queryKey: schluessel.regeln }), qc.invalidateQueries({ queryKey: schluessel.alarme })])
    onFertig()
  }

  return (
    <Formular onSubmit={senden} absenden={t('alarme.speichern')}>
      <Feld label={t('alarme.fName')} value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} />
      <Auswahl label={t('alarme.fMetrik')} value={metric} onChange={(e) => setMetric(e.target.value)}>
        {Object.entries(METRIKEN).map(([id, m]) => <option key={id} value={id}>{`${m.name} (${id})`}</option>)}
      </Auswahl>
      <Auswahl label={t('alarme.fFunktion')} value={func} onChange={(e) => setFunc(e.target.value)}>
        <option value="value">{t('alarme.fFunktionWert')}</option>
        <option value="increase">{t('alarme.fFunktionZunahme')}</option>
      </Auswahl>
      {func === 'increase' && <Feld label={t('alarme.fFenster')} hinweis={t('alarme.fFensterHinweis')} type="number" step="any" min={1} max={1440} value={fenster} onChange={(e) => setFenster(e.target.value)} required />}
      <Auswahl label={t('alarme.fBedingung')} value={op} onChange={(e) => setOp(e.target.value as Regel['op'])}>
        {OPERATOREN.map(([wert, text]) => <option key={wert} value={wert}>{t(text)}</option>)}
      </Auswahl>
      <Feld label={t('alarme.fWarnung')} hinweis={t('alarme.fSchwellenHinweis')} type="number" step="any" value={warnung} onChange={(e) => setWarnung(e.target.value)} />
      <Feld label={t('alarme.fKritisch')} type="number" step="any" value={kritisch} onChange={(e) => setKritisch(e.target.value)} />
      <Feld label={t('alarme.fDauer')} hinweis={t('alarme.fDauerHinweis')} type="number" step="any" min={0} max={1440} value={dauer} onChange={(e) => setDauer(e.target.value)} />
      <Feld label={t('alarme.fLabels')} hinweis={t('alarme.fLabelsHinweis')} value={labels} onChange={(e) => setLabels(e.target.value)} autoComplete="off" spellCheck={false} />
      <Bereich label={t('alarme.fFilter')} hinweis={t('alarme.fFilterHinweis')} fehler={filterFehler} value={filter} onChange={(e) => setFilter(e.target.value)} spellCheck={false} />
      <Haken label={t('alarme.fVorherOk')} checked={vorherOk} onChange={setVorherOk} />
      <Auswahl label={t('alarme.fServer')} value={serverId} onChange={(e) => setServerId(e.target.value)}>
        <option value="">{t('alarme.alleServer')}</option>
        {server.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </Auswahl>
      <Haken label={t('alarme.fAktiv')} checked={aktiv} onChange={setAktiv} />
    </Formular>
  )
}

