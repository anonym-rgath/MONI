import { useQueryClient } from '@tanstack/react-query'
import { schluessel } from '../../api/abfragen'

// Nach jedem Anmeldeschritt nur die Sitzung neu laden. Navigiert wird nicht hier: der Wächter (P6-R13) leitet von jeder Seite
// zur Seite der neuen Stage, bei full zum Rücksprungziel `weiter` oder zu /.
export function useNachSchritt() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: schluessel.sitzung })
}
