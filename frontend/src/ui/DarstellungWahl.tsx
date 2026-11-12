import { t } from '../texte/de'
import { useDarstellung, type Wahl } from './Darstellung'

const WAHLEN: { wahl: Wahl; text: 'rahmen.auto' | 'rahmen.hell' | 'rahmen.dunkel' }[] = [
  { wahl: 'auto', text: 'rahmen.auto' },
  { wahl: 'hell', text: 'rahmen.hell' },
  { wahl: 'dunkel', text: 'rahmen.dunkel' },
]

export function DarstellungWahl() {
  const { wahl, setzen } = useDarstellung()
  return (
    <div role="group" aria-label={t('rahmen.darstellung')} className="flex gap-0.5 rounded-[9px] border border-control p-[3px]">
      {WAHLEN.map((o) => (
        <button
          key={o.wahl}
          type="button"
          aria-pressed={wahl === o.wahl}
          onClick={() => setzen(o.wahl)}
          className="min-h-8.5 cursor-pointer rounded-md px-2.5 text-[0.8125rem] font-medium text-muted aria-pressed:bg-surface-2 aria-pressed:text-text"
        >
          {t(o.text)}
        </button>
      ))}
    </div>
  )
}
