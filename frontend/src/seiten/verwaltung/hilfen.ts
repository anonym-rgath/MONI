import { t } from '../../texte/de'
import { lokal } from '../../ui/Zeit'

export const zeitpunkt = (iso: string | null) => (iso ? lokal(Date.parse(iso), Date.now()) : t('verwaltung.ohne'))
export const tag = (iso: string | null) => (iso ? new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium' }).format(Date.parse(iso)) : t('verwaltung.ohne'))
