import { useQuery } from '@tanstack/react-query'
import { FileDown, FileSpreadsheet } from 'lucide-react'
import { useState } from 'react'
import { AccountPicker } from '../components/AccountPicker'
import { Field, Money, PageTitle, Segmented, Spinner, useToast } from '../components/ui'
import { api, download, qs } from '../lib/api'
import { cls, fmtDate, monthEnd, monthStart, today } from '../lib/format'
import { useI18n } from '../lib/i18n'

type Tab = 'balance' | 'income' | 'ledger' | 'journal' | 'monthly'
type Row = { id: number; code: string; name: string; depth: number; group: boolean; amount: string }

function Tree({ rows }: { rows: Row[] }) {
  return (
    <div>
      {rows.map((r) => (
        <div key={r.id} className={cls('flex justify-between gap-2 border-b border-slate-100 py-1.5 text-sm dark:border-slate-800', r.group && 'font-semibold')} style={{ paddingLeft: `${r.depth * 0.9}rem` }}>
          <span className="truncate"><span className="mr-2 font-mono text-xs text-slate-400">{r.code}</span>{r.name}</span>
          <Money value={r.amount} />
        </div>
      ))}
    </div>
  )
}

function Total({ label, value }: { label: string; value: string | number }) {
  return <div className="flex justify-between border-t-2 border-slate-300 py-2 text-sm font-bold dark:border-slate-600"><span>{label}</span><Money value={value} /></div>
}

