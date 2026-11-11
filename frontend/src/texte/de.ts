// Alle Texte der Oberfläche. Nur hier stehen Wörter für Menschen (Global Constraint).
export const de = {
  app: { name: 'moni', titel: 'Moni' },
  rahmen: {
    zumInhalt: 'Zum Inhalt springen',
    hauptnavigation: 'Hauptnavigation',
    uebersicht: 'Übersicht',
    alarme: 'Alarme',
    alarmeOffen: 'offen',
    verwaltung: 'Verwaltung',
    abmelden: 'Abmelden',
    darstellung: 'Darstellung',
    auto: 'Auto',
    hell: 'Hell',
    dunkel: 'Dunkel',
    benutzer: '{name} · {rolle}',
  },
  fehler: {
    netzwerk: 'Manager nicht erreichbar.',
    unerwartet: 'Der Manager hat unerwartet geantwortet ({status}).',
    allgemein: 'Das hat nicht geklappt. Versuche es noch einmal.',
  },
  waechter: {
    laden: 'Wird geladen …',
    erneutVersuchen: 'Erneut versuchen',
    keineBerechtigung: 'Keine Berechtigung',
  },
  seiten: {
    login: 'Anmelden',
    zweiterFaktor: 'Zweiter Faktor',
    einrichtung: 'Einrichtung',
    zweiFaktorEinrichten: 'Zwei-Faktor-Einrichtung',
    server: 'Server',
    alarmVerlauf: 'Alarmverlauf',
    alarmRegeln: 'Alarmregeln',
    alarmKanaele: 'Alarmkanäle',
    verwaltungServer: 'Server verwalten',
    benutzer: 'Benutzer',
    tokens: 'API-Tokens',
    audit: 'Audit-Protokoll',
    anmeldung: 'Anmeldung',
    konto: 'Konto',
  },
  formular: { wirdGesendet: 'Wird gesendet …' },
  einmalwerte: {
    kopieren: 'Kopieren',
    kopiert: 'In die Zwischenablage kopiert.',
    kopierenFehlt: 'Kopieren ist hier nicht möglich. Markiere die Codes und kopiere sie von Hand.',
    speichern: 'Als Datei speichern',
    dateiname: 'moni-wiederherstellungscodes.txt',
    bestaetigung: 'Ich habe die Codes sicher gespeichert',
    weiter: 'Weiter',
  },
  anmeldung: {
    email: 'E-Mail-Adresse',
    passwort: 'Passwort',
    weiter: 'Weiter',
    oder: 'oder',
    mitAnbieter: 'Mit {label} anmelden',
    darstellung: 'Darstellung',
    fehlerOidc: 'Die Anmeldung über den Anbieter ist fehlgeschlagen. Versuche es erneut oder melde dich mit Passwort an.',
    abgelaufen: 'Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.',
    codeApp: 'Code aus der Authenticator-App',
    codeHinweis: '6 Ziffern, der Code wechselt alle 30 Sekunden.',
    codeWiederherstellung: 'Wiederherstellungscode',
    codeWiederherstellungHinweis: 'Jeder Code gilt nur einmal.',
    zuWiederherstellung: 'Stattdessen Wiederherstellungscode verwenden',
    zuApp: 'Stattdessen Code aus der App verwenden',
    anmelden: 'Anmelden',
  },
  einrichtung: {
    setupToken: 'Setup-Token',
    setupTokenHinweis: 'Das Setup-Token steht im Log des Managers.',
    name: 'Name',
    email: 'E-Mail-Adresse',
    passwort: 'Passwort',
    anlegen: 'Konto anlegen',
  },
  zweiFaktor: {
    einfuehrung: 'Scanne den QR-Code mit einer Authenticator-App und gib danach den angezeigten Code ein.',
    qrAlt: 'QR-Code für die Authenticator-App',
    qrFehler: 'Der QR-Code konnte nicht erstellt werden. Tippe das Geheimnis in die App ab.',
    geheimnis: 'Geheimnis zum Abtippen',
    code: 'Code aus der App',
    bestaetigen: 'Bestätigen',
    wirdVorbereitet: 'Zwei-Faktor-Einrichtung wird vorbereitet …',
    codesTitel: 'Wiederherstellungscodes',
    codesHinweis: 'Mit diesen Codes meldest du dich an, wenn du keinen Zugriff auf die App hast. Jeder Code gilt nur einmal. Sie werden nicht noch einmal angezeigt.',
  },
  rolle: { admin: 'Admin', betrachter: 'Betrachter' },
} as const

type Pfade<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${P}${K}` : Pfade<T[K], `${P}${K}.`>
}[keyof T & string]

export type TextSchluessel = Pfade<typeof de>

export function t(schluessel: TextSchluessel, werte: Record<string, string | number> = {}): string {
  let knoten: unknown = de
  for (const teil of schluessel.split('.')) knoten = (knoten as Record<string, unknown>)[teil]
  return String(knoten).replace(/\{(\w+)\}/g, (m, name: string) => (name in werte ? String(werte[name]) : m))
}
