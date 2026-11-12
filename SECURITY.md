# Sicherheitsrichtlinie

## Unterstützte Versionen

Sicherheitskorrekturen gibt es für die **neueste Nebenversion der aktuellen Hauptversion** (derzeit 2.0.x). Ältere
Versionen, insbesondere 1.x (Linux Monitor), bekommen keine Korrekturen. Aktualisieren Sie auf die neueste Version
([Upgrade](docs/upgrade.md)).

## Schwachstelle melden

Melden Sie Schwachstellen **nicht** in einem öffentlichen Issue, sondern vertraulich über GitHub:
**Security, Report a vulnerability** auf der Seite des Repositorys
([Private Vulnerability Reporting](https://github.com/anonym-rgath/MONI/security/advisories/new)).

Bitte nennen Sie Version, Rolle (Agent oder Manager), Schritte zum Nachstellen und die Auswirkung. Hängen Sie keine
echten Zugangsdaten oder Logs mit Geheimnissen an (für Logs nutzen Sie `scripts/diagnose.sh`, siehe
[Betrieb](docs/betrieb.md)).

**Reaktionszeit:** Wir bestätigen den Eingang in der Regel innerhalb von 7 Tagen und melden uns mit einer
Einschätzung und einem Zeitplan. Moni wird in der Freizeit gepflegt, das ist eine Zusage nach bestem Bemühen, keine
vertragliche Frist. Wir veröffentlichen die Korrektur gemeinsam mit einem Security Advisory und nennen auf Wunsch
die meldende Person.

## Umfang

Im Umfang: Agent und Manager (Backend, Weboberfläche), die Compose-Dateien, die Betriebsskripte und die
veröffentlichten Images samt Release-Pipeline. Beispiele: Umgehung der Anmeldung oder Rechte, Auslesen von
Geheimnissen, Schwächen der mTLS-Kopplung, Ausbrechen aus der Container-Härtung, Injektion.

Nicht im Umfang: Angriffe, die schon Admin-Rechte oder Zugriff auf den Docker-Host voraussetzen; fehlende Härtung
einer Umgebung, die vom Standard abweicht (z. B. Agent-Port 9810 offen ins Internet, `MONI_TLS=off` ohne Proxy);
Schwachstellen in Abhängigkeiten ohne Auswirkung auf Moni; Denial-of-Service durch Überlast ohne Verstärkung.

## Bekannte, bewusst belassene CodeQL-Hinweise

CodeQL meldet zwei Stellen als mögliche Server-Side Request Forgery (SSRF). Beide bleiben absichtlich, weil die
Adresse zur Funktion gehört und nur ein Admin sie setzt:

- `backend/moni/auth/oidc.py`: Der Manager ruft die OIDC-Discovery eines vom **Admin eingetragenen Issuers** ab. Ohne
  diesen Abruf gäbe es keine OIDC-Anmeldung. Abgesichert ist der Aufruf trotzdem: nur `https://`, keine
  Weiterleitungen, keine Proxy-Variablen aus der Umgebung, Zeit- und Größenlimit, keine Fehlertexte mit Antwortinhalten.
- `backend/moni/manager/poller.py`: Der Manager fragt die vom **Admin konfigurierte Agent-URL** ab. Das ist der Zweck des
  Managers. Auch hier: keine Weiterleitungen, kein Proxy aus der Umgebung, Zeitlimit; mit mTLS prüft er das Zertifikat gegen die eigene CA.

Wer Admin ist, kann den Manager damit Adressen in seinem Netz abfragen lassen. Das gilt in Moni als Admin-Recht. Vergeben Sie es entsprechend sparsam.
Meldungen zu diesen beiden Stellen sind willkommen, wenn Sie einen Weg ohne Admin-Rechte zeigen.
