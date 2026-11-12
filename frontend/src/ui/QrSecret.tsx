import { toDataURL } from 'qrcode'
import { useEffect, useState } from 'react'
import { t } from '../texte/de'
import { Hinweis } from './Hinweis'

// QR-Code (nur als data:-Bild im Speicher) und Secret zum Abtippen; gemeinsam für die Ersteinrichtung und das Wechseln im Konto.
export function QrSecret({ secret, uri }: { secret: string; uri: string }) {
  const [bild, setBild] = useState('')
  const [qrFehler, setQrFehler] = useState(false)
  useEffect(() => {
    let aktuell = true
    toDataURL(uri, { margin: 2, width: 192 }).then((u) => aktuell && setBild(u), () => aktuell && setQrFehler(true))
    return () => { aktuell = false }
  }, [uri])
  return (
    <>
      {bild && <img src={bild} alt={t('zweiFaktor.qrAlt')} width={192} height={192} className="self-center rounded-lg" />}
      {qrFehler && <Hinweis art="fehler">{t('zweiFaktor.qrFehler')}</Hinweis>}
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium">{t('zweiFaktor.geheimnis')}</span>
        <code className="num rounded-lg bg-bg p-3 break-all">{secret}</code>
      </div>
    </>
  )
}
