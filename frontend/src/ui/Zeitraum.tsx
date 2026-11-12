// Knopfgruppe für eine Auswahl; der gewählte Knopf trägt aria-pressed (D11).
export function Zeitraum<T extends string>({ label, optionen, wert, onWahl }: { label: string; optionen: { id: T; text: string }[]; wert: T; onWahl: (id: T) => void }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1 rounded-[10px] border border-control bg-surface p-1">
      {optionen.map((o) => (
        <button key={o.id} type="button" aria-pressed={o.id === wert} onClick={() => onWahl(o.id)}
          className="min-h-9 cursor-pointer rounded-md px-3 text-sm font-medium aria-pressed:bg-surface-2 aria-pressed:font-semibold">
          {o.text}
        </button>
      ))}
    </div>
  )
}
