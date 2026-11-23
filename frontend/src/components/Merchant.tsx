import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ImageUp, Merge, Plus, RotateCcw, Search, Store, Trash2, Wand2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { cls, fold } from '../lib/format'
import { useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Merchant, MerchantBrief, Transaction } from '../lib/types'
import { Field, Money, Sheet, useToast } from './ui'

const sizes = { sm: 'h-8 w-8 text-[11px] rounded-lg', md: 'h-10 w-10 text-xs rounded-xl', lg: 'h-16 w-16 text-lg rounded-2xl' }

function initials(name: string) {
  const w = name.replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/).filter(Boolean)
  return ((w[0]?.[0] ?? '?') + (w[1]?.[0] ?? '')).toUpperCase()
}

// logos that failed to load in this session: show initials right away next time
const broken = new Set<string>()

/** Merchant logo (cached by the server), or its initials when there is none. */
export function MerchantLogo({ m, size = 'sm', className = '' }: { m: Pick<MerchantBrief, 'name' | 'logo_url'> | null | undefined; size?: keyof typeof sizes; className?: string }) {
  const [failed, setFailed] = useState(false)
  const url = m?.logo_url && !broken.has(m.logo_url) && !failed ? m.logo_url : null
  if (!m)
    return (
      <span className={cls('flex shrink-0 items-center justify-center bg-slate-100 text-slate-400 dark:bg-slate-800', sizes[size], className)}>
        <Store className="h-1/2 w-1/2" />
      </span>
    )
  if (url)
    return (
      <span className={cls('flex shrink-0 items-center justify-center overflow-hidden bg-white ring-1 ring-slate-200 dark:ring-slate-700', sizes[size], className)}>
        <img
          src={url}
          alt=""
          loading="lazy"
          className="h-[78%] w-[78%] object-contain"
          onError={() => {
            broken.add(url)
            setFailed(true)
          }}
        />
      </span>
    )
  return (
    <span className={cls('flex shrink-0 items-center justify-center bg-slate-100 font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300', sizes[size], className)} aria-hidden>
      {initials(m.name)}
    </span>
  )
}

export function useMerchants(enabled = true) {
  return useQuery({ queryKey: ['merchants'], queryFn: () => api.get<Merchant[]>('/api/merchants'), staleTime: 30_000, enabled })
}

export function useRefreshMerchants() {
  const qc = useQueryClient()
  const refresh = useRefreshLedger()
  return () => {
    refresh()
    for (const k of ['merchants', 'merchant', 'insights']) qc.invalidateQueries({ queryKey: [k] })
  }
}

type Choice = { merchant_id: number } | { name: string } | { none: true }

/** Pick an existing merchant, create one by typing its name, or choose "no merchant". */
export function MerchantPicker({ open, onClose, onPick, title, exclude, allowNone = true, footer }: {
  open: boolean
  onClose: () => void
  onPick: (c: Choice) => void
  title?: string
  exclude?: number
  allowNone?: boolean
  footer?: React.ReactNode
}) {
  const { t } = useI18n()
  const [q, setQ] = useState('')
  const list = useMerchants(open)
  const f = fold(q.trim())
  const items = (list.data ?? []).filter((m) => m.id !== exclude && (!f || fold(m.name).includes(f) || (m.domain ?? '').includes(f)))
  const exact = (list.data ?? []).some((m) => fold(m.name) === f)
  return (
    <Sheet open={open} onClose={onClose} title={title ?? t('merchant_choose')} tall>
      <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-3 bg-white px-4 pb-3 pt-4 dark:bg-slate-900">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input className="input pl-9" autoFocus placeholder={t('merchant_search')} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {footer}
      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {q.trim() && !exact && (
          <button className="flex w-full items-center gap-3 py-2.5 text-left" onClick={() => onPick({ name: q.trim() })}>
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"><Plus className="h-4 w-4" /></span>
            <span className="text-sm font-medium">{t('merchant_create', { name: q.trim() })}</span>
          </button>
        )}
        {allowNone && !q && (
          <button className="flex w-full items-center gap-3 py-2.5 text-left text-sm text-slate-500" onClick={() => onPick({ none: true })}>
            <MerchantLogo m={null} /> {t('merchant_none')}
          </button>
        )}
        {items.slice(0, 200).map((m) => (
          <button key={m.id} className="flex w-full items-center gap-3 py-2.5 text-left" onClick={() => onPick({ merchant_id: m.id })}>
            <MerchantLogo m={m} />
            <span className="min-w-0 flex-1 truncate text-sm">{m.name}</span>
            <span className="text-xs text-slate-400">{m.tx_count}</span>
          </button>
        ))}
      </div>
    </Sheet>
  )
}

