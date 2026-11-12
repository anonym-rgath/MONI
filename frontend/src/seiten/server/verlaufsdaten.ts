import type { Verlauf } from '../../api/typen'

export interface Punkt { ts: number; avg: number; max: number } // ts in ms
export interface Linie { name: string; punkte: Punkt[] }

const zahl = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

// raw: [ts, wert]; gröbere Auflösungen: [ts, min, avg, max]. Lücken (null) entfallen.
export function punkteDerSerie(punkte: Verlauf['series'][number]['points']): Punkt[] {
  const out: Punkt[] = []
  for (const p of punkte) {
    const ts = Date.parse(p[0])
    const avg = p.length === 2 ? p[1] : p[2]
    const max = p.length === 2 ? p[1] : p[3]
    if (zahl(avg) && zahl(max) && Number.isFinite(ts)) out.push({ ts, avg, max })
  }
  return out
}

// Kumulative Zähler (Sekunden, Bytes) zu Bytes/s. Fällt der Zähler, beginnt er neu (wie Prometheus): der neue Stand zählt als Zuwachs.
export function rate(punkte: [number, number][]): { ts: number; bytes_je_s: number }[] {
  const out: { ts: number; bytes_je_s: number }[] = []
  for (let i = 1; i < punkte.length; i++) {
    const [t0, v0] = punkte[i - 1]!
    const [t1, v1] = punkte[i]!
    if (t1 > t0) out.push({ ts: t1, bytes_je_s: (v1 >= v0 ? v1 - v0 : v1) / (t1 - t0) })
  }
  return out
}

// Zähler aus der Historie: Rate aus dem jeweils größten Wert je Zeitfenster (der Zähler steigt, das Maximum ist der Endstand).
export function rateDerSerie(punkte: Punkt[]): Punkt[] {
  return rate(punkte.map((p) => [p.ts / 1000, p.max])).map((r) => ({ ts: r.ts * 1000, avg: r.bytes_je_s, max: r.bytes_je_s }))
}

export interface Zeile { ts: number; [linie: string]: number }
export function zusammenfuehren(linien: Linie[]): Zeile[] {
  const nachZeit = new Map<number, Zeile>()
  linien.forEach((l, i) => l.punkte.forEach((p) => {
    const z = nachZeit.get(p.ts) ?? { ts: p.ts }
    z[`l${i}`] = p.avg
    nachZeit.set(p.ts, z)
  }))
  return [...nachZeit.values()].sort((a, b) => a.ts - b.ts)
}

export const mittel = (punkte: Punkt[]) => punkte.reduce((s, p) => s + p.avg, 0) / punkte.length
export const spitze = (punkte: Punkt[]) => Math.max(...punkte.map((p) => p.max))
