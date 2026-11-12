import type { ButtonHTMLAttributes } from 'react'

const ARTEN = {
  haupt: 'min-h-11.5 rounded-lg bg-accent px-4 font-semibold text-on-accent disabled:opacity-60',
  zweit: 'min-h-10 rounded-lg border border-control px-3 font-medium disabled:opacity-60',
  link: 'min-h-11 rounded-lg px-3 text-accent underline underline-offset-3',
} as const

export function Knopf({ art = 'zweit', type = 'button', className = '', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { art?: keyof typeof ARTEN }) {
  return <button {...rest} type={type} className={`cursor-pointer disabled:cursor-not-allowed ${ARTEN[art]} ${className}`} />
}
