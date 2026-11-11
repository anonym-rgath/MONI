import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { Geschuetzt } from './auth/Waechter'
import { SitzungProvider } from './auth/Sitzung'
import { Einrichtung } from './seiten/anmeldung/Einrichtung'
import { Login } from './seiten/anmeldung/Login'
import { ZweiFaktorEinrichten } from './seiten/anmeldung/ZweiFaktorEinrichten'
import { ZweiterFaktor } from './seiten/anmeldung/ZweiterFaktor'
import { Platzhalter } from './seiten/Platzhalter'
import { DarstellungProvider } from './ui/Darstellung'
import { FokusNachWechsel } from './ui/FokusNachWechsel'
import { Rahmen } from './ui/Rahmen'

// Alle Routen aus D9. Seiten sind Platzhalter, bis die jeweiligen Tasks sie ersetzen.
export function Routen() {
  return (
    <Routes>
      <Route path="login" element={<Geschuetzt stage="none"><Login /></Geschuetzt>} />
      <Route path="einrichtung" element={<Geschuetzt stage="none"><Einrichtung /></Geschuetzt>} />
      <Route path="login/zweiter-faktor" element={<Geschuetzt stage="mfa"><ZweiterFaktor /></Geschuetzt>} />
      <Route path="einrichtung/zwei-faktor" element={<Geschuetzt stage="enroll"><ZweiFaktorEinrichten /></Geschuetzt>} />
      <Route element={<Geschuetzt stage="full"><Rahmen><Outlet /></Rahmen></Geschuetzt>}>
        <Route index element={<Platzhalter titel="rahmen.uebersicht" />} />
        <Route path="server/:id" element={<Platzhalter titel="seiten.server" />} />
        <Route path="alarme" element={<Platzhalter titel="rahmen.alarme" />} />
        <Route path="alarme/verlauf" element={<Platzhalter titel="seiten.alarmVerlauf" />} />
        <Route path="alarme/regeln" element={<Platzhalter titel="seiten.alarmRegeln" />} />
        <Route path="alarme/kanaele" element={<Platzhalter titel="seiten.alarmKanaele" />} />
        <Route path="verwaltung" element={<Geschuetzt stage="full" rolle="admin"><Outlet /></Geschuetzt>}>
          <Route path="server" element={<Platzhalter titel="seiten.verwaltungServer" />} />
          <Route path="benutzer" element={<Platzhalter titel="seiten.benutzer" />} />
          <Route path="tokens" element={<Platzhalter titel="seiten.tokens" />} />
          <Route path="audit" element={<Platzhalter titel="seiten.audit" />} />
          <Route path="anmeldung" element={<Platzhalter titel="seiten.anmeldung" />} />
        </Route>
        <Route path="konto" element={<Platzhalter titel="seiten.konto" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export function App() {
  return (
    <DarstellungProvider>
      <BrowserRouter>
        <SitzungProvider>
          <FokusNachWechsel />
          <Routen />
        </SitzungProvider>
      </BrowserRouter>
    </DarstellungProvider>
  )
}
