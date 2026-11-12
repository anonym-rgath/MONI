# Moni 2.0 – Paket 6: Oberfläche – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Manager bekommt eine neue Weboberfläche. Dazu gehören Anmeldung mit 2FA und OIDC, Ersteinrichtung, Übersicht aller Server, Serverdetail mit Zeiträumen, Alarme (offen, Verlauf, Regeln, Kanäle), Verwaltung (Server koppeln, Benutzer, Tokens, Audit, Anmeldeanbieter) und das eigene Konto. Sie läuft mit hellem und dunklem Design, erfüllt WCAG 2.2 AA und spricht nur die Manager-API an.

**Architecture:**
- **Neubau statt Umbau.** `frontend/` wird von Create React App auf **Vite + React 19 + TypeScript (strict)** umgestellt. Von der alten Oberfläche bleiben nur Ideen; `App.js` wird gelöscht.
- **Daten.** Ein typisierter `fetch`-Client spricht `/api/v1` gleicher Herkunft an, mit Session-Cookie und CSRF-Header. **TanStack Query** übernimmt Laden, Zwischenspeichern und Live-Aktualisierung (Abfrage alle 15 s, pausiert bei verborgenem Tab).
- **Routen.** **React Router** mit echten Adressen je Bereich (z. B. `/alarme/regeln`). Wächter prüfen den Anmeldezustand (`stage`) und die Rolle.
- **Gestaltung.** Design-Werte (Farben, Abstände, Radien, Schriftgrößen) liegen zentral als CSS-Variablen für hell und dunkel. Tailwind greift nur auf diese Variablen zu. Spätere Optik-Änderungen betreffen damit eine Datei.
- **Texte.** Alle Texte für Menschen stehen zentral in `src/texte/de.ts` (Spec §5: Englisch später möglich).
- **Auslieferung.** Unverändert über nginx im Manager-Image, jetzt aus `frontend/dist`, mit einer strengeren CSP ohne externe Quellen.

**Tech Stack:**
- Vite 7, React 19, TypeScript 5 (strict), React Router 7, TanStack Query 5, Tailwind CSS 4, Recharts 3, lucide-react.
- `qrcode` für den TOTP-QR-Code, lokal erzeugt. `@fontsource/ibm-plex-sans` und `@fontsource/jetbrains-mono` liefern die Schriften selbst aus.
- Tests: Vitest, Testing Library, MSW 2 und vitest-axe, dazu Playwright für einen Browser-Smoke in der CI.
- Lint: ESLint 9 mit typescript-eslint, react-hooks und jsx-a11y.
- Paketmanager: **npm** mit `package-lock.json`.

**Spec:** [`docs/superpowers/specs/2026-11-04-moni-agent-manager-design.md`](../specs/2026-11-04-moni-agent-manager-design.md) (§6.9 Oberfläche, §6.7 Rollen, §5 Texte zentral, §7 Frontend-Lint/Build in der CI) · Übersicht: [`2026-11-04-moni-2-uebersicht.md`](2026-11-04-moni-2-uebersicht.md) · Vorgänger: [Paket 5](2026-11-05-moni-2-paket-5-alarmierung.md)

**Entwurf:** klickbarer Entwurf (Übersicht, Serverdetail, Alarme, Anmeldung; privat beim Autor): https://claude.ai/artifact/5JaA8GcGAZPXmDiVWHekJS. Er wurde mit dem apple-design-Skill (nur Grundlagen, keine iOS-Konventionen) und gegen WCAG 2.2 AA geprüft. Die Werte unten stammen aus dieser geprüften Fassung. Feinheiten der Optik passt der Autor nach der Umsetzung an.

## Ist-Stand (Schnittstellen, auf die dieser Plan baut)

- **Alte Oberfläche:** `frontend/` (CRA 5, `craco`, Yarn 1, JavaScript), eine Datei `src/App.js`, die die entfernten `/api/metrics/*` abfragt. Das Dockerfile baut sie im Stage `frontend-builder` (`node:26-alpine`, `yarn build`) und kopiert `frontend/build` nach `/app/frontend/build`.
- **nginx** (`backend/moni/manager/nginx.py`, `_BODY`): `location /` liefert `/app/frontend/build` mit `try_files $uri $uri/ /index.html`. Die CSP erlaubt heute noch `fonts.googleapis.com` und `fonts.gstatic.com` sowie `style-src 'unsafe-inline'`.
- **Manager-API v1** (alle unter `/api/v1`):
  - **Ohne Anmeldung:** `GET /health`, `GET /setup` → `{required}`, `POST /setup` `{setup_token, email, name, password}` → `{stage:"enroll"}`, `POST /auth/login` `{email, password}` → `{stage:"mfa"|"enroll"}`, `GET /auth/oidc` → `{providers:[{id,label}]}`, `GET /auth/oidc/start?provider=` (Weiterleitung). Ein OIDC-Fehler landet auf `/login?fehler=oidc`, ein Erfolg auf `/`.
  - **Während der Anmeldung:**
    - `POST /auth/login/mfa` `{code}` (TOTP oder Wiederherstellungscode) → `{stage:"full"}`.
    - `GET /auth/session` → `{stage, csrf_token, user:{id,email,name,role,kind,totp_enabled}}`.
    - `POST /auth/totp/setup` `{current_code?}` → `{secret, otpauth_uri}`. In Stage `full` mit aktivem TOTP braucht es `current_code`; so lässt sich das Gerät wechseln.
    - `POST /auth/totp/confirm` `{code}` → `{recovery_codes}`.
    - `POST /auth/logout`.
  - **Konto:** `POST /account/password` `{current_password, new_password}`, `POST /account/recovery-codes` `{code}` → neue Codes.
  - **Server:**
    - `GET /servers` und `GET /servers/{id}`.
    - `POST /servers`, entweder im Token-Modus `{name, url, token, headers?}` oder per mTLS `{name, pairing_code, url?}`.
    - `PATCH /servers/{id}` `{enabled}` (es gibt kein Umbenennen) und `DELETE /servers/{id}`.
    - `GET /servers/{id}/snapshot`, `GET /servers/{id}/series`, `GET /servers/{id}/history?metric=&from=&to=&resolution=auto&label=k=v`.
    - `PUT|DELETE /servers/{id}/mute` `{until}`.
    - `GET|POST /servers/{id}/agent-tokens` und `DELETE /servers/{id}/agent-tokens/{token_id}`.
  - **Alarme:**
    - `GET /alerts` und `GET /alerts/history?limit&before&server_id`.
    - `GET|POST /alert-rules` und `PATCH|DELETE /alert-rules/{id}`.
    - `GET|POST /alert-channels`, `PATCH|DELETE /alert-channels/{id}` und `POST /alert-channels/{id}/test` → `{ok, error}`.
    - `GET|PUT /alert-settings`.
  - **Verwaltung:**
    - `GET|POST /users`, `PATCH|DELETE /users/{id}`, `POST /users/{id}/password|reset-2fa|unlock`.
    - `GET|POST /tokens` und `DELETE /tokens/{id}`; das Token selbst steht nur einmal in der POST-Antwort.
    - `GET /audit?limit&before`.
    - `GET /auth/oidc/providers`, `PUT|DELETE /auth/oidc/providers/{pid}` und `PUT /auth/oidc/public-url`.
- **Antworten der API:**
  - Fehler kommen als RFC 9457 (`application/problem+json`, Feld `detail` mit deutschem Text).
  - Zeitstempel sind ISO 8601 UTC mit `Z`.
  - Jede schreibende Anfrage aus dem Browser braucht den Header `X-CSRF-Token` mit dem Wert aus `/auth/session`.
