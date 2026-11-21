import { useMutation, useQuery } from '@tanstack/react-query'
import { Camera, ExternalLink, FileText, RefreshCw, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AccountPicker } from '../components/AccountPicker'
import { TxRow } from '../components/Tx'
import { Badge, Empty, Field, Money, PageTitle, Sheet, Spinner, useToast } from '../components/ui'
import { api } from '../lib/api'
import { fmtDate } from '../lib/format'
import { useAccounts, useMe, useRefreshLedger } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Receipt, Transaction } from '../lib/types'

const statusColor = { pending: 'violet', unmatched: 'amber', matched: 'green', failed: 'red', manual: 'gray' } as const

export function Receipts() {
  const { t, locale } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const me = useMe()
  const camRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [payment, setPayment] = useState<number | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const q = useQuery({
    queryKey: ['receipts'],
    queryFn: () => api.get<Receipt[]>('/api/receipts'),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'pending') ? 3000 : false),
  })
  const upload = useMutation({
    mutationFn: (files: FileList) => {
      const fd = new FormData()
      Array.from(files).forEach((f) => fd.append('files', f))
      if (payment) fd.append('payment_account_id', String(payment))
      return api.post<Receipt[]>('/api/receipts', fd)
    },
    onSuccess: () => refresh(),
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const onFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) upload.mutate(e.target.files)
    e.target.value = ''
  }

  return (
    <div className="space-y-4">
      <PageTitle>{t('receipts')}</PageTitle>
      <div className="card space-y-3 p-4">
        <p className="text-sm text-slate-500">{t('receipt_help')}</p>
        {me.data && !me.data.features.vision && <p className="text-sm text-amber-700">{t('no_vision')}</p>}
        <Field label={t('payment_account_opt')}>
          <div className="flex gap-2">
            <AccountPicker className="flex-1" value={payment} onChange={(id) => setPayment(id)} kinds={['bank', 'credit_card', 'cash']} />
            {payment && <button className="btn-ghost" onClick={() => setPayment(null)}>×</button>}
          </div>
        </Field>
        <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={onFiles} />
        <input ref={fileRef} type="file" accept="image/*,application/pdf,.heic" multiple hidden onChange={onFiles} />
        <div className="grid grid-cols-2 gap-2">
          <button className="btn-primary py-3" onClick={() => camRef.current?.click()} disabled={upload.isPending}>
            <Camera className="h-5 w-5" /> {t('take_photo')}
          </button>
          <button className="btn-secondary py-3" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            <Upload className="h-5 w-5" /> {t('upload_receipt')}
          </button>
        </div>
      </div>

      {q.isLoading ? (
        <Spinner />
      ) : !q.data?.length ? (
        <Empty>—</Empty>
      ) : (
        <div className="card divide-y divide-slate-100 overflow-hidden dark:divide-slate-800">
          {q.data.map((r) => (
            <button key={r.id} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50" onClick={() => setOpen(r.id)}>
              <Thumb r={r} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{r.extracted?.merchant || r.filename}</div>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Badge color={statusColor[r.status]}>{t(`status_${r.status}`)}</Badge>
                  {r.extracted?.date && fmtDate(r.extracted.date, locale)}
                </div>
              </div>
              {r.extracted?.total && <Money value={r.extracted.total} className="text-sm font-medium" />}
            </button>
          ))}
        </div>
      )}
      {open && <ReceiptSheet id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function Thumb({ r }: { r: Receipt }) {
  if (r.mime_type.startsWith('image/') && r.mime_type !== 'image/heic' && r.mime_type !== 'image/heif')
    return <img src={`/api/receipts/${r.id}/file`} className="h-10 w-10 shrink-0 rounded-lg object-cover" loading="lazy" alt="" />
  return <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800"><FileText className="h-5 w-5 text-slate-400" /></div>
}

function ReceiptSheet({ id, onClose }: { id: number; onClose: () => void }) {
  const { t } = useI18n()
  const toast = useToast()
  const refresh = useRefreshLedger()
  const { accounts } = useAccounts()
  const q = useQuery({
    queryKey: ['receipts', id],
    queryFn: () => api.get<Receipt>(`/api/receipts/${id}`),
    refetchInterval: (q) => (q.state.data?.status === 'pending' ? 3000 : false),
  })
  const r = q.data
  const cands = useQuery({ queryKey: ['receipts', id, 'cands', r?.extracted?.total], queryFn: () => api.get<Transaction[]>(`/api/receipts/${id}/candidates`), enabled: !!r && r.status !== 'matched' && !!r.extracted?.total })
  const [form, setForm] = useState({ merchant: '', date: '', total: '', currency: '' })
  const [payment, setPayment] = useState<number | null>(null)
  useEffect(() => {
    if (r) {
      setForm({ merchant: r.extracted?.merchant ?? '', date: r.extracted?.date ?? '', total: r.extracted?.total ?? '', currency: r.extracted?.currency ?? '' })
      setPayment(r.payment_account_id)
    }
  }, [r?.id, r?.status]) // eslint-disable-line react-hooks/exhaustive-deps
  const done = () => { refresh(); q.refetch(); cands.refetch() }
  const err = (e: unknown) => toast(String((e as Error).message), 'error')
  const m = {
    save: useMutation({ mutationFn: () => api.patch(`/api/receipts/${id}`, { merchant: form.merchant || null, date: form.date || null, total: form.total || null, currency: form.currency || null }), onSuccess: () => { toast(t('saved')); done() }, onError: err }),
    link: useMutation({ mutationFn: (txId: number) => api.post(`/api/receipts/${id}/link`, { transaction_id: txId }), onSuccess: done, onError: err }),
    unlink: useMutation({ mutationFn: () => api.post(`/api/receipts/${id}/unlink`), onSuccess: done, onError: err }),
    retry: useMutation({ mutationFn: () => api.post(`/api/receipts/${id}/retry`), onSuccess: done, onError: err }),
    create: useMutation({ mutationFn: () => api.post<Transaction>(`/api/receipts/${id}/transaction`, { payment_account_id: payment }), onSuccess: done, onError: err }),
    del: useMutation({ mutationFn: () => api.del(`/api/receipts/${id}`), onSuccess: () => { refresh(); onClose() }, onError: err }),
  }
  const codeName = (c: string) => accounts.find((a) => a.code === c)?.name ?? c

  return (
    <Sheet open onClose={onClose} title={r?.extracted?.merchant || r?.filename} wide>
      {!r ? (
        <Spinner />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            {r.mime_type === 'application/pdf' || r.mime_type.includes('heic') || r.mime_type.includes('heif') ? (
              <a className="btn-secondary w-full" href={`/api/receipts/${r.id}/file`} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /> {r.filename}</a>
            ) : (
              <a href={`/api/receipts/${r.id}/file`} target="_blank" rel="noreferrer"><img src={`/api/receipts/${r.id}/file`} className="max-h-[50vh] w-full rounded-xl object-contain" alt="" /></a>
            )}
          </div>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Badge color={statusColor[r.status]}>{t(`status_${r.status}`)}</Badge>
              {r.status === 'pending' && <Spinner className="h-4 w-4" />}
            </div>
            {r.error && <div className="text-xs text-red-600">{r.error}</div>}
            <div className="grid grid-cols-2 gap-2">
              <Field label={t('merchant')} className="col-span-2"><input className="input" value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })} /></Field>
              <Field label={t('date')}><input type="date" className="input" value={form.date?.slice(0, 10)} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
              <Field label={t('amount')}><input inputMode="decimal" className="input text-right" value={form.total} onChange={(e) => setForm({ ...form, total: e.target.value })} /></Field>
            </div>
            <button className="btn-secondary w-full" onClick={() => m.save.mutate()}>{t('save')}</button>
            {r.extracted?.splits?.length ? (
              <div className="rounded-xl bg-slate-50 p-2 text-xs dark:bg-slate-800/50">
                {r.extracted.splits.map((s, i) => (
                  <div key={i} className="flex justify-between gap-2 py-0.5"><span>{s.account_code} {codeName(s.account_code)}{s.label && <span className="text-slate-400"> · {s.label}</span>}</span><Money value={s.amount} /></div>
                ))}
              </div>
            ) : null}

            {r.transaction_id ? (
              <div className="flex gap-2">
                <Link className="btn-primary flex-1" to={`/transactions/${r.transaction_id}`}>{t('open_tx')}</Link>
                <button className="btn-secondary" onClick={() => m.unlink.mutate()}>{t('unlink')}</button>
              </div>
            ) : (
              <>
                {cands.data?.length ? (
                  <div>
                    <div className="label">{t('candidates')}</div>
                    <div className="card divide-y divide-slate-100 overflow-hidden dark:divide-slate-800">
                      {cands.data.map((tx) => (
                        <div key={tx.id} className="flex items-center">
                          <div className="min-w-0 flex-1"><TxRow tx={tx} /></div>
                          <button className="btn-primary mr-2 px-2 py-1 text-xs" onClick={() => m.link.mutate(tx.id)}>{t('link')}</button>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : r.extracted?.total ? <p className="text-xs text-slate-500">{t('no_candidates')} — {t('status_unmatched')}</p> : null}
                {r.extracted?.total && (
                  <div className="space-y-2 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
                    <AccountPicker value={payment} onChange={(id) => setPayment(id)} types={['asset', 'liability']} placeholder={t('paid_from')} />
                    <button className="btn-secondary w-full" disabled={!payment} onClick={() => m.create.mutate()}>{t('create_tx_from_receipt')}</button>
                    <p className="text-xs text-slate-500">{t('expected_match')}</p>
                  </div>
                )}
              </>
            )}
            <div className="flex gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              {(r.status === 'failed' || r.status === 'unmatched' || r.status === 'manual') && (
                <button className="btn-ghost text-sm" onClick={() => m.retry.mutate()}><RefreshCw className="h-4 w-4" /> {t('retry')}</button>
              )}
              <button className="btn-danger ml-auto text-sm" onClick={() => confirm(t('are_you_sure')) && m.del.mutate()}><Trash2 className="h-4 w-4" /> {t('delete')}</button>
            </div>
          </div>
        </div>
      )}
    </Sheet>
  )
}

