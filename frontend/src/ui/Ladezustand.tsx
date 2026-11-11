import { t } from '../texte/de'

// Skelett für Karten; aria-busy plus Text für Screenreader.
export function Ladezustand({ text = t('uebersicht.laden') }: { text?: string }) {
  return (
    <div aria-busy="true" className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,280px),1fr))] gap-4">
      <p className="sr-only">{text}</p>
      {[0, 1, 2].map((i) => <div key={i} aria-hidden="true" className="h-52 animate-pulse rounded-[14px] border border-border bg-surface" />)}
    </div>
  )
}