- **Server-JSON** (`manager/api.py` `_out`): `id, name, url, is_local, enabled, status (unknown|ok|unreachable|error), failures, last_error, last_seen, header_names, warnings, agent_version, security, cert_not_after, muted_until`. Kennzahlen wie CPU oder RAM fehlen; Task 4 ergänzt sie.
- **Snapshot-Struktur:** wie die Agent-API (`backend/moni/agent/`, `metrics_model.flatten`): Host (CPU, RAM, Last, Laufzeit, Temperaturen), Dateisysteme, Netzwerk je Interface, Container, Docker-Status, `warnings`.
- **CI** (`.github/workflows/ci.yml`): Jobs `backend-tests` und `build`. Der Build-Job baut beide Images und führt danach die Smoke-Schritte aus, zuletzt „Kopplung (mTLS)“ und „Alarmkanal“. Der Status liegt in `$RUNNER_TEMP/ci-recovery`, die Wiederherstellungscodes werden mit `_moni.take_recovery` verbraucht.
- **Dependabot:** Der npm-Stream für `/frontend` existiert schon, mit einer Gruppe, die `react-scripts` nennt.
- **Nachlauf früherer Pakete, für die Oberfläche bestimmt:**
  - **Paket 4:**
    - Bei Fehlern nach erfolgter Kopplung auf `pair reset` hinweisen.
    - Der 401-Handler darf `"Kopplungs-Token ungültig"` nicht als „Sitzung abgelaufen“ deuten.
    - Für unbekannte Agent-Token-IDs 404 statt 502 zurückgeben.
    - `GET agent-tokens` in den Test aufnehmen, der prüft, was Betrachter lesen dürfen.
  - **Paket 5:**
    - Ein Regel-PATCH ohne Änderung schreibt kein Audit, die Oberfläche meldet trotzdem „gespeichert“.
    - Test-Versand mit kaputtem Geheimnis liefert 500 → Task 4 macht daraus einen festen Text.

## Entscheidungen dieses Plans (vom Planer gesetzt)

Im Ledger werden sie als Rulings P6-D1 bis P6-D12 geführt.

- **D1 Stack:** Vite, React 19, TypeScript `strict` (dazu `noUncheckedIndexedAccess`), React Router 7 im deklarativen Modus, TanStack Query 5, Tailwind 4 über `@tailwindcss/vite`, Recharts 3, lucide-react. `axios`, `craco`, `react-scripts` und Yarn entfallen.
  - Die Hauptversionen im Tech Stack sind Untergrenzen. Genommen wird die jeweils aktuelle stabile Version zum Zeitpunkt der Umsetzung.
  - Versionen werden exakt gepinnt (kein `^`), `package-lock.json` wird committet.
  - Alle Abhängigkeiten sind Open Source (Spec §5).
- **D2 Ordnerstruktur** unter `frontend/src/`:
  - `api/`: Client, Typen, Query-Hooks.
  - `auth/`: Sitzung und Wächter.
  - `ui/`: Bausteine wie Knopf, Feld, Badge, Dialog, Tabelle, Statusmarke, Hinweis und Leerzustand.
  - `seiten/<bereich>/`.
  - `texte/de.ts`.
  - `stil/tokens.css`.
  - `test/`: MSW-Handler und Fixtures.
- **D3 Design-Werte** (aus dem geprüften Entwurf; alle Kontraste berechnet):

| Token | Dunkel | Hell | Rolle |
|---|---|---|---|
| `--bg` | `#07090B` | `#F4F6F8` | Seitenhintergrund |
| `--surface` | `#0E1215` | `#FFFFFF` | Karten, Kopfzeile |
| `--surface-2` | `#161C21` | `#EAEFF3` | aktive Auswahl, neutrale Marken |
| `--border` | `#242C34` | `#D6DDE3` | nur Trennlinien (dekorativ) |
| `--control` | `#6E7A86` | `#7B8794` | Rahmen von Feldern und Zweitknöpfen (4,29 bzw. 3,38:1) |
| `--text` | `#E8ECEF` | `#10171E` | Text (≥ 14,46:1) |
| `--muted` | `#A0AAB4` | `#56626E` | Nebentext, Platzhalter (≥ 5,39:1) |
| `--accent` | `#5AB4FF` | `#0B6BCB` | Links, Hauptknopf, Diagrammlinie |
| `--on-accent` | `#06121D` | `#FFFFFF` | Schrift auf Akzent (8,46 bzw. 5,28:1) |
| `--focus` | `#5AB4FF` | `#0B6BCB` | Fokusrahmen 3 px, Abstand 2 px (≥ 4,56:1) |
| `--ok` / `--ok-bg` | `#3DDC84` / `rgba(61,220,132,.13)` | `#137A47` / `#E3F5EB` | Status OK |
| `--warn` / `--warn-bg` | `#F5B83D` / `rgba(245,184,61,.15)` | `#8F5A00` / `#FBF0DC` | Status Warnung |
| `--crit` / `--crit-bg` | `#FF6B85` / `rgba(255,107,133,.15)` | `#C21F43` / `#FBE4E9` | Status Kritisch, nicht erreichbar |
| `--track` | `#1D252C` | `#E2E8ED` | Balkenspur |

  - **Schrift:** IBM Plex Sans (400, 500, 600) für Text, JetBrains Mono (400, 500) mit `tabular-nums` für Messwerte.
  - **Größen in rem:** Text 0,9375 rem, klein 0,8125 rem als Untergrenze, H1 1,625 rem, H2 1,0625 rem.
  - **Abstände** im 4-px-Raster, Radien 8 px bei Feldern und 14 px bei Karten.
  - **Klickflächen:** mindestens 24 × 24 px (WCAG 2.5.8), Hauptaktionen 44 px hoch.
- **D4 Darstellung:** Standard „Auto“ folgt `prefers-color-scheme`. „Hell“ und „Dunkel“ sind in der Kopfzeile und im Konto wählbar. Die Wahl steht als Komfortwert in `localStorage` unter `moni.darstellung`; ist sie nicht lesbar, gilt „Auto“. Umgesetzt wird sie über `data-theme` am `<html>`. Ein Inline-Skript gegen kurzes Aufblitzen gibt es nicht (CSP); stattdessen setzt der erste Render das Attribut.
- **D5 Sicherheit im Frontend:**
  - **Keine Geheimnisse im Browser-Speicher.** Die Sitzung ist das HttpOnly-Cookie. `localStorage` enthält nur `moni.darstellung`.
  - **Einmalwerte** (API-Token, Agent-Token, Wiederherstellungscodes, TOTP-Secret und -URI, Kopplungs-Antworten) liegen nur im Zustand des jeweiligen Dialogs. Beim Schließen werden sie verworfen. Sie gehen nie in den Query-Cache (Mutationen mit `gcTime: 0`, kein `setQueryData`) und nie in Logs.
  - **Kein `dangerouslySetInnerHTML`** (ESLint `react/no-danger` als Fehler), kein `eval`, keine dynamischen `import()` aus Daten. Alle Serverdaten werden als Text gerendert.
  - **Anfragen:** nur relativ an `/api/v1`, `credentials: 'same-origin'`, `X-CSRF-Token` bei POST/PUT/PATCH/DELETE.
  - **Externe Links** mit `rel="noopener noreferrer"`. Der OIDC-Start ist eine echte Seitennavigation.
  - **Kein Source-Map** im Produktions-Build.
- **D6 CSP** (in `nginx.py`, Paket 4 D7 bleibt sonst gleich): `default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; require-trusted-types-for 'script'`.
  - `data:` ist nur für den QR-Code als Bild nötig.
  - React setzt `style`-Props über CSSOM, das erlaubt die CSP auch ohne `'unsafe-inline'`.
  - Ob alles unter dieser CSP läuft, beweist der Browser-Smoke in Task 10. Bricht eine Bibliothek daran, folgt ein Ruling mit der kleinstmöglichen Lockerung.
- **D7 Live-Daten:**
  - `refetchInterval` 15 s für `/servers`, `/alerts` und den Snapshot im Serverdetail; der Verlauf alle 60 s.
  - Kein Abruf bei verborgenem Tab (`refetchIntervalInBackground: false`).
  - Abfragen werden nicht wiederholt, wenn der Status 401 oder 403 lautet.
  - „aktualisiert vor X s“ zeigt das Alter der letzten erfolgreichen Antwort.
- **D8 Sitzung und 401:**
  - Ein 401 auf eine Anfrage mit Session führt zur Abmeldung im Client: Cache leeren und zu `/login?abgelaufen=1` mit Rücksprungziel springen.
  - **Ausnahme:** Lautet `detail` genau `"Kopplungs-Token ungültig"` (Kopplung), bleibt die Sitzung bestehen, und der Fehler erscheint am Formular (Nachlauf Paket 4).
  - Ein 403 zeigt „Keine Berechtigung“ am Ort, ohne Abmeldung.
