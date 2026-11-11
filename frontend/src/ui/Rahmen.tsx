import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { useSitzung } from '../auth/Sitzung'
import { t } from '../texte/de'
import { useDarstellung, type Wahl } from './Darstellung'

const WAHLEN: { wahl: Wahl; text: 'rahmen.auto' | 'rahmen.hell' | 'rahmen.dunkel' }[] = [
  { wahl: 'auto', text: 'rahmen.auto' },
  { wahl: 'hell', text: 'rahmen.hell' },
  { wahl: 'dunkel', text: 'rahmen.dunkel' },
]

const navLink = 'flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-muted aria-[current=page]:bg-surface-2 aria-[current=page]:font-semibold aria-[current=page]:text-text'

// offeneAlarme: Platzhalter, Task 5 verdrahtet /alerts.
export function Rahmen({ offeneAlarme = 0, children }: { offeneAlarme?: number; children: ReactNode }) {
  const { wahl, setzen } = useDarstellung()
  const { user, abmelden } = useSitzung()
  return (
    <>
      <a className="skip" href="#inhalt">{t('rahmen.zumInhalt')}</a>
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex min-h-15 max-w-310 flex-wrap items-center gap-x-6 gap-y-2 px-5 py-2">
          <div className="num flex items-center gap-2.5 text-lg font-medium">
            <span aria-hidden="true" className="size-2.5 rounded-full border-2 border-text" />
            <span>{t('app.name')}</span>
          </div>
          <nav aria-label={t('rahmen.hauptnavigation')} className="flex flex-1 flex-wrap gap-1">
            <NavLink to="/" end className={navLink}>{t('rahmen.uebersicht')}</NavLink>
            <NavLink to="/alarme" className={navLink}>
              {t('rahmen.alarme')}{' '}
              <span className="num rounded-full bg-crit-bg px-2 text-[0.8125rem] font-semibold text-crit">
                {offeneAlarme}{' '}
                <span className="sr-only">{t('rahmen.alarmeOffen')}</span>
              </span>
            </NavLink>
            {user?.role === 'admin' && <NavLink to="/verwaltung/server" className={navLink}>{t('rahmen.verwaltung')}</NavLink>}
          </nav>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <div role="group" aria-label={t('rahmen.darstellung')} className="flex gap-0.5 rounded-[9px] border border-control p-[3px]">
              {WAHLEN.map((o) => (
                <button
                  key={o.wahl}
                  type="button"
                  aria-pressed={wahl === o.wahl}
                  onClick={() => setzen(o.wahl)}
                  className="min-h-8.5 cursor-pointer rounded-md px-2.5 text-[0.8125rem] font-medium text-muted aria-pressed:bg-surface-2 aria-pressed:text-text"
                >
                  {t(o.text)}
                </button>
              ))}
            </div>
            <span className="text-sm text-muted">{user && t('rahmen.benutzer', { name: user.name, rolle: t(user.role === 'admin' ? 'rolle.admin' : 'rolle.betrachter') })}</span>
            <button type="button" onClick={() => void abmelden()} className="min-h-10 cursor-pointer rounded-lg border border-control px-3 text-sm">{t('rahmen.abmelden')}</button>
          </div>
        </div>
      </header>
      <main id="inhalt" tabIndex={-1} className="mx-auto flex max-w-310 flex-col gap-6 px-5 pt-7 pb-12">{children}</main>
    </>
  )
}
