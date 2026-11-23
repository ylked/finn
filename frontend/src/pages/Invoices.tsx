import { useMutation, useQuery } from '@tanstack/react-query'
import { Camera, FilePlus2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { InvoiceRow, InvoiceSheet, NewInvoiceSheet, useRefreshInvoices } from '../components/Invoice'
import { Chips, Empty, Money, PageTitle, Spinner, useToast } from '../components/ui'
import { api } from '../lib/api'
import { num } from '../lib/format'
import { useMe } from '../lib/hooks'
import { useI18n } from '../lib/i18n'
import type { Invoice } from '../lib/types'

type Tab = 'todo' | 'overdue' | 'draft' | 'paid' | 'all'

export function Invoices() {
  const { t } = useI18n()
  const toast = useToast()
  const me = useMe()
  const refresh = useRefreshInvoices()
  const [sp, setSp] = useSearchParams()
  const tab = (sp.get('tab') as Tab) || 'todo'
  const openId = sp.get('open') ? Number(sp.get('open')) : null
  const setParam = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp)
    if (v == null) n.delete(k)
    else n.set(k, v)
    setSp(n, { replace: k === 'tab' })
  }
  const [creating, setCreating] = useState(false)
  const camRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const q = useQuery({
    queryKey: ['invoices', 'list'],
    queryFn: () => api.get<Invoice[]>('/api/invoices'),
    refetchInterval: (q) => (q.state.data?.some((i) => i.reading) ? 3000 : false),
  })
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData()
      files.forEach((f) => fd.append('files', f))
      return api.post<Invoice[]>('/api/invoices/upload', fd)
    },
    onSuccess: (r) => {
      refresh()
      toast(t('inv_uploaded', { n: r.length }))
      if (r.length === 1) setParam('open', String(r[0].id))
    },
    onError: (e) => toast(String((e as Error).message), 'error'),
  })
  const onFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    // copy first: Safari empties the FileList in place when the input is reset
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    if (files.length) upload.mutate(files)
  }

  const all = q.data ?? []
  const open = all.filter((i) => i.status === 'open')
  const overdue = open.filter((i) => i.overdue)
  const drafts = all.filter((i) => i.status === 'draft')
  const paid = all.filter((i) => i.status === 'paid')
  const lists: Record<Tab, Invoice[]> = { todo: [...drafts, ...open], overdue, draft: drafts, paid, all }
  const items = lists[tab]
  const openTotal = open.reduce((s, i) => s + num(i.remaining), 0)
  const overdueTotal = overdue.reduce((s, i) => s + num(i.remaining), 0)
  const in30 = open.filter((i) => i.days_left != null && i.days_left >= 0 && i.days_left <= 30).reduce((s, i) => s + num(i.remaining), 0)

  return (
    <div className="space-y-4">
      <PageTitle actions={<button className="btn-secondary" onClick={() => setCreating(true)}><FilePlus2 className="h-4 w-4" /> {t('inv_enter')}</button>}>
        {t('nav_invoices')}
      </PageTitle>

      <div className="grid grid-cols-3 gap-3">
        <div className="card p-3 md:p-4">
          <div className="text-xs text-slate-500">{t('inv_to_pay')}</div>
          <Money value={openTotal} className="text-lg font-semibold md:text-xl" />
          <div className="text-xs text-slate-500">{t('n_invoices', { n: open.length })}</div>
        </div>
        <div className="card p-3 md:p-4">
          <div className="text-xs text-slate-500">{t('inv_next_30')}</div>
          <Money value={in30} className="text-lg font-semibold md:text-xl" />
        </div>
        <div className="card p-3 md:p-4">
          <div className="text-xs text-slate-500">{t('inv_overdue')}</div>
          <Money value={overdueTotal} className={overdueTotal > 0 ? 'text-lg font-semibold text-red-600 dark:text-red-400 md:text-xl' : 'text-lg font-semibold md:text-xl'} />
          <div className="text-xs text-slate-500">{t('n_invoices', { n: overdue.length })}</div>
        </div>
      </div>

      <div className="card space-y-3 p-4">
        <p className="text-sm text-slate-500">{t('inv_help')}</p>
        {me.data && !me.data.features.vision && <p className="text-sm text-amber-700">{t('no_vision')}</p>}
        <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={onFiles} />
        <input ref={fileRef} type="file" accept="image/*,application/pdf,.heic" multiple hidden onChange={onFiles} />
        <div className="grid grid-cols-2 gap-2">
          <button className="btn-primary py-3" onClick={() => camRef.current?.click()} disabled={upload.isPending}><Camera className="h-5 w-5" /> {t('take_photo')}</button>
          <button className="btn-secondary py-3" onClick={() => fileRef.current?.click()} disabled={upload.isPending}><Upload className="h-5 w-5" /> {t('inv_upload')}</button>
        </div>
      </div>

      <Chips
        value={tab}
        onChange={(v) => setParam('tab', v)}
        options={[
          { value: 'todo', label: t('inv_tab_todo'), count: drafts.length + open.length },
          { value: 'overdue', label: t('inv_overdue'), count: overdue.length },
          { value: 'draft', label: t('inv_drafts'), count: drafts.length },
          { value: 'paid', label: t('inv_tab_paid'), count: paid.length },
          { value: 'all', label: t('all'), count: all.length },
        ]}
      />

      {q.isLoading ? <Spinner /> : !items.length ? <Empty>{tab === 'todo' ? t('inv_none_open') : '—'}</Empty> : (
        <div className="card divide-y divide-slate-100 overflow-hidden dark:divide-slate-800">
          {items.map((i) => <InvoiceRow key={i.id} inv={i} onClick={() => setParam('open', String(i.id))} />)}
        </div>
      )}

      <InvoiceSheet id={openId} onClose={() => setParam('open', null)} />
      <NewInvoiceSheet open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setParam('open', String(id)) }} />
    </div>
  )
}
