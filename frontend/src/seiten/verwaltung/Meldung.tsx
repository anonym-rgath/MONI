// Ergebnis einer Aktion; die Region steht immer im DOM, damit Screenreader den neuen Text ansagen (D11).
export function Meldung({ text }: { text: string }) {
  return <div role="status">{text && <p className="m-0 rounded-lg bg-surface-2 px-3 py-2.5 text-sm">{text}</p>}</div>
}
