import { useId, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

const RAND = 'rounded-lg border border-control bg-bg px-3 text-text'

export function Auswahl({ label, hinweis, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label: string; hinweis?: string; children: ReactNode }) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <select {...rest} id={id} aria-describedby={hinweis ? `${id}-h` : undefined} className={`min-h-11 ${RAND}`}>{children}</select>
      {hinweis && <span id={`${id}-h`} className="text-sm text-muted">{hinweis}</span>}
    </div>
  )
}

export function Bereich({ label, hinweis, fehler, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hinweis?: string; fehler?: string }) {
  const id = useId()
  const beschreibung = [hinweis && `${id}-h`, fehler && `${id}-f`].filter(Boolean).join(' ') || undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <textarea {...rest} id={id} rows={3} aria-describedby={beschreibung} aria-invalid={fehler ? true : undefined} className={`py-2 ${RAND}`} />
      {hinweis && <span id={`${id}-h`} className="text-sm text-muted">{hinweis}</span>}
      {fehler && <span id={`${id}-f`} className="text-sm text-crit">{fehler}</span>}
    </div>
  )
}

export function Haken({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="size-5 accent-[var(--accent)]" />
      {label}
    </label>
  )
}

// Zeilen- und Kommalisten der Formulare.
export const zeilen = (s: string) => s.split('\n').map((z) => z.trim()).filter(Boolean)
export const kommaListe = (s: string) => s.split(',').map((z) => z.trim()).filter(Boolean)
