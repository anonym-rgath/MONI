import { useEffect } from 'react'
import { t } from '../texte/de'

// Seitentitel nach dem Schema „Bereich – Moni“ (D9).
export function useTitel(bereich: string) {
  useEffect(() => { document.title = `${bereich} – ${t('app.titel')}` }, [bereich])
}
