import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { ApiFehler } from '../api/client'
import { Feld } from './Feld'
import { Formular } from './Formular'

describe('Feld', () => {
  test('Label, Hinweis und Fehler sind mit dem Eingabefeld verbunden', () => {
    render(<Feld label="Name" hinweis="Dein Anzeigename" fehler="Pflichtfeld" />)
    const feld = screen.getByLabelText('Name')
    expect(feld).toHaveAccessibleDescription('Dein Anzeigename Pflichtfeld')
    expect(feld).toBeInvalid()
  })

  test('ohne Fehler ist das Feld gültig und ohne Beschreibung', () => {
    render(<Feld label="Name" />)
    expect(screen.getByLabelText('Name')).not.toHaveAttribute('aria-invalid')
    expect(screen.getByLabelText('Name')).not.toHaveAttribute('aria-describedby')
  })
})

describe('Formular', () => {
  test('zeigt die API-Meldung als Alert oben und behält die Eingaben', async () => {
    const user = userEvent.setup()
    render(
      <Formular absenden="Senden" onSubmit={() => Promise.reject(new ApiFehler(400, 'Falsch.'))}>
        <Feld label="Wert" defaultValue="" />
      </Formular>,
    )
    await user.type(screen.getByLabelText('Wert'), 'abc')
    await user.click(screen.getByRole('button', { name: 'Senden' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Falsch.')
    expect(screen.getByLabelText('Wert')).toHaveValue('abc')
    expect(screen.getByRole('button', { name: 'Senden' })).toBeEnabled()
  })

  test('sperrt doppeltes Absenden während der Anfrage', async () => {
    let fertig = () => {}
    const bei = vi.fn(() => new Promise<void>((r) => { fertig = r }))
    const user = userEvent.setup()
    render(<Formular absenden="Senden" onSubmit={bei}><Feld label="Wert" /></Formular>)
    const knopf = screen.getByRole('button', { name: 'Senden' })
    await user.click(knopf)
    await user.click(knopf)
    expect(bei).toHaveBeenCalledOnce()
    expect(knopf).toBeDisabled()
    fertig()
    await waitFor(() => expect(knopf).toBeEnabled())
  })
})
