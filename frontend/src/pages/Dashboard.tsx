import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CreditCard, Landmark, Sparkles, Wallet } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/api'
import { money, num } from '../lib/format'
import { useI18n } from '../lib/i18n'
import { Money, PageTitle, Spinner } from '../components/ui'

interface Dash {
  accounts: { id: number; code: string; name: string; kind: string; type: string; balance: string }[]
  people: { id: number; name: string; balance: string }[]
  month: { income: string; expenses: string; result: string; top_expenses: { id: number; code: string; name: string; amount: string }[] }
  series: { month: string; income: string; expenses: string }[]
  to_review: number
  unclassified: number
  net_worth: string
}

const icons: Record<string, typeof Wallet> = { bank: Landmark, credit_card: CreditCard, cash: Wallet }

export function Dashboard() {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<Dash>('/api/reports/dashboard') })
  if (!q.data) return <Spinner />
  const d = q.data
  const maxTop = Math.max(1, ...d.month.top_expenses.map((x) => num(x.amount)))
  const series = d.series.map((s) => ({ month: s.month.slice(5) + '/' + s.month.slice(2, 4), [t('income')]: num(s.income), [t('expenses')]: num(s.expenses) }))

  return (
    <div className="space-y-4">
      <PageTitle>{t('nav_dashboard')}</PageTitle>

      {(d.to_review > 0 || d.unclassified > 0) ? (
        <Link to="/review" className="card flex items-center gap-3 bg-amber-50 p-4 ring-amber-200 dark:bg-amber-950/40 dark:ring-amber-900">
          <Sparkles className="h-5 w-5 text-amber-600" />
          <div className="flex-1 text-sm">
            <div className="font-medium">{t('to_review_n', { n: d.to_review })}</div>
            {d.unclassified > 0 && <div className="text-amber-700 dark:text-amber-300">{t('unclassified_n', { n: d.unclassified })}</div>}
          </div>
          <ArrowRight className="h-4 w-4" />
        </Link>
      ) : (
        <div className="card p-4 text-sm text-slate-500">{t('nothing_to_review')}</div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <div className="card p-4">
          <div className="text-xs text-slate-500">{t('net_worth')}</div>
          <Money value={d.net_worth} className="text-2xl font-semibold" />
        </div>
        <div className="card p-4 md:col-span-2">
          <div className="mb-1 text-xs text-slate-500">{t('this_month')}</div>
          <div className="grid grid-cols-3 gap-2">
            <div><div className="text-xs text-slate-500">{t('income')}</div><Money value={d.month.income} className="font-semibold text-emerald-700 dark:text-emerald-400" /></div>
            <div><div className="text-xs text-slate-500">{t('expenses')}</div><Money value={d.month.expenses} className="font-semibold text-red-600 dark:text-red-400" /></div>
            <div><div className="text-xs text-slate-500">{t('result')}</div><Money value={d.month.result} colored className="font-semibold" /></div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
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
        <div className="card p-4">
          <div className="mb-2 text-sm font-semibold">{t('top_expenses')}</div>
          <div className="space-y-2">
            {d.month.top_expenses.map((x) => (
              <Link key={x.id} to={`/transactions?account_id=${x.id}`} className="block">
                <div className="flex justify-between text-sm"><span className="truncate">{x.name}</span><Money value={x.amount} /></div>
                <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-1.5 rounded-full bg-brand-600" style={{ width: `${(num(x.amount) / maxTop) * 100}%` }} /></div>
              </Link>
            ))}
            {!d.month.top_expenses.length && <div className="text-sm text-slate-400">—</div>}
          </div>
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

      <div className="card p-4">
        <div className="mb-2 text-sm font-semibold">{t('last_12_months')}</div>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={{ left: -10, right: 4 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#94a3b833" />
              <XAxis dataKey="month" fontSize={11} tickLine={false} axisLine={false} />
              <YAxis fontSize={11} tickLine={false} axisLine={false} />
              <Tooltip formatter={(v: number) => money(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey={t('income')} fill="#059669" radius={[4, 4, 0, 0]} />
              <Bar dataKey={t('expenses')} fill="#f43f5e" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
