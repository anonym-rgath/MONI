import type { ReactNode } from 'react'

// Überschrift plus scrollbarer, per Tastatur erreichbarer Bereich (role="region", benannt über die Überschrift).
export function Tabelle({ id, titel, kopf, aktion, children }: { id: string; titel: string; kopf: string[]; aktion?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={id} className="m-0 text-[1.0625rem] font-semibold">{titel}</h2>
        {aktion}
      </div>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollbarer Bereich muss per Tastatur erreichbar sein (WCAG 2.1.1) */}
      <div role="region" aria-labelledby={id} tabIndex={0} className="overflow-x-auto rounded-[14px] border border-border bg-surface">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-muted">
              {kopf.map((k) => <th key={k} scope="col" className="px-4 py-2.5 font-medium whitespace-nowrap">{k}</th>)}
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
    </section>
  )
}

export const Zeilenkopf = ({ children }: { children: ReactNode }) => <th scope="row" className="px-4 py-2.5 font-semibold whitespace-nowrap">{children}</th>
export const Zelle = ({ children, num = false, colSpan }: { children: ReactNode; num?: boolean; colSpan?: number }) => (
  <td colSpan={colSpan} className={`px-4 py-2.5 ${num ? 'num whitespace-nowrap' : ''}`}>{children}</td>
)
