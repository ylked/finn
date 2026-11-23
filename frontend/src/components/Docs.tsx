import { useMutation } from '@tanstack/react-query'
import { Banknote, ExternalLink, File, FileText, Paperclip, ReceiptText, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import type { DocKind } from '../lib/types'
import { Sheet, useToast } from './ui'

export const DOC_KINDS: DocKind[] = ['receipt', 'invoice', 'payment', 'other']
export const docIcons: Record<DocKind, typeof File> = { receipt: ReceiptText, invoice: FileText, payment: Banknote, other: File }

type Doc = { id: number; filename: string; mime_type: string; kind: DocKind }

export function isImage(mime: string) {
  return mime.startsWith('image/') && !mime.includes('heic') && !mime.includes('heif')
}

export function DocThumb({ d, size = 'h-10 w-10' }: { d: Doc; size?: string }) {
  if (isImage(d.mime_type)) return <img src={`/api/receipts/${d.id}/file`} className={`${size} shrink-0 rounded-lg object-cover`} loading="lazy" alt="" />
  const Icon = docIcons[d.kind] ?? FileText
  return <div className={`flex ${size} shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800`}><Icon className="h-5 w-5 text-slate-400" /></div>
}

/** Documents attached to an entry or an invoice: open, change type, delete, add (choosing the type). */
export function DocList({ docs, upload, kinds = DOC_KINDS, onChanged, addLabel }: {
  docs: Doc[]
  upload: (files: FileList, kind: DocKind) => Promise<unknown>
  kinds?: DocKind[]
  onChanged: () => void
  addLabel?: string
}) {
  const { t } = useI18n()
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [choose, setChoose] = useState(false)
  const [kind, setKind] = useState<DocKind>(kinds[0])
  const err = (e: unknown) => toast(String((e as Error).message), 'error')
  const add = useMutation({ mutationFn: (files: FileList) => upload(files, kind), onSuccess: onChanged, onError: err })
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/receipts/${id}`), onSuccess: () => { toast(t('doc_deleted')); onChanged() }, onError: err })
  const retype = useMutation({ mutationFn: ({ id, kind }: { id: number; kind: DocKind }) => api.put(`/api/receipts/${id}/kind`, { kind }), onSuccess: onChanged, onError: err })

  return (
    <div className="space-y-2">
      {docs.length > 0 && (
        <div className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
          {docs.map((d) => (
            <div key={d.id} className="flex items-center gap-3 px-3 py-2">
              <a href={`/api/receipts/${d.id}/file`} target="_blank" rel="noreferrer" className="flex min-w-0 flex-1 items-center gap-3">
                <DocThumb d={d} size="h-9 w-9" />
                <span className="min-w-0 flex-1 truncate text-sm">{d.filename}</span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0 text-slate-400" />
              </a>
              <select
                className="shrink-0 rounded-lg border-0 bg-slate-100 py-1 pl-2 pr-7 text-xs dark:bg-slate-800"
                value={d.kind}
                aria-label={t('doc_kind')}
                onChange={(e) => retype.mutate({ id: d.id, kind: e.target.value as DocKind })}
              >
                {DOC_KINDS.map((k) => <option key={k} value={k}>{t(`doc_${k}`)}</option>)}
              </select>
              <button className="btn-ghost shrink-0 p-1.5 text-red-500" aria-label={t('delete')} onClick={() => confirm(t('doc_delete_confirm', { name: d.filename })) && del.mutate(d.id)}>
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
      <input ref={fileRef} type="file" accept="image/*,application/pdf,.heic" multiple hidden onChange={(e) => { if (e.target.files?.length) add.mutate(e.target.files); e.target.value = '' }} />
      <button className="btn-ghost text-sm" onClick={() => (kinds.length > 1 ? setChoose(true) : fileRef.current?.click())} disabled={add.isPending}>
        <Paperclip className="h-4 w-4" /> {add.isPending ? '…' : addLabel ?? t('add_document')}
      </button>
      <Sheet open={choose} onClose={() => setChoose(false)} title={t('doc_kind_q')}>
        <div className="grid grid-cols-2 gap-2">
          {kinds.map((k) => {
            const Icon = docIcons[k]
            return (
              <button key={k} className="btn-secondary flex-col gap-1 py-4" onClick={() => { setKind(k); setChoose(false); fileRef.current?.click() }}>
                <Icon className="h-5 w-5" /> <span className="text-sm">{t(`doc_${k}`)}</span>
              </button>
            )
          })}
        </div>
      </Sheet>
    </div>
  )
}
