import { useQueries } from '@tanstack/react-query'
import { verlaufOptionen } from '../../api/abfragen'
import type { Snapshot } from '../../api/typen'
import { t } from '../../texte/de'
import { formatWert } from '../../texte/metriken'
import { Leerzustand } from '../../ui/Leerzustand'
import { Tabelle, Zeilenkopf, Zelle } from '../../ui/Tabelle'
import { istZahl } from './Kennzahlen'
import { punkteDerSerie, rateDerSerie } from './verlaufsdaten'

const FENSTER_MS = 10 * 60_000 // genug Rohpunkte für zwei Abstände, aber klein
const nv = t('server.nv')
const je = (w: number | undefined) => (w === undefined ? nv : `${formatWert('moni_network_receive_bytes', w)}/s`)

export function Netzwerk({ serverId, snapshot }: { serverId: number; snapshot: Snapshot | undefined }) {
  // Je Zähler eine Abfrage für alle Interfaces; die letzte Rate je Interface kommt aus den beiden jüngsten Punkten.
  const abfragen = useQueries({ queries: ['moni_network_receive_bytes', 'moni_network_transmit_bytes'].map((m) => verlaufOptionen(serverId, m, undefined, FENSTER_MS)) })
  const [rx, tx] = [abfragen[0]?.data, abfragen[1]?.data]
  const letzte = (daten: typeof rx, name: string | undefined) => {
    const serie = daten?.series.find((s) => s.labels.interface === name)
    return serie ? rateDerSerie(punkteDerSerie(serie.points)).at(-1)?.avg : undefined
  }
  const liste = snapshot?.host?.network ?? []
  if (liste.length === 0) return <Leerzustand text={t('server.keineSchnittstellen')} />
  return (
    <Tabelle id="netzwerk-h" titel={t('server.netzwerk')} kopf={[t('server.schnittstelle'), t('server.empfangenJeS'), t('server.gesendetJeS'), t('server.gesamt')]}>
      {liste.map((n, i) => (
        <tr key={`${n.interface}-${i}`} className="border-b border-border last:border-0">
          <Zeilenkopf>{n.interface ?? nv}</Zeilenkopf>
          <Zelle num>{je(letzte(rx, n.interface))}</Zelle>
          <Zelle num>{je(letzte(tx, n.interface))}</Zelle>
          <Zelle num>{istZahl(n.rx_bytes) && istZahl(n.tx_bytes) ? formatWert('moni_network_receive_bytes', n.rx_bytes + n.tx_bytes) : nv}</Zelle>
        </tr>
      ))}
    </Tabelle>
  )
}
