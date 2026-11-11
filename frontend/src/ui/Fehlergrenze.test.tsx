import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { lazy, Suspense } from 'react'
import { MemoryRouter, Link, Route, Routes } from 'react-router'
import { expect, test, vi } from 'vitest'
import { sitzungAdmin, sitzungMfa } from '../test/fixtures'
import { zeigeApp } from '../test/render'
import { server, sitzungAntwort } from '../test/server'
import { FokusNachWechsel } from './FokusNachWechsel'
import { Fehlergrenze } from './Fehlergrenze'

// Je eine Seite im Rahmen und eine Anmeldeseite werfen beim Rendern.
vi.mock('../seiten/konto/Konto', () => ({ Konto: () => { throw new Error('kaputt') } }))
vi.mock('../seiten/anmeldung/ZweiterFaktor', () => ({ ZweiterFaktor: () => { throw new Error('kaputt') } }))

test('fehlgeschlagenes Nachladen zeigt Hinweis mit Neu laden, die Navigation bleibt', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {}) // React meldet den gefangenen Fehler
  const Kaputt = lazy(() => Promise.reject(new Error('Failed to fetch dynamically imported module')))
  const neu = vi.fn()
  vi.stubGlobal('location', { ...location, reload: neu })
  render(
    <>
      <nav aria-label="Haupt">Navigation</nav>
      <Fehlergrenze><Suspense fallback="lädt"><Kaputt /></Suspense></Fehlergrenze>
    </>,
  )
  const hinweis = await screen.findByRole('alert')
  expect(hinweis).toHaveTextContent('Diese Seite konnte nicht geladen werden.')
  expect(screen.getByRole('navigation', { name: 'Haupt' })).toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: 'Neu laden' }))
  expect(neu).toHaveBeenCalled()
  vi.mocked(console.error).mockRestore()
})

test('Fokus geht auf die H1 einer nachladenden Seite', async () => {
  const Spaet = lazy(() => new Promise<{ default: () => React.JSX.Element }>((ok) => setTimeout(() => ok({ default: () => <h1 tabIndex={-1}>Spät da</h1> }), 20)))
  render(
    <MemoryRouter initialEntries={['/']}>
      <FokusNachWechsel />
      <Link to="/spaet">hin</Link>
      <Routes>
        <Route path="/" element={<h1 tabIndex={-1}>Start</h1>} />
        <Route path="/spaet" element={<Suspense fallback={<p>lädt</p>}><Spaet /></Suspense>} />
      </Routes>
    </MemoryRouter>,
  )
  await userEvent.setup().click(screen.getByRole('link', { name: 'hin' }))
  const h1 = await screen.findByRole('heading', { name: 'Spät da' })
  await vi.waitFor(() => expect(h1).toHaveFocus())
})

test('Renderfehler einer Seite: Hinweis im Rahmen, Navigation bleibt, Seitenwechsel erholt sich', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  server.use(sitzungAntwort(sitzungAdmin))
  zeigeApp('/konto')
  expect(await screen.findByRole('alert')).toHaveTextContent('Diese Seite konnte nicht geladen werden.')
  expect(screen.getByRole('navigation', { name: 'Hauptnavigation' })).toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('link', { name: 'Übersicht' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Übersicht' })).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  vi.mocked(console.error).mockRestore()
})

test('Renderfehler einer Anmeldeseite zeigt den Hinweis statt einer leeren App', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  server.use(sitzungAntwort(sitzungMfa))
  zeigeApp('/login/zweiter-faktor')
  expect(await screen.findByRole('alert')).toHaveTextContent('Diese Seite konnte nicht geladen werden.')
  vi.mocked(console.error).mockRestore()
})
