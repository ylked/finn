import { X, Loader2 } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cls, money, num } from '../lib/format'

export function Spinner({ className = '' }: { className?: string }) {
  return <Loader2 className={cls('h-5 w-5 animate-spin text-slate-400', className)} />
}

export function PageTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{children}</h1>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Money({ value, className = '', sign = false, colored = false }: { value: string | number | null | undefined; className?: string; sign?: boolean; colored?: boolean }) {
  const n = num(value)
  return (
    <span className={cls('tnum whitespace-nowrap', colored && n < 0 && 'text-red-600 dark:text-red-400', colored && n > 0 && 'text-emerald-700 dark:text-emerald-400', className)}>
      {money(value, { sign })}
    </span>
  )
}

const badgeColors: Record<string, string> = {
  gray: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  green: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  amber: 'bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  red: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
  blue: 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300',
  violet: 'bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300',
}

export function Badge({ color = 'gray', children, className = '' }: { color?: keyof typeof badgeColors; children: ReactNode; className?: string }) {
  return <span className={cls('inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium', badgeColors[color], className)}>{children}</span>
}

export function Field({ label, children, hint, className = '' }: { label: string; children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <label className={cls('block', className)}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-slate-500">{children}</div>
}

// Scroll lock shared by all open sheets: the first one locks, the last one to close unlocks.
// (Saving/restoring the previous value per sheet breaks when nested sheets unmount together.)
let scrollLocks = 0

function lockScroll() {
  if (scrollLocks++ === 0) {
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
  }
}

function unlockScroll() {
  scrollLocks = Math.max(0, scrollLocks - 1)
  if (scrollLocks === 0) {
    document.documentElement.style.overflow = ''
    document.body.style.overflow = ''
  }
}

/** Bottom sheet on phones, centered dialog on larger screens. */
export function Sheet({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; wide?: boolean }) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  // lock depends on `open` only, so it is taken once per opening (not on every re-render)
  useEffect(() => {
    if (!open) return
    lockScroll()
    return unlockScroll
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeRef.current()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  if (!open) return null
  // Portal to <body>: a sheet opened from inside a <label> (e.g. an AccountPicker in a Field) would
  // otherwise receive the label's activation click, which re-clicks the picker button and reopens it.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]" onClick={onClose} />
      <div className={cls('relative flex max-h-[92dvh] w-full flex-col rounded-t-3xl bg-white shadow-xl dark:bg-slate-900 md:rounded-2xl', wide ? 'md:max-w-3xl' : 'md:max-w-lg')}>
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <div className="min-w-0 truncate font-semibold">{title}</div>
          <button className="btn-ghost -mr-2 p-2" onClick={onClose} aria-label="close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="pb-safe overflow-y-auto overscroll-contain p-4">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

// ------------------------------------------------------------------ toasts
type Toast = { id: number; text: string; kind: 'ok' | 'error' }
const ToastCtx = createContext<(text: string, kind?: 'ok' | 'error') => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((text: string, kind: 'ok' | 'error' = 'ok') => {
    const id = Date.now() + Math.random()
    setItems((xs) => [...xs, { id, text, kind }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 3000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex flex-col items-center gap-2 px-4 md:bottom-6">
        {items.map((t) => (
          <div key={t.id} className={cls('pointer-events-auto max-w-md rounded-xl px-4 py-2.5 text-sm shadow-lg', t.kind === 'error' ? 'bg-red-600 text-white' : 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900')}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex w-full rounded-xl bg-slate-100 p-1 dark:bg-slate-800 md:w-auto">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cls('flex-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition md:flex-none', value === o.value ? 'bg-white shadow-sm dark:bg-slate-950' : 'text-slate-500')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
