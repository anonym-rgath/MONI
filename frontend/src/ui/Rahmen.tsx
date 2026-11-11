import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router'
import { useAlarme } from '../api/abfragen'
import { useSitzung } from '../auth/Sitzung'
import { t } from '../texte/de'
import { DarstellungWahl } from './DarstellungWahl'
import { Fehlergrenze } from './Fehlergrenze'

const navLink = 'flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-muted aria-[current=page]:bg-surface-2 aria-[current=page]:font-semibold aria-[current=page]:text-text'

export function Rahmen({ children }: { children: ReactNode }) {
  const { user, abmelden } = useSitzung()
  const { pathname } = useLocation()
  const offeneAlarme = (useAlarme().data ?? []).filter((a) => a.state === 'firing').length
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
            <DarstellungWahl />
            {user && <NavLink to="/konto" className="rounded-lg px-2 py-2 text-sm text-muted underline-offset-3 hover:underline aria-[current=page]:font-semibold aria-[current=page]:text-text">{t('rahmen.benutzer', { name: user.name, rolle: t(user.role === 'admin' ? 'rolle.admin' : 'rolle.betrachter') })}</NavLink>}
            <button type="button" onClick={() => void abmelden()} className="min-h-10 cursor-pointer rounded-lg border border-control px-3 text-sm">{t('rahmen.abmelden')}</button>
          </div>
        </div>
      </header>
      <main id="inhalt" tabIndex={-1} className="mx-auto flex max-w-310 flex-col gap-6 px-5 pt-7 pb-12">
        {/* Renderfehler einer Seite lassen Kopf und Navigation stehen; ein Seitenwechsel setzt die Grenze zurück */}
        <Fehlergrenze key={pathname}>{children}</Fehlergrenze>
      </main>
    </>
  )
}