- **D9 Routen und Wächter:**
  - Ohne Sitzung: `/login`, `/login/zweiter-faktor` (Stage `mfa`), `/einrichtung` (nur wenn `GET /setup` `required` liefert) und `/einrichtung/zwei-faktor` (Stage `enroll`).
  - Mit Stage `full`:
    - `/` (Übersicht) und `/server/:id`.
    - `/alarme`, `/alarme/verlauf`, `/alarme/regeln` und `/alarme/kanaele`.
    - `/verwaltung/server`, `/verwaltung/benutzer`, `/verwaltung/tokens`, `/verwaltung/audit` und `/verwaltung/anmeldung`.
    - `/konto`.
  - Betrachter sehen keine Admin-Bereiche und keine Admin-Aktionen. Ein direkter Aufruf zeigt „Keine Berechtigung“.
  - Der Seitentitel folgt dem Schema „Bereich – Moni“.
  - Nach einem Routenwechsel geht der Fokus auf die H1 (`tabIndex=-1`).
- **D10 Kennzahlen in der Serverliste:** `GET /servers` bekommt je Server ein Feld `summary`, berechnet aus `last_snapshot`, sonst `null`:
  - `{cpu_percent, memory_percent, temperature_max_c, filesystem_max: {mountpoint, percent} | null, containers_running, containers_total}`.
  - Jeder fehlende Wert ist `null`, es gibt keine Ersatzwerte.
  - So reicht eine einzige Anfrage für die Übersicht.
- **D11 Barrierefreiheit (WCAG 2.2 AA) als Abnahme:**
  - Sprunglink „Zum Inhalt springen“, Landmarken (`header`, `nav`, `main`) und eine H1 je Seite.
  - Status immer auch als Text. Diagramme haben eine sichtbare Zusammenfassung, die auch im `aria-label` steht.
  - Zweite Datenreihe gestrichelt, Warnschwelle gepunktet.
  - Tabellen mit `scope`, ihr Scrollbereich ist per Tastatur erreichbar.
  - Ergebnisse von Aktionen werden über `role="status"` angesagt.
  - Fehler stehen direkt am Feld (`aria-describedby`, `aria-invalid`).
  - Dialoge mit `<dialog>` und `showModal()`: Fokus wird gefangen, Esc schließt, der Fokus kehrt zurück.
  - Bei 320 px Breite gibt es kein seitliches Scrollen, und 200 % Schriftgröße bleibt nutzbar.
  - Jede Seite hat einen axe-Test ohne Verstöße.
- **D12 Texte und Begriffe:**
  - Schreibweise im Satzstil. Knöpfe nennen die Handlung („Server koppeln“, „Testnachricht senden“), Zeiträume heißen „1 Std., 24 Std., 7 Tage, 30 Tage, 1 Jahr“.
  - Regeln in Klartext („Löst aus, wenn …“), technische Metriknamen nur als Zusatz.
  - Fehlertexte der API (`detail`) erscheinen unverändert, denn sie sind fest und deutsch. Eigene Fehlertexte sagen, was zu tun ist.
  - Leere Zustände bieten den nächsten Schritt an, etwa „Noch keine Server. Server koppeln“.

## Global Constraints

- Branch `feature/moni-2`, Draft-PR #41 (nicht mergen). Commits mit Autor-E-Mail `261156405+anonym-rgath@users.noreply.github.com` (repo-lokal gesetzt). Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend-Tests: `cd backend && uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests -q`. Frontend: `cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build`. Beides bleibt grün.
- Node 24 LTS oder neuer lokal; im Dockerfile bleibt `node:26-alpine` mit Digest. `npm ci --ignore-scripts` im Image. Braucht ein Paket Install-Skripte, folgt ein Ruling.
- Backend: keine neue Abhängigkeit. Frontend: nur die Pakete aus dem Tech Stack. Weitere brauchen ein Ruling mit Begründung.
- Alle Texte für Menschen auf Deutsch mit korrekten Umlauten und nur aus `src/texte/de.ts`. Der Lint prüft das nicht; das Review prüft es.
- Keine Geheimnisse in `localStorage`, `sessionStorage`, URLs, Logs oder im Query-Cache (D5). Keine externen Ressourcen zur Laufzeit (Schriften, Bilder, Skripte).
- Zeitangaben in der Oberfläche: lokale Zeit im Format `de-DE` (`Intl.DateTimeFormat`), Dauern relativ („vor 12 min“). An die API gehen nur ISO 8601 mit `Z`.
- WCAG 2.2 AA nach D11; Kontrast nur mit den Tokens aus D3.
- Bestehende Smoke-Schritte der CI bleiben unverändert grün.

## Review Focus

1. **Sitzungsende und Rechte:** Läuft eine Sitzung ab, landet man mit Rücksprungziel auf `/login`, ohne Endlosschleife und ohne Datenreste anderer Benutzer im Cache. Ein Betrachter sieht nirgends Admin-Knöpfe. „Kopplungs-Token ungültig“ meldet den Benutzer nicht ab. Tests in Task 2, 8 und 10.
2. **Einmalwerte:** Ein API-Token, ein Agent-Token, Wiederherstellungscodes oder ein TOTP-Secret erscheinen genau einmal. Sie sind nach dem Schließen des Dialogs aus Zustand und Cache verschwunden und stehen nie im DOM einer anderen Seite. Tests in Task 3, 8 und 9.
3. **Kaputte oder fehlende Daten:** Ein Server ohne Snapshot, ein nicht erreichbarer Server, ein Snapshot ohne Temperatur oder Docker, leere Listen oder ein Manager, der 500 bzw. gar nicht antwortet, zeigen verständliche Zustände („keine Daten“, „Manager nicht erreichbar, neuer Versuch in 15 s“) statt weißer Seite oder `NaN`. Tests in Task 5 und 6.
4. **Formulare mit Server-Validierung:** Ein 422 oder 409 der API erscheint am Formular, mit `detail` am passenden Feld oder oben im Formular. Doppeltes Absenden ist gesperrt, solange die Anfrage läuft. Eingaben gehen bei einem Fehler nicht verloren. Tests in Task 3, 7 und 8.
5. **CSP und Barrierefreiheit im echten Browser:** Die Seite lädt unter der strengen CSP ohne Verstöße in der Konsole, ist komplett per Tastatur bedienbar, und axe meldet nichts. Test in Task 10.

---

### Task 1: Grundgerüst mit Vite, Design-Werten, App-Rahmen und Build-Integration

**Files:**
- Delete: `frontend/src/App.js`, `frontend/src/App.css`, `frontend/src/index.js`, `frontend/src/index.css`, `frontend/craco.config.js`, `frontend/jsconfig.json`, `frontend/postcss.config.js`, `frontend/tailwind.config.js`, `frontend/yarn.lock`, `frontend/public/*` (bis auf Favicon, falls vorhanden)
- Create: `frontend/package.json` (neu), `frontend/package-lock.json`, `frontend/index.html`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/eslint.config.js`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/stil/tokens.css`, `frontend/src/stil/app.css`, `frontend/src/texte/de.ts`, `frontend/src/ui/Darstellung.tsx`, `frontend/src/ui/Rahmen.tsx` (Kopfzeile, Navigation, Sprunglink, `<main>`), `frontend/src/test/setup.ts`, `frontend/src/ui/Rahmen.test.tsx`
- Modify: `Dockerfile` (Stage `frontend-builder`: `npm ci --ignore-scripts`, `npm run build`; kopiert `frontend/dist` nach `/app/frontend/dist`), `backend/moni/manager/nginx.py` (`root /app/frontend/dist`, CSP nach D6), `backend/tests/test_nginx_conf.py`, `.github/workflows/ci.yml` (neuer Job `frontend`: `npm ci`, `lint`, `typecheck`, `test`, `build`), `.github/dependabot.yml` (Gruppe `react` ohne `react-scripts`; Gruppe `vite` für `vite`, `@vitejs/*`, `vitest`)

