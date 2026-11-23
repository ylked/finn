import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowDownRight, ArrowRight, ArrowUpRight, ChevronLeft, ChevronRight, Eye, EyeOff, List, Sparkles, Store } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { MerchantLogo, MerchantRow } from '../components/Merchant'
import { TxSheet } from '../components/Tx'
import { Money, PageTitle, Segmented, Sheet, Spinner, useToast } from '../components/ui'
import { api, qs } from '../lib/api'
import { cls, fmtDate, money, num, today } from '../lib/format'
import { useAccounts, useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { MerchantBrief } from '../lib/types'

// ------------------------------------------------------------------ period (kept in the URL)
export type Mode = 'month' | '3m' | '12m' | 'year' | 'all'

function iso(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function usePeriod(defaultMode: Mode = '12m') {
  const [sp, setSp] = useSearchParams()
  const mode = (sp.get('p') as Mode) || defaultMode
  const offset = Number(sp.get('o') || 0)
  const set = (m: Mode, o = 0) => {
    const n = new URLSearchParams(sp)
    n.set('p', m)
    if (o) n.set('o', String(o))
    else n.delete('o')
    setSp(n, { replace: true })
  }
  const now = new Date()
  let start: string | undefined
  let end = today()
  if (mode === 'month') {
    start = iso(new Date(now.getFullYear(), now.getMonth() + offset, 1))
    end = offset ? iso(new Date(now.getFullYear(), now.getMonth() + offset + 1, 0)) : today()
  } else if (mode === '3m') start = iso(new Date(now.getFullYear(), now.getMonth() - 2, 1))
  else if (mode === '12m') start = iso(new Date(now.getFullYear(), now.getMonth() - 11, 1))
  else if (mode === 'year') {
    start = `${now.getFullYear() + offset}-01-01`
    end = offset ? `${now.getFullYear() + offset}-12-31` : today()
  }
  /** Jump to one month, e.g. after clicking a bar. */
  const toMonth = (ym: string) => {
    const [y, m] = ym.split('-').map(Number)
    set('month', (y - now.getFullYear()) * 12 + (m - 1 - now.getMonth()))
  }
  return { mode, offset, start, end, set, toMonth, search: sp.toString() }
}

export function PeriodBar({ p }: { p: ReturnType<typeof usePeriod> }) {
  const { t, locale } = useI18n()
  const label =
    p.mode === 'month' && p.start
      ? new Intl.DateTimeFormat(locale === 'fr' ? 'fr-CH' : 'en-GB', { month: 'long', year: 'numeric' }).format(new Date(p.start + 'T00:00'))
      : p.mode === 'year' && p.start
        ? p.start.slice(0, 4)
        : null
  return (
    <div className="flex flex-col gap-2 md:flex-row md:items-center">
      <div className="overflow-x-auto">
        <Segmented
          value={p.mode}
          onChange={(m) => p.set(m)}
          options={[
            { value: 'month', label: t('period_month') },
            { value: '3m', label: t('period_3m') },
            { value: '12m', label: t('period_12m') },
            { value: 'year', label: t('period_year') },
            { value: 'all', label: t('period_all') },
          ]}
        />
      </div>
      {label && (
        <div className="flex items-center justify-between gap-1 md:justify-start">
          <button className="btn-ghost p-2" aria-label={t('previous')} onClick={() => p.set(p.mode, p.offset - 1)}><ChevronLeft className="h-4 w-4" /></button>
          <span className="min-w-[9rem] text-center text-sm font-medium capitalize">{label}</span>
          <button className="btn-ghost p-2" aria-label={t('next')} disabled={p.offset >= 0} onClick={() => p.set(p.mode, p.offset + 1)}><ChevronRight className="h-4 w-4" /></button>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ chart helpers
function useDark() {
  const mq = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null
  const [dark, setDark] = useState(!!mq?.matches)
  useEffect(() => {
    if (!mq) return
    const on = () => setDark(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [mq])
  return dark
}

/** Chart tokens: one brand hue for single-series bars, recessive grid and axis ink. */
export function useChartColors() {
  const dark = useDark()
  return dark
    ? { bar: '#14b8a6', barMuted: '#134e4a', grid: '#1e293b', axis: '#94a3b8', cursor: '#ffffff10' }
    : { bar: '#0d9488', barMuted: '#99f6e4', grid: '#e2e8f0', axis: '#64748b', cursor: '#0f172a0a' }
}

export function compact(n: number) {
  const a = Math.abs(n)
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (a >= 10_000) return `${Math.round(n / 1000)}k`
  if (a >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(Math.round(n))
}

/** Round axis ticks: 0, step, 2·step… with step in 1/2/2.5/5 × 10^k. */
export function niceTicks(max: number, n = 4): number[] {
  if (!(max > 0)) return [0]
  const raw = max / n
  const pow = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((x) => x >= raw) ?? raw
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step)
}

export function monthLabel(ym: string, locale: string, withYear = true) {
  const d = new Date(ym + '-01T00:00')
  const m = new Intl.DateTimeFormat(locale === 'fr' ? 'fr-CH' : 'en-GB', { month: 'short' }).format(d).replace('.', '')
  return withYear ? `${m} ${ym.slice(2, 4)}` : m
}

function ChartTip({ active, payload, label, fmtLabel }: { active?: boolean; payload?: { value: number }[]; label?: string; fmtLabel: (l: string) => string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl bg-white px-3 py-2 text-xs shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
      <div className="text-slate-500">{fmtLabel(String(label))}</div>
      <div className="text-sm font-semibold tnum">{money(payload[0].value)}</div>
    </div>
  )
}

/** Single-series monthly columns; a click on a column calls onPick(month). */
export function MonthlyBars({ data, onPick, highlight }: { data: { month: string; value: number }[]; onPick?: (m: string) => void; highlight?: string }) {
  const { locale } = useI18n()
  const c = useChartColors()
  const long = data.length > 14
  const ticks = niceTicks(Math.max(0, ...data.map((d) => d.value)))
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: -12, right: 4, top: 8 }}>
          <CartesianGrid vertical={false} stroke={c.grid} />
          <XAxis dataKey="month" tickFormatter={(m) => monthLabel(m, locale, !long || m.endsWith('-01'))} fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} interval="preserveStartEnd" minTickGap={8} />
          <YAxis tickFormatter={compact} fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} width={48} ticks={ticks} domain={[0, ticks[ticks.length - 1]]} />
          <Tooltip cursor={{ fill: c.cursor }} content={<ChartTip fmtLabel={(m) => monthLabel(m, locale)} />} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={24} onClick={(d) => { const m = (d as unknown as { payload?: { month?: string } }).payload?.month; if (m) onPick?.(m) }} className={onPick ? 'cursor-pointer' : undefined}>
            {data.map((d) => (
              <Cell key={d.month} fill={!highlight || highlight === d.month ? c.bar : c.barMuted} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function WeekdayBars({ data }: { data: { day: number; amount: number | string }[] }) {
  const { t, locale } = useI18n()
  const c = useChartColors()
  const names = useMemo(() => {
    const f = new Intl.DateTimeFormat(locale === 'fr' ? 'fr-CH' : 'en-GB', { weekday: 'short' })
    // 2024-01-01 is a Monday
    return Array.from({ length: 7 }, (_, i) => f.format(new Date(2024, 0, 1 + i)).replace('.', ''))
  }, [locale])
  const rows = data.map((d) => ({ day: names[d.day], value: num(d.amount) }))
  const ticks = niceTicks(Math.max(0, ...rows.map((r) => r.value)))
  const top = rows.reduce((a, b) => (b.value > a.value ? b : a), rows[0])
  return (
    <div>
      {top && top.value > 0 && <p className="mb-2 text-sm text-slate-500">{t('weekday_top', { day: top.day })}</p>}
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ left: -12, right: 4, top: 4 }}>
            <CartesianGrid vertical={false} stroke={c.grid} />
            <XAxis dataKey="day" fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} />
            <YAxis tickFormatter={compact} fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} width={48} ticks={ticks} domain={[0, ticks[ticks.length - 1]]} />
            <Tooltip cursor={{ fill: c.cursor }} content={<ChartTip fmtLabel={(d) => d} />} />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={24} fill={c.bar} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export function Kpi({ label, value, sub, className = '' }: { label: string; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cls('card p-4', className)}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tracking-tight md:text-2xl">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

/** Change vs previous period. For spending, up is bad: amber arrow; down is good: green arrow. */
function Delta({ now, prev }: { now: number; prev: number }) {
  const { t } = useI18n()
  if (!prev) return null
  const d = (now - prev) / Math.abs(prev)
  if (Math.abs(d) < 0.005) return <span>= {t('vs_prev')}</span>
  const up = d > 0
  const Icon = up ? ArrowUpRight : ArrowDownRight
  return (
    <span className="inline-flex items-center gap-0.5">
      <Icon className={cls('h-3.5 w-3.5', up ? 'text-amber-600' : 'text-emerald-600')} />
      <span className="tnum">{up ? '+' : '−'}{Math.round(Math.abs(d) * 100)}%</span>&nbsp;{t('vs_prev')}
    </span>
  )
}

// ------------------------------------------------------------------ page
interface Cat { id: number; code: string; name: string; amount: number; prev: number; count: number; months: Record<string, number>; group_id?: number }
interface Overview {
  period: { start: string; end: string; days: number; months: string[] }
  totals: { expenses: number; income: number; net: number; savings_rate: number | null; prev_expenses: number; per_month: number; per_day: number; count: number; unclassified: number; with_merchant: number }
  monthly: { month: string; expenses: number; income: number }[]
  groups: Cat[]
  categories: Cat[]
  merchants: (MerchantBrief & { amount: number; count: number })[]
  weekday: { day: number; amount: number; count: number }[]
  largest: { id: number; date: string; label: string; amount: number; merchant: MerchantBrief | null; account: { id: number; code: string; name: string } }[]
  hidden: { count: number; amount: number; accounts: { id: number; code: string; name: string }[] }
}

export function Insights() {
  const { t, locale } = useI18n()
  const p = usePeriod('12m')
  const navigate = useNavigate()
  const [level, setLevel] = useState<'groups' | 'categories'>('groups')
  const [selected, setSelected] = useState<number | null>(null)
  const [allMerchants, setAllMerchants] = useState(false)
  const [openTx, setOpenTx] = useState<number | null>(null)
  const [hiding, setHiding] = useState<Largest | null>(null)
  const [showHidden, setShowHidden] = useState(false)
  const q = useQuery({ queryKey: ['insights', p.start, p.end], queryFn: () => api.get<Overview>(`/api/insights${qs({ start: p.start, end: p.end })}`) })
  const d = q.data
  useEffect(() => setSelected(null), [level])

  const rows = d ? (level === 'groups' ? d.groups : d.categories).filter((c) => num(c.amount) > 0) : []
  const sel = rows.find((r) => r.id === selected) ?? null
  const max = Math.max(1, ...rows.map((r) => num(r.amount)))
  const total = d ? num(d.totals.expenses) : 0
  const series = d ? d.period.months.map((m) => ({ month: m, value: sel ? num(sel.months[m]) : num(d.monthly.find((x) => x.month === m)?.expenses) })) : []
  const merchants = d?.merchants ?? []
  const mMax = Math.max(1, ...merchants.map((m) => num(m.amount)))
  const periodQs = qs({ p: p.mode, o: p.offset || undefined })

  return (
    <div className="space-y-4">
      <PageTitle actions={<Link to="/insights/merchants" className="btn-secondary"><Store className="h-4 w-4" /> {t('merchants')}</Link>}>
        {t('nav_insights')}
      </PageTitle>
      <PeriodBar p={p} />

      {!d ? (
        <Spinner />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label={t('kpi_spent')} value={<Money value={d.totals.expenses} />} sub={num(d.totals.prev_expenses) ? <Delta now={num(d.totals.expenses)} prev={num(d.totals.prev_expenses)} /> : t('n_purchases', { n: d.totals.count })} />
            <Kpi label={t('kpi_income')} value={<Money value={d.totals.income} />} sub={num(d.totals.prev_expenses) ? t('n_purchases', { n: d.totals.count }) : undefined} />
            <Kpi
              label={t('savings')}
              value={<Money value={d.totals.net} colored />}
              sub={d.totals.savings_rate != null ? t('savings_rate', { p: Math.round(d.totals.savings_rate * 100) }) : undefined}
            />
            <Kpi
              label={p.mode === 'month' ? t('per_day') : t('per_month')}
              value={<Money value={p.mode === 'month' ? d.totals.per_day : d.totals.per_month} />}
              sub={p.mode === 'month' ? undefined : t('per_day_v', { v: money(d.totals.per_day) })}
            />
          </div>

          {num(d.totals.unclassified) > 0 && (
            <Link to="/review" className="card flex items-center gap-3 bg-amber-50 p-3 text-sm ring-amber-200 dark:bg-amber-950/40 dark:ring-amber-900">
              <Sparkles className="h-4 w-4 shrink-0 text-amber-600" />
              <span className="flex-1">{t('insights_unclassified', { v: money(d.totals.unclassified) })}</span>
              <ArrowRight className="h-4 w-4" />
            </Link>
          )}

          <BalanceCard start={p.start} end={p.end} forecastable={p.offset === 0} />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* where the money goes */}
            <div className="card p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">{t('where_money_goes')}</h2>
                <div className="w-full sm:w-auto">
                  <Segmented value={level} onChange={setLevel} options={[{ value: 'groups', label: t('level_groups') }, { value: 'categories', label: t('level_categories') }]} />
                </div>
              </div>
              {!rows.length && <p className="py-6 text-center text-sm text-slate-400">{t('no_spending')}</p>}
              <div className="space-y-1">
                {rows.map((r) => {
                  const share = total ? num(r.amount) / total : 0
                  const active = r.id === selected
                  return (
                    <div key={r.id} className={cls('rounded-xl px-2 py-1.5 transition', active ? 'bg-brand-50 ring-1 ring-brand-200 dark:bg-brand-900/30 dark:ring-brand-800' : 'hover:bg-slate-50 dark:hover:bg-slate-800/50')}>
                    <button className="block w-full text-left" onClick={() => setSelected(active ? null : r.id)} aria-pressed={active}>
                      <div className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="min-w-0 truncate">{r.name}</span>
                        <span className="flex shrink-0 items-baseline gap-2">
                          <span className="text-xs text-slate-500 tnum">{Math.round(share * 100)}%</span>
                          <Money value={r.amount} className="font-medium" />
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
                        <div className="h-1.5 rounded-full bg-brand-600 dark:bg-brand-500" style={{ width: `${Math.max(1, (num(r.amount) / max) * 100)}%` }} />
                      </div>
                    </button>
                      {active && (
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                          <span>{t('n_entries', { n: r.count })} · <Delta now={num(r.amount)} prev={num(r.prev)} /></span>
                          <Link
                            to={`/transactions${qs({ account_id: r.id, date_from: d.period.start, date_to: d.period.end })}`}
                            className="inline-flex items-center gap-1 font-medium text-brand-700 dark:text-brand-200"
                          >
                            <List className="h-3.5 w-3.5" /> {t('see_entries')}
                          </Link>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="space-y-4">
              {/* monthly */}
              <div className="card p-4">
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <h2 className="truncate text-sm font-semibold">{sel ? t('monthly_of', { name: sel.name }) : t('monthly_spending')}</h2>
                  {sel && <button className="text-xs text-slate-500 underline" onClick={() => setSelected(null)}>{t('show_all')}</button>}
                </div>
                <p className="mb-2 text-xs text-slate-500">{t('tap_month')}</p>
                <MonthlyBars data={series} onPick={p.toMonth} highlight={p.mode === 'month' ? p.start?.slice(0, 7) : undefined} />
              </div>

              {/* weekday */}
              <div className="card p-4">
                <h2 className="mb-1 text-sm font-semibold">{t('by_weekday')}</h2>
                <WeekdayBars data={d.weekday} />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* merchants */}
            <div className="card p-4">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold">{t('top_merchants')}</h2>
                <Link to="/insights/merchants" className="text-xs text-slate-500 underline">{t('see_all')}</Link>
              </div>
              {!merchants.length && <p className="py-6 text-center text-sm text-slate-400">{t('no_merchants')}</p>}
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {merchants.slice(0, allMerchants ? 25 : 8).map((m) => (
                  <MerchantRow key={m.id} m={m} amount={m.amount} count={m.count} share={num(m.amount) / mMax} onClick={() => navigate(`/insights/merchants/${m.id}${periodQs}`)} />
                ))}
              </div>
              {merchants.length > 8 && !allMerchants && (
                <button className="btn-ghost mt-2 w-full text-sm" onClick={() => setAllMerchants(true)}>{t('show_more')}</button>
              )}
              {total > 0 && (
                <p className="mt-2 text-xs text-slate-500">{t('merchant_coverage', { p: Math.min(100, Math.round((num(d.totals.with_merchant) / total) * 100)) })}</p>
              )}
            </div>

            {/* largest */}
            <div className="card p-4">
              <h2 className="mb-2 text-sm font-semibold">{t('largest_expenses')}</h2>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {d.largest.map((x) => (
                  <div key={x.id} className="flex items-center gap-1">
                    <button className="flex min-w-0 flex-1 items-center gap-3 py-2 text-left" onClick={() => setOpenTx(x.id)}>
                      <MerchantLogo m={x.merchant} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{x.merchant?.name || x.label}</div>
                        <div className="truncate text-xs text-slate-500">{fmtDate(x.date, locale)} · {x.account.name}</div>
                      </div>
                      <Money value={x.amount} className="text-sm font-medium" />
                    </button>
                    <button className="btn-ghost shrink-0 p-2 text-slate-400" title={t('stats_hide')} aria-label={t('stats_hide')} onClick={() => setHiding(x)}>
                      <EyeOff className="h-4 w-4" />
                    </button>
                  </div>
                ))}
                {!d.largest.length && <p className="py-6 text-center text-sm text-slate-400">{t('no_spending')}</p>}
              </div>
              {(d.hidden.count > 0 || d.hidden.accounts.length > 0) && (
                <button className="mt-2 flex w-full items-center gap-2 text-left text-xs text-slate-500 hover:underline" onClick={() => setShowHidden(true)}>
                  <EyeOff className="h-3.5 w-3.5 shrink-0" />
                  {t('hidden_summary', { n: d.hidden.count, v: money(d.hidden.amount) })}
                </button>
              )}
            </div>
          </div>
        </>
      )}
      <TxSheet id={openTx} onClose={() => setOpenTx(null)} />
      {hiding && <HideSheet x={hiding} onClose={() => setHiding(null)} />}
      {showHidden && d && <HiddenSheet hidden={d.hidden} start={d.period.start} end={d.period.end} onClose={() => setShowHidden(false)} />}
    </div>
  )
}

type Largest = Overview['largest'][number]

function HideSheet({ x, onClose }: { x: Largest; onClose: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const hide = useMutation({
    mutationFn: (what: 'tx' | 'account') =>
      what === 'tx'
        ? api.post('/api/transactions/stats-hidden', { ids: [x.id], hidden: true })
        : api.post('/api/insights/hidden-accounts', { account_id: x.account.id, hidden: true }),
    onSuccess: () => { toast(t('stats_hidden_on')); refresh(); onClose() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <Sheet open onClose={onClose} title={t('stats_hide')}>
      <div className="space-y-3">
        <p className="text-sm text-slate-500">{t('stats_hidden_help')}</p>
        <button className="btn-secondary w-full justify-start" onClick={() => hide.mutate('tx')}>
          <EyeOff className="h-4 w-4" /> {t('hide_this_entry', { name: x.merchant?.name || x.label })}
        </button>
        <button className="btn-secondary w-full justify-start" onClick={() => hide.mutate('account')}>
          <EyeOff className="h-4 w-4" /> {t('hide_category', { name: x.account.name })}
        </button>
      </div>
    </Sheet>
  )
}

function HiddenSheet({ hidden, start, end, onClose }: { hidden: Overview['hidden']; start: string; end: string; onClose: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const txs = useQuery({
    queryKey: ['transactions', 'hidden', start, end],
    queryFn: () => api.get<{ items: import('../lib/types').Transaction[] }>(`/api/transactions${qs({ stats_hidden: true, date_from: start, date_to: end, limit: 100 })}`),
  })
  const show = useMutation({
    mutationFn: (a: { tx: number } | { account: number }) =>
      'tx' in a ? api.post('/api/transactions/stats-hidden', { ids: [a.tx], hidden: false }) : api.post('/api/insights/hidden-accounts', { account_id: a.account, hidden: false }),
    onSuccess: () => { refresh(); txs.refetch() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <Sheet open onClose={onClose} title={t('hidden_title')}>
      <div className="space-y-4">
        <p className="text-sm text-slate-500">{t('stats_hidden_help')}</p>
        {hidden.accounts.length > 0 && (
          <div>
            <div className="label">{t('hidden_categories')}</div>
            <div className="divide-y divide-slate-100 rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
              {hidden.accounts.map((a) => (
                <div key={a.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="truncate">{a.code} · {a.name}</span>
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => show.mutate({ account: a.id })}><Eye className="h-3.5 w-3.5" /> {t('stats_show')}</button>
                </div>
              ))}
            </div>
          </div>
        )}
        {!!txs.data?.items.length && (
          <div>
            <div className="label">{t('hidden_entries')}</div>
            <div className="divide-y divide-slate-100 rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
              {txs.data.items.map((tx) => (
                <div key={tx.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">{tx.merchant?.name || tx.counterparty || tx.description}</span>
                  <button className="btn-ghost shrink-0 px-2 py-1 text-xs" onClick={() => show.mutate({ tx: tx.id })}><Eye className="h-3.5 w-3.5" /> {t('stats_show')}</button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Sheet>
  )
}

// ------------------------------------------------------------------ balance over time
interface BalanceData {
  start: string
  end: string
  opening: number
  closing: number
  change: number
  min: number | null
  max: number | null
  points: { date: string; balance: number }[]
  forecast: { monthly_trend: number; months_used: number; open_invoices: number; points: { date: string; balance: number }[] } | null
}

const ts = (d: string) => new Date(d + 'T00:00').getTime()

export function BalanceCard({ start, end, forecastable = true, compactView = false }: { start?: string; end?: string; forecastable?: boolean; compactView?: boolean }) {
  const { t, locale } = useI18n()
  const c = useChartColors()
  const { accounts } = useAccounts()
  const [scope, setScope] = useState('liquid')
  const [showForecast, setShowForecast] = useState(true)
  const q = useQuery({
    queryKey: ['insights', 'balance', scope, start, end],
    queryFn: () => api.get<BalanceData>(`/api/insights/balance${qs({ scope, start, end })}`),
  })
  const b = q.data
  const fc = forecastable && showForecast ? b?.forecast : null
  const data = useMemo(() => {
    if (!b) return []
    const rows: { x: number; balance?: number; forecast?: number }[] = b.points.map((p) => ({ x: ts(p.date), balance: num(p.balance) }))
    for (const p of fc?.points ?? []) {
      const x = ts(p.date)
      const same = rows.find((r) => r.x === x)
      if (same) same.forecast = num(p.balance)
      else rows.push({ x, forecast: num(p.balance) })
    }
    return rows.sort((a, b2) => a.x - b2.x)
  }, [b, fc])
  const dfmt = (x: number, withDay = true) => {
    const d = new Date(x)
    return new Intl.DateTimeFormat(locale === 'fr' ? 'fr-CH' : 'en-GB', withDay ? { day: 'numeric', month: 'short', year: '2-digit' } : { month: 'short', year: '2-digit' }).format(d)
  }
  const money_accounts = accounts.filter((a) => !a.is_group && ['bank', 'cash', 'credit_card'].includes(a.kind) && a.is_active)
  const last = fc?.points[fc.points.length - 1]
  const has = data.length > 0
  const values = data.flatMap((r) => [r.balance, r.forecast]).filter((v): v is number => v != null)
  const lo = Math.min(0, ...values)
  const hi = Math.max(0, ...values)

  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{t('balance_evolution')}</h2>
        {!compactView && (
          <div className="flex flex-wrap items-center gap-2">
            <select className="input w-auto py-1.5 text-sm" value={scope} onChange={(e) => setScope(e.target.value)} aria-label={t('balance_scope')}>
              <option value="liquid">{t('scope_liquid')}</option>
              <option value="net_worth">{t('net_worth')}</option>
              {money_accounts.map((a) => <option key={a.id} value={String(a.id)}>{a.name}</option>)}
            </select>
            {forecastable && b?.forecast && (
              <label className="flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300">
                <input type="checkbox" className="h-4 w-4 accent-brand-700" checked={showForecast} onChange={(e) => setShowForecast(e.target.checked)} /> {t('forecast')}
              </label>
            )}
          </div>
        )}
      </div>
      {!b ? <Spinner /> : (
        <>
          <div className="mb-3 grid grid-cols-3 gap-2">
            <div>
              <div className="text-xs text-slate-500">{t('balance_now')}</div>
              <Money value={b.closing} className="text-lg font-semibold" />
            </div>
            <div>
              <div className="text-xs text-slate-500">{t('balance_change')}</div>
              <Money value={b.change} sign colored className="text-lg font-semibold" />
            </div>
            {fc && last ? (
              <div>
                <div className="text-xs text-slate-500">{t('forecast_on', { d: dfmt(ts(last.date), false) })}</div>
                <Money value={last.balance} className="text-lg font-semibold text-slate-500 dark:text-slate-400" />
              </div>
            ) : <div />}
          </div>
          {has && (
            <div className={compactView ? 'h-44' : 'h-64'}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={data} margin={{ left: -6, right: 8, top: 8 }}>
                  <defs>
                    <linearGradient id="balFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={c.bar} stopOpacity={0.25} />
                      <stop offset="100%" stopColor={c.bar} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke={c.grid} />
                  <XAxis dataKey="x" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(x) => dfmt(x, false)} fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} minTickGap={24} />
                  <YAxis tickFormatter={compact} fontSize={11} tickLine={false} axisLine={false} stroke={c.axis} width={52} domain={[lo < 0 ? 'auto' : 0, hi > 0 ? 'auto' : 0]} />
                  {lo < 0 && <ReferenceLine y={0} stroke={c.axis} strokeDasharray="2 2" />}
                  <Tooltip
                    cursor={{ stroke: c.axis, strokeDasharray: '3 3' }}
                    content={({ active, payload, label }) => {
                      if (!active || !payload?.length) return null
                      const p = payload.find((x) => x.value != null)
                      if (!p) return null
                      const isF = p.dataKey === 'forecast' && !payload.some((x) => x.dataKey === 'balance' && x.value != null)
                      return (
                        <div className="rounded-xl bg-white px-3 py-2 text-xs shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
                          <div className="text-slate-500">{dfmt(Number(label))}{isF && ` · ${t('forecast')}`}</div>
                          <div className="text-sm font-semibold tnum">{money(Number(p.value))}</div>
                        </div>
                      )
                    }}
                  />
                  <Area type="monotone" dataKey="balance" stroke={c.bar} strokeWidth={2} fill="url(#balFill)" dot={false} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />
                  {fc && <Line type="monotone" dataKey="forecast" stroke={c.bar} strokeWidth={2} strokeDasharray="5 4" strokeOpacity={0.7} dot={false} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
          {fc && (
            <p className="mt-2 text-xs text-slate-500">
              {t('forecast_note', { v: money(fc.monthly_trend, { sign: true }), n: fc.months_used })}
              {num(fc.open_invoices) > 0 && ' ' + t('forecast_invoices', { v: money(fc.open_invoices) })}
            </p>
          )}
        </>
      )}
    </div>
  )
}
