import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { Einmalwerte } from './Einmalwerte'

const werte = ['aaaa-1111', 'bbbb-2222']

afterEach(() => vi.restoreAllMocks())

describe('Einmalwerte', () => {
  test('Weiter geht erst nach der Bestätigung', async () => {
    const fertig = vi.fn()
    const user = userEvent.setup()
    render(<Einmalwerte titel="Wiederherstellungscodes" werte={werte} onFertig={fertig} />)
    expect(screen.getByText('aaaa-1111')).toBeInTheDocument()
    const weiter = screen.getByRole('button', { name: 'Weiter' })
    expect(weiter).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Ich habe die Codes sicher gespeichert' }))
    expect(weiter).toBeEnabled()
    await user.click(weiter)
    expect(fertig).toHaveBeenCalledOnce()
  })

  test('Kopieren schreibt alle Werte in die Zwischenablage', async () => {
    const schreiben = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<Einmalwerte titel="Codes" werte={werte} onFertig={() => {}} />)
    // userEvent.setup() ersetzt navigator.clipboard; erst danach überschreiben
    vi.stubGlobal('navigator', { clipboard: { writeText: schreiben } })
    await user.click(screen.getByRole('button', { name: 'Kopieren' }))
    expect(schreiben).toHaveBeenCalledWith('aaaa-1111\nbbbb-2222')
    expect(await screen.findByRole('status')).toHaveTextContent('In die Zwischenablage kopiert.')
  })

  test('ohne Zwischenablage erscheint ein Hinweis statt eines Fehlers', async () => {
    const user = userEvent.setup()
    render(<Einmalwerte titel="Codes" werte={werte} onFertig={() => {}} />)
    vi.stubGlobal('navigator', {})
    await user.click(screen.getByRole('button', { name: 'Kopieren' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Kopieren ist hier nicht möglich')
  })

  test('Als Datei speichern: Blob, Dateiname, Object-URL wird wieder freigegeben', async () => {
    const erzeugen = vi.fn(() => 'blob:moni-test')
    const freigeben = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: erzeugen, revokeObjectURL: freigeben }))
    let dateiname = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { dateiname = this.download })
    const user = userEvent.setup()
    render(<Einmalwerte titel="Codes" werte={werte} onFertig={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Als Datei speichern' }))
    const blob = (erzeugen.mock.calls[0] as unknown as [Blob])[0]
    expect(await blob.text()).toBe('aaaa-1111\nbbbb-2222\n')
    expect(dateiname).toBe('moni-wiederherstellungscodes.txt')
    expect(freigeben).toHaveBeenCalledWith('blob:moni-test')
  })
})
