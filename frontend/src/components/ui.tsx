import { X, Loader2, Search } from 'lucide-react'
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
    <label className={cls('block min-w-0', className)}>
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

/** Visible area (shrinks when the on-screen keyboard opens on iOS/Android), tracked while `active`. */
function useVisualViewport(active: boolean) {
  const [box, setBox] = useState<{ top: number; height: number } | null>(null)
  useEffect(() => {
    const vv = window.visualViewport
    if (!active || !vv) return
    const update = () => setBox({ top: vv.offsetTop, height: vv.height })
    update()
    vv.addEventListener('resize', update)
    vv.addEventListener('scroll', update)
    return () => {
      vv.removeEventListener('resize', update)
      vv.removeEventListener('scroll', update)
    }
  }, [active])
  return box
}

/** Bottom sheet on phones, centered dialog on larger screens. `tall` (lists with a search box) fills
 * the visible height on phones so results stay above the keyboard. */
export function Sheet({ open, onClose, title, children, wide = false, tall = false }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; wide?: boolean; tall?: boolean }) {
  const vv = useVisualViewport(open)
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
    <div
      className="fixed inset-x-0 top-0 z-50 flex h-dvh items-end justify-center md:items-center"
      style={vv ? { top: vv.top, height: vv.height } : undefined}
      role="dialog"
      aria-modal="true"
    >
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]" onClick={onClose} />
      <div
        className={cls(
          'relative flex max-h-[92%] w-full flex-col rounded-t-3xl bg-white shadow-xl dark:bg-slate-900 md:max-h-[85%] md:rounded-2xl',
          tall && 'h-[92%] md:h-auto',
          wide ? 'md:max-w-3xl' : 'md:max-w-lg',
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <div className="min-w-0 truncate font-semibold">{title}</div>
          <button className="btn-ghost -mr-2 p-2" onClick={onClose} aria-label="close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="pb-safe min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">{children}</div>
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
          className={cls('min-w-0 flex-1 rounded-lg px-2 py-1.5 text-sm font-medium leading-tight transition sm:px-3 md:flex-none md:whitespace-nowrap', value === o.value ? 'bg-white shadow-sm dark:bg-slate-950' : 'text-slate-500')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Search box with a one-click clear button. Works controlled (value) or uncontrolled (defaultValue). */
export function SearchInput({ value, defaultValue, onChange, onEnter, placeholder, autoFocus, className = '' }: { value?: string; defaultValue?: string; onChange: (v: string) => void; onEnter?: () => void; placeholder?: string; autoFocus?: boolean; className?: string }) {
  const ref = useRef<HTMLInputElement>(null)
  const [hasText, setHasText] = useState(!!(value ?? defaultValue))
  const shown = value !== undefined ? !!value : hasText
  return (
    <div className={cls('relative', className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      <input
        ref={ref}
        type="text"
        enterKeyHint="search"
        className="input pl-9 pr-9"
        placeholder={placeholder}
        autoFocus={autoFocus}
        value={value}
        defaultValue={value === undefined ? defaultValue : undefined}
        onChange={(e) => {
          setHasText(!!e.target.value)
          onChange(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onEnter?.()
          if (e.key === 'Escape' && shown) {
            e.stopPropagation()
            if (ref.current) ref.current.value = ''
            setHasText(false)
            onChange('')
          }
        }}
      />
      {shown && (
        <button
          type="button"
          aria-label="clear"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
          onClick={() => {
            if (ref.current) ref.current.value = ''
            setHasText(false)
            onChange('')
            ref.current?.focus()
          }}
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  )
}

/** Horizontal filter chips (scrolls on phones). */
export function Chips<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; count?: number }[] }) {
  return (
    <div className="-mx-4 flex gap-2 overflow-x-auto px-4 py-1 md:mx-0 md:flex-wrap md:px-0">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={cls(
            'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ring-1 transition',
            value === o.value
              ? 'bg-brand-700 text-white ring-brand-700 dark:bg-brand-500 dark:text-slate-950 dark:ring-brand-500'
              : 'bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-800',
          )}
        >
          {o.label}
          {o.count != null && <span className={cls('tnum text-xs', value === o.value ? 'opacity-80' : 'text-slate-400')}>{o.count}</span>}
        </button>
      ))}
    </div>
  )
}
