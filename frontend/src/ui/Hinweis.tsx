import type { ReactNode } from 'react'

// Fehler werden sofort angesagt (role="alert"), Infos höflich (role="status"); Status immer auch als Text (D11).
export function Hinweis({ art = 'info', children }: { art?: 'info' | 'fehler'; children: ReactNode }) {
  const farbe = art === 'fehler' ? 'bg-crit-bg text-crit' : 'bg-surface-2 text-text'
  return <p role={art === 'fehler' ? 'alert' : 'status'} className={`rounded-lg px-3 py-2.5 text-sm ${farbe}`}>{children}</p>
}
