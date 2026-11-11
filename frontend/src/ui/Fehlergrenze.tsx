import { Component, type ReactNode } from 'react'
import { t } from '../texte/de'
import { Knopf } from './Knopf'

// Fängt Ladefehler (z. B. React.lazy nach einem Deploy oder bei Netzabbruch), damit nicht die ganze App verschwindet.
export class Fehlergrenze extends Component<{ children: ReactNode }, { fehler: boolean }> {
  state = { fehler: false }
  static getDerivedStateFromError() { return { fehler: true } }
  render() {
    if (!this.state.fehler) return this.props.children
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 rounded-[14px] border border-border bg-surface px-5 py-4">
        <p className="m-0">{t('fehler.seiteLaden')}</p>
        <Knopf art="haupt" onClick={() => location.reload()}>{t('fehler.neuLaden')}</Knopf>
      </div>
    )
  }
}
