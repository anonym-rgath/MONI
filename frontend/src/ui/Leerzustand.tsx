import type { ReactNode } from 'react'

// Leerer Zustand: ein Satz und, wenn möglich, der nächste Schritt (D12).
export function Leerzustand({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[14px] border border-border bg-surface px-5 py-6">
      <p className="m-0">{text}</p>
      {children}
    </div>
  )
}
