# Release (für Maintainer)

Ein Release entsteht, wenn jemand ein Tag `vX.Y.Z` pusht (auch `vX.Y.Z-rc.1`). Der Ablauf steht in
[`.github/workflows/release.yml`](../.github/workflows/release.yml). Die Änderungen jeder Version stehen im
[CHANGELOG](../CHANGELOG.md).

## Ablauf der Pipeline

1. **verify** (je Image `agent` und `manager`, je Plattform `amd64` und `arm64`): Versionen prüfen
   (`scripts/ci/check_version.py`), Einzelbuild, Smoke (Imports im Container der Plattform) und Trivy
   (`scripts/ci/trivy.sh`; behebbare CRITICAL/HIGH-Funde brechen ab). arm64 läuft unter QEMU. Alles
   geschieht vor dem Push.
2. **publish** (nur bei einem Tag, nach `verify`): Multi-Arch-Build und Push **nur per Digest, ohne Tags**, mit SBOM (SPDX) und
   Provenance. Genau dieses Image wird je Plattform per Digest gezogen und erneut geprüft (Plattform, Smoke, Trivy).
3. Danach mit **cosign** keyless (GitHub-OIDC) über den Digest signieren, mit `cosign verify` selbst prüfen.
4. **Tags** setzen (`X.Y.Z`, `X.Y`, `X`, `latest`; Vorabversionen nur den eigenen Tag, ohne `latest`) per
   `imagetools` auf denselben Index-Digest und den Digest gegenprüfen.
5. **release**: GitHub-Release aus dem passenden Abschnitt von `CHANGELOG.md` (Vorabversionen als Pre-release).

Ein Fehler vor Schritt 4 lässt keinen Tag zurück: Ein Nutzer, der `:2.0.0` zieht, bekommt nie ein ungeprüftes Image.

**Trockenlauf im Pull Request:** Ändert ein PR `.github/workflows/release.yml`, das `Dockerfile` oder
`backend/requirements-docker.txt`, läuft nur Job `verify` (bauen, Smoke, Trivy). Kein Login, kein Push, keine
Signatur, kein Release.

## Ein Release schneiden

1. Version anheben, überall gleich: `backend/moni/__init__.py`, `frontend/package.json` (und `package-lock.json`), oberster
   Abschnitt in `CHANGELOG.md`, die Image-Standards in `compose.agent.yaml` und `compose.manager.yaml`.
2. **Im CHANGELOG-Abschnitt ein Datum eintragen** (`## [2.0.0] – 2026-12-01`). Mit einem Tag lehnt
   `check_version.py` die Überschrift „unveröffentlicht“ ab.
3. **OpenAPI neu erzeugen**, die Dokumente enthalten die Version ([API](api.md#openapi-neu-erzeugen)).
4. `python3 scripts/ci/check_version.py` und die Tests lokal laufen lassen, per PR nach `main` mergen.
5. Tag setzen und pushen: `git tag v2.0.0 && git push origin v2.0.0`. Prüfen Sie `check_version.py v2.0.0` vorher.
6. Den Lauf unter **Actions, Release** beobachten. Danach die Signatur prüfen ([Installation](installation.md#signatur-der-images-prüfen)).

Der Publish-Pfad (Schritt 2 bis 4 der Pipeline) läuft erst bei einem echten Tag und lässt sich im PR nicht
üben. Beim **ersten Lauf** beobachten Sie ihn genau: Er braucht auf dem Runner Docker ab Version 28
(`docker image inspect --platform`), und `docker buildx imagetools` sowie das Verhalten von `cosign` bei
SBOM- und Provenance-Manifesten sind dort zum ersten Mal im Einsatz.

## Hinweise

- **Fehlgeschlagener Scan:** Das Image liegt dann ohne Tag als Digest in GHCR. Löschen Sie die ungetaggte Version unter
  **Packages, moni-agent bzw. moni-manager, Versions**, wenn sie stört. Nutzer sehen sie nicht.
- **`latest`** zeigt auf das zuletzt getaggte Release (kein Vorab-Release). Ein Hotfix auf einer älteren Hauptversion
  setzt `latest` auf diese Version zurück. Danach ist ein Release der neuesten Linie nötig oder `latest` manuell neu zu setzen.
- Ein einmal veröffentlichtes Tag ändern Sie nicht. Bei einem Fehler erscheint eine neue Patch-Version.

## Einmalige GitHub-Einrichtung

Das erledigt der Repository-Admin einmal, nicht der Workflow:

1. **GHCR-Pakete öffentlich:** Nach dem ersten Publish sind `moni-agent` und `moni-manager` privat. Unter
   **Packages, Package settings, Change visibility** auf „public“ stellen, sonst kann niemand ohne Anmeldung ziehen.
2. **Branch-Schutz für `main`:** Pull Request vorgeschrieben, die CI-Jobs („Backend unit tests“, „Frontend“, „Build images & smoke tests“) als
   Pflicht-Checks, keine Force-Pushes.
3. **Tag-Schutz:** Unter **Settings, Rules, Rulesets** ein Tag-Ruleset für `v*` anlegen: nur Maintainer dürfen erstellen,
   kein Löschen, kein Überschreiben. Ein beliebiger Tag löst sonst ein Release aus.
4. **Private Vulnerability Reporting** einschalten (**Settings, Code security**), siehe [SECURITY.md](../SECURITY.md).
5. **Labels für Dependabot** müssen existieren, sonst scheitert das Anlegen der PRs: `dependencies`, `backend`,
   `frontend`, `docker`, `ci` (siehe `.github/dependabot.yml`).
