import { useEffect, useId, useRef, type ReactNode } from 'react'

// Natives <dialog> mit showModal(): Fokus bleibt im Dialog, Esc schließt. Der Fokus kehrt zum Auslöser zurück.
// Der Inhalt steht nur im DOM, solange der Dialog offen ist; so beginnt jedes Öffnen mit leeren Eingaben.
export function Dialog({ titel, offen, onClose, breit = false, children }: { titel: string; offen: boolean; onClose: () => void; breit?: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  const ausloeser = useRef<Element | null>(null)
  const titelId = useId()

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (offen && !d.open) {
      ausloeser.current = document.activeElement
      d.showModal()
    } else if (!offen && d.open) d.close()
  }, [offen])

  return (
    <dialog
      ref={ref}
      aria-labelledby={titelId}
      onClose={() => {
        onClose()
        if (ausloeser.current instanceof HTMLElement) ausloeser.current.focus()
      }}
      className={`m-auto ${breit ? 'w-[min(92vw,46rem)]' : 'w-[min(92vw,30rem)]'} rounded-[14px] border border-border bg-surface max-h-[92vh] overflow-y-auto p-5 text-text backdrop:bg-[rgb(0_0_0/0.6)]`}
    >
      {offen && (
        <div className="flex flex-col gap-4">
          <h2 id={titelId} className="m-0 text-[1.0625rem] font-semibold">{titel}</h2>
          {children}
        </div>
      )}
    </dialog>
  )
}
