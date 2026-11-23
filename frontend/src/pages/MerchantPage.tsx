import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink, Pencil, Wand2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { MerchantEditSheet, MerchantLogo, MerchantRow, useMerchants, useRefreshMerchants } from '../components/Merchant'
import { TxRow, TxSheet } from '../components/Tx'
import { Empty, Money, PageTitle, SearchInput, Spinner, useToast } from '../components/ui'
import { api, qs } from '../lib/api'
import { fmtDate, fold, money, num } from '../lib/format'
import { useI18n } from '../lib/i18n'
import type { Merchant, MerchantBrief, Transaction } from '../lib/types'
import { Kpi, MonthlyBars, PeriodBar, WeekdayBars, usePeriod } from './Insights'

interface MerchantStats {
  merchant: MerchantBrief & { domain: string | null }
  period: { start: string; end: string; months: string[] }
  totals: {
    amount: number; count: number; avg_ticket: number | null; median_ticket: number | null; max_ticket: number | null; per_month: number
    first_date: string | null; last_date: string | null; all_amount: number; all_count: number; all_per_month: number | null
  }
  monthly: { month: string; amount: number }[]
  categories: { id: number; code: string; name: string; amount: number }[]
  weekday: { day: number; amount: number }[]
}

const PAGE = 30

