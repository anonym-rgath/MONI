import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { useAlarme } from '../../api/abfragen'
import { useSitzung } from '../../auth/Sitzung'
import { t } from '../../texte/de'

const REITER = 'flex min-h-11 items-center border-b-[3px] border-transparent px-4 font-medium text-muted aria-[current=page]:border-accent aria-[current=page]:font-semibold aria-[current=page]:text-text'

// Rahmen aller Alarmseiten: H1 und Bereichsnavigation (echte Routen, aria-current über NavLink).
export function Alarme({ children }: { children: ReactNode }) {
  const { user } = useSitzung()
  const alarme = useAlarme().data
  const offen = alarme?.filter((a) => a.state === 'firing').length
  return (
    <>
      <h1 tabIndex={-1} className="m-0 text-[1.625rem] leading-tight font-semibold">{t('rahmen.alarme')}</h1>
      <nav aria-label={t('alarme.navLabel')} className="flex flex-wrap gap-1 border-b border-border">
        <NavLink to="/alarme" end className={REITER}>{offen === undefined ? t('alarme.navOffen') : t('alarme.navOffenMit', { n: offen })}</NavLink>
        <NavLink to="/alarme/verlauf" className={REITER}>{t('alarme.navVerlauf')}</NavLink>
        <NavLink to="/alarme/regeln" className={REITER}>{t('alarme.navRegeln')}</NavLink>
        {user?.role === 'admin' && <NavLink to="/alarme/kanaele" className={REITER}>{t('alarme.navKanaele')}</NavLink>}
      </nav>
      {children}
    </>
  )
}