**Interfaces:**
- Produces:
  - **npm-Skripte:** `dev`, `build` (`tsc -b && vite build`), `lint`, `typecheck` (`tsc -b --noEmit`), `test` (`vitest run`), `test:e2e` (Task 10).
  - **Entwicklung:** Der Vite-Dev-Server leitet `/api` an `https://localhost:8443` weiter (`secure: false`, nur Dev).
  - **`t(key)`:** einfacher Zugriff auf `de.ts` (verschachteltes Objekt, typisiert über `typeof de`). Platzhalter `{name}` werden ersetzt.
  - **`useDarstellung(): { wahl: 'auto'|'hell'|'dunkel', setzen(w) }`:** setzt `data-theme` (`light`/`dark`) am `<html>`, liest `matchMedia` und reagiert auf Änderungen.
  - **`<Rahmen>`:** Sprunglink, Kopfzeile mit Logo (neutraler Ring), Hauptnavigation (Übersicht, Alarme mit Zähler, Verwaltung nur für Admins), Darstellung, Benutzername mit Rolle, „Abmelden“ und `<main id="inhalt">`. Bis Task 2 kommt die Rolle aus Props.
  - **Tailwind:** Das Theme bildet nur die Tokens aus D3 ab (`bg-surface`, `text-muted`, `border-control` …). Freie Hex-Werte im Code verbietet das Review.

- [ ] **Step 1: Failing tests** (Vitest mit jsdom, Testing Library, vitest-axe)

```ts
test('Darstellung folgt dem System und lässt sich überschreiben', async () => {
  mockMatchMedia({ light: true }); render(<App />)
  expect(document.documentElement.dataset.theme).toBe('light')
  await user.click(screen.getByRole('button', { name: 'Dunkel' }))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(localStorage.getItem('moni.darstellung')).toBe('dunkel')
})
test('Darstellung ohne lesbaren Speicher fällt auf Auto zurück', ...)   // localStorage wirft
test('Sprunglink führt zu main und ist der erste Tab-Stopp', ...)
test('Rahmen hat keine axe-Verstöße', async () => { expect(await axe(container)).toHaveNoViolations() })
test('Verwaltung ist für Betrachter nicht in der Navigation', ...)
```

```python
def test_csp_has_no_external_sources_and_no_unsafe_inline():
    conf = nginx.render(settings)
    assert "googleapis" not in conf and "gstatic" not in conf and "'unsafe-inline'" not in conf
    assert "require-trusted-types-for 'script'" in conf and "object-src 'none'" in conf
    assert "root /app/frontend/dist;" in conf
```

- [ ] **Step 2–4:** Tests rot → Implementieren → Frontend und Backend grün. Das Manager-Image lokal bauen (`docker build --target manager .`): `curl -sk https://127.0.0.1:8443/` liefert `index.html`, und `/assets/*.js` antwortet mit 200.
- [ ] **Step 5: Commit** `feat(ui): Vite-Grundgerüst mit Design-Werten und App-Rahmen`

---

### Task 2: API-Client, Sitzung und Routenwächter

**Files:**
- Create: `frontend/src/api/client.ts`, `frontend/src/api/typen.ts`, `frontend/src/api/abfragen.ts` (Query-Keys und Hooks), `frontend/src/auth/Sitzung.tsx`, `frontend/src/auth/Waechter.tsx`, `frontend/src/test/server.ts` (MSW), `frontend/src/test/fixtures.ts`, Tests daneben
- Modify: `frontend/src/App.tsx` (Router und `QueryClientProvider`), `frontend/src/ui/Rahmen.tsx` (Benutzer aus der Sitzung)

**Interfaces:**
- Consumes: `t`, `<Rahmen>` (Task 1); die API aus dem Ist-Stand.
- Produces:
  - **`class ApiFehler extends Error { status: number; detail: string }`.**
  - **`api.get<T>(pfad, params?)`, `api.post|put|patch<T>(pfad, body)`, `api.delete(pfad)`:**
    - Relativ zu `/api/v1`, `credentials: 'same-origin'`, JSON hin und zurück.
    - Bei schreibenden Methoden der Header `X-CSRF-Token` aus der Sitzung.
    - Antwortet die API mit `problem+json`, wird daraus ein `ApiFehler` mit `detail`. Bei einer anderen Fehlerantwort lautet `detail` „Der Manager hat unerwartet geantwortet ({status}).“, bei einem Netzwerkfehler „Manager nicht erreichbar.“ (Status 0).
    - 204 ergibt `undefined`.
  - **`typen.ts`:** handgeschriebene Typen für Session, Server (mit `summary` aus Task 4), Snapshot, Serie, Verlaufspunkt, Alarm, Verlaufseintrag, Regel, Kanal, Benutzer, Token, Agent-Token, Audit-Eintrag und OIDC-Anbieter. Grundlage sind die Antworten der Endpunkte (bei Bedarf in `backend/moni/**` nachlesen); unbekannte Felder sind optional.
  - **`<SitzungProvider>` mit `useSitzung(): { stage, user, csrf, laden, abmelden() }`:**
    - Lädt `GET /auth/session`. Bei 401 gilt die Stage `none`.
    - `abmelden()` sendet `POST /auth/logout`, leert `queryClient.clear()` und springt nach `/login`.
  - **Globaler 401-Handler (D8):**
    - Er hängt am `QueryCache` und `MutationCache` (`onError`).
    - Bei Stage `full` und `status === 401` außer bei `detail === 'Kopplungs-Token ungültig'` leert er den Cache und springt nach `/login?abgelaufen=1&weiter=<aktueller Pfad>`.
    - `weiter` muss mit `/` beginnen und darf nicht `//` enthalten (Schutz vor Open Redirect).
  - **`<Geschuetzt stage="full" rolle?="admin">`:** leitet je nach Stage weiter, also `none` → `/login`, `mfa` → `/login/zweiter-faktor`, `enroll` → `/einrichtung/zwei-faktor`. Bei der falschen Rolle zeigt er „Keine Berechtigung“.
  - **Query-Defaults:** `retry` nur bei Netzwerkfehler und höchstens 2-mal, `refetchOnWindowFocus: true`, `refetchIntervalInBackground: false`.
  - **Fokus nach Routenwechsel** auf die H1 (D9).

- [ ] **Step 1: Failing tests** (MSW)

```ts
test('schreibende Anfrage sendet den CSRF-Header aus der Sitzung', ...)
test('problem+json wird zu ApiFehler mit detail', ...)
test('401 mit Sitzung führt zu /login mit abgelaufen und Rücksprungziel', ...)
test('401 mit "Kopplungs-Token ungültig" meldet nicht ab', ...)
test('weiter=//evil.example wird verworfen', ...)
test('Stage mfa wird auf /login/zweiter-faktor geleitet, enroll auf /einrichtung/zwei-faktor', ...)
test('Betrachter bekommt auf /verwaltung/benutzer "Keine Berechtigung"', ...)
test('abmelden leert den Query-Cache', ...)
test('Netzwerkfehler ergibt "Manager nicht erreichbar."', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): API-Client, Sitzung und Routenwächter`

---

### Task 3: Anmeldung, zweiter Faktor, OIDC und Ersteinrichtung

**Files:**
- Create: `frontend/src/seiten/anmeldung/{Login,ZweiterFaktor,Einrichtung,ZweiFaktorEinrichten}.tsx`, `frontend/src/ui/{Feld,Knopf,Hinweis,Formular,Einmalwerte}.tsx`, Tests daneben