export function MerchantPage() {
  const { id } = useParams()
  const mid = Number(id)
  const { t, locale } = useI18n()
  const navigate = useNavigate()
  const p = usePeriod('all')
  const [editing, setEditing] = useState(false)
  const [openTx, setOpenTx] = useState<number | null>(null)
  const m = useQuery({ queryKey: ['merchant', mid], queryFn: () => api.get<Merchant>(`/api/merchants/${mid}`) })
  const s = useQuery({ queryKey: ['insights', 'merchant', mid, p.start, p.end], queryFn: () => api.get<MerchantStats>(`/api/insights/merchants/${mid}${qs({ start: p.start, end: p.end })}`) })
  const params = { merchant_id: mid, date_from: p.start, date_to: p.end }
  const txs = useInfiniteQuery({
    queryKey: ['transactions', params],
    queryFn: ({ pageParam }) => api.get<{ items: Transaction[]; total: number }>(`/api/transactions${qs({ ...params, limit: PAGE, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last, all) => (all.length * PAGE < last.total ? all.length * PAGE : undefined),
  })
  const items = txs.data?.pages.flatMap((x) => x.items) ?? []
  const d = s.data
  const catTotal = d ? d.categories.reduce((a, c) => a + num(c.amount), 0) : 0

  if (m.isError) return <Empty>{t('not_found')}</Empty>
  return (
    <div className="space-y-4">
      <button className="btn-ghost -ml-2 px-2 text-sm" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/insights'))}>
        <ArrowLeft className="h-4 w-4" /> {t('back')}
      </button>
      <div className="flex items-center gap-4">
        <MerchantLogo m={m.data} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold tracking-tight md:text-2xl">{m.data?.name ?? '…'}</h1>
          {m.data?.domain && (
            <a href={`https://${m.data.domain}`} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:underline">
              {m.data.domain} <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
        <button className="btn-secondary" onClick={() => setEditing(true)} disabled={!m.data} aria-label={t('edit')}>
          <Pencil className="h-4 w-4" /> <span className="hidden sm:inline">{t('edit')}</span>
        </button>
      </div>

      <PeriodBar p={p} />

      {!d ? (
        <Spinner />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label={t('spent')} value={<Money value={d.totals.amount} />} sub={t('n_visits', { n: d.totals.count })} />
            <Kpi label={t('avg_ticket')} value={d.totals.avg_ticket != null ? <Money value={d.totals.avg_ticket} /> : '—'} sub={d.totals.max_ticket != null ? t('max_v', { v: money(d.totals.max_ticket) }) : undefined} />
            <Kpi label={t('per_month')} value={<Money value={d.totals.per_month} />} />
            <Kpi label={t('last_visit')} value={<span className="text-lg md:text-xl">{d.totals.last_date ? fmtDate(d.totals.last_date, locale) : '—'}</span>} sub={d.totals.first_date ? t('since_v', { v: fmtDate(d.totals.first_date, locale) }) : undefined} />
          </div>
          {d.totals.all_count > 0 && (
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {t('merchant_all_time', {
                v: money(d.totals.all_amount),
                n: d.totals.all_count,
                m: d.totals.all_per_month != null ? money(d.totals.all_per_month) : '—',
                d: d.totals.first_date ? fmtDate(d.totals.first_date, locale) : '',
              })}
            </p>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="card p-4">
              <h2 className="mb-1 text-sm font-semibold">{t('monthly_spending')}</h2>
              <p className="mb-2 text-xs text-slate-500">{t('tap_month')}</p>
              <MonthlyBars data={d.monthly.map((x) => ({ month: x.month, value: num(x.amount) }))} onPick={p.toMonth} highlight={p.mode === 'month' ? p.start?.slice(0, 7) : undefined} />
            </div>
            <div className="space-y-4">
              {d.categories.length > 0 && (
                <div className="card p-4">
                  <h2 className="mb-2 text-sm font-semibold">{t('categories')}</h2>
                  <div className="space-y-2">
                    {d.categories.map((c) => (
                      <div key={c.id}>
                        <div className="flex justify-between gap-2 text-sm">
                          <span className="truncate">{c.name}</span>
                          <span className="flex shrink-0 gap-2"><span className="text-xs text-slate-500 tnum">{Math.round((num(c.amount) / (catTotal || 1)) * 100)}%</span><Money value={c.amount} /></span>
                        </div>
                        <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
                          <div className="h-1.5 rounded-full bg-brand-600 dark:bg-brand-500" style={{ width: `${(num(c.amount) / (catTotal || 1)) * 100}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="card p-4">
                <h2 className="mb-1 text-sm font-semibold">{t('by_weekday')}</h2>
                <WeekdayBars data={d.weekday} />
              </div>
            </div>
          </div>
        </>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold">{t('transactions')} <span className="font-normal text-slate-500">· {txs.data?.pages[0]?.total ?? 0}</span></h2>
        {txs.isLoading ? (
          <Spinner />
        ) : !items.length ? (
          <Empty>{t('no_transactions')}</Empty>
        ) : (
          <div className="card divide-y divide-slate-100 overflow-hidden dark:divide-slate-800">
            {items.map((tx) => <TxRow key={tx.id} tx={tx} raw onClick={() => setOpenTx(tx.id)} />)}
          </div>
        )}
        {txs.hasNextPage && (
          <div className="mt-3 text-center">
            <button className="btn-secondary" onClick={() => txs.fetchNextPage()} disabled={txs.isFetchingNextPage}>{t('load_more')}</button>
          </div>
        )}
      </div>

      {m.data && editing && <MerchantEditSheet m={m.data} open={editing} onClose={() => setEditing(false)} onDeleted={() => navigate('/insights/merchants', { replace: true })} />}
      <TxSheet id={openTx} onClose={() => setOpenTx(null)} />
    </div>
  )
}

export function Merchants() {
  const { t } = useI18n()
  const toast = useToast()
  const navigate = useNavigate()
  const refresh = useRefreshMerchants()
  const [q, setQ] = useState('')
  const list = useMerchants()
  const f = fold(q.trim())
  const items = (list.data ?? []).filter((m) => !f || fold(m.name).includes(f) || (m.domain ?? '').includes(f))
  const max = Math.max(1, ...items.map((m) => num(m.total)))
  const detect = useMutation({
    mutationFn: () => api.post<{ updated: number }>('/api/merchants/detect', { redo: false }),
    onSuccess: (r) => {
      refresh()
      toast(t('merchant_detected', { n: r.updated }))
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <div className="space-y-3">
      <Link to="/insights" className="btn-ghost -ml-2 px-2 text-sm"><ArrowLeft className="h-4 w-4" /> {t('nav_insights')}</Link>
      <PageTitle actions={<button className="btn-secondary" onClick={() => detect.mutate()} disabled={detect.isPending} title={t('merchant_detect_help')}><Wand2 className="h-4 w-4" /> {t('merchant_detect')}</button>}>
        {t('merchants')}
      </PageTitle>
      <p className="text-sm text-slate-500">{t('merchants_help')}</p>
      <SearchInput placeholder={t('merchant_search')} value={q} onChange={setQ} />
      {list.isLoading ? (
        <Spinner />
      ) : !items.length ? (
        <Empty>{t('no_merchants')}</Empty>
      ) : (
        <div className="card divide-y divide-slate-100 px-3 dark:divide-slate-800 md:px-4">
          {items.map((m) => (
            <MerchantRow key={m.id} m={m} amount={m.total} count={m.tx_count} share={num(m.total) / max} onClick={() => navigate(`/insights/merchants/${m.id}`)} />
          ))}
        </div>
      )}
    </div>
  )
}
