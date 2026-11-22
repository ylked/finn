import { ChevronDown, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useAccounts } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Account, AccountKind, AccountType } from '../lib/types'
import { cls, fold } from '../lib/format'
import { Sheet } from './ui'

const TYPES: AccountType[] = ['expense', 'income', 'asset', 'liability']

export function AccountPicker({
  value,
  onChange,
  types,
  kinds,
  exclude = [],
  placeholder,
  disabled,
  allowGroups = false,
  className = '',
}: {
  value: number | null | undefined
  onChange: (id: number, acc: Account) => void
  types?: AccountType[]
  kinds?: AccountKind[]
  exclude?: number[]
  placeholder?: string
  disabled?: boolean
  allowGroups?: boolean
  className?: string
}) {
  const { t } = useI18n()
  const { accounts, byId } = useAccounts()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const current = value ? byId.get(value) : undefined

  const list = useMemo(() => {
    const s = fold(q.trim())
    return accounts.filter(
      (a) =>
        (allowGroups || !a.is_group) &&
        a.is_active &&
        !exclude.includes(a.id) &&
        (!types || types.includes(a.type)) &&
        (!kinds || kinds.includes(a.kind)) &&
        (!s || fold(a.code).startsWith(s) || fold(a.name).includes(s) || fold(a.keywords).includes(s) || fold(a.description).includes(s)),
    )
  }, [accounts, q, types, kinds, exclude, allowGroups])

  const grouped = TYPES.map((ty) => [ty, list.filter((a) => a.type === ty)] as const).filter(([, xs]) => xs.length)
  const pick = (a: Account) => {
    onChange(a.id, a)
    setOpen(false)
    setQ('')
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cls('input flex items-center justify-between gap-2 text-left', !current && 'text-slate-400', current?.kind === 'suspense' && 'border-amber-400 bg-amber-50 dark:bg-amber-950/40', className)}
      >
        <span className="min-w-0 truncate">{current ? `${current.code} · ${current.name}` : placeholder ?? t('choose_account')}</span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={placeholder ?? t('choose_account')} tall>
        <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-3 bg-white px-4 pb-3 pt-4 dark:bg-slate-900">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              autoFocus
              className="input pl-9"
              placeholder={t('search')}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && list.length) pick(list[0])
              }}
            />
          </div>
        </div>
        <div className="space-y-4">
          {grouped.map(([ty, xs]) => (
            <div key={ty}>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{t(`type_${ty}`)}</div>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {xs.map((a) => (
                  <button
                    type="button"
                    key={a.id}
                    onClick={() => pick(a)}
                    className={cls('flex w-full items-baseline gap-3 px-1 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800', a.id === value && 'font-semibold text-brand-700')}
                    style={{ paddingLeft: `${(a.code.replace(/\..*/, '').length - 2) * 0.25 + 0.25}rem` }}
                  >
                    <span className="w-16 shrink-0 font-mono text-xs text-slate-400">{a.code}</span>
                    <span className="min-w-0 flex-1">
                      {a.name}
                      {a.description && <span className="block truncate text-xs text-slate-400">{a.description}</span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
          {!grouped.length && <div className="py-6 text-center text-sm text-slate-400">—</div>}
        </div>
      </Sheet>
    </>
  )
}
