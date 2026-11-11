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
