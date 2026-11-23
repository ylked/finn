import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronRight, List, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AccountPicker } from '../components/AccountPicker'
import { Badge, Field, Money, PageTitle, Sheet, Spinner, useToast } from '../components/ui'
import { api } from '../lib/api'
import { cls, fmtDate, num } from '../lib/format'
import { useAccounts, useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Account, AccountKind, AccountType } from '../lib/types'

const TYPES: AccountType[] = ['asset', 'liability', 'income', 'expense']
const KINDS: AccountKind[] = ['other', 'bank', 'credit_card', 'cash', 'receivable', 'payable', 'equity', 'suspense']

export function Accounts() {
  const { t } = useI18n()
  const { accounts, isLoading } = useAccounts()
  const [showInactive, setShowInactive] = useState(false)
  const [edit, setEdit] = useState<Account | 'new' | null>(null)
  const children = useMemo(() => {
    const m = new Map<number | null, Account[]>()
    for (const a of accounts) {
      if (!showInactive && !a.is_active) continue
      m.set(a.parent_id, [...(m.get(a.parent_id) ?? []), a])
    }
    return m
  }, [accounts, showInactive])

  const render = (a: Account, depth: number): JSX.Element => (
    <div key={a.id}>
      <button
        className={cls('flex w-full items-center gap-2 py-2 pr-3 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50', a.is_group && 'font-semibold', !a.is_active && 'opacity-50')}
        style={{ paddingLeft: `${0.75 + depth * 1}rem` }}
        onClick={() => setEdit(a)}
      >
        <span className="w-16 shrink-0 font-mono text-xs text-slate-400">{a.code}</span>
        <span className="min-w-0 flex-1 truncate">{a.name}</span>
        {a.kind !== 'other' && !a.is_group && <Badge className="hidden md:inline-flex">{t(`kind_${a.kind}`)}</Badge>}
        <Money value={a.balance} className={cls('text-right', num(a.balance) === 0 && 'text-slate-300 dark:text-slate-600')} />
      </button>
      {(children.get(a.id) ?? []).map((c) => render(c, depth + 1))}
    </div>
  )

  return (
    <div>
      <PageTitle actions={<button className="btn-primary" onClick={() => setEdit('new')}><Plus className="h-4 w-4" /> {t('new_account')}</button>}>{t('chart')}</PageTitle>
      <label className="mb-3 flex items-center gap-2 text-sm text-slate-500">
        <input type="checkbox" className="accent-brand-700" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> {t('show_inactive')}
      </label>
      {isLoading ? (
        <Spinner />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {TYPES.map((ty) => (
            <div key={ty} className="card overflow-hidden py-1">
              <div className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{t(`type_${ty}`)}</div>
              {(children.get(null) ?? []).filter((a) => a.type === ty).map((a) => render(a, 0))}
            </div>
          ))}
        </div>
      )}
      {edit && <AccountSheet account={edit === 'new' ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  )
}

function AccountSheet({ account, onClose }: { account: Account | null; onClose: () => void }) {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const { byId } = useAccounts()
  const [f, setF] = useState({
    code: account?.code ?? '',
    name: account?.name ?? '',
    type: account?.type ?? ('expense' as AccountType),
    kind: account?.kind ?? ('other' as AccountKind),
    parent_id: account?.parent_id ?? null,
    is_group: account?.is_group ?? false,
    is_active: account?.is_active ?? true,
    iban: account?.iban ?? '',
    description: account?.description ?? '',
    keywords: account?.keywords ?? '',
  })
  const [opening, setOpening] = useState({ amount: account?.opening_balance ?? '', date: account?.opening_date ?? '' })
  const rec = useQuery({
    queryKey: ['reconciliation', account?.id],
    queryFn: () => api.get<{ has_statement: boolean; statement_date?: string; closing_balance?: string; ledger_balance?: string; difference?: string; unbooked_entries?: number }>(`/api/accounts/${account!.id}/reconciliation`),
    enabled: !!account && ['bank', 'credit_card'].includes(account.kind),
  })
  const err = (e: unknown) => toast(String((e as Error).message), 'error')
  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, iban: f.iban || null, description: f.description || null, keywords: f.keywords || null }
      return account ? api.patch(`/api/accounts/${account.id}`, body) : api.post('/api/accounts', body)
    },
    onSuccess: () => { toast(t('saved')); refresh(); onClose() },
    onError: err,
  })
  const saveOpening = useMutation({
    mutationFn: () => api.post(`/api/accounts/${account!.id}/opening`, { amount: opening.amount === '' ? null : opening.amount, date: opening.date || null }),
    onSuccess: () => { toast(t('saved')); refresh() },
    onError: err,
  })
  const del = useMutation({ mutationFn: () => api.del(`/api/accounts/${account!.id}`), onSuccess: () => { refresh(); onClose() }, onError: err })
  const parent = f.parent_id ? byId.get(f.parent_id) : undefined

  return (
    <Sheet open onClose={onClose} title={account ? `${account.code} ${account.name}` : t('new_account')}>
      <div className="space-y-3">
        {account && !account.is_group && (
          <Link to={`/transactions?account_id=${account.id}`} className="btn-secondary w-full"><List className="h-4 w-4" /> {t('transactions')} <ChevronRight className="h-4 w-4" /></Link>
        )}
        {rec.data?.has_statement && (
          <div className={cls('rounded-xl p-3 text-sm', num(rec.data.difference) === 0 ? 'bg-emerald-50 dark:bg-emerald-950/40' : 'bg-amber-50 dark:bg-amber-950/40')}>
            <div className="font-medium">{t('ledger_vs_statement', { d: fmtDate(rec.data.statement_date, locale) })}</div>
            <div className="flex justify-between"><span>{t('statement_balance')}</span><Money value={rec.data.closing_balance} /></div>
            <div className="flex justify-between"><span>{t('balance')}</span><Money value={rec.data.ledger_balance} /></div>
            <div className="flex justify-between font-medium"><span>{t('difference')}</span><Money value={rec.data.difference} /></div>
          </div>
        )}
        <div className="grid grid-cols-3 gap-2">
          <Field label={t('code')}><input className="input font-mono" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
          <Field label={t('name')} className="col-span-2"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label={t('type')}>
            <select className="input" value={f.type} disabled={!!account} onChange={(e) => setF({ ...f, type: e.target.value as AccountType, parent_id: null })}>
              {TYPES.map((x) => <option key={x} value={x}>{t(`type_${x}`)}</option>)}
            </select>
          </Field>
          <Field label={t('kind')}>
            <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as AccountKind })}>
              {KINDS.map((x) => <option key={x} value={x}>{t(`kind_${x}`)}</option>)}
            </select>
          </Field>
        </div>
        <Field label={t('parent')}>
          <div className="flex gap-2">
            <AccountPicker className="flex-1" value={f.parent_id} allowGroups types={[f.type]} exclude={account ? [account.id] : []} onChange={(id, a) => a.is_group ? setF({ ...f, parent_id: id }) : toast(t('is_group'), 'error')} placeholder={parent ? undefined : '—'} />
            {f.parent_id && <button className="btn-ghost" onClick={() => setF({ ...f, parent_id: null })}>×</button>}
          </div>
        </Field>
        {['bank', 'credit_card'].includes(f.kind) && (
          <Field label={t('iban')}><input className="input font-mono" value={f.iban} onChange={(e) => setF({ ...f, iban: e.target.value })} /></Field>
        )}
        <Field label={t('ai_hint')}><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label={t('keywords')}><input className="input" value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} /></Field>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" className="accent-brand-700" checked={f.is_group} disabled={!!account} onChange={(e) => setF({ ...f, is_group: e.target.checked })} /> {t('is_group')}</label>
          <label className="flex items-center gap-2"><input type="checkbox" className="accent-brand-700" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> {t('active')}</label>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary flex-1" disabled={!f.code || !f.name || save.isPending} onClick={() => save.mutate()}>{t('save')}</button>
          {account && <button className="btn-danger" onClick={() => confirm(t('are_you_sure')) && del.mutate()}>{t('delete')}</button>}
        </div>

        {account && !account.is_group && (account.type === 'asset' || account.type === 'liability') && account.kind !== 'equity' && account.kind !== 'suspense' && (
          <div className="space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
            <div className="text-sm font-medium">{t('opening_balance')}</div>
            <p className="text-xs text-slate-500">{t('opening_help')}</p>
            <div className="grid grid-cols-2 gap-2">
              <input inputMode="decimal" className="input text-right" placeholder="0.00" value={opening.amount} onChange={(e) => setOpening({ ...opening, amount: e.target.value })} />
              <input type="date" className="input" value={opening.date} onChange={(e) => setOpening({ ...opening, date: e.target.value })} />
            </div>
            <button className="btn-secondary w-full" onClick={() => saveOpening.mutate()}>{t('save')}</button>
          </div>
        )}
      </div>
    </Sheet>
  )
}
