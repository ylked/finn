import { useMutation, useQuery } from '@tanstack/react-query'
import { Bot, Check, CheckCheck, CheckSquare, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import { AccountPicker } from '../components/AccountPicker'
import { BulkBar, isNeutral, StateBadge, TxSheet, txAmount, useSelection } from '../components/Tx'
import { Empty, Money, PageTitle, Segmented, Spinner, useToast } from '../components/ui'
import { api, qs } from '../lib/api'
import { cents, cls, fmtDate } from '../lib/format'
import { useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Transaction } from '../lib/types'

/** Fast review queue: one card per entry, change the counter-account in one tap, confirm. */
export function Review() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const [filter, setFilter] = useState<'auto' | 'unclassified' | 'all'>('all')
  const [open, setOpen] = useState<number | null>(null)
  const sel = useSelection()
  const states = filter === 'all' ? ['auto', 'unclassified'] : [filter]
  const q = useQuery({
    queryKey: ['transactions', 'review', filter],
    queryFn: () => api.get<{ items: Transaction[]; total: number }>(`/api/transactions${qs({ state: states, limit: 200 })}`),
    refetchInterval: (q) => (q.state.data?.items.some((x) => x.ai_status === 'pending') ? 4000 : false),
  })

  const confirmMany = useMutation({
    mutationFn: (ids: number[]) => api.post<{ confirmed: number }>('/api/transactions/confirm', ids),
    onSuccess: (r) => {
      toast(`✓ ${r.confirmed}`)
      refresh()
    },
  })

  const setAccount = useMutation({
    mutationFn: ({ tx, accountId }: { tx: Transaction; accountId: number }) => {
      const src = tx.lines.find((l) => l.bank_entry_id) ?? tx.lines[0]
      return api.put<Transaction>(`/api/transactions/${tx.id}`, {
        date: tx.date,
        description: tx.description,
        counterparty: tx.counterparty,
        notes: tx.notes,
        confirm: true,
        lines: [
          { account_id: src.account_id, amount: src.amount, memo: src.memo, bank_entry_id: src.bank_entry_id, orig_amount: src.orig_amount, orig_currency: src.orig_currency },
          { account_id: accountId, amount: cents(-Number(src.amount)) },
        ],
      })
    },
    onSuccess: () => refresh(),
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  const items = q.data?.items ?? []
  const autoIds = items.filter((x) => x.state === 'auto').map((x) => x.id)

  return (
    <div>
      <PageTitle
        actions={
          <>
            {items.length > 0 && (
              <button className={sel.active ? 'btn-primary' : 'btn-secondary'} aria-label={t('select')} title={t('select')} onClick={() => (sel.active ? sel.stop() : sel.start())}>
                <CheckSquare className="h-4 w-4" /> <span className="hidden sm:inline">{t('select')}</span>
              </button>
            )}
            {autoIds.length > 0 && !sel.active && (
              <button className="btn-primary" onClick={() => confirm(`${t('confirm_all')} (${autoIds.length}) ?`) && confirmMany.mutate(autoIds)}>
                <CheckCheck className="h-4 w-4" /> {t('confirm_all')} ({autoIds.length})
              </button>
            )}
          </>
        }
      >
        {t('review_title')}
      </PageTitle>
      <p className="mb-3 text-sm text-slate-500">{t('review_help')}</p>
      <div className="mb-4">
        <Segmented value={filter} onChange={setFilter} options={[{ value: 'all', label: t('all') }, { value: 'auto', label: t('state_auto') }, { value: 'unclassified', label: t('state_unclassified') }]} />
      </div>
      {q.isLoading ? (
        <Spinner />
      ) : !items.length ? (
        <Empty>{t('nothing_to_review')}</Empty>
      ) : (
        <div className="space-y-2">
          {items.map((tx) => {
            const src = tx.lines.find((l) => l.bank_entry_id) ?? tx.lines[0]
            const others = tx.lines.filter((l) => l !== src)
            const simple = others.length === 1
            return (
              <div key={tx.id} className={cls('card p-3', sel.has(tx.id) && 'ring-2 ring-brand-600')}>
                <div className="flex items-start gap-3">
                {sel.active && (
                  <input type="checkbox" className="mt-1 h-4 w-4 accent-brand-700" checked={sel.has(tx.id)} onChange={(e) => sel.toggle(tx.id, e.target.checked)} />
                )}
                <button className="flex min-w-0 flex-1 items-start gap-3 text-left" onClick={() => (sel.active ? sel.toggle(tx.id) : setOpen(tx.id))}>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{tx.counterparty || tx.description}</span>
                      <StateBadge tx={tx} />
                    </div>
                    <div className="truncate text-xs text-slate-500">{fmtDate(tx.date, locale)} · {tx.description}</div>
                  </div>
                  <Money value={txAmount(tx)} sign={!isNeutral(tx)} colored={!isNeutral(tx)} className="font-semibold" />
                  <ChevronRight className="mt-0.5 h-4 w-4 text-slate-300" />
                </button>
                </div>
                {tx.ai_reason && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-violet-700 dark:text-violet-300">
                    <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span className="line-clamp-2">{tx.ai_reason}</span>
                  </div>
                )}
                <div className="mt-2 flex items-center gap-2">
                  {simple ? (
                    <AccountPicker className="flex-1" value={others[0].account_id} exclude={[src.account_id]} onChange={(id) => setAccount.mutate({ tx, accountId: id })} />
                  ) : (
                    <div className="flex-1 truncate text-sm text-slate-600">{others.map((l) => `${l.account_code} ${l.account_name}`).join(' · ')}</div>
                  )}
                  {tx.state === 'auto' && (
                    <button className="btn-primary px-3" title={t('confirm')} onClick={() => confirmMany.mutate([tx.id])}>
                      <Check className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {sel.active && <div className="h-16" />}
      <BulkBar sel={sel} allIds={items.map((x) => x.id)} />
      <TxSheet id={open} onClose={() => setOpen(null)} />
    </div>
  )
}
