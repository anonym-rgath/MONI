import { t } from '../texte/de'

const ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const FUELLUNG = { ok: 'bg-ok', warn: 'bg-warn', crit: 'bg-crit' } as const
const TEXT = { ok: 'text-text', warn: 'text-warn', crit: 'text-crit' } as const

export interface Grenzen { warn: number; crit: number; max: number } // max: Wert, bei dem der Balken voll ist

// Eine Kennzahl als Zeile einer <dl>: Name, dekorativer Balken, Zahl (bei Warnung oder Kritisch mit Wort).
// Fehlt der Wert oder ist er keine Zahl, steht "keine Daten" und der Balken bleibt leer.
export function Balken({ name, wert, einheit, grenzen }: { name: string; wert: number | null | undefined; einheit: string; grenzen: Grenzen }) {
  const da = typeof wert === 'number' && Number.isFinite(wert)
  const stufe = !da ? 'ok' : wert >= grenzen.crit ? 'crit' : wert >= grenzen.warn ? 'warn' : 'ok'
  const wort = stufe === 'crit' ? ` ${t('karte.wortKritisch')}` : stufe === 'warn' ? ` ${t('karte.wortWarnung')}` : ''
  const breite = da ? Math.min(100, Math.max(0, (wert / grenzen.max) * 100)) : 0
  return (
    <div className="grid grid-cols-[minmax(84px,auto)_minmax(40px,1fr)_auto] items-center gap-2.5 text-sm">
      <dt className="text-muted">{name}</dt>
      <dd aria-hidden="true" className="m-0 h-1.5 overflow-hidden rounded-[3px] bg-track">
        <span className={`block h-full ${FUELLUNG[stufe]}`} style={{ width: `${breite}%` }} />
      </dd>
      <dd className={`num m-0 text-right ${da ? TEXT[stufe] : 'text-muted'}`}>{da ? `${ZAHL.format(wert)} ${einheit}${wort}` : t('karte.keineDaten')}</dd>
    </div>
  )
}
