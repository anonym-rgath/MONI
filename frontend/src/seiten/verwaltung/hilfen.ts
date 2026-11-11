import { ApiFehler } from '../../api/client'
import { t } from '../../texte/de'
import { lokal } from '../../ui/Zeit'

// API-Texte (detail) erscheinen unverändert (D12); alles andere bekommt den allgemeinen Text.
export const fehlertext = (e: unknown) => (e instanceof ApiFehler ? e.detail : t('fehler.allgemein'))

export const zeitpunkt = (iso: string | null) => (iso ? lokal(Date.parse(iso), Date.now()) : t('verwaltung.ohne'))
export const tag = (iso: string | null) => (iso ? new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium' }).format(Date.parse(iso)) : t('verwaltung.ohne'))
