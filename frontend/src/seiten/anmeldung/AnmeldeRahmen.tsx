import type { ReactNode } from 'react'
import { t } from '../../texte/de'
import { DarstellungWahl } from '../../ui/DarstellungWahl'

// Seitenrahmen der Anmeldeseiten: ohne App-Rahmen, mit Darstellungswahl wie im Entwurf.
export function AnmeldeRahmen({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-5 px-4 py-10">
      <div className="flex w-full max-w-100 flex-wrap items-center justify-between gap-3">
        <div className="num flex items-center gap-2.5 text-[1.375rem] font-medium">
          <span aria-hidden="true" className="size-3 rounded-full border-2 border-text" />
          <span>{t('app.name')}</span>
        </div>
        <DarstellungWahl />
      </div>
      <main className="box-border w-full max-w-100 rounded-[14px] border border-border bg-surface p-7">{children}</main>
    </div>
  )
}

export function Ueberschrift({ children }: { children: ReactNode }) {
  return <h1 tabIndex={-1} className="mb-4.5 text-[1.375rem] font-semibold">{children}</h1>
}
