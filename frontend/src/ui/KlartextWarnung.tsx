import { t, type TextSchluessel } from '../texte/de'
import { Hinweis } from './Hinweis'

// N6: http:// schickt Token bzw. Alarminhalte im Klartext; ohne ausdrücklichen Haken lässt der Browser das Formular nicht absenden.
export function KlartextWarnung({ url, text, bestaetigt, onChange }: { url: string; text: TextSchluessel; bestaetigt: boolean; onChange: (v: boolean) => void }) {
  if (!/^\s*http:\/\//i.test(url)) return null
  return (
    <>
      <Hinweis art="fehler">{t(text)}</Hinweis>
      <label className="flex min-h-11 cursor-pointer items-center gap-3">
        <input type="checkbox" required checked={bestaetigt} onChange={(e) => onChange(e.target.checked)} className="size-5 accent-[var(--accent)]" />
        {t('formular.klartextBestaetigen')}
      </label>
    </>
  )
}
