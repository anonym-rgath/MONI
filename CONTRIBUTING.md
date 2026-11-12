# Mitmachen

Danke für Ihr Interesse an Moni. Fehler und Wünsche melden Sie als Issue, Schwachstellen vertraulich nach
[SECURITY.md](SECURITY.md). Größere Änderungen besprechen Sie bitte vorher in einem Issue.

## Entwicklung lokal

Voraussetzungen: [uv](https://docs.astral.sh/uv/), Python 3.14, Node 26, Docker (für Image und Stack-Tests).

**Backend** (aus dem Repository-Wurzelverzeichnis):

```
cd backend
uv run --python 3.14 --with-requirements requirements-dev.txt python -m pytest tests/ -W error::DeprecationWarning
```

**Frontend:**

```
cd frontend
npm ci --ignore-scripts
npm run lint
npm run typecheck
npm test
npm run build
```

Die End-to-End-Tests im Browser (`npm run test:e2e`) laufen in der CI gegen den echten Stack. Dort laufen auch Image-Build,
Trivy und der Test mit Manager und Agent im Compose-Verbund.

**Dokumentation:** `python3 scripts/ci/check_docs.py` prüft relative Links samt Überschriften und dass jede
`MONI_*`-Variable in `docs/installation.md` steht.

## Tests

Jede Änderung am Verhalten bekommt einen Test, Fehlerkorrekturen einen, der ohne die Korrektur fehlschlägt. Das
Backend läuft mit `-W error::DeprecationWarning`: Warnungen sind Fehler. Die CI führt die Tests ebenfalls aus; Fehler fallen dort spätestens beim Pull Request auf.

## Stil

- Sprache der Oberfläche, der Fehlermeldungen, Kommentare und Doku ist **Deutsch** mit korrekten Umlauten.
- **Alle Texte der Oberfläche stehen zentral** in `frontend/src/texte/de.ts`, nicht verstreut in Komponenten.
- Kleine, gezielte Änderungen; keine Abstraktion auf Vorrat. Kommentare erklären das Warum.
- Beispiele in Doku und Tests nutzen `example.org` und Platzhalter wie `<TOKEN>`, nie echte Hostnamen oder Geheimnisse.

## Commits

Kurze Betreffzeile mit Präfix und optionalem Bereich, wie im Verlauf (`git log`):

| Präfix | Bedeutung |
|---|---|
| `feat(bereich):` | neue Funktion |
| `fix(bereich):` | Fehlerkorrektur |
| `docs:` | Dokumentation |
| `ci:` | Pipeline |
| `refactor:`, `test:` | Umbau ohne Verhaltensänderung, reine Tests |
| `deps(backend\|frontend\|docker\|ci\|compose):` | Abhängigkeiten (meist von Dependabot) |

Bereiche sind etwa `agent`, `manager`, `auth`, `alerts`, `ui`, `tls`, `api`, `ops`, `scripts`, `release`.

## OpenAPI neu erzeugen

Ändern Sie Routen oder Modelle, erzeugen Sie die Dateien in `docs/api/` neu (Befehle in der
[API-Doku](docs/api.md#openapi-neu-erzeugen)). Der Test `test_openapi_snapshot.py` schlägt sonst fehl.

## Sicherheitsregeln

- **Keine Geheimnisse in Logs, Fehlermeldungen oder API-Antworten**: weder Passwörter, Tokens, Kopplungscodes noch
  Schlüssel. Einzige Ausnahme ist der Setup-Token beim ersten Start des Managers. Fehlertexte sind feste deutsche Sätze.
- Geheimnisse an der CLI nie als Argument (Shell-History), sondern per Datei oder stdin.
- **Abhängigkeiten exakt pinnen** (keine Bereiche) und nur Versionen nehmen, die mindestens **14 Tage** alt sind.
  GitHub Actions per Commit-SHA, Basis-Images per Digest.
- Keine neuen Abhängigkeiten für wenige Zeilen Code.
- Eingaben an Vertrauensgrenzen (API, CLI, Umgebung) werden geprüft; Container bleiben `read_only` mit `cap_drop: ALL`.

## Lizenz

Mit Ihrem Beitrag stimmen Sie zu, dass er unter der [Apache-License 2.0](LICENSE) steht.