**Interfaces:**
- Consumes: `api`, `useSitzung`, Routen (Task 2).
- Produces:
  - **`<Feld label hinweis? fehler? ...input>`:** Label sitzt per `htmlFor`, `aria-describedby` zeigt auf Hinweis und Fehler, `aria-invalid`.
  - **`<Formular onSubmit>`:** sperrt den Absende-Knopf während der Anfrage und zeigt `ApiFehler.detail` oben im Formular (`role="alert"`). Eingaben bleiben stehen.
  - **`<Einmalwerte titel werte[] onFertig>`:** zeigt Codes in Monoschrift, mit „Kopieren“ (`navigator.clipboard`), „Als Datei speichern“ (Blob, `moni-wiederherstellungscodes.txt`) und der Pflichtbestätigung „Ich habe die Codes sicher gespeichert“. Erst danach geht es weiter. Die Werte liegen nur im Komponentenzustand (D5).
  - **Login (`/login`):**
    - Felder E-Mail (`autocomplete="username"`) und Passwort (`current-password`), nie vorausgefüllt.
    - Die Antwort `stage` bestimmt das Ziel.
    - Gibt es OIDC-Anbieter (`GET /auth/oidc`), erscheinen sie als Links „Mit {label} anmelden“ zu `/api/v1/auth/oidc/start?provider={id}`.
    - Bei `?fehler=oidc` erscheint „Die Anmeldung über den Anbieter ist fehlgeschlagen. Versuche es erneut oder melde dich mit Passwort an.“, bei `?abgelaufen=1` „Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.“
    - Liefert `GET /setup` `required: true`, geht es weiter zu `/einrichtung`.
  - **Zweiter Faktor:** Eingabe des TOTP-Codes (`inputmode="numeric"`, `autocomplete="one-time-code"`). Ein Umschalter wechselt auf den Wiederherstellungscode. Nach Erfolg folgen `invalidate` der Sitzung und ein Sprung zu `weiter` bzw. `/`.
  - **Ersteinrichtung:** Felder Setup-Token, Name, E-Mail und Passwort, dazu der Hinweis, dass das Setup-Token im Log des Managers steht.
  - **Zwei-Faktor einrichten:**
    1. `POST /auth/totp/setup` liefert die Daten für den QR-Code (`qrcode.toDataURL`, als `<img alt="QR-Code für die Authenticator-App">`) und das Secret als Text zum Abtippen.
    2. Der Code wird mit `POST /auth/totp/confirm` bestätigt.
    3. Danach erscheinen die Wiederherstellungscodes mit `<Einmalwerte>`, dann geht es zu `/`.

- [ ] **Step 1: Failing tests**

```ts
test('Login mit stage mfa führt zur Codeeingabe, mit enroll zur Einrichtung', ...)
test('falsches Passwort zeigt detail der API und behält die E-Mail', ...)
test('OIDC-Anbieter erscheinen als Links mit richtiger start-URL; ohne Anbieter keine Trennlinie', ...)
test('fehler=oidc und abgelaufen=1 zeigen ihre Hinweise', ...)
test('Wiederherstellungscodes: Weiter erst nach Bestätigung; nach Verlassen nicht mehr im DOM oder Cache', ...)
test('QR-Code ist ein img mit Alt-Text, Secret steht als Text daneben', ...)
test('Absenden ist während der Anfrage gesperrt', ...)
test('Anmeldeseiten ohne axe-Verstöße', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Anmeldung, zweiter Faktor und Ersteinrichtung`

---

### Task 4: Backend – Kennzahlen in der Serverliste und Nachlauf

**Files:**
- Modify: `backend/moni/manager/api.py` (`_out`: Feld `summary`; Agent-Token-Fehler), `backend/moni/manager/alert_api.py` (Test-Versand bei kaputtem Geheimnis), `backend/tests/test_manager_api.py`, `backend/tests/test_alert_api.py`, `backend/tests/test_route_inventory.py`

**Interfaces:**
- Produces:
  - **`summary` nach D10:**
    - Grundlage ist `metrics_model.flatten(last_snapshot)`.
    - `cpu_percent` = `moni_host_cpu_usage_percent`, `memory_percent` = `moni_host_memory_usage_percent`.
    - `temperature_max_c` = Maximum von `moni_host_temperature_celsius`.
    - `filesystem_max` = Dateisystem mit der höchsten `moni_filesystem_usage_percent` samt `mountpoint`.
    - `containers_running` = Anzahl `moni_container_up == 1`, `containers_total` = Anzahl der Container-Proben.
    - Werte auf eine Nachkommastelle gerundet.
    - Ohne Snapshot ist `summary` gleich `null`; eine fehlende Metrik ergibt `null` im Feld.
  - **Unbekannte Agent-Token-ID:** `DELETE /servers/{id}/agent-tokens/{token_id}` antwortet mit 404 „Agent-Token nicht gefunden“ statt 502 (Nachlauf Paket 4). Antwortet der Agent selbst mit 404, wird das zu 404, ohne Text des Agents.
  - **Test-Versand:** Ist das Kanal-Geheimnis nicht entschlüsselbar (`SecretError` beim Laden in `test_channel`), lautet die Antwort 200 `{"ok": false, "error": "Kanal-Geheimnis nicht entschlüsselbar"}` und nicht 500 (Nachlauf Paket 5).
  - **Routen-Inventartest:** `test_viewer_cannot_read_admin_lists` prüft zusätzlich `GET /api/v1/servers/{id}/agent-tokens` (Nachlauf Paket 4).

- [ ] **Step 1: Failing tests**