/** Merchant line of the entry editor: shows the detected merchant and lets the user fix it. */
export function TxMerchant({ tx, onChanged }: { tx: Transaction; onChanged?: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshMerchants()
  const [open, setOpen] = useState(false)
  const [learn, setLearn] = useState(true)
  const set = useMutation({
    mutationFn: (c: Choice | 'auto') =>
      c === 'auto'
        ? api.post<{ merchant: MerchantBrief | null; applied: number }>(`/api/transactions/${tx.id}/merchant/reset`)
        : api.put<{ merchant: MerchantBrief | null; applied: number }>(`/api/transactions/${tx.id}/merchant`, {
            merchant_id: 'merchant_id' in c ? c.merchant_id : null,
            name: 'name' in c ? c.name : null,
            learn: !('none' in c) && learn,
          }),
    onSuccess: (r) => {
      setOpen(false)
      refresh()
      onChanged?.()
      toast(r.applied ? t('merchant_applied', { n: r.applied }) : t('saved'))
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-2 pr-1 dark:bg-slate-800/50">
      <MerchantLogo m={tx.merchant} />
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-slate-500">{t('merchant')}{tx.merchant_locked && ` · ${t('merchant_manual')}`}</div>
        {tx.merchant ? (
          <Link to={`/insights/merchants/${tx.merchant.id}`} className="block truncate text-sm font-medium hover:underline">{tx.merchant.name}</Link>
        ) : (
          <div className="text-sm text-slate-400">{t('merchant_none')}</div>
        )}
      </div>
      {tx.merchant_locked && (
        <button className="btn-ghost p-2" title={t('merchant_auto')} aria-label={t('merchant_auto')} onClick={() => set.mutate('auto')} disabled={set.isPending}>
          <RotateCcw className="h-4 w-4" />
        </button>
      )}
      <button className="btn-ghost px-2.5 py-1.5 text-sm" onClick={() => setOpen(true)}>{t('edit')}</button>
      <MerchantPicker
        open={open}
        onClose={() => setOpen(false)}
        onPick={(c) => set.mutate(c)}
        footer={
          <label className="mb-3 flex items-start gap-2 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
            <input type="checkbox" className="mt-0.5 accent-brand-700" checked={learn} onChange={(e) => setLearn(e.target.checked)} />
            <span>
              <Wand2 className="mr-1 inline h-3.5 w-3.5" />
              {t('merchant_learn')}
              <span className="block text-xs text-slate-500">{tx.counterparty || tx.description}</span>
            </span>
          </label>
        }
      />
    </div>
  )
}

/** Edit a merchant: name, website (logo), recognition patterns, custom logo, merge, delete. */
export function MerchantEditSheet({ m, open, onClose, onDeleted }: { m: Merchant; open: boolean; onClose: () => void; onDeleted?: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshMerchants()
  const [name, setName] = useState(m.name)
  const [domain, setDomain] = useState(m.domain ?? '')
  const [patterns, setPatterns] = useState(m.patterns.join('\n'))
  const [merging, setMerging] = useState(false)
  const navigate = useNavigate()
  const file = useRef<HTMLInputElement>(null)
  const err = (e: unknown) => toast(String((e as Error).message), 'error')
  const save = useMutation({
    mutationFn: () => api.patch<Merchant>(`/api/merchants/${m.id}`, { name, domain, patterns: patterns.split('\n').map((p) => p.trim()).filter(Boolean) }),
    onSuccess: () => {
      refresh()
      toast(t('saved'))
      onClose()
    },
    onError: err,
  })
  const logo = useMutation({
    mutationFn: (f: File | null) => {
      if (!f) return api.del<Merchant>(`/api/merchants/${m.id}/logo`)
      const fd = new FormData()
      fd.append('file', f)
      return api.put<Merchant>(`/api/merchants/${m.id}/logo`, fd)
    },
    onSuccess: () => refresh(),
    onError: err,
  })
  const merge = useMutation({
    mutationFn: (into: number) => api.post<Merchant>(`/api/merchants/${m.id}/merge`, { into_id: into }),
    onSuccess: (r) => {
      refresh()
      toast(t('merchant_merged', { name: r.name }))
      setMerging(false)
      onClose()
      navigate(`/insights/merchants/${r.id}`, { replace: true })
    },
    onError: err,
  })
  const del = useMutation({
    mutationFn: () => api.del(`/api/merchants/${m.id}`),
    onSuccess: () => {
      refresh()
      onClose()
      onDeleted?.()
    },
    onError: err,
  })
  return (
    <Sheet open={open} onClose={onClose} title={t('merchant_edit')}>
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <MerchantLogo m={m} size="lg" />
          <div className="flex flex-wrap gap-2">
            <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif" hidden onChange={(e) => e.target.files?.[0] && logo.mutate(e.target.files[0])} />
            <button className="btn-secondary text-sm" onClick={() => file.current?.click()} disabled={logo.isPending}><ImageUp className="h-4 w-4" /> {t('merchant_logo_upload')}</button>
            {(m.logo_status === 'custom' || m.logo_status === 'failed') && (
              <button className="btn-ghost text-sm" onClick={() => logo.mutate(null)} disabled={logo.isPending}><RotateCcw className="h-4 w-4" /> {t('merchant_logo_reset')}</button>
            )}
          </div>
        </div>
        <Field label={t('name')}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t('merchant_domain')} hint={t('merchant_domain_help')}>
          <input className="input" inputMode="url" autoCapitalize="none" placeholder="migros.ch" value={domain} onChange={(e) => setDomain(e.target.value)} />
        </Field>
        <Field label={t('merchant_patterns')} hint={t('merchant_patterns_help')}>
          <textarea className="input min-h-[6rem] font-mono text-sm" value={patterns} onChange={(e) => setPatterns(e.target.value)} />
        </Field>
        <button className="btn-primary w-full" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
          <Check className="h-4 w-4" /> {t('save')}
        </button>
        <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          <button className="btn-ghost text-sm" onClick={() => setMerging(true)}><Merge className="h-4 w-4" /> {t('merchant_merge')}</button>
          <button className="btn-danger text-sm" onClick={() => confirm(t('merchant_delete_confirm')) && del.mutate()}><Trash2 className="h-4 w-4" /> {t('delete')}</button>
        </div>
      </div>
      <MerchantPicker
        open={merging}
        onClose={() => setMerging(false)}
        exclude={m.id}
        allowNone={false}
        title={t('merchant_merge_into', { name: m.name })}
        onPick={(c) => 'merchant_id' in c && confirm(t('merchant_merge_confirm')) && merge.mutate(c.merchant_id)}
      />
    </Sheet>
  )
}

/** Compact list row used on the merchants page and in insights. */
export function MerchantRow({ m, amount, count, share, onClick }: { m: MerchantBrief; amount: string | number; count: number; share?: number; onClick?: () => void }) {
  const { t } = useI18n()
  return (
    <button className="flex w-full items-center gap-3 py-2 text-left" onClick={onClick}>
      <MerchantLogo m={m} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium">{m.name}</span>
          <Money value={amount} className="text-sm font-medium" />
        </div>
        <div className="mt-1 flex items-center gap-2">
          {share !== undefined && (
            <div className="h-1.5 flex-1 rounded-full bg-slate-100 dark:bg-slate-800">
              <div className="h-1.5 rounded-full bg-brand-600 dark:bg-brand-500" style={{ width: `${Math.max(2, share * 100)}%` }} />
            </div>
          )}
          <span className="shrink-0 text-xs text-slate-500">{t('n_visits', { n: count })}</span>
        </div>
      </div>
    </button>
  )
}
