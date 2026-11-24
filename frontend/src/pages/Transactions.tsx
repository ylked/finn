import { useInfiniteQuery } from '@tanstack/react-query'
import { CheckSquare, Filter } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AccountPicker } from '../components/AccountPicker'
import { BulkBar, TxRow, TxSheet, useSelection, useTxView } from '../components/Tx'
import { Empty, Money, PageTitle, SearchInput, Spinner } from '../components/ui'
import { api, qs } from '../lib/api'
import { fmtDate } from '../lib/format'
import { useAccounts } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Transaction } from '../lib/types'

const PAGE = 50

export function Transactions() {
  const { t, locale } = useI18n()
  const [sp, setSp] = useSearchParams()
  const { byId } = useAccounts()
  const [open, setOpen] = useState<number | null>(sp.get('open') ? Number(sp.get('open')) : null)
  const [showFilters, setShowFilters] = useState(false)
  const sel = useSelection()
  const accountId = sp.get('account_id') ? Number(sp.get('account_id')) : null
  const params = {
    account_id: accountId,
    q: sp.get('q'),
    state: sp.getAll('state'),
    date_from: sp.get('date_from'),
    date_to: sp.get('date_to'),
    unreconciled: sp.get('unreconciled') === '1',
  }
  const set = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp)
    if (v) n.set(k, v)
    else n.delete(k)
    setSp(n, { replace: true })
  }
  const q = useInfiniteQuery({
    queryKey: ['transactions', params],
    queryFn: ({ pageParam }) => api.get<{ items: Transaction[]; total: number }>(`/api/transactions${qs({ ...params, limit: PAGE, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last, all) => (all.length * PAGE < last.total ? all.length * PAGE : undefined),
  })
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  const total = q.data?.pages[0]?.total ?? 0
  const acc = accountId ? byId.get(accountId) : undefined
  const view = useTxView(accountId)

  // group rows by date for readability
  const groups: [string, Transaction[]][] = []
  for (const tx of items) {
    const last = groups[groups.length - 1]
    if (last && last[0] === tx.date) last[1].push(tx)
    else groups.push([tx.date, [tx]])
  }

  return (
    <div>
      <PageTitle
        actions={
          <>
            <button className={sel.active ? 'btn-primary' : 'btn-secondary'} aria-label={t('select')} title={t('select')} onClick={() => (sel.active ? sel.stop() : sel.start())}>
              <CheckSquare className="h-4 w-4" /> <span className="hidden sm:inline">{t('select')}</span>
            </button>
            <button className="btn-secondary" onClick={() => setShowFilters((x) => !x)}><Filter className="h-4 w-4" /></button>
          </>
        }
      >
        {acc ? `${acc.code} ${acc.name}` : t('transactions')}
      </PageTitle>
      {acc && (
        <div className="mb-3 text-sm text-slate-500">
          {t('balance')}: <Money value={acc.balance} className="font-semibold text-slate-900 dark:text-slate-100" />
        </div>
      )}
      <SearchInput className="mb-3" placeholder={t('search')} defaultValue={params.q ?? ''} onChange={(v) => set('q', v)} />
      {showFilters && (
        <div className="card mb-3 grid gap-3 p-3 md:grid-cols-4">
          <div>
            <span className="label">{t('filter_account')}</span>
            <div className="flex gap-1">
              <AccountPicker value={accountId} onChange={(id) => set('account_id', String(id))} allowGroups />
              {accountId && <button className="btn-ghost" onClick={() => set('account_id', null)}>×</button>}
            </div>
          </div>
          <div>
            <span className="label">{t('filter_state')}</span>
            <select className="input" value={params.state[0] ?? ''} onChange={(e) => set('state', e.target.value || null)}>
              <option value="">{t('all')}</option>
              <option value="unclassified">{t('state_unclassified')}</option>
              <option value="auto">{t('state_auto')}</option>
              <option value="confirmed">{t('state_confirmed')}</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div><span className="label">{t('from')}</span><input type="date" className="input" value={params.date_from ?? ''} onChange={(e) => set('date_from', e.target.value)} /></div>
            <div><span className="label">{t('to')}</span><input type="date" className="input" value={params.date_to ?? ''} onChange={(e) => set('date_to', e.target.value)} /></div>
          </div>
          <label className="flex items-center gap-2 self-end pb-2 text-sm">
            <input type="checkbox" className="accent-brand-700" checked={params.unreconciled} onChange={(e) => set('unreconciled', e.target.checked ? '1' : null)} />
            {t('only_unreconciled')}
          </label>
        </div>
      )}
      <div className="mb-2 text-xs text-slate-500">{total} {t('transactions').toLowerCase()}</div>
      {q.isLoading ? (
        <Spinner />
      ) : !items.length ? (
        <Empty>{t('no_transactions')}</Empty>
      ) : (
        <div className="card overflow-hidden">
          {groups.map(([date, txs]) => (
            <div key={date}>
              <div className="sticky top-0 z-10 bg-slate-50/95 px-4 py-1 text-xs font-medium text-slate-500 backdrop-blur dark:bg-slate-950/90">{fmtDate(date, locale)}</div>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {txs.map((tx) =>
                  sel.active ? (
                    <TxRow key={tx.id} tx={tx} view={view} selected={sel.has(tx.id)} onSelect={(v) => sel.toggle(tx.id, v)} onClick={() => sel.toggle(tx.id)} />
                  ) : (
                    <TxRow key={tx.id} tx={tx} view={view} onClick={() => setOpen(tx.id)} />
                  ),
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {q.hasNextPage && (
        <div className="mt-4 text-center">
          <button className="btn-secondary" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>{t('load_more')}</button>
        </div>
      )}
      {sel.active && <div className="h-16" />}
      <BulkBar sel={sel} allIds={items.map((x) => x.id)} />
      <TxSheet id={open} onClose={() => setOpen(null)} />
    </div>
  )
}