```python
def test_server_list_has_summary_from_snapshot(...):   # cpu, memory, temp max, fs max mit mountpoint, container 5/6
def test_summary_null_without_snapshot_and_null_fields_for_missing_metrics(...)
def test_unknown_agent_token_is_404(...)
def test_test_send_with_broken_secret_returns_fixed_text(...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → volle Backend-Suite grün.
- [ ] **Step 5: Commit** `feat(api): Kennzahlen in der Serverliste, Nachlauf aus Paket 4 und 5`

---

### Task 5: Übersicht

**Files:**
- Create: `frontend/src/seiten/uebersicht/{Uebersicht,Serverkarte,Aufmerksamkeit}.tsx`, `frontend/src/ui/{Statusmarke,Balken,Leerzustand,Ladezustand,Zeit}.tsx`, Tests daneben

**Interfaces:**
- Consumes: `useServers()` und `useAlarme()` mit 15 s Takt (Task 2), `summary` (Task 4).
- Produces:
  - **`dringlichkeit(server, alarme)`** ist eine reine Funktion; die Reihenfolge lautet nicht erreichbar oder Fehler < kritischer Alarm < Warnung < OK < stummgeschaltet < deaktiviert.
  - **Abschnitt „Braucht Aufmerksamkeit“:**
    - Eine Zeile je Server mit offenem `firing`-Alarm oder Status `unreachable`/`error`.
    - Inhalt: Schwere als Text, Servername als Link, Regel, Wert mit Schwelle und Dauer.
    - Darunter der Satz „Die übrigen N laufen normal, davon M stummgeschaltet.“
    - Ohne Probleme erscheint ein Hinweis „Alle Server laufen normal.“
  - **`<Serverkarte>`** ist ein Link zu `/server/:id`:
    - H3 mit Namen, Art (Manager lokal, Agent mTLS bzw. Token) und Statusmarke (Text).
    - Balken für CPU, RAM, Speicher (mit Einhängepunkt) und Temperatur. Der Wert steht als Zahl daneben, bei Warnung oder Kritisch mit dem Wort dazu.
    - Fußzeile mit „x von y Containern laufen“ und „zuletzt gesehen vor …“ bzw. „stumm bis …“.
    - Ist `summary` gleich `null`, steht dort „keine Daten“.
  - **Kopf** mit „N Server · aktualisiert vor X s“ und, nur für Admins, dem Knopf „Server koppeln“ (→ `/verwaltung/server?koppeln=1`).
  - **Zustände:**
    - Laden: Skelett mit `aria-busy`.
    - Leer: „Noch keine Server. Server koppeln“ (für Betrachter ohne Knopf).
    - Fehler: „Manager nicht erreichbar, neuer Versuch in 15 s“, dazu der letzte Stand ausgegraut, falls vorhanden.

- [ ] **Step 1: Failing tests**

```ts
test('Sortierung nach Dringlichkeit', () => { expect(sortiere(fixtures).map(s => s.name)).toEqual(['vps-backup','sau-index','pi','nas']) })
test('Karte ohne summary zeigt "keine Daten" statt NaN', ...)
test('Aufmerksamkeit nennt Regel, Wert und Schwelle; ohne Probleme "Alle Server laufen normal."', ...)
test('Betrachter sieht keinen Knopf "Server koppeln"', ...)
test('Fehler des Managers zeigt Hinweis und behält letzte Daten', ...)
test('Übersicht ohne axe-Verstöße in hell und dunkel', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Übersicht mit Dringlichkeit und Live-Aktualisierung`

---

### Task 6: Serverdetail

**Files:**
- Create: `frontend/src/seiten/server/{Serverdetail,Kennzahlen,Verlauf,Diagramm,Dateisysteme,Netzwerk,Container,StummDialog}.tsx`, `frontend/src/ui/{Dialog,Tabelle,Zeitraum}.tsx`, Tests daneben

**Interfaces:**
- Consumes: `GET /servers/{id}`, `/snapshot`, `/history`, `/alerts`, `/alert-rules` (für Schwellenlinien), `PUT|DELETE /servers/{id}/mute`, `PATCH /servers/{id}`.
- Produces:
  - **Kopf:**
    - Brotkrumen „Übersicht / {name}“ mit `aria-current`, H1 und Statusmarke.
    - Ein Satz mit dem dringendsten Problem, dazu Agent-Version, Sicherheit (mTLS mit Zertifikat bis …, oder Token), URL und „zuletzt gesehen“.
    - Bei `last_error` erscheint der Text der API, bei gekoppelten Servern mit Kopplungsfehler zusätzlich der Hinweis „Agent zurücksetzen mit `python -m moni.cli pair reset` und neu koppeln“ (Nachlauf Paket 4).
  - **Admin-Aktionen:** „Stummschalten …“ öffnet einen Dialog mit 1 Std., 4 Std., bis morgen 8 Uhr oder einem eigenen Zeitpunkt (lokale Eingabe, an die API in ISO mit `Z`, höchstens 30 Tage). Ist der Server stumm, heißt die Aktion „Stummschaltung aufheben“. Daneben „Deaktivieren“ bzw. „Aktivieren“.
  - **Kennzahlen** als `<dl>`: CPU (Kerne, Last), RAM (genutzt von gesamt), Temperatur (Zone), höchster Speicher und Laufzeit. Fehlende Werte heißen „nicht verfügbar“.
  - **Verlauf:**
    - `<Zeitraum>` ist eine Knopfgruppe mit `aria-pressed` (1 Std., 24 Std., 7 Tage, 30 Tage, 1 Jahr) und einer Überschrift „Verlauf der letzten …“.
    - Vier Diagramme (CPU, RAM, Netzwerk des Haupt-Interfaces, Temperatur) mit Recharts `LineChart`/`AreaChart`.
    - Je Diagramm eine sichtbare Zusammenfassung (Ø, Spitze, Bezug zur Warnschwelle), die auch im `aria-label` steht (D11).
    - Bei Netzwerk ist „gesendet“ gestrichelt, die Warnschwelle aus der passenden aktiven Regel gepunktet.
    - Ohne Daten erscheint „Für diesen Zeitraum liegen keine Daten vor.“
  - **Tabellen** (`<Tabelle>`: `scope="col"`, Zeilenkopf `scope="row"`, scrollbarer Bereich mit `tabIndex=0`, `role="region"`, `aria-labelledby`):
    - Dateisysteme: Einhängepunkt, Gerät, Typ, belegt von gesamt, Auslastung als Balken plus Text.
    - Netzwerk: Interface, empfangen/s, gesendet/s, gesamt.
    - Container: Name, Image, Zustand, Healthcheck (gesund, krank, startet, keiner), CPU, RAM, Neustarts, läuft seit.
  - **Containerverlauf (Spec §6.9 „Container-Liste mit Details und Verlauf“):** In jeder Container-Zeile öffnet ein Knopf „Verlauf von {name}“ (`aria-expanded`, `aria-controls`) eine Zeile darunter. Sie enthält zwei Diagramme: CPU (`moni_container_cpu_usage_percent`) und RAM (`moni_container_memory_usage_bytes` in MB/GB). Beide nutzen `label=name={name}` und denselben Zeitraum wie oben, dazu kommen Details wie Image, Ports und Start, soweit sie im Snapshot stehen.
  - **Netzwerk-Rate:** Die Verlaufswerte `moni_network_*_bytes` sind kumulativ. `rate(punkte) -> {ts, bytes_je_s}[]` ist eine reine Funktion und rechnet daraus Raten: die Differenz geteilt durch Δt. Fällt der Zähler, beginnt die Zählung neu wie bei Prometheus (P5-R7), es entsteht keine negative Rate.
  - **`<Dialog>`:** `<dialog>` mit `showModal()`, Titel per `aria-labelledby`. Esc und „Abbrechen“ schließen, der Fokus kehrt zum Auslöser zurück.

- [ ] **Step 1: Failing tests**

```ts
test('Zeitraumwechsel lädt history mit from/to und setzt aria-pressed', ...)
test('Diagramm-Zusammenfassung nennt Durchschnitt, Spitze und Schwelle', ...)
test('Snapshot ohne Temperatur und Docker zeigt "nicht verfügbar", keine leere Tabelle ohne Hinweis', ...)
test('Stummschalten sendet ISO mit Z, höchstens 30 Tage; Fehler 422 erscheint im Dialog', ...)
test('Dialog: Esc schließt und Fokus kehrt zum Knopf zurück', ...)
test('Betrachter sieht keine Aktionen', ...)
test('Kopplungsfehler zeigt den Hinweis auf pair reset', ...)
test('rate: kumulative Bytes ergeben Bytes/s, Zähler-Reset ergibt keine negative Rate', () => {
  expect(rate([[0, 100], [10, 300], [20, 50]])).toEqual([{ ts: 10, bytes_je_s: 20 }, { ts: 20, bytes_je_s: 5 }])
})
test('Containerverlauf klappt per Knopf auf, aria-expanded wechselt, Abfrage mit label=name=…', ...)
test('Serverdetail ohne axe-Verstöße', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Serverdetail mit Verlauf, Tabellen und Stummschalten`

---

### Task 7: Alarme

**Files:**
- Create: `frontend/src/seiten/alarme/{Alarme,Offen,Verlauf,Regeln,RegelFormular,Kanaele,KanalFormular,Erinnerung}.tsx`, `frontend/src/texte/metriken.ts` (Klartext-Namen der Metriken), Tests daneben

**Interfaces:**
- Consumes: Alarm-Endpunkte aus dem Ist-Stand, `<Dialog>`, `<Formular>`, `<Feld>`, `<Tabelle>`.
- Produces:
  - **Bereichsnavigation** als Links mit `aria-current="page"` (echte Routen, keine ARIA-Reiter).
  - **Offen:**
    - Liste mit Schwere (Text plus linker Rand), „Regel · Server“, Labels in Klartext (`mountpoint=/var` → „Einhängepunkt /var“), Wert mit Schwelle, „seit …“ und dem Hinweis „stummgeschaltet bis …“.
    - Leer: „Keine offenen Alarme.“
  - **Verlauf:**
    - Tabelle mit Zeit, Ereignis (ausgelöst, behoben, Schwere geändert, Erinnerung), Schwere, Regel, Server, Wert und Grund.
    - „Ältere laden“ blättert über `next_before`, dazu ein Filter nach Server.
  - **Regeln:**
    - Tabelle mit Regel, „Löst aus, wenn …“ (Klartext aus `metriken.ts` und `op`), Warnung ab, Kritisch ab, Mindestdauer, Gilt für, Aktiv.
    - Admins: „Regel anlegen“ und „Bearbeiten“ im Dialog.
    - Das Formular hat die Felder Name, Metrik (Auswahl), Funktion (Wert oder Zunahme mit Fenster), Bedingung, Warn- und Kritisch-Schwelle, Mindestdauer in Minuten (an die API in Sekunden), Gruppierung, Filter, „Nur auslösen, wenn vorher OK“, Server (alle oder einer) und Aktiv.
    - Das PATCH sendet nur geänderte Felder. Eine Antwort ohne Änderung zählt trotzdem als „Gespeichert“ (Nachlauf Paket 5).
    - „Löschen“ fragt vorher nach: „Regel ‚{name}‘ löschen? Offene Alarme dieser Regel werden ohne Benachrichtigung aufgelöst.“
  - **Kanäle** (nur Admins; für Betrachter fehlt der Bereich in der Navigation):
    - Karten mit Ziel (`url_hint` bzw. Host und Anzahl Empfänger), „Meldet“ (Warnungen und Kritisches bzw. nur Kritisches) und „Geheimnis gesetzt“ bzw. „ohne Geheimnis“.
    - „Testnachricht senden“ zeigt das Ergebnis in `role="status"` (`error` der API unverändert; 429 ergibt „Zu viele Tests, bitte eine Minute warten.“).
    - Formular Webhook: Name, URL, HMAC-Secret optional. Formular E-Mail: Host, Port, Sicherheit (STARTTLS oder TLS), Benutzer, Passwort, Absender und Empfänger (eine je Zeile, 1 bis 10). Dazu „Meldet ab“ und Aktiv.
    - Beim Bearbeiten bleiben Geheimnisfelder leer, mit dem Hinweis „Leer lassen, um das gespeicherte zu behalten“.
  - **Erinnerung:** Feld „Erinnern, solange ein Alarm offen ist, alle … Minuten“ (0 bis 10080, mit Hinweis), nur für Admins.

- [ ] **Step 1: Failing tests**

```ts
test('Bereichsnavigation sind Links mit aria-current', ...)
test('Regel-Formular sendet Minuten als Sekunden und nur geänderte Felder; 422 erscheint am Formular', ...)
test('Regel löschen fragt vorher und nennt die stille Auflösung', ...)
test('Kanal bearbeiten: leeres Passwortfeld wird nicht gesendet', ...)
test('Testnachricht: ok und Fehlertext werden angesagt; 429 zeigt Wartehinweis', ...)
test('Verlauf blättert mit before=next_before', ...)
test('Betrachter sieht keine Kanäle und keine Bearbeiten-Knöpfe', ...)
test('Alarmseiten ohne axe-Verstöße', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Alarme mit Regeln, Kanälen und Verlauf`

---

### Task 8: Verwaltung

**Files:**
- Create: `frontend/src/seiten/verwaltung/{Verwaltung,Server,Koppeln,AgentTokens,Benutzer,Tokens,Audit,Anmeldeanbieter}.tsx`, Tests daneben

**Interfaces:**
- Consumes: Server-, Benutzer-, Token-, Audit- und OIDC-Endpunkte; `<Einmalwerte>`, `<Dialog>`, `<Formular>`.
- Produces (alles nur für Admins, Bereichsnavigation als Links):
  - **Server:**
    - Tabelle mit Name, Art, Status, URL, Zertifikat bis und Aktionen (Aktivieren/Deaktivieren, Agent-Tokens, Entfernen mit Rückfrage „Server ‚{name}‘ entfernen? Verlauf und Alarme dieses Servers werden gelöscht.“).
    - Der lokale Server lässt sich nicht entfernen; die Aktion fehlt dort.
  - **Koppeln** (Dialog, auch über `?koppeln=1`), zwei Wege:
    - **Empfohlen** mit Kopplungscode: Felder Name und Kopplungscode (`moni-pair-v1:…`) mit Hinweis, wo der Code steht (Log des Agents bzw. `python -m moni.cli pair code`), optional die URL. Bei „Kopplungs-Token ungültig“ erscheint der Fehler am Feld, ohne Abmeldung (D8).
    - **Ohne mTLS** mit Token und URL. Dazu der Warnhinweis „Nur hinter einem vertrauenswürdigen Proxy verwenden.“ und optionale Zusatz-Header (Name und Wert, Werte als Passwortfeld).
  - **Agent-Tokens:** Liste (Name, Bereich, Ablauf, zuletzt benutzt), „Token erstellen“ mit `<Einmalwerte>` und Widerrufen mit Rückfrage. Ein 404 ergibt „Agent-Token nicht gefunden“.
  - **Benutzer:**
    - Tabelle mit Name, E-Mail, Rolle, Art (lokal oder OIDC), 2FA, Status (aktiv, gesperrt, deaktiviert) und zuletzt angemeldet.
    - Anlegen mit Rolle, Art und Startpasswort für lokale Konten.
    - Rolle ändern, Deaktivieren, Passwort zurücksetzen, 2FA zurücksetzen und Entsperren, jeweils mit Rückfrage, wo es Folgen hat.
    - Der letzte Admin ist geschützt; die API meldet das, die Oberfläche zeigt `detail`.
  - **Tokens:** Liste (Name, Bereich, Ablauf, zuletzt benutzt), Anlegen (Name, Bereich Lesen oder Admin, Ablauf in Tagen optional) mit `<Einmalwerte>` und Widerrufen.
  - **Audit:** Tabelle mit Zeit, Akteur, Aktion, Ziel und IP, Paging mit „Ältere laden“. Die Details erscheinen als Text, nie als HTML.
  - **Anmeldeanbieter:**
    - Öffentliche URL des Managers (`PUT /auth/oidc/public-url`), zusammen mit der daraus abgeleiteten Redirect-URI je Anbieter zum Kopieren.
    - Anbieter anlegen oder bearbeiten mit Issuer, Client-ID, Client-Secret (beim Bearbeiten leer = behalten; bei Issuer-Wechsel Pflicht, die API meldet das) und Bezeichnung.
    - Löschen mit Rückfrage.

- [ ] **Step 1: Failing tests**

```ts
test('Koppeln mit Code sendet {name, pairing_code}; "Kopplungs-Token ungültig" erscheint am Feld ohne Abmeldung', ...)
test('Token-Modus zeigt den Proxy-Warnhinweis', ...)
test('API-Token erscheint einmal; nach Schließen weder im DOM noch im Query-Cache', ...)
test('lokaler Server hat keine Entfernen-Aktion', ...)
test('Entfernen und Widerrufen fragen vorher nach', ...)
test('Audit-Details werden als Text gerendert (Fixture mit <script> im Ziel)', ...)
test('Verwaltung ohne axe-Verstöße', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Verwaltung für Server, Benutzer, Tokens, Audit und Anmeldeanbieter`

---

### Task 9: Konto

**Files:**
- Create: `frontend/src/seiten/konto/Konto.tsx`, Tests daneben

**Interfaces:**
- Consumes: `POST /account/password`, `POST /account/recovery-codes`, `POST /auth/totp/setup|confirm`, `useDarstellung`, `<Einmalwerte>`.
- Produces:
  - **Abschnitt Profil:** Name, E-Mail, Rolle, Art (nur lesen).
  - **Abschnitt Passwort ändern:** aktuelles und neues Passwort (`new-password`), Hinweis auf die Mindestlänge laut API-Fehlertext. Nur für lokale Konten.
  - **Abschnitt Zwei-Faktor:**
    - Status „eingerichtet“.
    - „Authenticator wechseln“: erst den aktuellen Code abfragen (`current_code`), dann QR-Code mit Secret, dann den neuen Code bestätigen, dann neue Wiederherstellungscodes mit `<Einmalwerte>`.
    - „Neue Wiederherstellungscodes erzeugen“ fragt einen aktuellen Code ab und warnt: „Die alten Codes werden ungültig.“
  - **Abschnitt Darstellung:** Auto, Hell oder Dunkel (Optionsfeld-Gruppe), gleicher Zustand wie in der Kopfzeile.
  - OIDC-Konten sehen statt Passwort und 2FA den Hinweis „Anmeldung über {Anbieter}; Passwort und zweiter Faktor verwaltet der Anbieter.“

- [ ] **Step 1: Failing tests**

```ts
test('Authenticator wechseln fragt zuerst den aktuellen Code', ...)
test('neue Wiederherstellungscodes warnen vor Ungültigkeit der alten und erscheinen einmal', ...)
test('OIDC-Konto sieht keinen Passwort-Abschnitt', ...)
test('Darstellung im Konto und in der Kopfzeile sind synchron', ...)
test('Konto ohne axe-Verstöße', ...)
```

- [ ] **Step 2–4:** Tests rot → Implementieren → grün.
- [ ] **Step 5: Commit** `feat(ui): Konto mit Passwort, Zwei-Faktor und Darstellung`

---

### Task 10: Browser-Smoke in der CI (CSP, Tastatur, axe)

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/e2e/smoke.spec.ts`
- Modify: `frontend/package.json` (Skript `test:e2e`), `.github/workflows/ci.yml` (Build-Job: neuer Schritt „Oberfläche im Browser“ nach „Alarmkanal“)

**Interfaces:**
- Consumes: den laufenden Manager-Container aus dem Build-Job und den CI-Status mit E-Mail und Passwort des Admins (aus `manager_auth_smoke.py`, falls nicht vorhanden dort ergänzen und mit `write_secret_file` speichern) sowie einen Wiederherstellungscode über `python3 -c "import sys; sys.path.insert(0,'scripts/ci'); import _moni; print(_moni.take_recovery(...))"`. Werte nur über Umgebungsvariablen, nie in der Ausgabe.
- Produces: `smoke.spec.ts` gegen `https://127.0.0.1:8443` (`ignoreHTTPSErrors`, nur CI). Der Ablauf:
  1. Er sammelt `console`-Meldungen und `securitypolicyviolation`-Ereignisse.
  2. Er meldet sich an mit E-Mail, Passwort und Wiederherstellungscode.
  3. Die Übersicht zeigt den lokalen Server.
  4. `Tab` erreicht zuerst den Sprunglink, Enter springt nach `main`.
  5. Er öffnet das Serverdetail und die Alarme.
  6. `@axe-core/playwright` liefert 0 Verstöße auf Übersicht, Serverdetail und Alarmen, jeweils hell und dunkel.
  7. Am Ende gibt es 0 CSP-Verstöße und 0 Konsolenfehler.
  8. Er meldet sich ab.
- **CI:** `npx playwright install --with-deps chromium`. Die Ausgabe geht zuerst in eine Datei (R7). Danach wird geprüft, dass weder Passwort noch Code im Manager-Log stehen.
- **Neue Abhängigkeiten:** `@playwright/test` und `@axe-core/playwright` als devDependencies (Ruling, als Vorgriff auf den E2E-Test aus Paket 7).

- [ ] **Step 1:** Spec und CI-Schritt schreiben. Lokal gegen den Compose-Stack ausführen (Scratchpad-Kopie wie in Paket 4 und 5; den Manager-Stack reicht). Expected: alle Prüfungen grün.
- [ ] **Step 2:** Volle Suiten grün (Backend und Frontend).
- [ ] **Step 3: Commit** `ci: Browser-Smoke mit CSP- und axe-Prüfung`

---

### Task 11 (Betreiber, optional): Oberfläche beim Autor

Nicht durch Subagents.

1. Auf dem Pi die neue Oberfläche öffnen, anmelden, den eigenen Server prüfen: Werte gegen `htop`/`df -h`, Diagramme in allen Zeiträumen.
2. Hell und dunkel ansehen, auf dem Handy und am Rechner. Was optisch nicht gefällt, notieren; Anpassungen gehen über `src/stil/tokens.css` und die Bausteine in `src/ui/`.
3. Mit 200 % Browser-Zoom und nur mit der Tastatur einmal durch Übersicht, Serverdetail und Alarme.
4. Falls ein zweiter Agent existiert: über „Server koppeln“ mit Kopplungscode koppeln.

## Nachlauf aus dem Abschlussreview (2026-11-12)

Paket 6 ist umgesetzt (Commits `cb19939..04bd191`). Stand der Prüfungen:
- Frontend: 229 Tests grün, mehrfach hintereinander.
- Backend: 1023 Tests grün.
- Der Browser-Smoke lief lokal im echten Chromium gegen den Manager-Container: 0 CSP-Verstöße, 0 axe-Verstöße auf allen Seiten in hell und dunkel. Auf GitHub Actions laufen der Frontend-Job und der Browser-Smoke mit diesem Push zum ersten Mal.

### Rulings während der Umsetzung

- **P6-R1:** Tests dürfen deutsche Texte als Literale prüfen; im Produktionscode kommen sie nur aus `de.ts`.
- **P6-R2:** TypeScript 6 und ESLint 9 statt der neuesten Hauptversionen, weil typescript-eslint und jsx-a11y sie noch nicht tragen.
- **P6-R3:** Für die CSP gilt:
  - Keine `data:`-Schriften (`assetsInlineLimit: 0`).
  - Der `noscript`-Text steht direkt in `index.html`.
  - Die Darstellung läuft über einen eigenen Kontext.
- **P6-R4:** Die Rolle heißt in der API `viewer`, angezeigt wird „Betrachter“. Verlaufs-Labels haben die Form `label=key:value`.
- **P6-R5:** Abmelden leert alle Daten gezielt statt über `queryClient.clear()`, das die Stage hängen ließ.
- **P6-R6:** Fokus auf die H1 überall im Dokument; die Weiterleitung zum Login trägt das Rücksprungziel.
- **P6-R7:** Fokus nach „Bestätigen“, dauerhafte Live-Region, Leerzeichen beim Einfügen von Codes und verzögertes `revokeObjectURL`.
- **P6-R8:** Klartext, Einheiten und Ja/Nein-Zustände aller Metriken stehen zentral in `src/texte/metriken.ts`.
- **P6-R9:** Das Serverdetail wird nachgeladen. Ports fehlen, weil der Snapshot keine enthält.
- **P6-R10:** Präzisierungen im Serverdetail:
  - Schwellenlinien beachten `label_filter`.
  - Beim Rückfall auf die kritische Schwelle heißt sie „Kritische Schwelle“.
  - „Bis morgen 8 Uhr“ meint vor 8 Uhr den heutigen Tag.
  - Eine ungültige ID ergibt „Server nicht gefunden“.
- **P6-R11:** „Zuletzt benutzt“ entfällt bei Tokens, weil die API kein Feld dafür hat. Neue Benutzer sind standardmäßig Betrachter.
- **P6-R12:** Fokus auf das Codefeld bei einem Kopplungsfehler, nummerierte Header-Zeilen und ein echter Clipboard-Test.
- **P6-R13:** Eine einzige 401-Regel in `api/client.ts`: Jede 401 außer „Kopplungs-Token ungültig“ und „Anmeldung fehlgeschlagen“ meldet ab. Für Ablauf und Abmelden gibt es nur eine Navigationsquelle, den Wächter.
- **P6-R14:** `resolution=auto` fällt auch auf feinere Stufen zurück, damit Diagramme direkt nach der Installation nicht leer sind.
- **P6-R15:** Die CSP enthält zusätzlich `trusted-types 'none'`.
- **P6-R16:** Kleine Punkte aus dem Abschlussreview:
  - README und Dependabot-Gruppen.
  - „Schlüssel=Wert“ mit Umlaut.
  - Doppelklick-Sperre bei Rückfragen.
  - Geblätterte Listen laden beim Fensterfokus nicht neu.
  - Hinweis im Serverdetail, wenn der Manager ausfällt.
  - Ändert man die eigene Rolle, wird die Sitzung neu geladen.
  - Der Smoke-Test besucht auch Konto und Verwaltung.

### Geparkte Punkte

- **Paket 7 (Härtung und Aufräumen):**
  - Abmelden während einer laufenden Abfrage kann als „Sitzung abgelaufen“ enden (nur Kosmetik). Es fehlt ein Test für eine 401 auf den Logout-Aufruf selbst. `Innen` ist über `useLocation` an den Ablauf nach dem Login gekoppelt.
  - Die Helfer für Rückfragen, Fehlertexte und Statusmeldungen gibt es mehrfach (Alarme, Serverdetail, Verwaltung); sie sollten zusammengeführt werden.
  - Kein `AbortSignal` an `fetch`. Abmelden hat kein Timeout. Der Seitentitel „Keine Berechtigung“ fehlt.
  - Agent-Token-404 wird über den Fehlertext des Pollers erkannt; besser wäre `AgentError.status`.
  - Kanal-PATCH sendet alle Felder, deshalb nennt das Audit auch unveränderte. Ein Fehler beim Laden der Erinnerungs-Einstellung bleibt still.
  - Kleinigkeiten im Serverdetail:
    - Die Spitze kommt aus dem Bucket-Maximum, die Linie zeigt den Mittelwert.
    - Fällt eine Reihe aus, fehlt auch die Zusammenfassung der anderen.
    - `last_error` erscheint doppelt.
    - Die Legende zeigt kein Strichmuster.
  - npm-audit-Meldungen in der Dev-Lint-Kette (minimatch); CI läuft mit Node 24, das Dockerfile mit Node 26.
- **Später:**
  - Server umbenennen: Die API kann es noch nicht.
  - „Zuletzt benutzt“ für Tokens.
  - Ports der Container im Snapshot.
