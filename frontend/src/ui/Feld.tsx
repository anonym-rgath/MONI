import { useId, type InputHTMLAttributes } from 'react'

interface FeldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hinweis?: string
  fehler?: string
}

// Label per htmlFor, Hinweis und Fehler per aria-describedby (D11).
export function Feld({ label, hinweis, fehler, id, className = '', ...rest }: FeldProps) {
  const auto = useId()
  const feldId = id ?? auto
  const beschreibung = [hinweis && `${feldId}-hinweis`, fehler && `${feldId}-fehler`].filter(Boolean).join(' ') || undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={feldId} className="text-sm font-medium">{label}</label>
      <input
        {...rest}
        id={feldId}
        aria-describedby={beschreibung}
        aria-invalid={fehler ? true : undefined}
        className={`min-h-11 rounded-lg border border-control bg-bg px-3 text-text ${className}`}
      />
      {hinweis && <span id={`${feldId}-hinweis`} className="text-sm text-muted">{hinweis}</span>}
      {fehler && <span id={`${feldId}-fehler`} className="text-sm text-crit">{fehler}</span>}
    </div>
  )
}
