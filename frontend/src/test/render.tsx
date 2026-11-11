import { render } from '@testing-library/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { api, type ApiFehler } from '../api/client'
import { Routen } from '../App'
import { SitzungProvider } from '../auth/Sitzung'
import { FokusNachWechsel } from '../ui/FokusNachWechsel'
import { DarstellungProvider } from '../ui/Darstellung'

function Ort() {
  const { pathname, search } = useLocation()
  return <output data-testid="ort">{pathname + search}</output>
}

// Testsonde im Sitzungs-Kontext: löst Anfragen über den echten Client aus und zeigt den Cache.
export function Sonde() {
  const qc = useQueryClient()
  const [wert, setWert] = useState('')
  const q = useQuery<unknown, ApiFehler>({ queryKey: ['sonde'], queryFn: () => api.get('/alerts'), enabled: false })
  return (
    <div>
      <button type="button" onClick={() => void q.refetch()}>Anfrage auslösen</button>
      <button type="button" onClick={() => void api.post('/sonde', {})}>Schreiben</button>
      <button type="button" onClick={() => qc.setQueryData(['x'], 'da')}>Wert setzen</button>
      <button type="button" onClick={() => setWert(String(qc.getQueryData(['x'])))}>Wert lesen</button>
      <output data-testid="wert">{wert}</output>
      <output data-testid="fehler">{q.error?.detail ?? ''}</output>
    </div>
  )
}

export const zeigeApp = (pfad: string, extra?: ReactNode) =>
  render(
    <DarstellungProvider>
      <MemoryRouter initialEntries={[pfad]}>
        <SitzungProvider>
          <FokusNachWechsel />
          <Routen />
          {extra}
        </SitzungProvider>
        <Ort />
      </MemoryRouter>
    </DarstellungProvider>,
  )
