import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, CreditCard, FileText, Landmark, ReceiptText, Sparkles, Wallet } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { dueText } from '../components/Invoice'
import { MerchantLogo, MerchantRow } from '../components/Merchant'
import { Money, PageTitle, Spinner } from '../components/ui'
import { api, qs } from '../lib/api'
import { cls, money, num, today } from '../lib/format'
import { useI18n } from '../lib/i18n'
import type { InvoiceSummary, MerchantBrief } from '../lib/types'
import { BalanceCard, Kpi } from './Insights'

interface Dash {
  accounts: { id: number; code: string; name: string; kind: string; type: string; balance: string }[]
  people: { id: number; name: string; balance: string }[]
  to_review: number
  unclassified: number
  net_worth: string
  liquid: string
  invoices: InvoiceSummary
  receipts: Record<string, number>
  ai_pending: number
}

interface MonthInsights {
  totals: { expenses: number; income: number; net: number; prev_expenses: number; per_day: number; count: number }
  groups: { id: number; name: string; amount: number }[]
  merchants: (MerchantBrief & { amount: number; count: number })[]
}

const icons: Record<string, typeof Wallet> = { bank: Landmark, credit_card: CreditCard, cash: Wallet }

function monthsAgo(n: number) {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function Todo({ to, icon: Icon, tone = 'amber', children }: { to: string; icon: typeof Wallet; tone?: 'amber' | 'red' | 'violet' | 'slate'; children: ReactNode }) {
  const tones = {
    amber: 'text-amber-600 dark:text-amber-400',
    red: 'text-red-600 dark:text-red-400',
    violet: 'text-violet-600 dark:text-violet-400',
    slate: 'text-slate-500',
  }
  return (
    <Link to={to} className="flex items-center gap-3 px-4 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50">
      <Icon className={cls('h-4 w-4 shrink-0', tones[tone])} />
      <span className="min-w-0 flex-1">{children}</span>
      <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />
    </Link>
  )
}

export function Dashboard() {
  const { t, locale } = useI18n()
  const navigate = useNavigate()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<Dash>('/api/reports/dashboard') })
  const monthStart = today().slice(0, 8) + '01'
  const m = useQuery({ queryKey: ['insights', monthStart, today()], queryFn: () => api.get<MonthInsights>(`/api/insights${qs({ start: monthStart, end: today() })}`) })
  if (!q.data) return <Spinner />
  const d = q.data
  const inv = d.invoices
  const rc = d.receipts
  const receiptsTodo = (rc.unmatched ?? 0) + (rc.manual ?? 0) + (rc.failed ?? 0)
  const todos: ReactNode[] = []
  if (inv.overdue_count) todos.push(<Todo key="over" to="/invoices?tab=overdue" icon={AlertTriangle} tone="red"><b>{t('home_overdue', { n: inv.overdue_count })}</b> · <Money value={inv.overdue_total} /></Todo>)
  if (d.to_review || d.unclassified) todos.push(
    <Todo key="rev" to="/review" icon={Sparkles}>
      {d.to_review > 0 && <b>{t('to_review_n', { n: d.to_review })}</b>}
      {d.to_review > 0 && d.unclassified > 0 && ' · '}
      {d.unclassified > 0 && t('unclassified_n', { n: d.unclassified })}
    </Todo>,
  )
  if (inv.due_soon_count) todos.push(<Todo key="soon" to="/invoices" icon={FileText}>{t('home_due_soon', { n: inv.due_soon_count })} · <Money value={inv.due_soon_total} /></Todo>)
  if (inv.drafts) todos.push(<Todo key="drafts" to="/invoices?tab=draft" icon={FileText} tone="slate">{t('home_inv_drafts', { n: inv.drafts })}</Todo>)
  if (receiptsTodo) todos.push(<Todo key="rec" to="/receipts?status=todo" icon={ReceiptText} tone="slate">{t('home_receipts', { n: receiptsTodo })}</Todo>)
  if (d.ai_pending || rc.pending) todos.push(<Todo key="ai" to="/review" icon={Bot} tone="violet">{t('home_ai', { n: d.ai_pending + (rc.pending ?? 0) })}</Todo>)

  const mi = m.data
  const groups = (mi?.groups ?? []).filter((g) => num(g.amount) > 0).slice(0, 5)
  const gMax = Math.max(1, ...groups.map((g) => num(g.amount)))
  const merchants = (mi?.merchants ?? []).slice(0, 5)
  const mMax = Math.max(1, ...merchants.map((x) => num(x.amount)))
  const monthName = new Intl.DateTimeFormat(locale === 'fr' ? 'fr-CH' : 'en-GB', { month: 'long' }).format(new Date())

  return (
    <div className="space-y-4">
      <PageTitle>{t('nav_dashboard')}</PageTitle>

      {/* what needs attention */}
      {todos.length ? (
        <div className="card divide-y divide-slate-100 overflow-hidden dark:divide-slate-800">{todos}</div>
      ) : (
        <div className="card flex items-center gap-3 p-4 text-sm text-slate-500">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" /> {t('home_all_good')}
        </div>
      )}

      {/* key numbers */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label={t('scope_liquid')} value={<Money value={d.liquid} />} />
        <Kpi label={t('net_worth')} value={<Money value={d.net_worth} />} />
        <Kpi
          label={t('home_spent_month', { m: monthName })}
          value={mi ? <Money value={mi.totals.expenses} /> : '…'}
          sub={mi && num(mi.totals.prev_expenses) > 0 ? t('home_vs_prev', { v: money(mi.totals.prev_expenses) }) : undefined}
        />
        <Kpi
          label={t('inv_to_pay')}
          value={<Money value={inv.open_total} />}
          sub={inv.open_count ? t('n_invoices', { n: inv.open_count }) : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* balance + forecast */}
        <BalanceCard start={monthsAgo(6)} compactView />

        {/* upcoming invoices */}
        <div className="card p-4">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{t('home_upcoming')}</h2>
            <Link to="/invoices" className="text-xs text-slate-500 underline">{t('see_all')}</Link>
          </div>
          {!inv.upcoming.length ? (
            <p className="py-6 text-center text-sm text-slate-400">{t('inv_none_open')}</p>
          ) : (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {inv.upcoming.map((i) => (
                <Link key={i.id} to={`/invoices?open=${i.id}`} className="flex items-center gap-3 py-2.5">
                  {i.merchant ? <MerchantLogo m={i.merchant} /> : <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800"><FileText className="h-4 w-4" /></span>}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{i.issuer || t('invoice')}</div>
                    <div className={cls('text-xs', i.overdue ? 'font-medium text-red-600 dark:text-red-400' : 'text-slate-500')}>{dueText(t, locale, { status: 'open', due_date: i.due_date })}</div>
                  </div>
                  <Money value={i.remaining} className="text-sm font-medium" />
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* this month by category */}
        <div className="card p-4">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{t('home_month_where', { m: monthName })}</h2>
            <Link to="/insights?p=month" className="text-xs text-slate-500 underline">{t('nav_insights')}</Link>
          </div>
          {!mi ? <Spinner /> : !groups.length ? <p className="py-6 text-center text-sm text-slate-400">{t('no_spending')}</p> : (
            <div className="space-y-2.5">
              {groups.map((g) => (
                <div key={g.id}>
                  <div className="flex justify-between gap-2 text-sm"><span className="truncate">{g.name}</span><Money value={g.amount} /></div>
                  <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-1.5 rounded-full bg-brand-600 dark:bg-brand-500" style={{ width: `${(num(g.amount) / gMax) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* this month by merchant */}
        <div className="card p-4">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{t('top_merchants')}</h2>
            <Link to="/insights/merchants" className="text-xs text-slate-500 underline">{t('see_all')}</Link>
          </div>
          {!mi ? <Spinner /> : !merchants.length ? <p className="py-6 text-center text-sm text-slate-400">{t('no_merchants')}</p> : (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {merchants.map((x) => (
                <MerchantRow key={x.id} m={x} amount={x.amount} count={x.count} share={num(x.amount) / mMax} onClick={() => navigate(`/insights/merchants/${x.id}?p=month`)} />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <div className="mb-2 text-sm font-semibold">{t('money_accounts')}</div>
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {d.accounts.map((a) => {
              const Icon = icons[a.kind] ?? Wallet
              return (
                <Link key={a.id} to={`/transactions?account_id=${a.id}`} className="flex items-center gap-3 py-2.5">
                  <Icon className="h-4 w-4 text-slate-400" />
                  <span className="flex-1 truncate text-sm">{a.name}</span>
                  <Money value={a.kind === 'credit_card' ? -num(a.balance) : a.balance} colored={a.kind === 'credit_card'} className="text-sm font-medium" />
                </Link>
              )
            })}
          </div>
        </div>

        {d.people.length > 0 && (
          <div className="card p-4">
            <div className="mb-2 text-sm font-semibold">{t('people_balances')}</div>
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {d.people.map((p) => (
                <Link key={p.id} to={`/transactions?account_id=${p.id}`} className="flex items-center justify-between py-2 text-sm">
                  <span>{p.name}</span>
                  <span className={num(p.balance) > 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600'}>
                    {num(p.balance) > 0 ? t('owes_you') : t('you_owe')} <b className="tnum">{money(Math.abs(num(p.balance)))}</b>
                  </span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
