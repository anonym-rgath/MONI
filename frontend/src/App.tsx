import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { Geschuetzt } from './auth/Waechter'
import { SitzungProvider } from './auth/Sitzung'
import { Einrichtung } from './seiten/anmeldung/Einrichtung'
import { Login } from './seiten/anmeldung/Login'
import { ZweiFaktorRoute } from './seiten/anmeldung/ZweiFaktorEinrichten'
import { ZweiterFaktor } from './seiten/anmeldung/ZweiterFaktor'
import { Platzhalter } from './seiten/Platzhalter'
import { Alarme } from './seiten/alarme/Alarme'
import { Kanaele } from './seiten/alarme/Kanaele'
import { Offen } from './seiten/alarme/Offen'
import { Regeln } from './seiten/alarme/Regeln'
import { Verlauf } from './seiten/alarme/Verlauf'
import { Uebersicht } from './seiten/uebersicht/Uebersicht'
import { Anmeldeanbieter } from './seiten/verwaltung/Anmeldeanbieter'
import { Audit } from './seiten/verwaltung/Audit'
import { Benutzer } from './seiten/verwaltung/Benutzer'
import { Server } from './seiten/verwaltung/Server'
import { Tokens } from './seiten/verwaltung/Tokens'
import { Verwaltung } from './seiten/verwaltung/Verwaltung'
import { DarstellungProvider } from './ui/Darstellung'
import { FokusNachWechsel } from './ui/FokusNachWechsel'
import { t } from './texte/de'
import { Fehlergrenze } from './ui/Fehlergrenze'
import { Ladezustand } from './ui/Ladezustand'
import { Rahmen } from './ui/Rahmen'

// Recharts steckt nur hier; die Seite lädt erst bei Bedarf, damit der Hauptteil klein bleibt.
const Serverdetail = lazy(() => import('./seiten/server/Serverdetail').then((m) => ({ default: m.Serverdetail })))

// Alle Routen aus D9. Seiten sind Platzhalter, bis die jeweiligen Tasks sie ersetzen.
export function Routen() {
  return (
    <Routes>
      <Route path="login" element={<Geschuetzt stage="none"><Login /></Geschuetzt>} />
      <Route path="einrichtung" element={<Geschuetzt stage="none"><Einrichtung /></Geschuetzt>} />
      <Route path="login/zweiter-faktor" element={<Geschuetzt stage="mfa"><ZweiterFaktor /></Geschuetzt>} />
      <Route path="einrichtung/zwei-faktor" element={<ZweiFaktorRoute />} />
      <Route element={<Geschuetzt stage="full"><Rahmen><Outlet /></Rahmen></Geschuetzt>}>
        <Route index element={<Uebersicht />} />
        <Route path="server/:id" element={<Fehlergrenze><Suspense fallback={<Ladezustand text={t('server.laden')} />}><Serverdetail /></Suspense></Fehlergrenze>} />
        <Route path="alarme" element={<Alarme><Offen /></Alarme>} />
        <Route path="alarme/verlauf" element={<Alarme><Verlauf /></Alarme>} />
        <Route path="alarme/regeln" element={<Alarme><Regeln /></Alarme>} />
        <Route path="alarme/kanaele" element={<Geschuetzt stage="full" rolle="admin"><Alarme><Kanaele /></Alarme></Geschuetzt>} />
        <Route path="verwaltung" element={<Geschuetzt stage="full" rolle="admin"><Outlet /></Geschuetzt>}>
          <Route index element={<Navigate to="server" replace />} />
          <Route path="server" element={<Verwaltung titel="seiten.verwaltungServer"><Server /></Verwaltung>} />
          <Route path="benutzer" element={<Verwaltung titel="seiten.benutzer"><Benutzer /></Verwaltung>} />
          <Route path="tokens" element={<Verwaltung titel="seiten.tokens"><Tokens /></Verwaltung>} />
          <Route path="audit" element={<Verwaltung titel="seiten.audit"><Audit /></Verwaltung>} />
          <Route path="anmeldung" element={<Verwaltung titel="seiten.anmeldung"><Anmeldeanbieter /></Verwaltung>} />
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