export function Reports() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('balance')
  const year = new Date().getFullYear()
  const [start, setStart] = useState(`${year}-01-01`)
  const [end, setEnd] = useState(today())
  const [at, setAt] = useState(today())
  const [account, setAccount] = useState<number | null>(null)

  const endpoint = {
    balance: `/api/reports/balance-sheet${qs({ at })}`,
    income: `/api/reports/income-statement${qs({ start, end })}`,
    ledger: `/api/reports/ledger${qs({ start, end, account_id: account ? [account] : [] })}`,
    journal: `/api/reports/journal${qs({ start, end })}`,
    monthly: `/api/reports/monthly${qs({ start, end })}`,
  }[tab]
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const q = useQuery<any>({ queryKey: ['reports', endpoint], queryFn: () => api.get(endpoint) })
  const exp = (format: 'pdf' | 'csv') => download(endpoint + (endpoint.includes('?') ? '&' : '?') + `format=${format}&lang=${locale}`).catch((e) => toast(String(e.message), 'error'))
  const presets: [string, string, string][] = [
    [t('this_month'), monthStart(), today()],
    [t('last_month'), monthStart(-1), monthEnd(-1)],
    [t('this_year'), `${year}-01-01`, today()],
    [t('last_year'), `${year - 1}-01-01`, `${year - 1}-12-31`],
  ]

  return (
    <div className="space-y-4">
      <PageTitle
        actions={
          <>
            <button className="btn-secondary" onClick={() => exp('pdf')}><FileDown className="h-4 w-4" /> {t('export_pdf')}</button>
            <button className="btn-secondary" onClick={() => exp('csv')}><FileSpreadsheet className="h-4 w-4" /> {t('export_csv')}</button>
          </>
        }
      >
        {t('reports')}
      </PageTitle>
      <div className="overflow-x-auto">
        <Segmented
          value={tab}
          onChange={setTab}
          options={[
            { value: 'balance', label: t('balance_sheet') },
            { value: 'income', label: t('income_statement') },
            { value: 'monthly', label: t('monthly') },
            { value: 'ledger', label: t('general_ledger') },
            { value: 'journal', label: t('journal') },
          ]}
        />
      </div>
      <div className="card flex flex-wrap items-end gap-3 p-3">
        {tab === 'balance' ? (
          <Field label={t('at_date')}><input type="date" className="input" value={at} onChange={(e) => setAt(e.target.value)} /></Field>
        ) : (
          <>
            <Field label={t('from')}><input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label={t('to')}><input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
            <div className="flex flex-wrap gap-1">
              {presets.map(([l, s, e]) => <button key={l} className="btn-ghost px-2 py-1 text-xs" onClick={() => { setStart(s); setEnd(e) }}>{l}</button>)}
            </div>
          </>
        )}
        {tab === 'ledger' && (
          <Field label={t('account')} className="min-w-[14rem] flex-1">
            <div className="flex gap-1">
              <AccountPicker value={account} onChange={(id) => setAccount(id)} placeholder={t('all_accounts')} />
              {account && <button className="btn-ghost" onClick={() => setAccount(null)}>×</button>}
            </div>
          </Field>
        )}
      </div>

      {q.isLoading || !q.data ? (
        <Spinner />
      ) : tab === 'balance' ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="card p-4"><div className="mb-2 font-semibold">{t('type_asset')}</div><Tree rows={q.data.assets} /><Total label={t('total_assets')} value={q.data.total_assets} /></div>
          <div className="card p-4">
            <div className="mb-2 font-semibold">{t('type_liability')}</div>
            <Tree rows={q.data.liabilities} />
            <div className="flex justify-between py-1.5 text-sm"><span>{t('cumulative_result')}</span><Money value={q.data.result} /></div>
            <Total label={t('total_liabilities')} value={q.data.total_passive} />
          </div>
          <div className="card p-4 md:col-span-2"><div className="flex justify-between font-semibold"><span>{t('net_worth')}</span><Money value={q.data.net_worth} /></div></div>
        </div>
      ) : tab === 'income' ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="card p-4"><div className="mb-2 font-semibold">{t('income')}</div><Tree rows={q.data.income} /><Total label={t('total')} value={q.data.total_income} /></div>
          <div className="card p-4"><div className="mb-2 font-semibold">{t('expenses')}</div><Tree rows={q.data.expenses} /><Total label={t('total')} value={q.data.total_expenses} /></div>
          <div className="card p-4 md:col-span-2"><div className="flex justify-between font-semibold"><span>{t('result')}</span><Money value={q.data.result} colored /></div></div>
        </div>
      ) : tab === 'monthly' ? (
        <div className="card overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-slate-200 dark:border-slate-700"><th className="sticky left-0 bg-white p-2 text-left dark:bg-slate-900">{t('account')}</th>{q.data.months.map((m: string) => <th key={m} className="p-2 text-right">{m.slice(5)}/{m.slice(2, 4)}</th>)}<th className="p-2 text-right">{t('total')}</th></tr></thead>
            <tbody>
              {q.data.rows.map((r: { id: number; code: string; name: string; type: string; months: Record<string, string>; total: string }) => (
                <tr key={r.id} className="border-b border-slate-100 dark:border-slate-800">
                  <td className="sticky left-0 whitespace-nowrap bg-white p-2 dark:bg-slate-900"><span className={r.type === 'income' ? 'text-emerald-700' : ''}>{r.code} {r.name}</span></td>
                  {q.data.months.map((m: string) => <td key={m} className="p-2 text-right"><Money value={r.months[m] ?? ''} /></td>)}
                  <td className="p-2 text-right font-semibold"><Money value={r.total} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : tab === 'ledger' ? (
        <div className="space-y-4">
          {q.data.map((a: { account: { id: number; code: string; name: string }; opening: string; closing: string; total_debit: string; total_credit: string; rows: { tx_id: number; date: string; description: string; counterparty: string | null; counter: string; debit: string; credit: string; balance: string }[] }) => (
            <div key={a.account.id} className="card overflow-x-auto p-4">
              <div className="mb-2 font-semibold">{a.account.code} {a.account.name}</div>
              <table className="w-full text-xs">
                <thead><tr className="border-b border-slate-200 text-slate-500 dark:border-slate-700"><th className="py-1 text-left">{t('date')}</th><th className="text-left">{t('description')}</th><th className="hidden text-left md:table-cell">{t('account')}</th><th className="text-right">{t('debit')}</th><th className="text-right">{t('credit')}</th><th className="text-right">{t('balance')}</th></tr></thead>
                <tbody>
                  <tr className="text-slate-500"><td /><td className="py-1">{t('opening')}</td><td className="hidden md:table-cell" /><td /><td /><td className="text-right"><Money value={a.opening} /></td></tr>
                  {a.rows.map((r, i) => (
                    <tr key={i} className="border-b border-slate-100 dark:border-slate-800">
                      <td className="whitespace-nowrap py-1 pr-2">{fmtDate(r.date, locale)}</td>
                      <td className="max-w-[14rem] truncate pr-2">{r.counterparty || r.description}</td>
                      <td className="hidden font-mono text-slate-400 md:table-cell">{r.counter}</td>
                      <td className="text-right">{Number(r.debit) ? <Money value={r.debit} /> : ''}</td>
                      <td className="text-right">{Number(r.credit) ? <Money value={r.credit} /> : ''}</td>
                      <td className="text-right"><Money value={r.balance} /></td>
                    </tr>
                  ))}
                  <tr className="font-semibold"><td /><td className="py-1">{t('closing')}</td><td className="hidden md:table-cell" /><td className="text-right"><Money value={a.total_debit} /></td><td className="text-right"><Money value={a.total_credit} /></td><td className="text-right"><Money value={a.closing} /></td></tr>
                </tbody>
              </table>
            </div>
          ))}
        </div>
      ) : (
        <div className="card divide-y divide-slate-100 dark:divide-slate-800">
          {q.data.map((tx: { id: number; date: string; description: string; counterparty: string | null; state: string; lines: { code: string; name: string; memo: string | null; debit: string | null; credit: string | null }[] }) => (
            <div key={tx.id} className="p-3 text-xs">
              <div className="mb-1 flex justify-between font-medium"><span>{fmtDate(tx.date, locale)} · {tx.counterparty || tx.description}</span><span className="text-slate-400">#{tx.id}</span></div>
              {tx.lines.map((l, i) => (
                <div key={i} className="grid grid-cols-[1fr_5rem_5rem] gap-2">
                  <span className={cls('truncate', l.credit && 'pl-4')}>{l.code} {l.name}{l.memo && <span className="text-slate-400"> · {l.memo}</span>}</span>
                  <span className="text-right">{l.debit && <Money value={l.debit} />}</span>
                  <span className="text-right">{l.credit && <Money value={l.credit} />}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
