import type { Regel } from '../../api/typen'
import { t, type TextSchluessel } from '../../texte/de'
import { formatWert, istZustand, labelName, metrikName } from '../../texte/metriken'
import { dauer } from '../../ui/Zeit'

const VERGLEICH: Record<Regel['op'], TextSchluessel> = {
  '>': 'alarme.vergleichGroesser', '>=': 'alarme.vergleichGroesserGleich', '<': 'alarme.vergleichKleiner', '<=': 'alarme.vergleichKleinerGleich',
}

// "Löst aus, wenn …": Klartext aus Metrik, Funktion und Vergleich. Ja/Nein-Metriken als Zustand, nie als Zahl mit Schwelle (P6-R8).
export function bedingung(r: Regel): string {
  const was = metrikName(r.metric)
  if (istZustand(r.metric)) return t('alarme.bedingungZustand', { was, zustand: formatWert(r.metric, 0) })
  const text = r.func === 'increase' && r.window_seconds
    ? t('alarme.bedingungZunahme', { was, fenster: dauer(r.window_seconds), vergleich: t(VERGLEICH[r.op]) })
    : t('alarme.bedingungWert', { was, vergleich: t(VERGLEICH[r.op]) })
  const je = r.key_labels.map(labelName).join(', ')
  return je ? t('alarme.bedingungJe', { text, labels: je }) : text
}

export const schwelleText = (r: Regel, wert: number | null) =>
  wert == null ? '–' : istZustand(r.metric) ? t('alarme.beiEintritt') : formatWert(r.metric, wert)
