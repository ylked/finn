import { useMutation } from '@tanstack/react-query'
import { Paperclip, Plus, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AccountPicker } from '../components/AccountPicker'
import { Field, PageTitle, Segmented, useToast } from '../components/ui'
import { api } from '../lib/api'
import { cents, cls, money, parseAmount, today } from '../lib/format'
import { useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Transaction } from '../lib/types'

type Mode = 'expense' | 'income' | 'transfer' | 'advanced'
type Split = { key: number; account_id: number | null; amount: string; memo: string }
type JLine = { key: number; account_id: number | null; debit: string; credit: string; memo: string }

let seq = 0

export function NewEntry() {
  const { t } = useI18n()
  const toast = useToast()
  const nav = useNavigate()
  const refresh = useRefreshLedger()
  const fileRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<Mode>('expense')
  const [date, setDate] = useState(today())
  const [description, setDescription] = useState('')
  const [counterparty, setCounterparty] = useState('')
  const [amount, setAmount] = useState('')
  const [payment, setPayment] = useState<number | null>(() => Number(localStorage.getItem('finn.lastPayment')) || null)
  const [splits, setSplits] = useState<Split[]>([{ key: ++seq, account_id: null, amount: '', memo: '' }])
  const [target, setTarget] = useState<number | null>(null)
  const [jlines, setJlines] = useState<JLine[]>([
    { key: ++seq, account_id: null, debit: '', credit: '', memo: '' },
    { key: ++seq, account_id: null, debit: '', credit: '', memo: '' },
  ])
  const [files, setFiles] = useState<File[] | null>(null)

  const total = parseAmount(amount) ?? 0
  // first split takes the remainder
  const splitAmounts = useMemo(() => {
    const rest = splits.slice(1).reduce((s, x) => s + (parseAmount(x.amount) ?? 0), 0)
    return [Math.round((total - rest) * 100) / 100, ...splits.slice(1).map((x) => parseAmount(x.amount) ?? 0)]
  }, [splits, total])
  const jdiff = Math.round(jlines.reduce((s, l) => s + (parseAmount(l.debit) ?? 0) - (parseAmount(l.credit) ?? 0), 0) * 100) / 100

  const lines = (): { account_id: number | null; amount: string; memo?: string | null }[] => {
    if (mode === 'advanced')
      return jlines.map((l) => ({ account_id: l.account_id, amount: cents((parseAmount(l.debit) ?? 0) - (parseAmount(l.credit) ?? 0)), memo: l.memo || null }))
    if (mode === 'transfer')
      return [
        { account_id: payment, amount: cents(-total) },
        { account_id: target, amount: cents(total) },
      ]
    const sign = mode === 'expense' ? 1 : -1
    return [
      { account_id: payment, amount: cents(-sign * total) },
      ...splits.map((s, i) => ({ account_id: s.account_id, amount: cents(sign * splitAmounts[i]), memo: s.memo || null })),
    ]
  }

  const valid = (() => {
    const ls = lines()
    const sum = Math.round(ls.reduce((s, l) => s + Number(l.amount), 0) * 100)
    return ls.length >= 2 && sum === 0 && ls.every((l) => l.account_id && Number(l.amount) !== 0)
  })()

  const save = useMutation({
    mutationFn: async () => {
      const tx = await api.post<Transaction>('/api/transactions', { date, description: description || counterparty, counterparty: counterparty || null, lines: lines() })
      if (files?.length) {
        const fd = new FormData()
        Array.from(files).forEach((f) => fd.append('files', f))
        fd.append('transaction_id', String(tx.id))
        await api.post('/api/receipts', fd)
      }
      return tx
    },
    onSuccess: (tx) => {
      if (payment) localStorage.setItem('finn.lastPayment', String(payment))
      toast(t('saved'))
      refresh()
      nav(`/transactions/${tx.id}`)
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })

  return (
    <div className="mx-auto max-w-2xl">
      <PageTitle>{t('new_entry')}</PageTitle>
      <div className="mb-4">
        <Segmented
          value={mode}
          onChange={setMode}
          options={[
            { value: 'expense', label: t('mode_expense') },
            { value: 'income', label: t('mode_income') },
            { value: 'transfer', label: t('mode_transfer') },
            { value: 'advanced', label: t('mode_advanced') },
          ]}
        />
      </div>
      <div className="card space-y-4 p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('date')}>
            <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          {mode !== 'advanced' && (
            <Field label={t('amount')}>
              <input inputMode="decimal" className="input text-right text-lg font-semibold tnum" placeholder="0.00" value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
            </Field>
          )}
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label={t('counterparty')}>
            <input className="input" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} />
          </Field>
          <Field label={t('description')}>
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>

        {mode !== 'advanced' && (
          <Field label={mode === 'expense' ? t('paid_from') : mode === 'income' ? t('received_on') : t('from_account')}>
            <AccountPicker value={payment} onChange={(id) => setPayment(id)} types={['asset', 'liability']} />
          </Field>
        )}

        {mode === 'transfer' && (
          <Field label={t('to_account')}>
            <AccountPicker value={target} onChange={(id) => setTarget(id)} types={['asset', 'liability']} exclude={payment ? [payment] : []} />
          </Field>
        )}

        {(mode === 'expense' || mode === 'income') && (
          <div>
            <span className="label">{t('category')}</span>
            <div className="space-y-2">
              {splits.map((s, i) => (
                <div key={s.key} className="flex items-center gap-2">
                  <AccountPicker
                    className="flex-1"
                    value={s.account_id}
                    types={mode === 'expense' ? ['expense', 'asset', 'liability'] : ['income', 'asset', 'liability']}
                    exclude={payment ? [payment] : []}
                    onChange={(id) => setSplits((xs) => xs.map((x) => (x.key === s.key ? { ...x, account_id: id } : x)))}
                  />
                  {splits.length > 1 &&
                    (i === 0 ? (
                      <div className="w-24 text-right text-sm text-slate-500 tnum">{money(splitAmounts[0])}</div>
                    ) : (
                      <input inputMode="decimal" className="input w-24 text-right tnum" value={s.amount} onChange={(e) => setSplits((xs) => xs.map((x) => (x.key === s.key ? { ...x, amount: e.target.value } : x)))} />
                    ))}
                  {i > 0 && (
                    <button className="btn-ghost p-2 text-red-500" onClick={() => setSplits((xs) => xs.filter((x) => x.key !== s.key))}>
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <button className="btn-ghost mt-2 text-sm" onClick={() => setSplits((xs) => [...xs, { key: ++seq, account_id: null, amount: '', memo: '' }])}>
              <Plus className="h-4 w-4" /> {t('split')}
            </button>
          </div>
        )}

        {mode === 'advanced' && (
          <div>
            <div className="mb-2 flex justify-between">
              <span className="label mb-0">{t('lines')}</span>
              <span className={cls('text-xs font-medium', jdiff === 0 ? 'text-emerald-600' : 'text-red-600')}>{jdiff === 0 ? `✓ ${t('balanced')}` : t('unbalanced_by', { v: money(jdiff) })}</span>
            </div>
            <div className="space-y-2">
              {jlines.map((l) => (
                <div key={l.key} className="grid grid-cols-[1fr_1fr_auto] gap-2 rounded-xl p-2 ring-1 ring-slate-200 dark:ring-slate-800 md:grid-cols-[1fr_7rem_7rem_auto] md:ring-0">
                  <div className="col-span-3 md:col-span-1">
                    <AccountPicker value={l.account_id} onChange={(id) => setJlines((xs) => xs.map((x) => (x.key === l.key ? { ...x, account_id: id } : x)))} />
                  </div>
                  <input inputMode="decimal" placeholder={t('debit')} className="input text-right tnum" value={l.debit} onChange={(e) => setJlines((xs) => xs.map((x) => (x.key === l.key ? { ...x, debit: e.target.value, credit: '' } : x)))} />
                  <input inputMode="decimal" placeholder={t('credit')} className="input text-right tnum" value={l.credit} onChange={(e) => setJlines((xs) => xs.map((x) => (x.key === l.key ? { ...x, credit: e.target.value, debit: '' } : x)))} />
                  <button className="btn-ghost p-2 text-red-500" disabled={jlines.length <= 2} onClick={() => setJlines((xs) => xs.filter((x) => x.key !== l.key))}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
            <button
              className="btn-ghost mt-2 text-sm"
              onClick={() => setJlines((xs) => [...xs, { key: ++seq, account_id: null, debit: jdiff < 0 ? cents(-jdiff) : '', credit: jdiff > 0 ? cents(jdiff) : '', memo: '' }])}
            >
              <Plus className="h-4 w-4" /> {t('add_line')}
            </button>
          </div>
        )}

        <div className="flex items-center gap-2">
          <input ref={fileRef} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          <button className="btn-ghost text-sm" onClick={() => fileRef.current?.click()}>
            <Paperclip className="h-4 w-4" /> {files?.length ? `${files.length} ✓` : t('attach_receipt')}
          </button>
        </div>

        <p className="text-xs text-slate-500">{t('expected_match')}</p>
        <button className="btn-primary w-full py-3" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {t('save')}
        </button>
      </div>
    </div>
  )
}
