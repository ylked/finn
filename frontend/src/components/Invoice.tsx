import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Banknote, CheckCircle2, ExternalLink, FileText, Link2, RefreshCw, Trash2, Unlink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../lib/api'
import { fmtDate, money, num, today } from '../lib/format'
import { useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { DocKind, Invoice, Transaction } from '../lib/types'
import { AccountPicker } from './AccountPicker'
import { DocList, isImage } from './Docs'
import { MerchantLogo } from './Merchant'
import { TxRow, TxSheet, txAmount } from './Tx'
import { Badge, Empty, Field, Money, SearchInput, Sheet, Spinner, useToast } from './ui'

export function useRefreshInvoices() {
  const qc = useQueryClient()
  const refresh = useRefreshLedger()
  return () => {
    qc.invalidateQueries({ queryKey: ['invoices'] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
    refresh()
  }
}

export function InvoiceStatus({ inv }: { inv: Pick<Invoice, 'status' | 'overdue' | 'reading' | 'days_left'> }) {
  const { t } = useI18n()
  if (inv.reading) return <Badge color="violet"><Spinner className="h-3 w-3 text-violet-500" /> {t('status_pending')}</Badge>
  if (inv.status === 'draft') return <Badge color="gray">{t('inv_draft')}</Badge>
  if (inv.status === 'paid') return <Badge color="green"><CheckCircle2 className="h-3 w-3" /> {t('inv_paid')}</Badge>
  if (inv.overdue) return <Badge color="red"><AlertTriangle className="h-3 w-3" /> {t('inv_overdue')}</Badge>
  if (inv.days_left != null && inv.days_left <= 7) return <Badge color="amber">{t('inv_due_soon')}</Badge>
  return <Badge color="blue">{t('inv_open')}</Badge>
}

/** "due in 5 days", "3 days late", "due 30.04.2026" */
export function dueText(t: ReturnType<typeof useI18n>['t'], locale: string, inv: { status: string; due_date: string | null; days_left?: number | null; paid_date?: string | null }) {
  if (inv.status === 'paid') return inv.paid_date ? t('inv_paid_on', { d: fmtDate(inv.paid_date, locale) }) : t('inv_paid')
  if (!inv.due_date) return t('inv_no_due')
  const d = inv.days_left ?? Math.round((new Date(inv.due_date + 'T00:00').getTime() - new Date(today() + 'T00:00').getTime()) / 86400000)
  if (d < 0) return t('inv_late_n', { n: -d })
  if (d === 0) return t('inv_due_today')
  if (d <= 30) return t('inv_due_in_n', { n: d })
  return t('inv_due_on', { d: fmtDate(inv.due_date, locale) })
}

export function InvoiceRow({ inv, onClick }: { inv: Invoice; onClick: () => void }) {
  const { t, locale } = useI18n()
  return (
    <button className="flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50 md:px-4" onClick={onClick}>
      {inv.merchant ? <MerchantLogo m={inv.merchant} /> : (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800"><FileText className="h-4 w-4" /></span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{inv.issuer || inv.documents[0]?.filename || t('invoice')}{inv.number && <span className="font-normal text-slate-500"> · {inv.number}</span>}</div>
        <div className="flex items-center gap-1.5 truncate text-xs text-slate-500">
          <InvoiceStatus inv={inv} />
          <span className={inv.overdue ? 'truncate font-medium text-red-600 dark:text-red-400' : 'truncate'}>{inv.reading ? '' : dueText(t, locale, inv)}</span>
        </div>
      </div>
      <div className="text-right">
        {inv.amount ? <Money value={inv.status === 'open' ? inv.remaining : inv.amount} className="text-sm font-medium" /> : <span className="text-sm text-slate-400">—</span>}
        {inv.status === 'open' && inv.remaining && inv.amount && num(inv.remaining) !== num(inv.amount) && (
          <div className="text-xs text-slate-500">{t('inv_of_v', { v: money(inv.amount) })}</div>
        )}
      </div>
    </button>
  )
}

type Form = { issuer: string; number: string; amount: string; currency: string; issue_date: string; due_date: string; account_id: number | null; reference: string; iban: string; description: string }

function toForm(inv?: Invoice | null): Form {
  return {
    issuer: inv?.issuer ?? '', number: inv?.number ?? '', amount: inv?.amount != null ? String(inv.amount) : '', currency: inv?.currency ?? 'CHF',
    issue_date: inv?.issue_date ?? '', due_date: inv?.due_date ?? '', account_id: inv?.account_id ?? null,
    reference: inv?.reference ?? '', iban: inv?.iban ?? '', description: inv?.description ?? '',
  }
}

function payload(f: Form) {
  return {
    issuer: f.issuer || null, number: f.number || null, amount: f.amount ? f.amount.replace(',', '.').replace(/[' ]/g, '') : null,
    currency: f.currency || null, issue_date: f.issue_date || null, due_date: f.due_date || null, account_id: f.account_id,
    reference: f.reference || null, iban: f.iban || null, description: f.description || null,
  }
}

function InvoiceFields({ f, set }: { f: Form; set: (p: Partial<Form>) => void }) {
  const { t } = useI18n()
  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label={t('inv_issuer')} className="col-span-2"><input className="input" value={f.issuer} onChange={(e) => set({ issuer: e.target.value })} /></Field>
      <Field label={t('amount')}>
        <div className="flex gap-1">
          <input inputMode="decimal" className="input min-w-0 flex-1 text-right tnum" value={f.amount} onChange={(e) => set({ amount: e.target.value })} />
          <input className="input w-16 uppercase" maxLength={3} value={f.currency} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} aria-label={t('currency')} />
        </div>
      </Field>
      <Field label={t('inv_number')}><input className="input" value={f.number} onChange={(e) => set({ number: e.target.value })} /></Field>
      <Field label={t('inv_issue_date')}><input type="date" className="input" value={f.issue_date} onChange={(e) => set({ issue_date: e.target.value })} /></Field>
      <Field label={t('inv_due_date')}><input type="date" className="input" value={f.due_date} onChange={(e) => set({ due_date: e.target.value })} /></Field>
      <Field label={t('category')} className="col-span-2"><AccountPicker value={f.account_id} onChange={(id) => set({ account_id: id })} types={['expense']} /></Field>
      <Field label={t('inv_description')} className="col-span-2"><input className="input" value={f.description} onChange={(e) => set({ description: e.target.value })} /></Field>
      <Field label={t('inv_reference')} className="col-span-2 md:col-span-1"><input className="input font-mono text-xs" value={f.reference} onChange={(e) => set({ reference: e.target.value })} /></Field>
      <Field label="IBAN" className="col-span-2 md:col-span-1"><input className="input font-mono text-xs" value={f.iban} onChange={(e) => set({ iban: e.target.value })} /></Field>
    </div>
  )
}

/** Enter an invoice by hand (no document). */
export function NewInvoiceSheet({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshInvoices()
  const [f, setF] = useState<Form>(() => ({ ...toForm(), issue_date: today() }))
  useEffect(() => { if (open) setF({ ...toForm(), issue_date: today() }) }, [open])
  const create = useMutation({
    mutationFn: () => api.post<Invoice>('/api/invoices', payload(f)),
    onSuccess: (inv) => { refresh(); onCreated(inv.id) },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <Sheet open={open} onClose={onClose} title={t('inv_new')}>
      <div className="space-y-4">
        <InvoiceFields f={f} set={(p) => setF((x) => ({ ...x, ...p }))} />
        <p className="text-xs text-slate-500">{t('inv_booking_help')}</p>
        <button className="btn-primary w-full" disabled={!f.issuer && !f.amount} onClick={() => create.mutate()}>{t('save')}</button>
      </div>
    </Sheet>
  )
}

export function InvoiceSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshInvoices()
  const q = useQuery({
    queryKey: ['invoices', id],
    queryFn: () => api.get<Invoice>(`/api/invoices/${id}`),
    enabled: id != null,
    refetchInterval: (q) => (q.state.data?.reading ? 3000 : false),
  })
  const inv = q.data
  const [f, setF] = useState<Form>(toForm())
  const [linking, setLinking] = useState(false)
  const [paying, setPaying] = useState(false)
  const [openTx, setOpenTx] = useState<number | null>(null)
  useEffect(() => { if (inv) setF(toForm(inv)) }, [inv?.id, inv?.reading, inv?.status, inv?.amount]) // eslint-disable-line react-hooks/exhaustive-deps
  const done = () => { refresh(); q.refetch() }
  const err = (e: unknown) => toast(String((e as Error).message), 'error')
  const save = useMutation({ mutationFn: () => api.patch<Invoice>(`/api/invoices/${id}`, payload(f)), onSuccess: () => { toast(t('saved')); done() }, onError: err })
  const unlink = useMutation({ mutationFn: (txId: number) => api.del(`/api/invoices/${id}/payments/${txId}`), onSuccess: done, onError: err })
  const reread = useMutation({ mutationFn: () => api.post(`/api/invoices/${id}/read`), onSuccess: done, onError: err })
  const del = useMutation({ mutationFn: () => api.del(`/api/invoices/${id}`), onSuccess: () => { refresh(); onClose() }, onError: err })
  const changed = inv && JSON.stringify(payload(f)) !== JSON.stringify(payload(toForm(inv)))
  const main = inv?.documents.find((d) => d.kind === 'invoice') ?? inv?.documents[0]

  return (
    <Sheet open={id != null} onClose={onClose} title={inv ? (inv.issuer || t('invoice')) + (inv.number ? ` · ${inv.number}` : '') : t('invoice')} wide>
      {!inv ? <Spinner /> : (
        <div className="grid gap-4 md:grid-cols-[2fr_3fr]">
          <div className="space-y-3">
            {main && (isImage(main.mime_type) ? (
              <a href={`/api/receipts/${main.id}/file`} target="_blank" rel="noreferrer"><img src={`/api/receipts/${main.id}/file`} className="max-h-[45vh] w-full rounded-xl bg-slate-50 object-contain dark:bg-slate-800" alt="" /></a>
            ) : main.mime_type === 'application/pdf' ? (
              <div className="space-y-2">
                <object data={`/api/receipts/${main.id}/file#toolbar=0&view=FitH`} type="application/pdf" className="hidden h-[45vh] w-full rounded-xl md:block" aria-label={main.filename} />
                <a className="btn-secondary w-full" href={`/api/receipts/${main.id}/file`} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /> {main.filename}</a>
              </div>
            ) : null)}
            <div>
              <div className="label">{t('documents')}</div>
              <DocList
                docs={inv.documents}
                kinds={['payment', 'invoice', 'other']}
                upload={(files: FileList, kind: DocKind) => { const fd = new FormData(); Array.from(files).forEach((x) => fd.append('files', x)); fd.append('kind', kind); return api.post(`/api/invoices/${inv.id}/documents`, fd) }}
                onChanged={done}
              />
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <InvoiceStatus inv={inv} />
              {!inv.reading && <span className={inv.overdue ? 'text-sm font-medium text-red-600 dark:text-red-400' : 'text-sm text-slate-500'}>{dueText(t, locale, inv)}</span>}
            </div>
            {inv.error && <div className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{inv.error}</div>}
            {inv.reading && <p className="text-sm text-violet-600">{t('inv_reading')}</p>}

            {inv.status !== 'draft' && inv.amount && (
              <div className="grid grid-cols-2 gap-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
                <div><div className="text-xs text-slate-500">{t('amount')}</div><Money value={inv.amount} className="text-lg font-semibold" /></div>
                <div><div className="text-xs text-slate-500">{t('inv_remaining')}</div><Money value={inv.remaining} className={num(inv.remaining) > 0 ? 'text-lg font-semibold' : 'text-lg font-semibold text-emerald-700 dark:text-emerald-400'} /></div>
              </div>
            )}

            <InvoiceFields f={f} set={(p) => setF((x) => ({ ...x, ...p }))} />
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary flex-1" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
                {inv.status === 'draft' ? t('inv_save_book') : t('save')}
              </button>
              {inv.transaction_id && <button className="btn-secondary" onClick={() => setOpenTx(inv.transaction_id)}>{t('inv_booking')}</button>}
            </div>
            {inv.status === 'draft' && <p className="text-xs text-slate-500">{t('inv_booking_help')}</p>}

            {inv.transaction_id && (
              <div className="space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                <div className="label mb-0">{t('inv_payments')}</div>
                {inv.payments?.length ? (
                  <div className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
                    {inv.payments.map((p) => (
                      <div key={p.transaction.id} className="flex items-center">
                        <div className="min-w-0 flex-1"><TxRow tx={p.transaction} raw onClick={() => setOpenTx(p.transaction.id)} /></div>
                        <button className="btn-ghost mr-2 p-1.5" title={t('unlink')} aria-label={t('unlink')} onClick={() => confirm(t('inv_unlink_confirm')) && unlink.mutate(p.transaction.id)}><Unlink className="h-4 w-4" /></button>
                      </div>
                    ))}
                  </div>
                ) : <p className="text-sm text-slate-500">{t('inv_no_payment')}</p>}
                {num(inv.remaining) > 0 && (
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-secondary flex-1" onClick={() => setLinking(true)}><Link2 className="h-4 w-4" /> {t('inv_link_payment')}</button>
                    <button className="btn-secondary flex-1" onClick={() => setPaying(true)}><Banknote className="h-4 w-4" /> {t('inv_mark_paid')}</button>
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              {main && !inv.reading && <button className="btn-ghost text-sm" onClick={() => reread.mutate()}><RefreshCw className="h-4 w-4" /> {t('inv_read_again')}</button>}
              <button className="btn-danger ml-auto text-sm" onClick={() => confirm(t('inv_delete_confirm')) && del.mutate()}><Trash2 className="h-4 w-4" /> {t('delete')}</button>
            </div>
          </div>
        </div>
      )}
      {inv && linking && <LinkPaymentSheet inv={inv} onClose={() => setLinking(false)} onDone={() => { setLinking(false); done() }} />}
      {inv && paying && <PaySheet inv={inv} onClose={() => setPaying(false)} onDone={() => { setPaying(false); done() }} />}
      <TxSheet id={openTx} onClose={() => { setOpenTx(null); done() }} />
    </Sheet>
  )
}

function LinkPaymentSheet({ inv, onClose, onDone }: { inv: Invoice; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const [q, setQ] = useState('')
  const cands = useQuery({ queryKey: ['invoices', inv.id, 'cands'], queryFn: () => api.get<Transaction[]>(`/api/invoices/${inv.id}/candidates`) })
  const search = useQuery({
    queryKey: ['transactions', 'inv-search', q],
    queryFn: () => api.get<{ items: Transaction[] }>(`/api/transactions${qs({ q, limit: 20 })}`),
    enabled: q.trim().length >= 2,
  })
  const link = useMutation({
    mutationFn: (txId: number) => api.post(`/api/invoices/${inv.id}/payments`, { transaction_id: txId }),
    onSuccess: () => { toast(t('inv_linked')); onDone() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const list = q.trim().length >= 2 ? (search.data?.items ?? []).filter((x) => txAmount(x) < 0 && !x.invoice) : cands.data ?? []
  return (
    <Sheet open onClose={onClose} title={t('inv_link_payment')} tall>
      <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-3 space-y-2 bg-white px-4 pb-3 pt-4 dark:bg-slate-900">
        <p className="text-sm text-slate-500">{t('inv_link_help', { v: money(inv.remaining) })}</p>
        <SearchInput placeholder={t('search')} value={q} onChange={setQ} />
      </div>
      {(cands.isLoading || search.isFetching) && <Spinner />}
      {!list.length && !cands.isLoading ? <Empty>{t('no_candidates')}</Empty> : (
        <div className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
          {list.map((tx) => (
            <div key={tx.id} className="flex items-center">
              <div className="min-w-0 flex-1"><TxRow tx={tx} raw /></div>
              <button className="btn-primary mr-2 px-2 py-1 text-xs" disabled={link.isPending} onClick={() => link.mutate(tx.id)}>{t('link')}</button>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function PaySheet({ inv, onClose, onDone }: { inv: Invoice; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const [account, setAccount] = useState<number | null>(null)
  const [date, setDate] = useState(today())
  const pay = useMutation({
    mutationFn: () => api.post(`/api/invoices/${inv.id}/pay`, { account_id: account, date }),
    onSuccess: () => { toast(t('saved')); onDone() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  return (
    <Sheet open onClose={onClose} title={t('inv_mark_paid')}>
      <div className="space-y-3">
        <p className="text-sm text-slate-500">{t('inv_pay_help', { v: money(inv.remaining) })}</p>
        <Field label={t('paid_from')}><AccountPicker value={account} onChange={setAccount} kinds={['bank', 'credit_card', 'cash']} /></Field>
        <Field label={t('date')}><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <button className="btn-primary w-full" disabled={!account || pay.isPending} onClick={() => pay.mutate()}>{t('inv_mark_paid')}</button>
      </div>
    </Sheet>
  )
}

/** In the entry editor: the invoice this entry records or pays, or a way to link a payment to one. */
export function TxInvoice({ tx, onChanged }: { tx: Transaction; onChanged: () => void }) {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshInvoices()
  const [pick, setPick] = useState(false)
  const amt = txAmount(tx)
  const canPay = !tx.invoice && tx.source !== 'invoice' && tx.source !== 'opening' && amt < 0 && tx.lines.some((l) => ['bank', 'credit_card', 'cash'].includes(l.account_kind ?? ''))
  const open = useQuery({ queryKey: ['invoices', 'list', 'open'], queryFn: () => api.get<Invoice[]>('/api/invoices?status=open'), enabled: pick })
  const link = useMutation({
    mutationFn: (iid: number) => api.post(`/api/invoices/${iid}/payments`, { transaction_id: tx.id }),
    onSuccess: () => { toast(t('inv_linked')); setPick(false); refresh(); onChanged() },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  if (tx.invoice) {
    const i = tx.invoice
    return (
      <Link to={`/invoices?open=${i.id}`} className="flex items-center gap-3 rounded-xl bg-sky-50 p-3 text-sm text-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
        <FileText className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1">
          {i.role === 'booking' ? t('inv_tx_booking') : t('inv_tx_payment')}{' '}
          <b>{i.issuer || t('invoice')}{i.number && ` · ${i.number}`}</b>
          <span className="block text-xs opacity-80">{dueText(t, locale, { status: i.status, due_date: i.due_date })}</span>
        </span>
        <ExternalLink className="h-4 w-4 shrink-0 opacity-60" />
      </Link>
    )
  }
  if (!canPay) return null
  const sorted = [...(open.data ?? [])].sort((a, b) => Math.abs(num(a.remaining) + amt) - Math.abs(num(b.remaining) + amt))
  return (
    <>
      <button className="btn-ghost text-sm" onClick={() => setPick(true)}><FileText className="h-4 w-4" /> {t('inv_pays_invoice')}</button>
      <Sheet open={pick} onClose={() => setPick(false)} title={t('inv_pays_invoice')} tall>
        {open.isLoading ? <Spinner /> : !sorted.length ? <Empty>{t('inv_none_open')}</Empty> : (
          <div className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
            {sorted.map((inv) => (
              <div key={inv.id} className="flex items-center">
                <div className="min-w-0 flex-1"><InvoiceRow inv={inv} onClick={() => link.mutate(inv.id)} /></div>
                {Math.abs(num(inv.remaining) + amt) < 0.005 && <Badge color="green" className="mr-3">=</Badge>}
              </div>
            ))}
          </div>
        )}
      </Sheet>
    </>
  )
}
