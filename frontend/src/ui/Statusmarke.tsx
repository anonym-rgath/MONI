export type Ton = 'ok' | 'warn' | 'crit' | 'neutral'
const FARBE: Record<Ton, string> = { ok: 'bg-ok-bg text-ok', warn: 'bg-warn-bg text-warn', crit: 'bg-crit-bg text-crit', neutral: 'bg-surface-2 text-muted' }

// Status immer als Text; die Farbe unterstützt nur (D11).
export function Statusmarke({ ton, id, children }: { ton: Ton; id?: string; children: string }) {
  return <span id={id} className={`rounded-full px-2.5 py-0.5 text-[0.8125rem] font-semibold ${FARBE[ton]}`}>{children}</span>
}
