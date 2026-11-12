import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { t, type TextSchluessel } from '../../texte/de'
import { useTitel } from '../../ui/useTitel'

const REITER = 'flex min-h-11 items-center border-b-[3px] border-transparent px-4 font-medium text-muted aria-[current=page]:border-accent aria-[current=page]:font-semibold aria-[current=page]:text-text'

// Rahmen aller Verwaltungsseiten (nur Admins, die Route ist geschützt): H1, Seitentitel und Bereichsnavigation als echte Links.
export function Verwaltung({ titel, children }: { titel: TextSchluessel; children: ReactNode }) {
  const text = t(titel)
  useTitel(text)
  return (
    <>
      <h1 tabIndex={-1} className="m-0 text-[1.625rem] leading-tight font-semibold">{text}</h1>
      <nav aria-label={t('verwaltung.navLabel')} className="flex flex-wrap gap-1 border-b border-border">
        <NavLink to="/verwaltung/server" className={REITER}>{t('verwaltung.navServer')}</NavLink>
        <NavLink to="/verwaltung/benutzer" className={REITER}>{t('verwaltung.navBenutzer')}</NavLink>
        <NavLink to="/verwaltung/tokens" className={REITER}>{t('verwaltung.navTokens')}</NavLink>
        <NavLink to="/verwaltung/audit" className={REITER}>{t('verwaltung.navAudit')}</NavLink>
        <NavLink to="/verwaltung/anmeldung" className={REITER}>{t('verwaltung.navAnmeldung')}</NavLink>
      </nav>
      {children}
    </>
  )
}
